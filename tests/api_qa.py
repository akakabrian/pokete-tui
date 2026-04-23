"""Agent REST API QA — start the server on a free port, hit each endpoint,
assert on shape and content.

Runs as a single subprocess (no per-test isolation needed — the server
starts fresh each invocation). Must be run AFTER the engine smoke gate.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp  # noqa: E402

from pokete_tui import engine  # noqa: E402
from pokete_tui import agent_api  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _wait_serial(target: int, timeout: float = 6.0) -> None:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if engine.STATE.serial >= target:
            return
        await asyncio.sleep(0.05)


async def run() -> int:
    port = _free_port()
    # Start engine so the snapshot endpoint has something to show.
    with tempfile.TemporaryDirectory() as tmp:
        engine.start(save_dir=Path(tmp))
        await _wait_serial(20, timeout=8.0)

        runner, _site = await agent_api.serve(port=port)
        try:
            async with aiohttp.ClientSession() as session:
                base = f"http://127.0.0.1:{port}"
                failed = []

                # health
                async with session.get(f"{base}/health") as r:
                    j = await r.json()
                    if not (r.status == 200 and j.get("ok")):
                        failed.append(f"/health bad: {r.status} {j}")

                # state
                async with session.get(f"{base}/state") as r:
                    j = await r.json()
                    if not (r.status == 200
                            and "serial" in j and "cols" in j):
                        failed.append(f"/state bad: {j}")

                # snapshot text
                async with session.get(f"{base}/snapshot") as r:
                    j = await r.json()
                    if not (r.status == 200
                            and isinstance(j.get("rows"), list)
                            and len(j["rows"]) == engine.STATE.rows):
                        failed.append(
                            f"/snapshot text bad: rows={len(j.get('rows',[]))}"
                        )
                    elif not any(row.strip() for row in j["rows"]):
                        failed.append("/snapshot text entirely blank")

                # snapshot cells
                async with session.get(
                    f"{base}/snapshot?format=cells"
                ) as r:
                    j = await r.json()
                    rows = j.get("rows") or []
                    if r.status != 200 or not rows:
                        failed.append(f"/snapshot cells bad: {j}")
                    else:
                        # Each cell should be [glyph, fg, bg].
                        sample = rows[0][0]
                        if (not isinstance(sample, list)
                                or len(sample) != 3):
                            failed.append(
                                f"/snapshot cells shape wrong: {sample!r}"
                            )

                # snapshot invalid format
                async with session.get(
                    f"{base}/snapshot?format=bogus"
                ) as r:
                    if r.status != 400:
                        failed.append(
                            f"/snapshot bogus: expected 400, got {r.status}"
                        )

                # post key — special name
                before = engine.STATE.serial
                async with session.post(
                    f"{base}/key", json={"key": "down"}
                ) as r:
                    j = await r.json()
                    if r.status != 200 or not j.get("ok"):
                        failed.append(f"POST /key down: {r.status} {j}")
                await asyncio.sleep(0.4)
                if engine.STATE.serial == before:
                    failed.append(
                        f"key press did not bump serial "
                        f"({before} -> {engine.STATE.serial})"
                    )

                # post key — char
                async with session.post(
                    f"{base}/key", json={"key": "a"}
                ) as r:
                    j = await r.json()
                    if r.status != 200 or not j.get("ok"):
                        failed.append(f"POST /key a: {r.status} {j}")

                # post key — bad
                async with session.post(
                    f"{base}/key", json={"key": "superblast"}
                ) as r:
                    if r.status != 400:
                        failed.append(
                            f"POST /key bad: expected 400, got {r.status}"
                        )

                # missing key
                async with session.post(
                    f"{base}/key", json={}
                ) as r:
                    if r.status != 400:
                        failed.append(
                            f"POST /key missing: expected 400, got {r.status}"
                        )

                if failed:
                    print(f"== api_qa: {len(failed)} failures ==")
                    for f in failed:
                        print(f"  FAIL {f}")
                    return 1
                print("== api_qa: all endpoints green ==")
                return 0
        finally:
            await runner.cleanup()
            engine.stop(timeout=1.0)


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
