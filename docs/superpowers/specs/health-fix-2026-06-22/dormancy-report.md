# Dormancy Report — ★6 Implementation

## Status: COMPLETE. 1211 passed, 0 failures.

---

## Gate Placement (loop/lore.py)

### Import added (top of file)
```python
from loop.graph_utils import ancestor_of_level
```

### Protagonist resolution (before the per-line loop, after `already` set)

Inserted a block that:
1. Looks up the OntologySystem graph from `world["systems"]["ontology"]`.
2. Iterates `g.entities` to find the first `Person` with `tier=="tracked"` — the protagonist.
3. Resolves `located_in` at `day` to get the L3 place, then walks `contained_by` via `ancestor_of_level(..., 2)` to get the L2 town.
4. Falls back: if `ancestor_of_level` returns None, checks if the protagonist is already directly at an L2 place (level==2 attr).
5. `cur_town` is None when: no ontology graph, no tracked Person, no `located_in` edge, or the place has no L2 ancestor.

### Dormancy gate (inside the per-line loop, after expiry check, before `stages = ln.get(...)`)

```python
complexity_now = ln.get("complexity")
is_town_anchored = complexity_now in ("simple", "medium")
if is_town_anchored:
    line_anchor = ln.get("anchor")
    dormant = (cur_town is None) or (line_anchor != cur_town)
    if dormant:
        log.debug(...)
        continue  # freeze: skip 暗骰 advance + checkpoint rescue this turn
```

Placement: AFTER the expiry block (`quest_expired` / `quest_finale_due` / `continue` path) so expiry runs for all lines including dormant ones. BEFORE the `stages` / `idx` / `new_idx` / Oracle roll lines — so the 暗骰 advance and its downstream checkpoint rescue are fully skipped.

The `pending_finale` branch (block B) is also NOT gated. It runs before the expiry check, so complex lines' finale detection is unaffected. The dormancy gate only touches the 暗骰 advance path.

---

## Defensive rule: cur_town is None

When the protagonist has no resolvable L2 location (off-graph, unplaced, or in a place with no L2 ancestor), `cur_town = None`. All simple/medium lines are dormant. Complex lines still brew (the gate only applies to `is_town_anchored`). Expiry still runs. This matches the spec's "treat all simple/medium as dormant when player is nowhere resolvable."

---

## Existing tests adjusted (3 tests in 3 files)

These tests used `_reg()` (OntologySystem + LoreSystem only) and had no protagonist seeded, so with dormancy their simple/medium lines silently became dormant and didn't advance. Fix: switched to `_reg_full()` + `_seed_protagonist_in_anchor()` which registers PlaceSystem + CharacterSystem and seeds a tracked hero located_in the line's anchor town.

| Test | File | Reason adjusted |
|------|------|----------------|
| `test_run_lore_advances_when_threshold_passes` | `test_lore_loop.py` | Simple line with threshold=100, no protagonist → dormant |
| `test_run_lore_stops_at_last_stage` | `test_lore_loop.py` | Same — multi-call advance test |
| `test_run_lore_idempotent_on_stale_world` | `test_lore_loop.py` | Same — idempotency test |
| `test_run_lore_skips_ming_and_liujie_lines` | `test_quest_dark.py` | Simple 暗 line, no protagonist → dormant, not advanced |
| `test_run_lore_all_an_lines_still_advance` | `test_quest_dark.py` | Same |
| `test_world_push_does_not_surface_simple_line` | `test_quest_transitions.py` | Simple line at last stage, no protagonist → dormant |

Each adjusted test now explicitly places the protagonist in the line's anchor town, which is the semantically correct fixture for "the advance should happen."

---

## New tests (tests/loop/test_dormancy.py, 10 tests)

1. `test_simple_dormant_when_protagonist_in_different_town` — no `lore_advanced` for simple line when player elsewhere
2. `test_simple_dormant_stage_idx_unchanged` — `stage_idx` stays -1
3. `test_simple_advances_when_protagonist_in_anchor_town` — `lore_advanced` when player is home
4. `test_simple_advances_when_protagonist_in_l3_venue_of_anchor` — player in L3 venue → L2 resolves → not dormant
5. `test_complex_advances_even_when_protagonist_in_different_town` — complex never dormant
6. `test_dormant_simple_still_expires_on_elapsed_lifespan` — `quest_expired` fires even when dormant
7. `test_medium_dormant_when_protagonist_in_different_town` — medium follows same rule
8. `test_medium_advances_when_protagonist_in_anchor_town` — medium advances when player home
9. `test_simple_dormant_when_protagonist_has_no_location` — off-graph → dormant
10. `test_complex_advances_when_protagonist_has_no_location` — complex not frozen even off-graph
