# RCV-118 — independent, bounded recap maintenance

Date: 2026-10-25 UTC. Base: `759e979116051dce124844e7a72ee7272d3708bb`,
branch `handoff/codex-cloud-20261025`. Implementation commit: the commit adding
this report (`git log --diff-filter=A --format=%H -- <this-file>`).

## Problem and change

Recompression was nested inside a successful new scene summary. An existing
eligible summary backlog could not retry without new aged raw text; a scene
provider failure blocked older independent recap work. Recompression failure
also left a misleading `saved` status, and an invalid staged recap could roll
back its successful sibling summary at the enclosing digest savepoint.

`digest_fleet` now attempts at most one scene summary and one recompression,
independently, with the unchanged fanout/gap rules and at most one structured
repair each. It freezes host provenance before generation and uses existing
`append_many` validation/savepoints for each job. A successful sibling survives
ordinary failure; cancellation still aborts the entire outer turn. `pending`
dominates `saved` when either attempted job fails. The CLI says “更新尚未完成”
because partial success is possible. Status describes this invocation, not an
empty historical queue. Maintenance day/scene come from projected current state.

No background daemon, retry timer, new recall API or schema was introduced.
An idle campaign still requires a subsequent digest invocation to resume work.

## Actual tests

Same independent Python 3.12.14 / pytest 8.4.2 environment as RCV-117; dependency
check remains clean. Before production edits, the seven new cases yielded
**5 failed, 2 passed, 7 deselected in 1.43s**. Existing cancellation behavior
already passed. Failures reproduced blocked retries, missing independent calls,
false success and loss of the valid sibling.

First implementation target: **2 failed, 81 passed in 6.43s**. Both failures
were duplicate-action test setup: the retry passed the post-action world instead
of the original request revision. Corrected the fixture to reuse the original
world; fingerprint production code and rejection behavior were not weakened.

Final target:

```bash
.venv/bin/python -m pytest tests/test_recap_integrity.py tests/test_recap_sources.py tests/loop/test_fleet.py tests/loop/test_turn_density_hook.py tests/app/test_cli_turn_status.py tests/systems/test_narrative_system.py tests/test_world_continuity.py tests/context/test_assembler.py -o "addopts=-m 'not slow'" -q
```

**118 passed in 7.55s**. Includes real `run_turn` batches, partial success in
both directions, separate malformed-output repair budgets, cancellation in
either job, invalid staged recap isolation, existing backlog retry after cold
reopen, bounded advancement 6 then 12, and durable duplicate-action zero calls
and zero writes.

```bash
.venv/bin/python -m pytest -o "addopts=-m 'not slow'" -q
```

**2142 passed, 1 deselected in 90.51s**. Only the existing real-embedding slow
test is deselected; no new skip/xfail. No production/test changes after this
run. The subsequently added native verification script was executed separately.
`git diff --check` passed.

## Native model evidence (separate from deterministic regression)

The user authorized “5.6 terra 或者 luna medium”; available native model
`gpt-6-luna`, reasoning `medium`, was used for one bounded generation task, two
independent requests. It did not develop code, delegate, access credentials or
read private campaigns. Inputs are deliberately human-authored synthetic history.

```bash
.venv/bin/python scripts/verify_recap_native.py prepare docs/verification/rcv118-native
# Luna independently writes responses.json from requests.json, no manual editing.
.venv/bin/python scripts/verify_recap_native.py verify docs/verification/rcv118-native
```

Prepare uses disposable placeholder output only to capture the exact production
requests; these placeholders are not acceptance evidence. Verify rebuilds a new
fixture, checks each request byte-equivalent at the message/kwargs level, uses
Luna's original response strings, and runs actual structured validation, source
binding, SQLite append, cold reopen and no-repeat maintenance checks. Two model
responses passed with zero repairs. See [requests](rcv118-native/requests.json),
[original responses](rcv118-native/responses.json), [result and SHA-256 hashes](rcv118-native/result.json).

Manual reading: the scene response preserves the unpaid fare, unopened delivery,
shore position and deadline. The recap preserves the deadline, no debt promise,
unverified bridge tax, unfinished delivery and unpaid/unboarded crossing. It
merges the confidentiality request into the relationship sentence, losing its
explicit day-3 placement and making chronology ambiguous. This is a small
semantic sample, not exact quotation or long-run quality proof. Hashes verify
sources, not semantic faithfulness. Native generation plus exact-request replay
is **not** an end-to-end remote API transport/credential test.

## Updated authorization and remaining work

After this implementation began the user explicitly said: “不要管之前的hold，
你重新做一次全量的评估，我们换环境了应该都可以推进了”. B1 detail retrieval
and profiling are now authorized for fresh assessment and implementation; the
old HOLD is not a current blocker. Do not erase historical evidence of the
previous pause or infer that changing environments alone removed engineering
risks. A separate current assessment follows this stage.

Player-input archival, actor/revision-bound source reading, progressive
locate/open/neighbours, outcome-first prose and durable NPC autonomy are still
not implemented by this change. Private interpretations stay off by default.
No push, main changes, PR, deploy, credentials changes, embedding downloads,
private data export or Library/Drive/email uploads.
