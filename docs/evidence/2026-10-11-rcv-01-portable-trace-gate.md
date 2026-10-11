# RCV-01 — Portable trace CLI regression gate

- Code checkpoint: `7a3c82cae9c3f0e7269ce9204096626683c25803`
- Parent baseline: `6b544111a1b0e26fbc232ba0dcb3df33e92b8dbb`
- Date: 2026-10-11 UTC
- Network/model activity: none; `pytest` only was installed into a temporary
  `/tmp/llm-rpg-test-deps` directory because the standard user site is read-only.

## Initial result retained

```text
PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest
1705 passed, 7 failed, 1 deselected in 18.27s
```

All seven failures were subprocess `PermissionError` exceptions before the
trace CLI started. `tests/app/test_trace_view.py` set both `cwd` and
`PYTHONPATH` to the historical checkout `/root/rpg-engine-app`, which is not
available in the recovered workspace.

## Corrective change

The test module now derives the project root from its own location, uses that
path as its subprocess cwd, and prefixes (rather than discards) an inherited
`PYTHONPATH`. This verifies the actual module entry point from any checkout,
instead of encoding the former developer machine into every end-to-end test.

## Verification

```text
PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest tests/app/test_trace_view.py
38 passed in 0.31s

PYTHONPATH=/tmp/llm-rpg-test-deps:. python -m pytest
1712 passed, 1 deselected in 16.66s
```

The default deselection is the repository's existing `slow` marker; no tests
were removed, skipped, or marked xfail by this change.
