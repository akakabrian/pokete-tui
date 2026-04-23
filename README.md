# pokete-tui

A Textual re-shell over [lxgr-linux/pokete](https://github.com/lxgr-linux/pokete)
— the terminal Pokemon-like. Pokete provides all the game logic
(creatures, moves, maps, battles, saves); this project wraps it in a
modern Textual UI with proper panes, a session sidebar, and an optional
REST API so remote agents can play.

Original creatures by the Pokete authors, not Nintendo IP — the project
is a clean derivative, licensed GPL-3.0 to match upstream.

## Status

Stages 1-5 of the `tui-game-build` skill are complete:

1. Research — engine identified, binding strategy chosen.
2. Engine bindings — pure-Python monkey-patch of `scrap_engine.Map.show`,
   Pokete runs on a worker thread; see `DECISIONS.md`.
3. TUI scaffold — `MapView` + `Sidebar` + `HelpScreen`.
4. QA harness — 10 scenarios, subprocess isolated, all green.
5. Perf baseline — `render_line` ~19 us, full-grid ~0.6 ms (headroom
   for 30 Hz × 10).

Also shipped:
- Agent REST API (`/health`, `/state`, `/snapshot`, `/key`) + its QA.
- Playtest harness dumping SVG artefacts.

Stage 6 phases (UI beauty, submenus, sound, LLM advisor) are future
work — the core re-shell is playable now.

## Install

```bash
make all       # clones Pokete into engine/, sets up the venv
make run       # launches the Textual app
```

## Try it

```bash
make run                       # plain TUI
make run -- --agent 8778       # TUI + REST API on 127.0.0.1:8778
make test                      # 10-scenario QA + API QA + perf
make test-only PAT=render      # only scenarios matching 'render'
make playtest                  # scripted run → tests/out/*.svg
make smoke                     # engine-only smoke (no Textual)
```

## Layout

```
pokete-tui/
├── run_pokete.py       # entry (renamed to avoid shadowing pokete pkg)
├── pokete_tui/
│   ├── engine.py       # monkey-patches + worker-thread harness
│   ├── app.py          # PoketeApp, MapView, Sidebar
│   ├── screens.py      # HelpScreen
│   ├── agent_api.py    # aiohttp REST routes
│   └── tui.tcss
├── engine/             # upstream Pokete (cloned by `make bootstrap`)
├── tests/
│   ├── smoke_engine.py  # stage 2 gate
│   ├── qa.py           # stage 4 harness (10 scenarios, subprocess)
│   ├── api_qa.py       # REST API assertions
│   ├── perf.py         # hot-path benchmarks
│   └── playtest.py     # scripted run → SVG evidence
└── DECISIONS.md        # design writeup
```

## Binding strategy (summary)

Pokete's screen layer is `scrap_engine.Map`, which prints an
ANSI-escaped string to stdout. We monkey-patch `Map.show()` so the grid
lands in a thread-safe Python buffer instead. Input mirrors in reverse:
Textual keys → `pokete.base.input._ev.set(Key)` directly, no terminal
raw-mode. Pokete itself is unchanged; we only patch `scrap_engine.Map`,
the Recogniser, `tss`, and `GameContext` (to suppress alt-screen escape
writes).

Full writeup in `DECISIONS.md`.

## License

GPL-3.0-only — matches Pokete upstream.
