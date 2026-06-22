# Task 3 Implementation Report — Density Generation Integration

Date: 2026-06-21  
Branch: app  
Commits: 5aa0859..618a480

---

## What was built

### A — LoreSystem gen-state + events (systems/lore.py)

- `empty_state()` now returns `{"lines": {}, "gen": {}}`.
- `event_types()` adds `"lore_seeded"` and `"density_refreshed"`.
- `apply()` handles both new branches:
  - `lore_seeded` (deltas: `{town}`) — `gen.setdefault(town, {})["seeded"] = True` and `gen[town]["last_refresh_day"] = event["day"]` (so refresh interval counts from seeding, not from day 1).  Replay-safe: re-applying is a harmless overwrite.
  - `density_refreshed` (deltas: `{town}`) — `gen.setdefault(town, {})["last_refresh_day"] = event["day"]`.
- Two existing test assertions `empty_state() == {"lines": {}}` were updated in `test_lore_system.py` and `test_story_system.py`.
- New test file: `tests/systems/test_lore_gen_state.py` (5 tests): seeding, refresh without prior seed, idempotency, multi-town independence.

### B — run_density orchestrator (loop/density.py)

Constants added: `BASE = 10`, `REFRESH_INTERVAL_DAYS = 3`, `STAGE_COUNT = {"simple": 2, "medium": 3, "complex": 5}`.

**How L3 children (venues) of a town are found:**  
`FactGraph` exposes no reverse-edge API (no `reverse_neighbors`). The approach used is a linear scan of `g.entities.items()`, filtering for entities with `attrs["level"] == 3` and checking `g.neighbors(eid, "contained_by", day)` contains `town_id`.  This is placed in a dedicated helper `_town_venues(g, town_id, day) -> list[str]`.  The world graph is typically small (hundreds of entities), so the O(N) scan is acceptable; it is also used at most once per backstage turn call.

**run_density signature (after design refinement — drop `prev_l2`):**

```python
def run_density(registry, store, world, protagonist, *,
                provider, day, scene, turn) -> list[dict]
```

Logic:
1. Resolve protagonist's current L3 via `g.neighbors(protagonist, "located_in", day)`, then walk up via `_ancestor_of_level(g, l3, day, 2)` to get `town`.  Falls back to checking if the L3 is itself an L2.  Returns `[]` if town is None.
2. Read gen state and density/region inputs.
3. Seeding branch (`not gen.get("seeded")`): if `provider is None` → return `[]` without marking seeded (defers to when provider is available).  Otherwise roll `round(density * BASE)` slots via `roll_complexity`, call `generate_lore_batch`, `create_lore_line` per skeleton, append `lore_seeded` event.  Seeded even if 0 skeletons came back.
4. Refresh branch: if `day - last_refresh_day < REFRESH_INTERVAL_DAYS` → `[]`.  If `provider is None` → `[]`.  Roll `d100 < density * 100`; if hit, roll 1 complexity, generate 1 line.  Always append `density_refreshed` (records the check regardless of spawn).
5. Outer `run_density` wraps `_run_density_inner` in a blanket `try/except` → logs and returns `[]` on any unexpected error.

**Cap-drift choice (seeding batch):**  
All `target = round(density * BASE)` slots are rolled against the world state before the batch begins.  The world is not re-projected between individual `create_lore_line` calls.  Consequence: the cap checks during rolling see the pre-batch counts, so a single seeding call can exceed a cap by up to `(target - 1)` lines for any one tier.  For `density 0.3`, `target = 3`, so drift is at most 2 extra lines above the cap in the worst case (all 3 slots hit the same tier).  This is acceptable because (a) the batch is tiny, (b) caps are soft limits that protect against runaway generation over many turns, not against a single controlled seed, and (c) re-projection between every line would require N LLM calls instead of 1.  The refresh path is not affected: it generates at most 1 line per interval.

New test file: `tests/loop/test_run_density.py` (10 tests covering seeding, idempotency, provider-None guards, orphan-venue no-op, 0-skeleton seeding, refresh interval, provider-None at refresh, determinism).

### C — run_turn hook (loop/turn.py)

- Added `from loop.density import run_density` import.
- Hook inserted after the demote-on-leave block (~L375 in the original, now ~L392):

```python
try:
    if registry.owner_of_event("lore_seeded") is not None:
        dens_events = run_density(
            registry, store, new_world, protagonist,
            provider=(cascade_provider or provider),
            day=day, scene=scene_id, turn=turn_num_before,
        )
        if dens_events:
            new_world = project(registry, store.iter_events())
            log.debug("run_turn: density appended %d event(s)", len(dens_events))
except Exception:
    log.exception("run_turn: run_density failed (non-fatal, backstage)")
```

Provider preference: `cascade_provider or provider` — prefers the cheap cascade model; falls back to the main narrator provider when `cascade_provider` is None (e.g., `RPG_CASCADE_MODEL` not set).

New test file: `tests/loop/test_turn_density_hook.py` (4 tests): fresh-town entry generates lines + seeded marker; cascade=None fallback to main provider; narrator-only provider doesn't crash; monkeypatched crash in run_density doesn't crash the turn.

---

## Test summary

Base: 1022 passed (pre-Task-3).  
After Task 3: **1041 passed, 1 deselected** — all green.

---

## Concerns / notes

- **gen sub-state in LoreSystem, not PlaceSystem**: consistent with the integration-map decision (gen state is mutable per-town tracking, not a place attribute).
- **`_town_venues` O(N) scan**: acceptable at current scale; if world graphs reach 10k+ entities, a reverse-index in FactGraph would be needed.
- **Cap-drift**: documented above.  Considered acceptable for the seeding use case.
- **`othervenue` in test_turn_density_hook.py is at L3 inside region1 (no L2 parent)**: hero starts there, then moves to venue1 (L3 inside town1).  The density hook only fires after the move is applied.  The L3 `othervenue` has no L2 ancestor, so the hook correctly returns `[]` for the initial location and seeds `town1` after the move.
