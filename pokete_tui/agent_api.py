"""REST API for remote agents.

Background aiohttp server on the same asyncio loop as the Textual app.
Intended for LLM agent harnesses that want to drive Pokete from outside
the terminal.

Endpoints:
  GET  /health             — liveness probe
  GET  /state              — engine + figure summary
  GET  /snapshot?format=   — grid: text (default) | cells (with rgb)
  POST /key {"key": "up"}  — inject a keystroke

No auth. Bind to 127.0.0.1 only — trust the OS boundary.
"""

from __future__ import annotations

from aiohttp import web

from . import engine


_SPECIAL_KEYS = {
    "up", "down", "left", "right", "enter", "return",
    "escape", "esc", "backspace", "space", "tab",
}


async def _health(_: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "running": engine.STATE.running,
        "serial": engine.STATE.serial,
    })


async def _state(_: web.Request) -> web.Response:
    return web.json_response(engine.describe_state())


async def _snapshot(req: web.Request) -> web.Response:
    fmt = req.query.get("format", "text")
    if fmt == "text":
        rows = engine.snapshot_text()
        return web.json_response({
            "serial": engine.STATE.serial,
            "rows": rows,
        })
    elif fmt in ("cells", "rgb"):
        # List of rows, each a list of [glyph, fg_hex, bg_hex] triples.
        cells = engine.snapshot_cells()
        rows = [
            [[g, fg, bg] for (g, fg, bg) in row]
            for row in cells
        ]
        return web.json_response({
            "serial": engine.STATE.serial,
            "rows": rows,
        })
    else:
        return web.json_response(
            {"error": f"unknown format: {fmt}"}, status=400,
        )


async def _post_key(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "body must be JSON"}, status=400)
    raw = body.get("key")
    if raw is None:
        return web.json_response({"error": "'key' required"}, status=400)
    name = raw
    char = None
    if isinstance(raw, str) and len(raw) == 1:
        char = raw
        name = raw
    elif isinstance(raw, str) and raw.lower() in _SPECIAL_KEYS:
        name = raw.lower()
    else:
        return web.json_response(
            {"error": f"unsupported key: {raw!r}"}, status=400,
        )
    ok = engine.post_key(name, char=char)
    if not ok:
        return web.json_response(
            {"error": f"engine rejected key: {raw!r}"}, status=400,
        )
    return web.json_response({"ok": True, "key": raw})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/state", _state)
    app.router.add_get("/snapshot", _snapshot)
    app.router.add_post("/key", _post_key)
    return app


async def serve(*, host: str = "127.0.0.1",
                port: int = 8778) -> "tuple[web.AppRunner, web.TCPSite]":
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    return runner, site
