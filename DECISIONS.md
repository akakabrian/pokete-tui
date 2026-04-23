# pokete-tui — design decisions

## Upstream
- **Engine:** [Pokete](https://github.com/lxgr-linux/pokete) by lxgr-linux —
  active, GPL-3.0, pure Python. Uses `scrap_engine` (same author) as a
  curses-free rendering abstraction over ANSI escape codes. Vendored at
  `engine/`.
- **Language parity:** Pokete is Python. We don't need SWIG, ctypes, or a
  shared library — we import its modules directly and run them on a
  worker thread.

## Binding strategy — strategy (1), "replace the platform layer"

Pokete renders via `scrap_engine.Map.show()`, which prints an ANSI-escaped
string to stdout at every redraw. Input enters via `Recogniser` in
`pokete.base.input.recogniser` which spawns a thread that reads from
`sys.stdin` and sets `_ev` with key events.

**We intercept both sides in pure Python, no process isolation of Pokete
itself.**

1. **Render capture.** Monkey-patch `scrap_engine.Map.show()` to copy
   `self.map` (a 2D list of cell-strings) into a thread-safe grid buffer
   on our side, instead of writing to stdout. `self.out_old` book-keeping
   stays so the dynfps skip logic still works. Submap.show overrides
   exist too — we patch those as well.
2. **Input injection.** Instead of running the Recogniser thread, call
   `_ev.set(Key)` directly from the Textual layer when a key comes in.
   No terminal raw-mode, no stdin contention.
3. **stdout silencing.** Pokete writes `\033[H` cursor resets and other
   escape codes in a few places outside Map.show (notify, loops). We
   redirect stdout to devnull on the worker thread to keep the real
   terminal clean — Textual owns the TTY.
4. **sys.argv / no-arg bootstrap.** `pokete.__main__.main()` reads argv
   via `PoketeCommand`. We set `sys.argv = ["pokete"]` on the worker
   thread so PoketeCommand uses defaults (no logging, no mods, default
   save dir).

Advantages over subprocess+pty capture:
- Zero parsing. We get the raw `(y, x) → cell_str` grid.
- No ANSI-escape re-interpretation — we can either render the cell
  strings verbatim through Rich (Rich understands ANSI), or we can parse
  them into (glyph, fg, bg) tuples once and cache the Styles.
- Cleaner shutdown: we have direct references to the game thread and
  can set `running = False` flags.
- The agent REST API reads the same grid buffer the TUI reads.

Advantages over direct import with no platform swap:
- Pokete would paint over Textual's grid. Swapping `Map.show` is the
  single narrow waist that lets Textual keep control of the terminal.

## Why not strategy (3) — fork the screen layer upstream?
Higher quality ceiling but we'd fork scrap_engine *and* Pokete, and both
are active projects. Monkey-patching `Map.show` costs one function
indirection per redraw; the performance and correctness case for forking
isn't there.

## Render contract
- Pokete calls `Map.show()` on map change.
- Our patched `show()` snapshots `self.map` (list[list[str]]) into a
  `POKETE_STATE.grid` dict keyed by map id.
- Each cell-string may contain embedded ANSI escapes
  (`"\033[37mX\033[0m"`), so the payload is variable-length — NOT a single
  glyph. We parse lazily in render_line using a pre-compiled regex and
  cache (raw_cell_str → (glyph, fg, bg)) pairs. The cache hit rate is
  high because Pokete's palette is small (~20 colors) and tiles repeat.

## Input contract
- Textual's `on_key` maps Textual key names to `pokete.base.input.key.Key`
  instances and calls `_ev.set(key)`.
- We never start Pokete's Recogniser thread (we monkey-patch its
  `__call__` to a no-op).
- `_ev.set()` calls `emit_fn()` — we keep the upstream `emit_fn` so
  timer-based "input pressed" detection still works.

## Dimensions
- Pokete's default min window is 70×20. We build the Textual app assuming
  a 100×30 logical grid; the map widget scrolls if the real terminal is
  smaller. `tss` (ResizeScreen) is normally invoked on terminal resize;
  we monkey-patch it so `__call__` uses our configured dimensions rather
  than `os.get_terminal_size()` (which would return the REAL terminal,
  not the Textual-widget dimensions).

## Agent API
- Shares the render buffer with the TUI.
- Endpoints:
  - `GET /health` — liveness + serial
  - `GET /state` — current map name, figure position, game-over flag, serial
  - `GET /snapshot?format=text|ansi` — grid dump
  - `POST /key {"key": "up"|"a"|"enter"|…}` — inject a keypress
- Unlike Brogue, Pokete doesn't take mouse input as a first-class thing
  (it has opt-in mouse support in menus via `mouse_manager`). We add
  `POST /click {x,y}` but only route it into the mouse manager when
  the current screen supports mouse input — otherwise it's a no-op.

## QA harness — process isolation
Pokete has substantial **module-level state**: `mvp.movemap`, `obmp.ob_maps`,
`asset_service`, `recogniser` (thread-local), `notifier`, timer threads,
etc. Once `main()` runs once in an interpreter, the asset service is
loaded globally, the recogniser thread is started (we patch it, but the
thread still exists), multiple daemon threads are running. Running two
QA scenarios in the same interpreter will cross-contaminate.

Same pattern as brogue-tui: **fork a subprocess per scenario**. The
harness driver spawns `python -m tests.qa --child <scenario>`, each
child runs exactly one scenario in a fresh interpreter, reports JSON on
stdout, exits. Cost: ~0.5s startup per scenario × ~10 scenarios = 5s
total. Well worth it for reliable green/red signal.

## Save data isolation
Pokete normally writes to `~/.config/pokete/` by default. We point it
at a temp dir under `tests/qa_saves/` so the harness doesn't mutate the
user's actual save. The `--save-dir` CLI flag threads through to the
worker.

## Module-level assumptions we'll need to keep alive
- `pokete.base.tss.tss` must be invoked before `loading_screen()` — it
  sets initial dimensions.
- `mvp.movemap` is mutated by map changes; anything reading it mid-tick
  must hold a lock OR accept stale reads. The grid snapshot is what we
  actually render, so stale `mvp.movemap` is fine.
- `settings("load_mods").val` defaults to True in the upstream config;
  we force it False at init so third-party mod loads don't break QA.

## Open questions parked for later
- Can we cleanly interrupt `pokete.main()` mid-game without a real SIGINT?
  Worker thread is daemonic so process exit takes it down — but save-on-
  exit requires a real signal path. Likely fine for Stage 1-5, revisit
  for polish.
- Multiplayer: Pokete has an opt-in mp mode. We ignore it for now; the
  agent API + TUI run single-player only.
