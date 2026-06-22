# A1 Report: Replay-Safety Fix for Director Consumed State

**Date:** 2026-06-22  
**Status:** DONE  
**Branch:** app

---

## Approach Chosen: (b) `consumed_through_turn` watermark via `directive_consumed` event

### Why (b) not (a)

Directives carry a `turn` field (set from `event.get("turn")` in `director_fired` apply, line 58). That makes the watermark approach clean and structurally parallel to `CascadeSystem.consumed_through_turn`. Approach (a) — a `directive_consumed` event per directive — would have required matching by id or index, adding fragility. Approach (b) folds a single monotonic integer into the slice; replay is trivially idempotent (`max(current, new)`).

The `DirectorSystem.empty_state()` already returned `"consumed_through_turn": 0` (it was a stub, never set by any event). This fix gives it its real meaning.

---

## What Changed

### `systems/director.py`

1. **`event_types()`** — added `"directive_consumed"`.

2. **`apply()` new branch** (before `thread_open`):
   ```python
   if t == "directive_consumed":
       through_turn = d.get("through_turn")
       if through_turn is not None:
           slice_["consumed_through_turn"] = max(
               slice_.get("consumed_through_turn", 0), int(through_turn)
           )
       return
   ```

3. **`inject()` filter updated**:
   ```python
   consumed_through = slice_.get("consumed_through_turn", 0)
   pending = [
       d for d in slice_.get("pending", [])
       if not d.get("consumed") and d.get("turn", 0) > consumed_through
   ]
   ```
   The `not d.get("consumed")` guard is kept for the in-memory `thread_advance surface` path which also appends to `pending` with `consumed=False`; the watermark is now the primary source of truth for `director_fired` directives.

### `loop/director.py`

Replaced the in-memory mutation block:
```python
# OLD (bug): mutates only in-memory world; lost after project()
for d in slice_.get("pending", []):
    d["consumed"] = True
```
with:
```python
# NEW (fix): emit directive_consumed event, folded by project() on every replay
if pending:
    through_turn = max(d.get("turn", 0) for d in pending)
    consumed_ev = kernel_event(
        "directive_consumed", day=ev_day, scene=ev_scene,
        summary=f"directive consumed through turn={through_turn}",
        deltas={"through_turn": through_turn},
        turn=through_turn,
    )
    store.append(consumed_ev)
```

`through_turn` is the max turn among all pending directives, so all of them fall at or below the watermark.

---

## Invariant Test

**File:** `tests/loop/test_director_loop.py::test_consumed_directive_not_reinjected_after_reproject`

**Scenario:**
1. Build a campaign that fires a directive (Turn N).
2. Confirm `inject()` shows the directive on Turn N (correct — it was just fired).
3. Simulate Turn N+1: call `run_director` again (this emits `directive_consumed`).
4. Reproject from the event log (`project(reg, store.iter_events())`).
5. Assert `inject()` returns `None` — the directive must not reappear.

**Before fix:** Step 5 returned a Fragment (bug — `consumed=False` was restored by `project()`).  
**After fix:** Step 5 returns `None` — `consumed_through_turn` is folded from the `directive_consumed` event.

---

## Test Results

- Regression test: RED before fix, GREEN after.
- All director tests: 31/31 passed.
- Full suite: 1197 passed, 0 failed.
- Updated `test_director_owns_event_types` to include `directive_consumed` in the expected set (correctness update, not a suppression).
