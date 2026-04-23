"""Real-run playtest — spawn the full Textual app in-process with
Pilot, drive a short scripted sequence, dump a final screenshot.

This is the smoke artefact you hand a human reviewer to say "look, the
whole thing works end-to-end". Not a pass/fail gate — if the QA suite
is green, this will be too. Its job is to produce evidence.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokete_tui import engine  # noqa: E402
from pokete_tui.app import PoketeApp  # noqa: E402


OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


async def run() -> int:
    print("== pokete-tui playtest ==")
    with tempfile.TemporaryDirectory() as tmp:
        app = PoketeApp(save_dir=Path(tmp))
        async with app.run_test(size=(140, 40)) as pilot:
            # Wait for menu.
            for _ in range(80):
                if engine.STATE.serial > 30:
                    break
                await pilot.pause(0.1)
            print(f"  menu up (serial={engine.STATE.serial})")
            app.save_screenshot(str(OUT / "playtest_menu.svg"))

            # Navigate menu a bit.
            for k in ("down", "down", "up", "enter"):
                await pilot.press(k)
                await pilot.pause(0.4)
            print(f"  after nav (serial={engine.STATE.serial})")
            app.save_screenshot(str(OUT / "playtest_after_nav.svg"))

            # Open shell help.
            app.action_show_help()
            await pilot.pause(0.3)
            app.save_screenshot(str(OUT / "playtest_help.svg"))

            # Dismiss.
            await pilot.press("escape")
            await pilot.pause(0.2)
            app.save_screenshot(str(OUT / "playtest_final.svg"))

    print(f"  artefacts in {OUT}/")
    return 0


def main() -> int:
    t0 = time.monotonic()
    rc = asyncio.run(run())
    print(f"done in {time.monotonic() - t0:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
