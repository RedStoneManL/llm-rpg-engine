# Task 2 Build Report — run_lore endgame wiring

Date: 2026-06-21
Branch: app
Base commit: 6ffb0e1

---

## Files modified / created

### Modified: `loop/lore.py`

Two insertion points in `run_lore`, plus a lazy-import block at function entry.

#### Lazy import (circular-import fix)

`loop/endgame.py` imports `loop/density.py`, which in turn imports `create_lore_line`
from `loop/lore.py`. A top-level import of `loop.endgame` in `loop/lore.py` would
produce a circular import. Fixed by moving the import **inside** `run_lore`:

```python
# loop/lore.py — top of run_lore body (line ~74)
from loop.endgame import (  # noqa: PLC0415
    RESCUE_GRACE_STAGES, FINALE_RESCUE_CHANCE,
    roll_world_rescue, rescue_summary, build_catastrophe_events,
)
```

#### Insertion B — Finale (lines ~108-139, top of per-line loop, before expiry block)

Position: immediately after the `state != "暗"` guard, before the lifespan expiry block.

```python
# ---- B. Finale: pending_finale (set on a PRIOR turn by lifespan expiry) ----
if ln.get("complexity") == "complex" and ln.get("pending_finale"):
    oracle = Oracle(scene_seed(campaign_seed, f"finale:{lid}", now_day))
    if oracle.d100() <= FINALE_RESCUE_CHANCE:
        # emit quest_world_resolved{by:"world_rescue:finale"}
        ...
        continue
    else:
        # emit build_catastrophe_events (quest_catastrophe + world_change if cascade registered)
        emit_wc = registry.owner_of_event("world_change") is not None
        cat_evs = build_catastrophe_events(ln, world, day=now_day, scene=scene,
                                           turn=next_turn, emit_world_change=emit_wc)
        for ev in cat_evs: store.append(ev); appended.append(ev)
        ...
    continue  # resolved; skip normal 暗骰 advance this trip
```

Why before the expiry block: the expiry block emits `quest_finale_due` and `continue`s.
`pending_finale` is only `True` after that event has been **applied** on a prior turn
(LoreSystem apply sets `ln["pending_finale"] = True` when it sees `quest_finale_due`).
So on the SAME trip the expiry fires, `ln.get("pending_finale")` is still `False` in
the projected world — the finale guard is a no-op. On the NEXT turn, `pending_finale=True`
is already in the projection and the finale guard fires first (before the expiry block
would re-fire, which the expiry guard also prevents via `not ln.get("pending_finale")`).

#### Insertion A — Checkpoint rescue (lines ~194-217, inside the advance block after `store.append(ev)`)

Position: after `lore_advanced` is emitted and appended, before the existing
world-push surface branch.

```python
# ---- A. Checkpoint world-rescue (暗骰酝酿期,渐进式) ----
is_last_stage = (new_idx == len(stages) - 1)
is_complex = ln.get("complexity") == "complex"
if is_complex and new_idx >= RESCUE_GRACE_STAGES and not is_last_stage:
    rescue_oracle = Oracle(scene_seed(campaign_seed, f"rescue:{lid}", new_idx))
    if roll_world_rescue(rescue_oracle, new_idx, len(stages)):
        # emit quest_world_resolved{by:"world_rescue"}
        ...
        continue  # skip world-push surface this trip
```

`is_last_stage` is computed once and reused by both the rescue guard and the
existing world-push surface branch. The `continue` after rescue success skips
the world-push `if is_last_stage and is_complex:` block entirely.

---

## Double-processing guarantees

| Scenario | Guard |
|---|---|
| Checkpoint rescue success at stage N | `continue` skips world-push surface same trip; projected `state="了结"` makes subsequent turns skip the line via `state != "暗"` guard |
| Checkpoint at last stage | `not is_last_stage` prevents rescue; world-push fires normally |
| Checkpoint at stage 0 | `new_idx >= RESCUE_GRACE_STAGES` (=1) is False; no rescue roll |
| Finale fires | `continue` at end of B block skips expiry block and advance block; `state != "暗"` guard on subsequent turns |
| Already-了结 line | Outer `state != "暗"` guard — first thing in the loop body |
| Non-complex line | Both A and B guarded on `complexity == "complex"` |
| `world_change` without cascade | `registry.owner_of_event("world_change") is not None` → `emit_world_change=False` |

LoreSystem.apply for `quest_world_resolved` and `quest_catastrophe` both have a
replay-safe guard: `if ln.get("state") == "了结": return` (no-op on re-apply).

---

## Tests — `tests/loop/test_lore_endgame_wiring.py` (27 tests)

- `test_pinned_seeds_sanity`: verifies all campaign_seed/stage/day pinned values
  produce the expected rolls before any other test relies on them.
- `TestCheckpointRescueSuccess` (4 tests): seed 2 → rescue at stage 1 → `quest_world_resolved{by:world_rescue}` + line 了结; no `quest_surfaced` same trip.
- `TestCheckpointRescueFail` (4 tests): seed 1 → no rescue at stage 1; `lore_advanced` still emitted; line stays 暗; seed 5 (fails stages 1+2) → last stage → `quest_surfaced` fires.
- `TestStageZeroNoRescue` (1 test): first advance (-1→0) never triggers rescue.
- `TestFinaleSuccess` (4 tests): seed 3, day 25, `pending_finale=True` → `quest_world_resolved{by:world_rescue:finale}`; no catastrophe.
- `TestFinaleFail` (6 tests): seed 1, day 25, `pending_finale=True` → `quest_catastrophe` + `world_change` at `region_scope(town1)`; no world_change without cascade; summary contains expected text.
- `TestNonComplexLinesUntouched` (3 tests): simple/medium lines produce no endgame events even with rescue-success seed.
- `TestIdempotentRewind` (3 tests): resolved line not re-processed; deterministic replay; catastrophe'd line not re-processed after projection.

---

## Concerns

1. The pending_finale injection in tests uses direct mutation of the projected world
   dict (`ln["pending_finale"] = True`). This simulates a prior-turn `quest_finale_due`
   being applied without needing to build a full lifespan scenario. In production the
   path is: lifespan elapses → `quest_finale_due` emitted → LoreSystem apply sets
   `pending_finale=True` → next turn's projection has it truthy → finale fires.

2. `now_day` in the finale oracle uses `world["meta"]["day"]` (falling back to last
   event day). Tests must inject `w["meta"]["day"] = _FINALE_DAY` to get deterministic
   oracle seeds; real usage reads this from the world clock naturally.

3. The lazy import runs on every `run_lore` call. Python caches module imports after
   the first load (no repeated disk IO), so performance impact is negligible.
