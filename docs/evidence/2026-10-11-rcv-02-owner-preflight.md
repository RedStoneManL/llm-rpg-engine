# RCV-02 / Commit-section ownership preflight

- Code checkpoint: recorded by the commit that adds this evidence file.
- Parent checkpoint: `7e311981d74e497f4ddb9fadb8d818320769032b`.
- Date: 2026-10-11 UTC.
- Model activity: none.

## Root cause

`loop.turn.apply_turn()` is public, but its unowned-section behavior differed
from the validated `run_turn()` path: it logged a warning and skipped the
section. A direct caller could therefore report a successful turn while
silently losing a declared effect. Iteration order also meant a later unknown
section could be noticed only after another owner had started materializing a
local proposal.

## Change

`apply_turn()` now resolves every commit section owner before invoking any
owner. Missing ownership raises `TurnRejected` with the section names. The
per-section loop asserts that preflight invariant rather than treating a
missing owner as a recoverable no-op.

## Tests

The added regression passes a valid `facts` section followed by an unowned
section. It verifies that `TurnRejected` is raised, no `facts.to_events()`
call occurs, and the event store stays byte-for-byte at its pre-call history.

```text
PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest \
  tests/test_action_integrity.py tests/loop/test_turn.py \
  tests/loop/test_turn_multiturn.py tests/loop/test_turn_density_hook.py \
  tests/kernel/test_validation.py
43 passed in 0.29s

PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest
1715 passed, 1 deselected in 16.17s
```

No test was removed, skipped, or marked xfail. This closes only the direct
section-owner boundary; historical replay/reopen behavior remains a separate
RCV-02 audit item.
