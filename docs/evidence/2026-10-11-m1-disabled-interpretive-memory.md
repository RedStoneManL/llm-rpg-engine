# M1 — disabled interpretive memory substrate

- Source commit: `df4af23e7dc4e21be96e5dd45a62f7f8d60f24af`
- Parent: `e69632e9c4b46710691342498b46006b411fb98a`
- Date: 2026-10-11 UTC

## Implemented boundary

`InterpretiveMemorySystem` is an opt-in, event-sourced projection. The normal
`build_engine()` registry does not register it, so the normal store rejects
`character_interpretation_recorded`. When explicitly registered in an isolated
registry, it keeps records grouped by actor, survives reopen replay, and loses
retracted records on replay. It never writes the ontology fact graph and has
no commit section, context injection, recall hook, GUI/CLI endpoint, or model
generation path.

## Important non-claim

`records_for_actor(world, actor)` only performs data partitioning. It accepts
an actor parameter and therefore is **not an authorization boundary**. Until a
trusted caller identity and authorization policy are introduced, an untrusted
caller must not receive this world slice or call this function. No claim is
made that raw event history, GM/CLI paths, traces, or future public endpoints
cannot access opt-in records.

## Verification

```text
PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest tests/systems/test_interpretive_memory.py
3 passed in 0.05s

PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest
1719 passed, 1 deselected in 18.51s
```

No real model call, generation path, deployment, B1 retrieval work, profiling,
or production activation occurred.
