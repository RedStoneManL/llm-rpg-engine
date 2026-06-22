# Task 1 Report — Density Logic Foundation

**Status:** DONE
**Commit range:** 74b2ecf..HEAD (see git log for short hash after commit)
**Test summary:** 1008 passed (19 new, 989 prior baseline)

## What was built

### loop/density.py (new)
Pure-logic module, no I/O, no LLM. All functions rewind-safe and deterministic.

- `DENSITY_DEFAULT=0.3`, `CAP_SIMPLE=15`, `CAP_MEDIUM=8`, `CAP_COMPLEX=2`
- `_ancestor_of_level(g, place_id, day, level)` — walks `contained_by` edges upward; adapted from `_l2_ancestor` in `loop/lore_disclosure.py`.
- `resolve_density(world, town_id, day)` — returns L1 ancestor's `density` attr if present, else `DENSITY_DEFAULT`. Edge case: L1 exists but has no density key → still returns default.
- `region_scope(world, town_id, day)` — returns L1 id if found, else `town_id` (region-less degradation).
- `count_tier(world, scope_id, complexity)` — counts `status=="active"` and `state in ("暗","明")` lines; `simple`/`medium` match by `anchor==scope_id` (town); `complex` matches by `region_scope(anchor)==scope_id` (region). Uses `world["meta"]["day"]` for edge walks.
- `roll_complexity(oracle, world, town_id, region_id)` — d100 → tier; cascade downgrade: complex→medium→simple→None when caps are full.

### systems/place.py (modified)
Extended the attr-copy loop at line 124 to include `"density"` alongside level/kind/seed/detail. Only stored when present in deltas (falsy-safe: `density=0.0` is stored because the loop checks `if k in d`, not `if d[k]`).

## Tests added
- `tests/loop/test_density_logic.py` (16 tests) — real pipeline via `store + project`; covers all five public functions including: L1 density propagation, no-L1 default, region_scope degradation, simple/medium/complex count_tier with cross-town region logic, 了结 exclusion, 明-state counts, deterministic roll sequence, tier ranges, all three downgrade scenarios, and full-caps→None.
- `tests/systems/test_place.py` (3 tests appended) — density attr stored, absent, and zero-valued.

## Concerns
None. All interfaces match the integration-map exactly. `density=0.0` edge case handled correctly by `if k in d` rather than `if d[k]`.
