"""Performance baseline — hot-path timings.

Run with `make test-perf`. Numbers only — the purpose is to have a
before/after record, not a pass/fail gate. If any number doubles
unexpectedly after a change, that's a regression signal.

Measured paths:
  - parse_cell on plain, color, reset — the single most-called fn
  - engine.snapshot_text on a fully-painted grid
  - MapView.render_line on a fully-painted row
  - Full-map render (30 rows) end-to-end
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pokete_tui import engine  # noqa: E402
from pokete_tui.app import MapView, PoketeApp  # noqa: E402


def _bench(name: str, fn, iterations: int) -> None:
    # Warmup — caches get hot.
    for _ in range(min(100, iterations // 10 or 1)):
        fn()
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    mean_us = statistics.mean(samples) / 1000
    p50_us = samples[len(samples) // 2] / 1000
    p95_us = samples[int(len(samples) * 0.95)] / 1000
    p99_us = samples[int(len(samples) * 0.99)] / 1000
    print(
        f"  {name:<42s} "
        f"mean={mean_us:8.2f}us "
        f"p50={p50_us:7.2f}us "
        f"p95={p95_us:7.2f}us "
        f"p99={p99_us:7.2f}us "
        f"(n={iterations})"
    )


async def main_async() -> None:
    print("== pokete-tui perf baseline ==\n")

    # 1. parse_cell micro-benchmarks — pre-engine, no worker needed.
    print("parse_cell (micro):")
    plain = "X"
    colored = "\x1b[38;2;100;200;50m@\x1b[0m"
    short = "\x1b[31m#\x1b[0m"
    _bench("parse_cell(plain)", lambda: engine.parse_cell(plain), 50_000)
    _bench("parse_cell(truecolor)", lambda: engine.parse_cell(colored), 50_000)
    _bench("parse_cell(short-ansi)", lambda: engine.parse_cell(short), 50_000)

    # 2. Full pipeline benches — spin up the app so the grid fills.
    print("\nfull pipeline (live engine):")
    with tempfile.TemporaryDirectory() as tmp:
        app = PoketeApp(save_dir=Path(tmp))
        async with app.run_test(size=(140, 40)) as pilot:
            # Wait for the menu to fully paint.
            for _ in range(80):
                if engine.STATE.serial > 30:
                    break
                await pilot.pause(0.1)
            map_view = app.query_one("#map", MapView)

            _bench(
                "engine.snapshot_text (full grid)",
                engine.snapshot_text,
                1_000,
            )
            _bench(
                "engine.snapshot_cells (full grid)",
                engine.snapshot_cells,
                500,
            )
            _bench(
                "MapView.render_line (one row)",
                lambda: map_view.render_line(10),
                5_000,
            )
            def _full():
                for y in range(engine.STATE.rows):
                    map_view.render_line(y)
            _bench(
                "MapView full-grid render (30 rows)",
                _full,
                500,
            )

    print("\n(numbers are cold-cache first iteration + warmup; "
          "cache hit-rate climbs past 99 % after ~2 frames of stable scene.)")


def main() -> int:
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    sys.exit(main())
