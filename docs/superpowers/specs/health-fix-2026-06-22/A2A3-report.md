# Health Fix Report — A2 / A3 / D1 / D2 (2026-06-22)

## Status: COMPLETE — all 4 items fixed and tested, suite green.

## Fixes

### A2 — `place.to_events` crash on non-int `arrive_day`
**File:** `systems/place.py`, `PlaceSystem.validate`, moves section.
Added type check after existing `to` dangling-ref check: if `arrive_day` is present,
`int(arrive_day)` is attempted; on `TypeError`/`ValueError` a `ValidationError` with
`code="bad_type"` and field `[i].arrive_day` is appended. The gate therefore bounces
the commit for repair before `to_events` can crash.

### A3 — `lore_advanced` apply double-counts clues on re-apply
**File:** `systems/lore.py`, `LoreSystem.apply`, `lore_advanced` branch.
Changed unconditional `ln["clues_dropped"].append(hint)` to
`if hint and hint not in ln["clues_dropped"]: ln["clues_dropped"].append(hint)`.
Stage-idx update and `last_advanced_day` are unchanged.

### D1 — backstage fault-injection tests
**File:** `tests/loop/test_turn_density_hook.py` (appended to existing class).
Added 3 tests mirroring the existing `run_density` fault test:
- `test_run_lore_exception_does_not_crash_turn`
- `test_run_catchup_exception_does_not_crash_turn`
- `test_run_demote_on_leave_exception_does_not_crash_turn`

Each monkeypatches the backstage hook to raise `RuntimeError`, runs a full
`run_turn`, and asserts narration is produced and no exception escapes.

### D2 — multi-system replay-idempotency test
**File:** `tests/kernel/test_projection.py` (appended).
Added `test_multi_system_project_twice_yields_equal_worlds`: builds a realistic
event stream (L1→L2→L3 places, character, `lore_created`, two `lore_advanced`)
and projects it twice with the real full-engine registry (all 11 systems, excluding
`DirectorSystem` to avoid touching that agent's files). Asserts `meta`, lore `lines`
(clues_dropped, stage_idx, state), lore `gen`, and system-key set are all equal
between the two projections.

## Test files changed
- `tests/systems/test_place.py` — 4 new tests for A2 (`arrive_day` validation)
- `tests/systems/test_lore_system.py` — 2 new tests for A3 (dedup guard)
- `tests/loop/test_turn_density_hook.py` — 3 new D1 fault-injection tests
- `tests/kernel/test_projection.py` — 1 new D2 idempotency test

## Suite results
- Covering tests (place + lore + turn_density + projection): **86 passed**
- Full suite: **1197 passed, 1 deselected** (was 1186; +11 new tests)
- No failures, no director tests touched.

## Concerns
- `DirectorSystem` excluded from D2 registry to honour the "do not touch director files"
  constraint; D2 still covers 11 real systems and the entire lore + place + character stack.
- D1 `_run_demote_on_leave` is patched at the module level inside `loop.turn`; the
  existing guard fires only when `registry.owner_of_event("quest_demoted")` is not None
  (which it is in the seed world). The monkeypatch replaces the function before that
  guard runs, so the crash path is exercised correctly.
