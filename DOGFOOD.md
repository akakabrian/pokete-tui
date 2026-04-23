# DOGFOOD — pokete

_Session: 2026-04-23T13:30:36, driver: pty, duration: 3.0 min_

**PASS** — ran for 1.2m, captured 9 snap(s), 1 milestone(s), 0 blocker(s), 0 major(s).

## Summary

Ran a rule-based exploratory session via `pty` driver. Found no findings worth flagging. Game reached 12 unique state snapshots. Captured 1 milestone shot(s); top candidates promoted to `screenshots/candidates/`. 2 coverage note(s) — see Coverage section.

## Findings

### Blockers

_None._

### Majors

_None._

### Minors

_None._

### Nits

_None._

### UX (feel-better-ifs)

_None._

## Coverage

- Driver backend: `pty`
- Keys pressed: 566 (unique: 24)
- State samples: 25 (unique: 12)
- Score samples: 0
- Milestones captured: 1
- Phase durations (s): A=49.0, B=5.9, C=18.1
- Snapshots: `/home/brian/AI/projects/tui-dogfood/reports/snaps/pokete-20260423-132921`

Unique keys exercised: 1, 2, 3, 4, ?, R, a, b, ctrl+h, d, down, enter, escape, i, left, m, n, p, r, right, s, space, up, w

### Coverage notes

- **[CN1] Phase A exited early due to saturation**
  - State hash unchanged for 10 consecutive samples after 20 golden-path loop(s); no further learning expected.
- **[CN2] Phase B exited early due to saturation**
  - State hash unchanged for 10 consecutive samples during the stress probe; remaining keys skipped.

## Milestones

| Event | t (s) | Interest | File | Note |
|---|---|---|---|---|
| first_input | 0.4 | 0.0 | `pokete-20260423-132921/milestones/first_input.txt` | key=enter |
