# RCV-117 — recursive recap source chain

Date: 2026-10-25 (Asia/Shanghai client date).
Base: `d1833ade0a49a6cb6eeb7c1b103ca52d6a7ad52d` on
`handoff/codex-cloud-20261025`; production ancestor
`45c270dd2f145c55969629652a221f08b1d18ea4` (RCV-116).
Implementation commit: the commit introducing this report (resolve with
`git log --diff-filter=A --format=%H -- docs/verification/RCV-117-recap-source-chain.md`).

## Environment restoration

The starting directory was not a checkout. Cloned the explicitly requested
handoff branch; no old main reconstruction or existing work replacement.
`git status --short` was empty, `git branch --show-current` matched, and
`git merge-base --is-ancestor d1833ade0a49a6cb6eeb7c1b103ca52d6a7ad52d HEAD`
exited 0. Read all five handoff documents, the repository SKILL.md (gameplay
protocol, not a development workflow), and checked parent/repository AGENTS.md;
none applied. No subagents were started.

Created `.venv` with Python 3.12.14, `include-system-site-packages = false`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest==8.4.2 numpy pyyaml
.venv/bin/python -m pytest -o "addopts=-m 'not slow'" -q
```

Installed pytest 8.4.2, NumPy 2.5.4, PyYAML 6.0.3 and requirements.txt
(Langfuse 3.15.0 with its dependencies); `pip check` passed. Dependencies were
downloaded only for setup; no model weights or real/paid inference were used.
Actual unmodified baseline: **2107 passed, 1 deselected in 116.43s**.
The sole deselection remains `tests/test_embed_real.py::test_real_embedding`.

## Change and original failure evidence

RCV-115 already keeps unsummarized old narration visible. RCV-116 already binds
scene summaries and stops producer-side compression at gaps. The missing boundary
was recursive recap provenance: projection accepted a coverage index without
checking which summaries or previous recap supplied the text.

Before production edits, ran:

```bash
.venv/bin/python -m pytest tests/test_recap_sources.py -o "addopts=-m 'not slow'" -q
```

Actual result: **25 failed in 1.00s**. Positive chain/legacy tests failed with
`KeyError: 'super_summary_source'`; malformed/null/version/index/omitted/reordered/
duplicate/wrong-ID/changed-text/missing/retracted/private-ID/empty-text/out-of-range/
extra-field sources and wrong predecessor/rewritten-branch cases failed with
`Failed: DID NOT RAISE <class 'ValueError'>`. These establish the new contract;
they do not claim the old event format promised this verification.

Implementation reuses NarrativeSystem, digest_fleet and standard EventStore
preflight. The host freezes source v1 before calling the recap provider; projection
reconstructs the expected ordered input range and predecessor. IDs plus content
and source hashes distinguish identical wording, revisits and rewritten turn
numbers. JSON comparison is type-strict. New malformed provenance is rejected,
never downgraded to the legacy path. No new storage schema or search service.

Legacy summaries/recaps keep their old replay behavior and are explicitly
`legacy_unbound` when used by a new recap. A later legacy recap clears the prior
bound descriptor. A partial legacy update with no single text+coverage event
remains readable but cannot serve as a fabricated predecessor for new provenance.

## Verification

Target command:

```bash
.venv/bin/python -m pytest tests/test_recap_sources.py tests/systems/test_narrative_system.py tests/test_recap_integrity.py tests/loop/test_fleet.py tests/test_world_continuity.py tests/context/test_assembler.py -o "addopts=-m 'not slow'" -q
```

First implementation: **82 passed in 5.29s**. Expanded integration checks:
**85 passed in 3.71s** (additional legacy-partial, damaged durable row, and real
15-turn recursive recap tests). Final targeted run after strengthening the legacy
repair-loop assertions: **85 passed in 5.02s**.

Full gate (same command as the baseline): **2135 passed, 1 deselected in 99.62s**.
The only change after full-gate startup was additional assertions in the existing
legacy repair-loop test; the final targeted run above includes those assertions.
No production code changed during or after that full run. `git diff --check`
passed. No skip/xfail/marker selection was changed.

Evidence covers:

- Actual `run_turn → digest_fleet → EventBatch → SQLite` at default thresholds:
  15 foreground turns, two recaps covering 6 and 12 buckets, exact ordered
  summary IDs and predecessor link, durable-prefix source reconstruction,
  and each referenced text present in the actual fixed provider request.
- Host provenance ignores extra model-supplied source data. Nonempty private
  interpretation sentinels never enter these recap requests or author context.
- Invalid append is zero event/revision/receipt/result-sidecar/JSONL writes.
  Simulated externally damaged durable recap fails closed on cold reopen,
  leaving SQLite/JSONL bytes unchanged.
- Cold reopen preserves the chain; completed action-id replay returns the saved
  event payload without recap provider calls or writes; undo restores the
  preceding recap, and a second cold reopen does not revive the undone recap.
- Rewritten history with identical wording/turn/scene cannot reuse old sources.
  Legacy compression through the real repair loop remains possible and marks
  its input summaries unverified.

## Limits and next work

All generation in these tests is deterministic/local. Hashes verify provenance,
not faithful meaning, quote completeness, real-model recall or long-run quality.
`bound` denotes an immediate verified source descriptor; ancestors can still
contain explicit `legacy_unbound` links. Historical unbound source gaps are not
backfilled, and old index-only recap semantics are not silently migrated.

No new locate/open tool, public-history API, player-input archive, actor ACL,
embedding or vector index was enabled. Private memory remains off by default.
B1 detail retrieval/profiling HOLD is unchanged. Locate/open/neighbour expansion
would overlap B1 and needs explicit authorization before connection; independent
source maintenance and outcome-first narration remain available work.

Audit confirmed the input fingerprint is a hash, GUI host envelope holds explicit
speech but not normal action input, and optional CLI transcript is post-publish.
These are not a unified atomic original-input archive. Archive scene+turn keys
and vector search without actor/revision filtering are not authoritative references.

Next independent slice: decouple recap recompression retries from a newly saved
scene summary. Current nesting can leave existing summary backlog unmaintained.
Outcome-first narration and the unified input archive remain unimplemented.

No credential changes, campaign uploads, Library/Drive/email writes, push, main
changes, PR, deployment, real model invocation or embedding download occurred.
