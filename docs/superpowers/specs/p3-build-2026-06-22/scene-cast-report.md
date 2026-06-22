# scene-cast co-location fix — report

**Date:** 2026-06-22  
**Branch:** app  
**Base commit:** d1b85cf

## What changed

### `app/play.py::_build_scene` (the core fix)

**Before:** `present` was every tracked Person except the protagonist — a placeholder
that ignored physical location entirely.  `location` was always `meta["scene"]` (the
scene-id string, not a place entity id).

**After (co-location rule):**

1. **`location`** — derived via `g.neighbors(protagonist_id, "located_in", day)` first
   result.  Falls back to `None` if the protagonist has no `located_in` edge (the
   `meta["scene"]` field is no longer used for `location`).

2. **`present`** — every OTHER tracked Person whose first `located_in` neighbor at
   `day` equals the protagonist's place.  If the protagonist has no location, `present
   = []`.  Linear scan over `g.entities`, mirroring `loop/density.py::_town_venues`.

3. **`id`** — unchanged (`meta["scene"]` string, the scene-id for event stamping).

4. **`day`, `protagonist`** — unchanged.

Defensive: missing graph, protagonist with no edge, NPC with no edge → handled
gracefully (excluded from present / None location), never crashes.

## Why it matters

`_resolve_pov` in `llm/tools.py` gates on `pov in scene["present"]`.  Under the old
placeholder, every tracked NPC was always "present", so POV tools would never reject a
character who wasn't actually in the scene — defeating the fog-of-war discipline.
With the co-location rule, `present` only includes Persons physically at the same
immediate place as the protagonist, so querying an absent NPC's perspective now
correctly returns an error.

## Tests added (3 new)

File: `tests/app/test_play.py`

| Test | Scenario |
|---|---|
| `test_build_scene_present_only_collocated` | protagonist + npc_x in place_a, npc_y in place_b → present=[npc_x], location='place_a' |
| `test_build_scene_present_empty_when_protagonist_has_no_location` | protagonist has no located_in edge → present=[] |
| `test_build_scene_location_derived_from_graph_not_meta` | graph says 'place_real', meta says 'meta_scene_different' → location='place_real' |

All three were written RED first, verified to fail for the correct reason, then turned
GREEN by the implementation.

## Existing tests / demos adjusted

**None required adjustment.**

- `tests/loop/test_time_loop.py` — patches `play_mod._build_scene` with a
  `_fake_build_scene` mock that returns explicit scene dicts.  The real implementation
  is never called in that test, so the change is transparent.

- `tests/loop/test_density_e2e.py` — calls `run_turn` directly with hand-crafted scene
  dicts (`present=[]`); never calls `_build_scene`.

- `tests/app/test_play.py` (existing) — calls `play_loop` with `new_game` engine.
  After the fix, `_build_scene` correctly derives the protagonist's location from the
  graph (`starting_location`) and `present=[]` (no other tracked Persons in
  `new_game`).  All existing play_loop tests pass unchanged.

- `docs/superpowers/specs/**/*.py` demos (probe.py, clock_smoke.py, etc.) — these are
  not pytest tests; they run against a live LLM.  They either (a) override
  `scene["protagonist"]` after calling `_build_scene`, or (b) pass a hand-crafted
  scene to `run_turn`.  None asserts on `present` containing a non-co-located
  character, so no changes needed.

## Test summary

```
1201 passed, 1 deselected in ~54s   (0 failures, 0 errors)
```

3 new tests added; prior baseline was 1198 passed.
