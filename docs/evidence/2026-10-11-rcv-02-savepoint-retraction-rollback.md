# RCV-02 / Savepoint retraction rollback

- Code checkpoint: recorded by the commit that adds this evidence file.
- Parent checkpoint: `74071f6b8db59379924906519da553f33a63ea12`.
- Date: 2026-10-11 UTC.
- Model activity: none. All providers are offline fixtures.

## Root cause

`EventBatch.savepoint()` used `len(self.events)` as its only rollback
boundary. A background hook could call `retract_from_turn()` and then raise.
The hook's appended events were removed, but its `self.retractions` entries
and the in-memory `retracted` flags remained. `EventBatch.publish()` would
later pass those leaked retractions to `EventStore.append_many()`, retracting
durable history as part of an otherwise successful foreground turn.

## Change

Savepoints now remember the pre-savepoint retraction-list length and the
existing events' `retracted` flags. On any exception they remove only that
savepoint's staged events and retractions, then restore those flags. This is
safe for nested savepoints: a failed inner hook cannot remove its caller's
earlier staged work.

## Tests

New regression coverage in `tests/test_action_integrity.py` proves that:

1. A failed digest hook which stages `retract_from_turn(1)` cannot retract a
   previously committed turn when the foreground action publishes.
2. An inner failed savepoint cannot leak its retraction intent through an
   outer successful savepoint.

```text
PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest \
  tests/test_action_integrity.py tests/test_store.py tests/loop/test_turn.py \
  tests/loop/test_fleet.py tests/test_world_continuity.py
59 passed in 0.54s

PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest
1714 passed, 1 deselected in 16.97s
```

No test was deleted, skipped, or marked xfail. The existing default `slow`
marker remains deselected by repository configuration.
