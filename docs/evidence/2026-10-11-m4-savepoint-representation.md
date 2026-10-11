# M4 savepoint representation fidelity

## Finding

The normal `kernel_event()` and `make_event()` helpers include
`"retracted": False`, but `validate_event()` does not require that optional
field. A manually assembled, otherwise valid event can omit it.

Before this repair, `EventBatch.savepoint()` saved only
`ev.get("retracted", False)` and restored with an unconditional assignment.
If a failed hook had temporarily retracted a pre-savepoint manual source, its
rollback changed the source representation from no key to `"retracted":
False`. An existing M4 memory writer correctly treats that changed source
digest as tampering and then refuses to publish.

## Repair

The savepoint snapshot now preserves `(had_key, original_value)`. Its exception
path restores the value when the key existed and removes the key when it did
not. It does not relax memory digests or alter the meaning of retraction.

## Red to green

Before the repair:

```text
tests/systems/test_interpretive_memory.py -k 'failed_savepoint_preserves_missing or failed_savepoint_restores_retracted'
2 failed
```

After the repair:

```text
same targeted command
2 passed

tests/systems/test_interpretive_memory.py
29 passed

full offline pytest
1750 passed, 1 deselected in 17.42s
```

The first regression starts with a manually assembled source without
`retracted`, stages its memory writer and validators, then makes an inner
savepoint retract and fail. The original writer remains intact and publishes
both source and private record. The second preserves all three pre-savepoint
states: absent, explicit `False`, and explicit `True`, while confirming that
the failed retraction intent and no validators leak.

This is a storage representation compatibility repair only. It does not add a
`run_turn` integration path or change the default-disabled memory boundary.
