# RCV-120 — shared lexical/semantic candidate scope

Date: 2026-10-25 UTC. Base: `46e8d15` (RCV-119), branch
`handoff/codex-cloud-20261025`. Implementation commit: the commit adding this report.

## Reproduced issue and implementation

The fresh RCV-119 assessment searched for day 1 / 阿岚 and received day 2 / 船主:
legacy `engine.recall.recall` filtered the FTS query but searched the entire vector
index. Post-filtering top-k alone would still lose lower-ranked eligible matches.

Both paths now reuse ArchiveStore's exact day/entity scope. Entity membership is
checked with SQLite `json_each`, not a JSON-text LIKE pattern; empty IDs and IDs
containing percent, underscore, quotes, backslashes or Unicode remain literal.
Short-text LIKE fallback also escapes its metacharacters. No schema migration.

`VectorStore.search(..., allowed_ids=...)` selects candidate IDs in SQL before
vector decoding/scoring and top-k. An empty set produces no hits; `None` retains
the old explicit whole-index behavior. Parameterized ID batches avoid SQLite
variable limits. Out-of-scope malformed-dimensional vectors are not loaded.
The vector index still decides neither authorization nor world facts.

Hybrid recall materializes the scoped archive candidate rows before embedding
and returns those exact rows, preventing an intervening same-ID replacement
from retargeting the result to another scope. Only extant candidate archive IDs
enter vector search, so stale vector rows cannot crowd out valid matches.
Vector/embedding errors retain the FTS result. No embedding model was loaded.

## Actual verification

New test file before production edits:

```bash
.venv/bin/python -m pytest tests/test_recall_scope.py -o "addopts=-m 'not slow'" -q
```

**23 failed, 5 passed in 2.44s**. Failures include real filter bypass, empty-scope
fallback, stale-vector crowding, literal wildcard mismatch, and missing new
candidate APIs. Some old behavior already passed; these are not all new bugs.
Added one subsequent test for replacement while embedding is executing.

Final targeted command:

```bash
.venv/bin/python -m pytest tests/test_recall_scope.py tests/test_archive.py tests/test_recall.py tests/test_vectorstore.py tests/kernel/test_recall.py tests/memory/test_recall.py -o "addopts=-m 'not slow'" -q
```

First targeted run: **70 passed in 2.33s**. Final with the intervening replacement
case: **71 passed in 2.16s**. Covers lower-ranked in-scope matches, day/entity
intersection, empty candidates without embedding calls, JSON member identity,
FTS short and long queries, 1,201 allowed IDs, invalid scope types, stale index
rows, separate-store reopen and FTS fallback during embedding failure.

Full command:

```bash
.venv/bin/python -m pytest -o "addopts=-m 'not slow'" -q
```

**2171 passed, 1 deselected in 127.06s**. The sole deselection remains the existing
slow real-embedding test. No skip/xfail expansion. Production code and assertions
were unchanged during the full run; only an older test module's introductory
docstring was corrected to acknowledge its explicitly synthetic backlog fixtures.

Re-ran the assessment's exact `gaps()` probes separately; raw results:
[rcv120-gap-recheck.json](rcv120-gap-recheck.json). Requested day 1 / 阿岚 now
returns day 1 / 阿岚. Searching literal `%` in text without that symbol now
returns zero. Other known gaps remain reproduced and are explicitly retained,
not erased to make the assessment look green. `git diff --check` passed.

## Scope and remaining limitations

This is an implemented query-scope fix and reusable vector allowlist interface.
Entity/day selection is **not** actor authorization. The caller still needs to
construct a trusted campaign/actor/revision/active-history candidate set before
any new model-facing history tool. No locate/open/neighbour tool was added here.

Archive's legacy scene+turn key still replaces old content. Captured candidates
are stable only within this call, not a durable citation identity. There is no
atomic shared revision across FTS/archive/vector stores, and a stale same-ID
vector can still affect relevance until reindexing. Queries materialize scoped
candidate rows; this is linear memory/time in that scope, not a new scale claim.
The prior unscoped profiling report is not relabelled as this interface's
performance measurement.

Original-input atomic archival, provenance-bound reading handles, outcome-first
final narration and long-running NPC autonomy remain unfinished. No private
interpretation default change, external model call, weight download, credentials,
private-data export, push/main/PR/deploy or Library/Drive/email upload in this stage.
