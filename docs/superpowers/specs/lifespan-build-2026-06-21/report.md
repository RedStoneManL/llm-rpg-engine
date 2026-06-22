# Quest Lifespan Build Report — 2026-06-21

## Status: DONE

## Summary

Implemented quest lifespan + expiry + idle-demote-by-day as specified in
`docs/superpowers/specs/2026-06-21-quest-lifespan-design.md`.

## Changes per file

### `systems/lore.py`

- Added `_LIFESPAN_DEFAULTS = {"simple": 3, "medium": 7, "complex": 20}` constant.
- Added `quest_expired` and `quest_finale_due` to `event_types()`.
- `lore_created` apply: folds `born_day = event.get("day")` and `lifespan_days`
  (from deltas or default by complexity) into the new line dict.
- `quest_created` apply: same born_day/lifespan_days folding.
- `quest_opened` apply: same folding; also sets `last_advanced_day = born_day`.
- `lore_advanced` apply: sets `last_advanced_day = event.get("day")`.
- `quest_surfaced` apply: sets `last_advanced_day = event.get("day")`.
- `quest_advanced` apply: sets `last_advanced_day = event.get("day")`.
- New `quest_expired` apply: replay-safe (guard state==了结); sets state=了结, resolved={"by":"expiry"}.
- New `quest_finale_due` apply: replay-safe (guard pending_finale or state==了结); sets pending_finale=True.

### `loop/lore.py`

- `create_lore_line`: added optional `lifespan_days` param (L3 hook).
- `run_lore`: expiry check block before the 暗骰 advance loop.
  - Reads `now_day = world.meta.day`.
  - Per 暗 line with born_day+lifespan_days: if (now_day-born_day) >= lifespan_days:
    - complex → emit quest_finale_due (guard: skip if already pending_finale or 了结).
    - simple/medium → emit quest_expired.
    - continue (skip 暗骰-advance this trip).
  - Defensive: if born_day or lifespan_days is None (legacy), skip expiry entirely.

### `loop/turn.py`

- Added `IDLE_DEMOTE_DAYS = 2`.
- `_run_demote_on_leave` Rule (b): changed from turn-based to day-based:
  `last_adv_day = ln.get("last_advanced_day"); (day - last_adv_day) >= IDLE_DEMOTE_DAYS`.
  `day` already threaded in from run_turn call site. No call site change needed.

### `tests/loop/test_quest_transitions.py`

- Updated test_idle_demote_fires: uses IDLE_DEMOTE_DAYS, day-based, asserts last_advanced_day.
- Updated test_idle_demote_no_fire_recent: day-based values.
- Updated test_last_advanced_turn_on_surface/advance: added assertions for last_advanced_day.
- Added 14 new tests (born_day, lifespan defaults, last_advanced_day, expiry simple/medium/complex,
  finale idempotency, legacy guard, idle-demote by day fires/no-fire).

## Design notes

- `last_advanced_turn` kept (not removed) alongside `last_advanced_day` for backward compat.
  The day-based logic supersedes it in `_run_demote_on_leave`.
- `IDLE_DEMOTE_TURNS = 3` kept as constant for backward compat (no callers removed).
- `quest_opened` sets `last_advanced_day = born_day` so a brand-new 明 line doesn't idle-demote immediately.

## Test counts

Baseline: 974 passed
After build: 988 passed (14 new tests), 1 deselected (pre-existing, unrelated)
Full suite: all green, zero regressions.

---

## Fix pass (commit cc7182d, 2026-06-21)

Applied all 5 review findings against base d3a054f.

**I1 — vestigial turn-based field/constant**
- Removed `ln["last_advanced_turn"] = turn` from `quest_surfaced` apply (systems/lore.py).
- Removed `ln["last_advanced_turn"] = event.get("turn")` from `quest_advanced` apply (systems/lore.py).
- Removed `IDLE_DEMOTE_TURNS = 3` constant (loop/turn.py).
- Updated `test_last_advanced_turn_on_surface` and `test_last_advanced_turn_on_advance`: dropped dead `last_advanced_turn` assertions; retained live `last_advanced_day`/`surfaced_turn` assertions and updated docstrings. Tests kept (not deleted) as they still assert non-redundant values.
- Updated stale docstring in `_setup_ming_line_world` helper.
- Grep confirms zero remaining writes/reads of `last_advanced_turn` or `IDLE_DEMOTE_TURNS` in production code.

**I2 — expiry events stamped with wrong day**
- Changed `day=day` → `day=now_day` in both `quest_finale_due` and `quest_expired` `kernel_event(...)` calls in `run_lore` (loop/lore.py).

**m1 — misleading test comment**
- Fixed comment in `test_idle_demote_no_fire_recent`: now reads "surface at turn 1 day=1, advance at turn 3 day=1 (helper uses day=1 throughout), now_day=2 → idle=1 < IDLE_DEMOTE_DAYS=2 → no fire".

**m2 — missing test for lore_advanced → last_advanced_day**
- Added `test_last_advanced_day_set_on_lore_advanced`: creates a 暗 line, applies `lore_advanced` event with day=9, asserts `last_advanced_day==9`. Production code was already correct; test guards it.

**m3 — now_day falsy-zero footgun**
- Replaced `now_day = (world.get("meta", {}) or {}).get("day") or day` with explicit None-check:
  `_md = (...).get("day"); now_day = _md if _md is not None else day` (loop/lore.py).

Covering tests: `tests/loop/test_quest_transitions.py` — 32 passed (was 31; +1 from m2).
Full suite: 989 passed, 1 deselected.
