"""Textual application — re-shell over lxgr-linux/pokete.

Pokete runs on a worker thread (see engine.py); we mirror its rendered
grid into a MapView widget and forward key events back via
`_ev.set(Key)`. Stage 3 is deliberately minimal: one map pane, one
sidebar with session info, a footer.

Later stages add a message log, party/inventory panes, a battle overlay,
and an agent REST API.
"""

from __future__ import annotations

from typing import Optional

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Footer, Header, Static

from . import engine
from .screens import HelpScreen


# ---------------------------------------------------------------------------
# Rendering helpers.

_BLANK = Style(color="rgb(230,230,230)", bgcolor="rgb(0,0,0)")


def _style_for(fg: Optional[str], bg: Optional[str]) -> Style:
    """Build a rich.Style for a parsed cell. Hex strings → Color objects."""
    kwargs: dict = {}
    if fg:
        kwargs["color"] = Color.parse(fg)
    if bg:
        kwargs["bgcolor"] = Color.parse(bg)
    return Style(**kwargs) if kwargs else _BLANK


class MapView(ScrollView):
    """Shows the Pokete worker-thread grid buffer.

    Polls engine.STATE.serial at 30 Hz; repaints only on a bump. The
    parse + style build is per-cell but cached on (fg, bg) pairs — the
    palette is narrow enough that cache hit rate is near 100 % after
    the first second."""

    DEFAULT_CSS = """
    MapView {
        background: #000;
        color: #fff;
    }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._last_serial = -1
        self.virtual_size = Size(engine.STATE.cols, engine.STATE.rows)
        # Cache keyed on (raw_cell_str) — the parse is the expensive bit.
        # Value is (glyph, style).
        self._cell_cache: dict[str, tuple[str, Style]] = {}
        # Cache keyed on (fg_hex, bg_hex) — multiple cells often share
        # the same palette entry.
        self._style_cache: dict[tuple[Optional[str], Optional[str]], Style] = {}

    def on_mount(self) -> None:
        # 30 Hz refresh cap. The engine bumps serial per Map.show, so
        # this will often sleep through frames where nothing changed.
        self.set_interval(1 / 30, self._maybe_refresh)

    def _maybe_refresh(self) -> None:
        s = engine.STATE.serial
        if s != self._last_serial:
            self._last_serial = s
            # Track dimension changes (some submaps are larger than the
            # base grid — engine grows it under the lock).
            self.virtual_size = Size(engine.STATE.cols, engine.STATE.rows)
            self.refresh()

    def render_line(self, y: int) -> Strip:
        scroll_y = int(self.scroll_offset.y)
        world_y = y + scroll_y
        if world_y < 0 or world_y >= engine.STATE.rows:
            return Strip.blank(self.size.width, _BLANK)

        # Copy the row under the lock — scrap_engine may be mid-mutation
        # otherwise, and we don't want partial updates on screen.
        with engine.STATE.lock:
            row = list(engine.STATE.grid[world_y])

        # Build run-length segments: adjacent cells with identical style
        # collapse into one Segment.
        segments: list[Segment] = []
        cur_style: Style | None = None
        cur_text: list[str] = []
        cell_cache = self._cell_cache
        style_cache = self._style_cache
        for raw in row:
            entry = cell_cache.get(raw)
            if entry is None:
                glyph, fg, bg = engine.parse_cell(raw)
                sk = (fg, bg)
                style = style_cache.get(sk)
                if style is None:
                    style = _style_for(fg, bg)
                    style_cache[sk] = style
                entry = (glyph, style)
                # Bound the cache — scrap_engine has finite variety but
                # mod content can blow the bound. 10k is plenty.
                if len(cell_cache) < 10_000:
                    cell_cache[raw] = entry
            glyph, style = entry
            if style is cur_style:
                cur_text.append(glyph)
            else:
                if cur_style is not None:
                    segments.append(Segment("".join(cur_text), cur_style))
                cur_style = style
                cur_text = [glyph]
        if cur_style is not None:
            segments.append(Segment("".join(cur_text), cur_style))

        strip = Strip(segments)
        scroll_x = int(self.scroll_offset.x)
        return strip.crop(scroll_x, scroll_x + self.size.width)


class Sidebar(Static):
    """Session / engine-state panel — Textual chrome Pokete doesn't have."""

    DEFAULT_CSS = """
    Sidebar {
        width: 30;
        height: 100%;
        padding: 1;
        border: round $primary;
        background: $surface;
    }
    """

    def __init__(self, **kw) -> None:
        super().__init__("", **kw)

    def on_mount(self) -> None:
        self.border_title = "pokete-tui"
        self.set_interval(0.5, self._refresh_panel)
        self._refresh_panel()

    def _refresh_panel(self) -> None:
        info = engine.describe_state()
        lines = [
            "[bold cyan]pokete-tui[/bold cyan]",
            "",
            f"[dim]map[/dim]        {info.get('map_name') or '—'}",
            f"[dim]pos[/dim]        "
            f"{info.get('map_x', '?')},{info.get('map_y', '?')}",
            "",
            f"[dim]serial[/dim]     {info['serial']}",
            f"[dim]grid[/dim]       {info['cols']}×{info['rows']}",
            f"[dim]running[/dim]    {'yes' if info['running'] else 'no'}",
        ]
        if info.get("exc"):
            lines.append("")
            lines.append(f"[bold red]error[/bold red]: {info['exc'][:40]}")
        lines.append("")
        lines.append("[dim]ctrl+h[/dim] help")
        lines.append("[dim]ctrl+q[/dim] quit")
        self.update("\n".join(lines))


class PoketeApp(App):
    """Main Textual app — owns the engine lifecycle + keybindings."""

    CSS_PATH = "tui.tcss"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("ctrl+q", "quit", "Quit", show=True),
        # ctrl+h opens shell-level help. Plain '?' is Pokete's; we don't
        # steal it.
        Binding("ctrl+h", "show_help", "Shell Help", show=True),
    ]

    TITLE = "pokete-tui"
    SUB_TITLE = "Textual re-shell over Pokete"

    def __init__(self, *, save_dir=None, agent_port: int | None = None) -> None:
        super().__init__()
        self.save_dir = save_dir
        self.agent_port = agent_port
        self._agent_runner = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield MapView(id="map")
            yield Sidebar(id="sidebar")
        yield Footer()

    def on_mount(self) -> None:
        engine.start(save_dir=self.save_dir)
        map_view = self.query_one("#map", MapView)
        map_view.focus()
        if self.agent_port is not None:
            self.run_worker(self._start_agent(), exclusive=True)

    async def _start_agent(self) -> None:
        from . import agent_api
        assert self.agent_port is not None
        port = self.agent_port
        try:
            self._agent_runner, _ = await agent_api.serve(port=port)
            self.notify(f"agent API listening on 127.0.0.1:{port}")
        except OSError as e:
            self.notify(f"agent API failed to bind: {e}", severity="warning")

    def on_unmount(self) -> None:
        try:
            engine.stop(timeout=1.5)
        except Exception:
            pass
        if self._agent_runner is not None:
            try:
                self.run_worker(self._agent_runner.cleanup(), exclusive=True)
            except Exception:
                pass

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    # --- input forwarding ---------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        # Let app-level binds through — they'd otherwise get swallowed.
        if event.key in ("ctrl+c", "ctrl+q", "ctrl+h"):
            return

        name = event.key
        char = event.character
        if engine.post_key(name, char=char):
            event.stop()


def run(*, save_dir=None, agent_port: int | None = None) -> None:
    """Entry point. `pokete.py` calls this."""
    PoketeApp(save_dir=save_dir, agent_port=agent_port).run()
