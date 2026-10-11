# M4 host-bound memory write adapter

Initial lifecycle mutation matrix: 17 passed, 4 failed (turn, revision,
registry and store binding were not rechecked at publish). The four red cases
are retained in `test_memory_write_lifecycle_mutations_are_rejected`.

Root fix: shared `MemoryWriteSession._verify_binding()` now checks batch/store/
registry identity and revision/turn at stage and final publish. EventBatch runs
validators before touching store and again after original preflight. Result:
21 focused tests passed; full offline pytest: 1742 passed, 1 deselected.

Uncovered: real model generation, production auth, public memory outlets and
multi-process concurrency. Default app remains disabled.
