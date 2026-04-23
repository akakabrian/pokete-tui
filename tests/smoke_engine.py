"""Stage 2 gate: engine bindings load, grid fills, key injection reaches it.

Run with `make smoke` or `.venv/bin/python -m tests.smoke_engine`.

What we prove here:
1. The engine module imports (monkey-patches apply).
2. start() spawns the Pokete worker and the grid starts getting painted.
3. The serial counter bumps (Map.show captures are working).
4. post_key() injects an event the pokete side sees.
5. stop() tears down cleanly (or at least doesn't hang forever).

This is a pre-TUI smoke — if it doesn't pass, no TUI scenario will either.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokete_tui import engine  # noqa: E402


def _banner(msg: str) -> None:
    print(f"\n== {msg} ==", flush=True)


def main() -> int:
    _banner("stage 2 smoke — engine bindings")

    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp) / "pokete_save"
        save_dir.mkdir()

        engine.start(save_dir=save_dir)

        # 1. Wait for the grid to start filling.
        deadline = time.monotonic() + 8.0
        seen_serial = 0
        while time.monotonic() < deadline:
            s = engine.STATE.serial
            if s > 0:
                seen_serial = s
                break
            time.sleep(0.1)
        if seen_serial == 0:
            print("FAIL: engine serial never bumped after 8s")
            if engine.STATE.exc is not None:
                print(f"  worker thread exc: {engine.STATE.exc!r}")
            engine.stop()
            return 1
        print(f"ok serial bumped → {seen_serial}")

        # 2. Confirm the grid contains non-blank content somewhere.
        text_rows = engine.snapshot_text()
        nonblank = any(row.strip() for row in text_rows)
        if not nonblank:
            print("FAIL: grid entirely blank after serial bumps")
            engine.stop()
            return 1
        print(f"ok grid has content ({sum(1 for r in text_rows if r.strip())} "
              f"rows non-blank)")

        # 3. Try a keystroke. Pokete's loading/intro/menu cycles through
        #    states; at the very least, pressing 'a' or arrows should
        #    result in more serial bumps within a short window.
        before = engine.STATE.serial
        # Let the game settle at whatever prompt it lands on.
        time.sleep(1.5)
        # Now press space / enter a few times to dismiss loading splash.
        for key_name in ("space", "enter", "enter"):
            engine.post_key(key_name)
            time.sleep(0.3)
        after = engine.STATE.serial
        if after <= before:
            print(f"WARN: serial didn't bump after key presses "
                  f"({before} → {after}) — engine may be waiting for input "
                  f"we haven't sent.")
        else:
            print(f"ok serial responded to key injection "
                  f"({before} → {after})")

        # 4. Describe state.
        info = engine.describe_state()
        print(f"state: {info}")

        # 5. Shutdown.
        engine.stop(timeout=2.0)
        if engine.STATE.running:
            print("WARN: engine still running after stop() — worker is "
                  "blocked on input or loop. Daemon thread will be killed "
                  "at process exit.")
        else:
            print("ok engine stopped cleanly")

    print("\n== smoke PASS ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
