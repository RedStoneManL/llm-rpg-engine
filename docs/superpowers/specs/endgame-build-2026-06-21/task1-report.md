# Task 1 Build Report — complex-line endgame core + LoreSystem events

Date: 2026-06-21  
Branch: app  
Base commit: b559c90

---

## Files created / modified

### Created: `loop/endgame.py`
Pure-logic module. No random/time calls; callers pass a seeded Oracle.

Constants:
```python
RESCUE_GRACE_STAGES = 1
RESCUE_BASE = 10
RESCUE_RANGE = 40
FINALE_RESCUE_CHANCE = 60
```

Functions:
- `world_rescue_chance(stage_idx, n_stages) -> int`  
  Formula: `max(0, min(100, RESCUE_BASE + round(stage_idx / max(1, n_stages-1) * RESCUE_RANGE)))`  
  At stage 0 → 10; at last stage (n=5) → 50; monotonic non-decreasing; clamped [0,100].
- `roll_world_rescue(oracle, stage_idx, n_stages) -> bool`  
  Returns `oracle.d100() <= world_rescue_chance(stage_idx, n_stages)`.
- `rescue_summary(line) -> str`  
  Template: `f"【世界自行了结】{about}：外力介入，事态平息"`
- `catastrophe_summary(line, region) -> str`  
  Template: `f"【终局】{about}失控，{secret}，波及{region}"`
- `build_catastrophe_events(line, world, *, day, scene, turn, emit_world_change=True) -> list[dict]`  
  Returns `[quest_catastrophe, world_change]` (or just `[quest_catastrophe]` when `emit_world_change=False`).

### Modified: `systems/lore.py`

Added to `event_types()`:
- `"quest_world_resolved"`
- `"quest_catastrophe"`

Added `apply()` branches (both with replay-safe guard: skip if `state == "了结"`):

**`quest_world_resolved`** deltas shape: `{id, by?, summary?}`
- Sets `state="了结"`, `status="resolved"`, `resolved={"by": d.get("by", "world_rescue"), "summary": d.get("summary")}`, `pending_finale=False`.

**`quest_catastrophe`** deltas shape: `{id, summary?, anchor?}`
- Sets `state="了结"`, `status="resolved"`, `resolved={"by": d.get("by", "catastrophe"), "summary": d.get("summary")}`, `pending_finale=False`.

---

## `world_change` event shape (for Task 2)

Based on grep + reading `loop/cascade.py` lines 463-476 and `systems/cascade.py` lines 101-137:

```python
kernel_event(
    "world_change",
    day=day, scene=scene,
    summary="<human-readable description>",
    deltas={
        "place":   "<region_id>",    # REQUIRED by CascadeSystem.apply; used for ontology fact
        "level":   1,                # REQUIRED: int; used by _vertical_bfs root_level (default 1)
        "summary": "<str>",          # written to FactGraph as a "world_change" fact on the place
        # optional cascade flags (NOT emitted by endgame):
        # "deferred": True           # deferred remote-region hop
        # "reason": "remote"
        # "depth": <int>
        # "deferred_consume_through": <int>   # bookkeeping watermark
    },
    turn=turn,
)
```

The **minimum required deltas** for a catastrophe trigger are `{"place": region, "level": 1}`.
`summary` in deltas is written to the FactGraph as an ontology fact (`g.assert_fact(place, "world_change", summary, ...)`), so include it for rich context.

The `cascade` system's `apply()` guards: skips if `place` is missing or entity is dangling.

`_vertical_bfs` reads `root_level` from the trigger event's `deltas["level"]` (line 864-867 of `loop/cascade.py`); default is 1 if absent. Level 1 = region-anchored catastrophe → cascade descends from the L1 region downward.

---

## Tests

### `tests/loop/test_endgame.py` (32 tests)
- `TestWorldRescueChance`: monotonic, base at stage 0, higher at last stage, clamped, n_stages=1 no-divide-zero
- `TestRollWorldRescue`: Oracle(1).d100()=18 ≤ 30 → True; Oracle(3).d100()=31 > 30 → False; determinism; bool type
- `TestSummaries`: about/secret/region text presence, missing-fields no crash
- `TestBuildCatastropheEvents`: 2 events default; types; id match; world_change.place == region_scope; level is int; emit_world_change=False gives 1 event; kernel fields day/scene/turn; no random dependency; no-L1-ancestor fallback

### `tests/systems/test_lore_endgame.py` (20 tests)
- `TestQuestWorldResolved`: state=了结, status=resolved, by=world_rescue, summary set, pending_finale cleared, default by, custom by preserved, idempotent no-crash, idempotent stays 了结
- `TestQuestCatastrophe`: same set + cross-idempotent (world_resolved then catastrophe = no-op)
- `TestEventTypesRegistration`: both event types in event_types()

---

## Concerns for Task 2

1. `run_lore` currently emits `quest_surfaced{by:"world"}` when a complex line reaches its last stage. The spec says: "末 stage 不在这里骰 (no checkpoint rescue roll at last stage); complex line at last stage keeps the existing world-push surface." Task 2 must guard `new_idx != len(stages) - 1` before the checkpoint rescue roll, matching that spec intent exactly.

2. `run_lore` processes `pending_finale` lines in the same loop as normal 暗骰 advancing, but `continue`s before the advance logic when a line is expired. Task 2's finale hook must be inserted in the expiry-check `continue` path: detect `complexity=="complex" and pending_finale`, then do the finale roll instead of (or after) the `quest_finale_due` guard.

3. `registry.owner_of_event("world_change")` guard in Task 2: emit the `world_change` leg only when CascadeSystem is registered, matching the spec's "若 PlaceSystem/cascade 注册了 world_change" condition. The `emit_world_change` parameter on `build_catastrophe_events` was included exactly for this guard.
