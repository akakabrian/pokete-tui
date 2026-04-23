"""Engine bindings for Pokete.

Strategy (1) from DECISIONS.md: monkey-patch `scrap_engine.Map.show()` so
its 2D grid lands in our own thread-safe buffer instead of being printed
to stdout. Then start `pokete.__main__.main()` on a worker thread and
feed keystrokes by calling `pokete.base.input.event._ev.set(Key)` from
the TUI.

The cell buffer is a list[list[str]]; every cell string may contain
embedded ANSI escape codes (`"\\033[37mX\\033[0m"`). Parsing to
(glyph, fg, bg) happens lazily in the renderer.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# Ensure the vendored Pokete source is importable. The repo layout puts
# the upstream project at `engine/`, with its `src/pokete/` subpath
# holding the actual package.
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_VENDOR_SRC = _REPO / "engine" / "src"
if _VENDOR_SRC.exists() and str(_VENDOR_SRC) not in sys.path:
    sys.path.insert(0, str(_VENDOR_SRC))


# ---------------------------------------------------------------------------
# ANSI parsing — decode the cell strings that scrap_engine writes.

# Matches the leading SGR sequence if any, captures its parameters, and
# the one-character glyph that follows. Reset sequences (\033[0m) trail
# the glyph but we don't bother capturing them — we only care about the
# *applied* style for the glyph.
_ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")


def parse_cell(s: str) -> tuple[str, Optional[str], Optional[str]]:
    """Decode one cell string → (glyph, fg_rgb, bg_rgb).

    scrap_engine's Map.map cells hold payload like:
        "#"                        — plain glyph
        "\\033[37mX\\033[0m"         — dim white X
        "\\033[38;2;255;0;0mX\\033[0m" — truecolor red X
    We return hex strings ("#rrggbb") so callers can pass them straight
    to a Textual Style without re-parsing.
    """
    if not s:
        return (" ", None, None)
    # Strip ANSI codes while we scan for the payload glyph.
    fg: Optional[str] = None
    bg: Optional[str] = None
    idx = 0
    plain = []
    while idx < len(s):
        m = _ANSI_RE.match(s, idx)
        if m is None:
            plain.append(s[idx])
            idx += 1
            continue
        params = m.group(1)
        idx = m.end()
        if params == "" or params == "0":
            continue  # reset
        parts = params.split(";")
        # Walk SGR params. Only FG/BG color codes matter to us.
        i = 0
        while i < len(parts):
            p = parts[i]
            if p == "38" and i + 4 < len(parts) and parts[i + 1] == "2":
                r, g, b = int(parts[i + 2]), int(parts[i + 3]), int(parts[i + 4])
                fg = f"#{r:02x}{g:02x}{b:02x}"
                i += 5
            elif p == "48" and i + 4 < len(parts) and parts[i + 1] == "2":
                r, g, b = int(parts[i + 2]), int(parts[i + 3]), int(parts[i + 4])
                bg = f"#{r:02x}{g:02x}{b:02x}"
                i += 5
            elif p.isdigit():
                code = int(p)
                if 30 <= code <= 37:
                    fg = _BASIC_FG[code - 30]
                elif 40 <= code <= 47:
                    bg = _BASIC_FG[code - 40]
                elif 90 <= code <= 97:
                    fg = _BRIGHT_FG[code - 90]
                elif 100 <= code <= 107:
                    bg = _BRIGHT_FG[code - 100]
                # Ignore style codes (1,2,3,4,7) for now — they're rare
                # enough in Pokete's palette to skip.
                i += 1
            else:
                i += 1
    glyph = "".join(plain)[:1] or " "
    return (glyph, fg, bg)


# Standard ANSI colors — approximated to 24-bit for consistent rendering.
_BASIC_FG = [
    "#000000", "#aa0000", "#00aa00", "#aa5500",
    "#0000aa", "#aa00aa", "#00aaaa", "#aaaaaa",
]
_BRIGHT_FG = [
    "#555555", "#ff5555", "#55ff55", "#ffff55",
    "#5555ff", "#ff55ff", "#55ffff", "#ffffff",
]


# ---------------------------------------------------------------------------
# Engine state — single-instance container holding the grid, serial,
# and bookkeeping for the Pokete worker thread.

@dataclass
class EngineState:
    """Shared state between Pokete worker thread and Textual."""

    # Dimensions we configure the engine with. Pokete's hard minimum is
    # 70×20; we use 100×30 so the map pane has comfortable breathing room.
    cols: int = 100
    rows: int = 30

    # Live grid — indexed [y][x], each cell a raw scrap_engine string
    # (may contain ANSI). Produced by patched Map.show(); consumed by the
    # TUI renderer under the lock.
    grid: list[list[str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # Monotonic counter — bumps on every show(). TUI timer checks this
    # to skip redraws when nothing changed.
    serial: int = 0

    # Worker thread / run state.
    running: bool = False
    thread: Optional[threading.Thread] = None
    exc: Optional[BaseException] = None

    # Set to True by the shell when we're shutting down — the Pokete
    # worker is daemonic so process exit takes it, but set this so any
    # patched hook (notifier, loops) can short-circuit if needed.
    dying: bool = False

    # Path where Pokete writes its save data. We point this at a project-
    # local dir (or a tmpdir for tests) to avoid clobbering a real install.
    save_dir: Optional[Path] = None


STATE = EngineState()
STATE.grid = [[" "] * STATE.cols for _ in range(STATE.rows)]


def _ensure_grid_dims(h: int, w: int) -> None:
    """Grow/shrink STATE.grid to match engine dimensions."""
    with STATE.lock:
        if len(STATE.grid) != h or (STATE.grid and len(STATE.grid[0]) != w):
            STATE.grid = [[" "] * w for _ in range(h)]
            STATE.cols = w
            STATE.rows = h


# ---------------------------------------------------------------------------
# Monkey patches — applied once at start().

_patched = False


def _patch_scrap_engine() -> None:
    """Replace Map.show / Submap.show with buffer-capturing versions."""
    import scrap_engine as se

    # Capture the original to preserve dynfps skip semantics.
    def _captured_show(self, init: bool = False) -> None:  # noqa: D401
        # Build a plain stringified version of the cell grid for dynfps
        # comparison — same bookkeeping as upstream, without printing.
        rows = self.map
        # dynfps compare: join everything into one string.
        try:
            out = "".join("".join(row) for row in rows)
        except Exception:
            # Defensive — pokete occasionally has non-string fill during
            # transitions. Skip without raising.
            return
        if self.out_old == out and not init and self.dynfps:
            return
        self.out_old = out
        # Take a shallow copy — each inner list is recreated so later
        # engine mutations don't race with the TUI renderer.
        with STATE.lock:
            # If the engine is rendering a smaller sub-map (e.g. a
            # Submap during fight), paint it at the origin. Pokete blits
            # several layered maps during transitions; the last one to
            # call show() wins this frame.
            h = len(rows)
            w = len(rows[0]) if rows else 0
            if h > STATE.rows or w > STATE.cols:
                # Oversized — grow the backing grid.
                STATE.grid = [[" "] * max(w, STATE.cols)
                              for _ in range(max(h, STATE.rows))]
                STATE.rows = max(h, STATE.rows)
                STATE.cols = max(w, STATE.cols)
            # Copy cells — one list comp is cheaper than a nested loop.
            grid = STATE.grid
            for y in range(h):
                src = rows[y]
                dst = grid[y]
                # Fill visible columns from the source, pad the rest.
                for x in range(w):
                    dst[x] = src[x]
                for x in range(w, STATE.cols):
                    dst[x] = " "
            # Blank any leftover rows below h.
            for y in range(h, STATE.rows):
                grid[y] = [" "] * STATE.cols
            STATE.serial += 1

    se.Map.show = _captured_show
    # Submap inherits show from Map — but pokete's GameSubmap sets
    # show via its own superclass chain. The patched Map.show is
    # enough.


def _patch_recogniser() -> None:
    """Kill the Recogniser thread. We inject keys from Textual instead."""
    from pokete.base.input.recogniser import recogniser

    # The Recogniser's __call__ starts a blocking terminal read loop.
    # Replace with a no-op that just parks the thread forever.
    def _noop() -> None:  # noqa: D401
        while True:
            time.sleep(1.0)
            if STATE.dying:
                return

    recogniser.recogniser = _noop
    # Also defang reset() which would otherwise run termios.tcsetattr on
    # a TTY we don't own.
    recogniser.reset = lambda: None  # type: ignore[method-assign]


def _patch_game_context() -> None:
    """Stop GameContext from writing alt-screen / mouse-tracking escape
    sequences to the real terminal. Textual owns the TTY."""
    from pokete.classes import game_context as gc_mod

    class _QuietGameContext:
        def __enter__(self):
            # Kick off the no-op recogniser thread so code that depends
            # on its existence (none that we know of yet, but defensive)
            # doesn't see a missing thread. Also start single_event
            # periodic monitor the same way upstream does.
            from pokete.base.exception_propagation import PropagatingThread
            from pokete.base.input.recogniser import recogniser
            from pokete.base.single_event import single_event_periodic_event
            PropagatingThread(target=recogniser, daemon=True).start()
            try:
                single_event_periodic_event.monitor()
            except Exception:
                pass
            return self

        def __exit__(self, exc_type, exc_value, exc_tb):
            # Swallow everything that isn't a real error, and kill audio.
            try:
                from pokete.classes.audio import audio
                audio.kill()
            except Exception:
                pass
            if exc_type is KeyboardInterrupt or exc_type is SystemExit:
                return True
            return False

    gc_mod.GameContext = _QuietGameContext
    # Update the __main__ module's alias if already imported.
    for modname in list(sys.modules):
        if modname.startswith("pokete"):
            m = sys.modules[modname]
            if getattr(m, "GameContext", None) is gc_mod.GameContext:
                continue
            if hasattr(m, "GameContext"):
                try:
                    m.GameContext = _QuietGameContext
                except Exception:
                    pass


class _FixedSize:
    """Stand-in for pokete.base.tss.ResizeScreen.

    Pokete's tss module instantiates a ResizeScreen at import time, which
    calls os.get_terminal_size() — that raises OSError when stdout isn't
    a TTY (our case: we're on a worker thread in a Textual app where
    stdout has been redirected to /dev/null). We preempt the import by
    shimming our own `tss` object before pokete.base.tss is first touched.
    """

    def __init__(self, w: int, h: int) -> None:
        self.width = w
        self.height = h
        self.map = None
        self.warning_label = None
        self.size_label = None
        self.frame = None

    def __call__(self) -> bool:
        return False  # no resize


def _pre_patch_terminal_size() -> None:
    """Patch os.get_terminal_size() to return our configured dims.

    Must be called BEFORE any pokete submodule is imported — tss.py
    evaluates it at import time and several other spots call it later.
    """
    import os

    def _fake_terminal_size(fd=None):  # noqa: ARG001
        # os.terminal_size is a namedtuple-like type — return a compatible obj.
        class _TS:
            def __init__(self, cols, lines):
                self.columns = cols
                self.lines = lines

            def __iter__(self):
                yield self.columns
                yield self.lines

        return _TS(STATE.cols, STATE.rows + 1)

    os.get_terminal_size = _fake_terminal_size


def _patch_tss() -> None:
    """Swap in our _FixedSize for the already-imported tss object.

    After pre-patching os.get_terminal_size() the upstream ResizeScreen
    instantiates fine, but its __call__ path still does scrap_engine
    setup that we don't want running. Replace the instance with our
    no-op, and propagate it across every already-imported pokete module
    that bound `tss` via `from ... import tss`.
    """
    from pokete.base import tss as tss_mod

    tss_mod.tss = _FixedSize(STATE.cols, STATE.rows + 1)
    for modname in list(sys.modules):
        if modname.startswith("pokete"):
            m = sys.modules[modname]
            if getattr(m, "tss", None) is not None and m.tss is not tss_mod.tss:
                try:
                    m.tss = tss_mod.tss
                except Exception:
                    pass


def _apply_patches() -> None:
    global _patched
    if _patched:
        return
    _patch_scrap_engine()
    _patched = True
    # Recogniser + tss are imported lazily (they need pokete). Apply
    # them after the pokete package is on sys.path AND imported on the
    # worker thread. Done inside _worker_target.


# ---------------------------------------------------------------------------
# Key translation — Textual event key name → pokete Key instance.

def _pokete_key_for(name: str, char: Optional[str] = None):
    """Translate a Textual key name (lowercase) + optional character
    into a pokete.base.input.key.Key instance. Returns None if no match.
    """
    from pokete.base.input import key as kmod

    table = {
        "up": kmod.UP,
        "down": kmod.DOWN,
        "left": kmod.LEFT,
        "right": kmod.RIGHT,
        "enter": kmod.ENTER,
        "return": kmod.ENTER,
        "escape": kmod.ESC,
        "backspace": kmod.BACKSPACE,
        "space": kmod.SPACE,
        "tab": kmod.Key("\t", rep="tab"),
    }
    if name in table:
        return table[name]
    if char and len(char) == 1:
        return kmod.Key(char)
    # Single-letter names already covered by char above; bail.
    return None


# ---------------------------------------------------------------------------
# Lifecycle — start / stop the Pokete worker.

def start(*, save_dir: Optional[Path] = None) -> None:
    """Start the Pokete worker thread. Idempotent."""
    if STATE.running:
        return
    _apply_patches()

    if save_dir is not None:
        STATE.save_dir = save_dir

    def _target() -> None:
        try:
            # Pre-import patches: os.get_terminal_size is called at
            # import time by pokete.base.tss. Patch it first.
            _pre_patch_terminal_size()

            # Apply post-import patches — must happen AFTER pokete is
            # imported because they target pokete.* modules, but BEFORE
            # pokete.__main__ is imported (since __main__ binds GameContext
            # into its own namespace at import time via `from … import`).
            from pokete import release  # noqa: F401 — ensures package import
            # Force the game_context module to exist so we can swap its
            # class pointer before __main__ does `from … import GameContext`.
            import pokete.classes.game_context  # noqa: F401
            _patch_recogniser()
            _patch_tss()
            _patch_game_context()

            # We do NOT redirect sys.stdout — it's process-wide and Textual
            # depends on it for Rich console ownership. Map.show() is the
            # main culprit for terminal output and we've already patched
            # it. Any residual prints from pokete (notifier chimes,
            # loading_screen) are either empty (no TTY) or go to the
            # existing pipe Textual manages. Observed: acceptable noise.

            # Point pokete at our save dir via argv, which PoketeCommand
            # reads.
            argv = ["pokete", "--no_audio"]
            if STATE.save_dir is not None:
                argv += ["--save_dir", str(STATE.save_dir)]
            sys.argv = argv

            # Run.
            from pokete.__main__ import main as pokete_main
            pokete_main()
        except SystemExit:
            pass
        except BaseException as e:  # noqa: BLE001 — capture for later inspection
            STATE.exc = e
        finally:
            STATE.running = False

    STATE.running = True
    STATE.dying = False
    STATE.thread = threading.Thread(
        target=_target, name="pokete-engine", daemon=True,
    )
    STATE.thread.start()


def stop(timeout: float = 1.5) -> None:
    """Signal the worker to quit. Daemon thread dies with the process."""
    STATE.dying = True
    # Post an EXIT key to wake up any blocked input-waiting code.
    try:
        from pokete.base.input import _ev
        from pokete.base.input import key as kmod
        _ev.set(kmod.EXIT)
    except Exception:
        pass
    if STATE.thread is not None:
        STATE.thread.join(timeout=timeout)


def post_key(name: str, *, char: Optional[str] = None) -> bool:
    """Push a keystroke into pokete's event bus. Returns True if handled."""
    try:
        from pokete.base.input import _ev
    except Exception:
        return False
    k = _pokete_key_for(name, char)
    if k is None:
        return False
    try:
        _ev.set(k)
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# State-snapshot helpers for the agent API / UI panels.

def snapshot_text() -> list[str]:
    """Return the current grid as a list of plain-text rows (no ANSI)."""
    out = []
    with STATE.lock:
        for row in STATE.grid:
            # Each cell is a raw scrap_engine string; strip ANSI for text.
            line_chars = []
            for cell in row:
                glyph, _, _ = parse_cell(cell)
                line_chars.append(glyph)
            out.append("".join(line_chars))
    return out


def snapshot_cells() -> list[list[tuple[str, Optional[str], Optional[str]]]]:
    """Return the grid as parsed (glyph, fg, bg) tuples."""
    with STATE.lock:
        rows = [list(row) for row in STATE.grid]
    return [[parse_cell(c) for c in row] for row in rows]


def describe_state() -> dict:
    """Summary dict for GET /state."""
    figure_info = _describe_figure()
    return {
        "running": STATE.running,
        "serial": STATE.serial,
        "cols": STATE.cols,
        "rows": STATE.rows,
        "dying": STATE.dying,
        "exc": repr(STATE.exc) if STATE.exc else None,
        **figure_info,
    }


def _describe_figure() -> dict:
    """Try to pull player info from pokete modules. Best-effort."""
    try:
        from pokete.classes import movemap as mvp
    except Exception:
        return {}
    info: dict = {}
    try:
        mm = getattr(mvp, "movemap", None)
        if mm is not None:
            info["map_x"] = getattr(mm, "x", None)
            info["map_y"] = getattr(mm, "y", None)
            bmap = getattr(mm, "bmap", None)
            if bmap is not None:
                info["map_name"] = getattr(bmap, "name", None)
    except Exception:
        pass
    return info
