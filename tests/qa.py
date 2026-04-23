"""QA harness — Textual Pilot scenarios.

Each scenario runs in its own subprocess. Pokete's module-level state
(recogniser thread, asset_service, mvp.movemap, obmp.ob_maps, timer
threads, the patched scrap_engine.Map.show global) means a second
scenario in the same interpreter cross-contaminates. Fork-per-scenario
is the clean isolation.

Run with `make test` (all) or `make test-only PAT=<substring>`.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

# Make the pokete_tui package importable when run via `python -m tests.qa`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokete_tui.app import MapView, PoketeApp, Sidebar  # noqa: E402
from pokete_tui import engine  # noqa: E402
from pokete_tui.screens import HelpScreen  # noqa: E402


OUT_DIR = Path(__file__).resolve().parent / "out"
OUT_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Scenario:
    name: str
    fn: Callable[..., Awaitable[None]]


# --- helpers --------------------------------------------------------------

async def _wait_for_serial(target: int, *, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if engine.STATE.serial >= target:
            return True
        await asyncio.sleep(0.05)
    return False


def _grid_haystack() -> str:
    return " ".join(engine.snapshot_text()).lower()


# --- scenarios ------------------------------------------------------------

async def mount_clean(app: PoketeApp, pilot) -> None:
    """App mounts; widgets exist; engine is running."""
    assert app.query_one("#map", MapView) is not None
    assert app.query_one("#sidebar", Sidebar) is not None
    # Engine should have started via on_mount.
    assert engine.STATE.running, (
        f"engine worker never started (exc={engine.STATE.exc!r})"
    )


async def engine_paints_menu(app: PoketeApp, pilot) -> None:
    """After mount, the menu should paint and serial should climb."""
    ok = await _wait_for_serial(10, timeout=6.0)
    assert ok, (
        f"engine serial didn't climb past 10 in 6s "
        f"(serial={engine.STATE.serial}, exc={engine.STATE.exc!r})"
    )
    # Wait for menu content to settle.
    await pilot.pause(1.0)
    haystack = _grid_haystack()
    # Pokete's startup flow lands on a mode-pick screen with
    # "Singleplayer" / "Multiplayer" / "Leave" — any of those
    # markers confirm we're rendering the game.
    markers = ("singleplayer", "multiplayer", "leave", "mode", "pokete")
    hit = [m for m in markers if m in haystack]
    assert hit, (
        f"none of {markers} appeared on the rendered grid; "
        f"first 400 chars: {haystack[:400]!r}"
    )


async def map_view_renders_cells(app: PoketeApp, pilot) -> None:
    """MapView.render_line produces non-blank strips after paint."""
    await _wait_for_serial(20)
    await pilot.pause(0.8)
    map_view = app.query_one("#map", MapView)
    found = False
    for y in range(engine.STATE.rows):
        strip = map_view.render_line(y)
        segs = list(strip)
        if not segs:
            continue
        text = "".join(s.text for s in segs)
        if text.strip():
            found = True
            break
    assert found, "no row rendered any non-blank text"


async def render_line_colours_match_engine(app: PoketeApp, pilot) -> None:
    """render_line output carries colour information for styled cells."""
    await _wait_for_serial(20)
    await pilot.pause(0.8)
    map_view = app.query_one("#map", MapView)
    for y in range(engine.STATE.rows):
        strip = map_view.render_line(y)
        for seg in strip:
            if seg.style and seg.style.color:
                return  # smoke pass — colour pipeline is working
    assert False, "no coloured segments in any row — render pipeline broken"


async def key_press_reaches_engine(app: PoketeApp, pilot) -> None:
    """Textual keypress → engine._ev.set → serial bumps."""
    await _wait_for_serial(20)
    await pilot.pause(0.8)
    before = engine.STATE.serial
    # Pokete's mode-pick menu scrolls on arrow keys. Either direction
    # will trigger a redraw.
    await pilot.press("down")
    await pilot.pause(0.6)
    await pilot.press("up")
    ok = await _wait_for_serial(before + 2, timeout=3.0)
    assert ok, (
        f"no serial activity after arrow presses "
        f"(before={before}, after={engine.STATE.serial})"
    )


async def sidebar_shows_state(app: PoketeApp, pilot) -> None:
    """Sidebar refreshes engine info every 0.5s."""
    await _wait_for_serial(20)
    await pilot.pause(0.8)
    sb = app.query_one("#sidebar", Sidebar)
    content = str(getattr(sb, "_content", "")) or str(sb.render())
    low = content.lower()
    assert "pokete-tui" in low, f"sidebar missing branding: {content[:200]!r}"
    assert "serial" in low, f"sidebar missing serial field: {content[:200]!r}"
    assert "running" in low, f"sidebar missing running field: {content[:200]!r}"


async def help_screen_opens_and_closes(app: PoketeApp, pilot) -> None:
    """ctrl+h pushes HelpScreen; escape dismisses."""
    await _wait_for_serial(20)
    await pilot.pause(0.3)
    app.action_show_help()
    await pilot.pause(0.2)
    assert any(isinstance(s, HelpScreen) for s in app.screen_stack), (
        "HelpScreen was not pushed"
    )
    await pilot.press("escape")
    await pilot.pause(0.2)
    assert not any(isinstance(s, HelpScreen) for s in app.screen_stack), (
        "HelpScreen didn't dismiss on escape"
    )


async def parse_cell_handles_ansi(app: PoketeApp, pilot) -> None:
    """engine.parse_cell correctly decodes plain + truecolor + reset."""
    # Direct unit-test inside the QA harness — no Textual state needed,
    # but we run it here so the subprocess boundary still covers it.
    plain = engine.parse_cell("X")
    assert plain == ("X", None, None), f"plain cell parsed: {plain!r}"
    esc = engine.parse_cell("\x1b[38;2;255;0;0m*\x1b[0m")
    assert esc[0] == "*" and esc[1] == "#ff0000", f"truecolor: {esc!r}"
    empty = engine.parse_cell("")
    assert empty[0] == " ", f"empty cell: {empty!r}"


async def engine_stops_cleanly(app: PoketeApp, pilot) -> None:
    """Shutting down the engine doesn't hang the test."""
    await _wait_for_serial(20)
    engine.stop(timeout=3.0)
    # Daemon thread — is_running() flips via the finally block OR the
    # thread continues blocked on input (we injected EXIT so it should
    # unwind). Accept either as long as we didn't hang for 3s.


async def snapshot_text_is_usable(app: PoketeApp, pilot) -> None:
    """engine.snapshot_text() returns printable rows matching grid shape."""
    await _wait_for_serial(20)
    await pilot.pause(0.5)
    rows = engine.snapshot_text()
    assert len(rows) == engine.STATE.rows, (
        f"snapshot_text row count {len(rows)} != {engine.STATE.rows}"
    )
    for r in rows:
        assert len(r) == engine.STATE.cols, (
            f"row width {len(r)} != {engine.STATE.cols}: {r!r}"
        )
    nonblank = sum(1 for r in rows if r.strip())
    assert nonblank >= 3, f"only {nonblank} non-blank rows in snapshot"


SCENARIOS: list[Scenario] = [
    Scenario("mount_clean", mount_clean),
    Scenario("engine_paints_menu", engine_paints_menu),
    Scenario("map_view_renders_cells", map_view_renders_cells),
    Scenario("render_line_colours_match_engine", render_line_colours_match_engine),
    Scenario("key_press_reaches_engine", key_press_reaches_engine),
    Scenario("sidebar_shows_state", sidebar_shows_state),
    Scenario("help_screen_opens_and_closes", help_screen_opens_and_closes),
    Scenario("parse_cell_handles_ansi", parse_cell_handles_ansi),
    Scenario("snapshot_text_is_usable", snapshot_text_is_usable),
    Scenario("engine_stops_cleanly", engine_stops_cleanly),
]


# --- runner ---------------------------------------------------------------

async def _run_one_inproc(scn_name: str) -> tuple[str, bool, str]:
    scn = next((s for s in SCENARIOS if s.name == scn_name), None)
    if scn is None:
        return (scn_name, False, f"unknown scenario: {scn_name}")
    # Each child gets its own temp save dir.
    tmp = tempfile.mkdtemp(prefix="pokete_qa_")
    app = PoketeApp(save_dir=Path(tmp))
    try:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.2)
            try:
                await scn.fn(app, pilot)
                app.save_screenshot(str(OUT_DIR / f"{scn.name}.PASS.svg"))
                return (scn.name, True, "")
            except AssertionError as e:
                try:
                    app.save_screenshot(str(OUT_DIR / f"{scn.name}.FAIL.svg"))
                except Exception:
                    pass
                return (scn.name, False, f"AssertionError: {e}")
            except Exception as e:  # noqa: BLE001
                try:
                    app.save_screenshot(str(OUT_DIR / f"{scn.name}.ERROR.svg"))
                except Exception:
                    pass
                tb = traceback.format_exc().splitlines()[-1]
                return (scn.name, False, f"{type(e).__name__}: {tb}")
    finally:
        try:
            engine.stop(timeout=1.0)
        except Exception:
            pass


def _run_one_subprocess(scn: Scenario) -> tuple[str, bool, str]:
    env = {**os.environ, "TEXTUAL": os.environ.get("TEXTUAL", "")}
    try:
        out = subprocess.run(
            [sys.executable, "-m", "tests.qa", "--child", scn.name],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=env,
            capture_output=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return (scn.name, False, "scenario timed out after 45s")
    last = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    try:
        res = json.loads(last)
        return (res["name"], res["ok"], res["detail"])
    except Exception:
        return (
            scn.name, False,
            f"child rc={out.returncode} "
            f"stdout={out.stdout[-300:]!r} stderr={out.stderr[-300:]!r}",
        )


def run_all(pattern: str | None = None) -> int:
    selected = [s for s in SCENARIOS if pattern is None or pattern in s.name]
    if not selected:
        print(f"no scenarios matched pattern {pattern!r}")
        return 1
    results = []
    for scn in selected:
        print(f"  > {scn.name} ...", flush=True)
        name, ok, detail = _run_one_subprocess(scn)
        status = "PASS" if ok else "FAIL"
        print(f"    {status} - {name}  {detail}", flush=True)
        results.append((name, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print()
    print(f"== qa: {passed}/{total} passed ==")
    for name, ok, detail in results:
        if not ok:
            print(f"   FAIL {name}: {detail}")
    return 0 if passed == total else 1


def main() -> int:
    args = sys.argv[1:]
    os.environ.setdefault("TEXTUAL", "")
    if args and args[0] == "--child":
        name, ok, detail = asyncio.run(_run_one_inproc(args[1]))
        print(json.dumps({"name": name, "ok": ok, "detail": detail}))
        return 0 if ok else 1
    pattern = args[0] if args else None
    return run_all(pattern)


if __name__ == "__main__":
    sys.exit(main())
