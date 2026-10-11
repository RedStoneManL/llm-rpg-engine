# M2 — offline host-bound interpretive-memory handles

- Code commits: `ce00578aa75829e638ded66ca6cd1f78c4703568`, `0cfdb3b9df57fd6f2937a44f38404444a04f726c`
- Date: 2026-10-11 UTC

`MemoryReadSession` is only an in-process trusted-host boundary. It binds an
opaque object-identity handle to the exact world object, store object, and
host-selected role. Reads reject fake handles, another session's handle, a
different store, a different projected world, and a closed session. DM/model
POV remains unrelated; the default tool registry has no memory read or issue
operation and the normal app still does not register the memory event.

Verification: `tests/systems/test_interpretive_memory.py` → 5 passed;
full offline pytest → 1721 passed, 1 deselected.

Limits: trusted Python host code can call the factory with a role, so this is
not multi-user authentication or a defence against malicious in-process host
code. No entity-deletion lifecycle exists in the current event model. No public,
GM, CLI, context, trace, or raw-history memory outlet is enabled or accepted.
