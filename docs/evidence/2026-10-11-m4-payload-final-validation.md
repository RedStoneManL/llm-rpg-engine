# M4 actual-payload final validation

Parent checkpoint: `e369ac4d547c6211b9cd2c08bca67905aab3a91c`.

## Finding

`EventBatch.publish()` originally re-ran its validators after `preflight`, but
those validators only inspected `EventBatch.events`. `EventStore.append_many()`
deep-copies that list before calling preflight. A preflight callback could
therefore modify the copied, later-inserted event dictionaries while the batch
validators continued to see the original objects.

The review source and excerpts are retained in
`2026-10-11-m4-transaction-order-review.md`.

## Repair

- `EventStore.append_many(..., final_validate=...)` invokes the validator after
  preflight and before the first SQLite insert, inside the existing `BEGIN
  IMMEDIATE` transaction.
- `EventBatch` validators receive the publication payload (without transient
  `seq`) both before entering the store and after preflight.
- `MemoryWriteSession` captures separate digests for the in-memory batch event
  and the publication form. It requires exactly one matching source and memory
  record in the actual payload, in addition to the existing identity, revision,
  turn and registry checks.

## Regression matrix

The new parameterized test makes preflight mutate the exact future payload:

| Target | Mutations |
| --- | --- |
| Foreground source | `deltas.value` |
| Private memory record | `deltas.actor`, `deltas.source_event_id`, `type`, `id`, `turn` |

Every case expects `PermissionError` and asserts no active or retracted events,
revision `0`, no action receipt, and no JSONL mirror file.

## Commands and results

Test dependency was installed only into `/tmp/llm-rpg-engine-testenv`; it did
not change project dependencies or call a model provider.

```text
/tmp/llm-rpg-engine-testenv/bin/python -m pytest -q tests/systems/test_interpretive_memory.py
27 passed

/tmp/llm-rpg-engine-testenv/bin/python -m pytest
1747 passed, 1 skipped, 1 deselected in 17.29s
```

The skipped test and deselection are inherited pytest marker configuration; no
test was deleted, skipped, or xfailed for this repair.
