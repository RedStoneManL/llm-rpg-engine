# RCV-02 / Reopen and full-history replay

- Code checkpoint: recorded by the commit that adds this evidence file.
- Parent checkpoint: `bcf688513ba2803aa1cb4706ce0e5d5fda0c14c5`.
- Date: 2026-10-11 UTC.
- Model activity: none; offline `FakeLLMProvider` only.

## Coverage added

The regression executes a real first `run_turn` with core fact and
`narration_recorded` events, rewinds that entire turn, closes the SQLite store,
and builds a fresh engine from the campaign directory. It verifies:

1. every turn-1 event remains in full history with `retracted=True`;
2. the reopened projection has the same serialised state signature as the
   pre-turn projection; and
3. explicitly replaying the full stored history (including retracted rows)
   produces that same pre-turn signature.

The signature compares graph entities, facts and relations structurally rather
than comparing independent in-memory `FactGraph` object identities.

## Verification

```text
PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest \
  tests/test_action_integrity.py tests/test_store.py tests/test_rewind.py \
  tests/test_determinism.py tests/test_world_continuity.py
41 passed in 0.49s

PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest
1716 passed, 1 deselected in 16.86s
```

This is an acceptance result for the current registered foreground and
background mechanisms, not evidence that future unregistered hook types are
safe without the same transaction/replay regression coverage.
