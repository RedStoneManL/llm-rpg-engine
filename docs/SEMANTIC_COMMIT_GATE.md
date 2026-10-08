# Foreground semantic commit gate (v1)

The repeated failure is a gap between prose and declared effects: a new NPC
speaks locally without a position, or a usable passage is narrated without a
map edge. Structural validators can reject invalid rows but cannot identify an
omitted row. More examples in a prompt do not close that publication gap.

## Shared production path

Every shipped Author/Hybrid candidate, including empty-effect turns and compare
alternatives, now enters one bounded finalization step before display/publication:

1. Build the existing ordered private physical preview and a POV-safe packet.
   Candidate creation IDs are separately labeled as candidate-only, with unknown
   placement. They grant no observed identity or knowledge. No private profiles,
   goals, raw history or clock rationale are included.
2. Make one tool-free model call to extract exactly quoted, typed claims about
   Person location/co-presence/movement and passage connectivity/change. The host
   checks shape, quotes, references, coverage and chronological positions against
   before/intermediate/after canonical snapshots. Missing support is unknown,
   not absence. Unrelated prose is outside the two-domain scope.
3. On mismatch, allow at most one correction and re-audit. Text changes are
   exact evidence-offset patches, with ambiguous/overlapping ranges rejected and
   all other prose retained byte-for-byte. All approved effect
   rows and their order are preserved. The host allows only narrowly bounded
   missing placement of an implicated public candidate-created NPC beside the
   actor, or an open connection involving an implicated candidate-created Place.
   Existing actors, player moves and existing topology cannot be changed merely
   to make a draft true. Other errors require truthful narration, including an
   explicit incomplete outcome where appropriate. Extracted claims never become
   events automatically.
4. Reject an invalid audit, unsupported critical claims, exhausted correction,
   or invalid domain proposal. No stale prose fallback is published. If an
   earlier structural repair already required narration reconciliation, that
   consumes the correction allowance and the rewritten prose is audited once.

The extractor is still a model. It can miss or misclassify meaning, intent,
quotation and time; coverage is an attestation, not a proof of complete semantic
recall. Host comparisons are deterministic conditional on those extracted claims.
This does not guarantee that all narration, NPC knowledge, facts or background
simulation is correct.

## Publication binding and compatibility

Host-only approval binds actor, source revision/state, actual input and resolved
context, exact narration, ordered section pairs, approved day/scene, and audit
record. Reordered effects, changed prose or source, or stale prepared candidates
are rejected. Approval is neither imported from model JSON nor restored as an
active authorization from saved event metadata. Normal, prepared and compare
paths share finalization. Historical replay and explicitly trusted manual
TurnCommit writes remain available; this is not a sandbox against arbitrary
Python within the host.

A final narration event retains the bounded host audit record for inspection.
This records the foreground check only; background callbacks run later. The
existing atomic event publication/revision lease and delayed conversation commit
remain authoritative.

Compare rejects any invalid alternative before display. The application prepares
one read-only resource/variation prefix from a consistent event-store snapshot;
both candidates receive the same resolved world and private balance locks. The
selected candidate stages those exact events once after actor/input/revision
checks, with no second resource classification, variation draw or audit. Prefix
retention is checked again before publication. Preparation itself writes nothing.
Standalone resource-enabled compare callers provide their authoritative store;
selected prepared candidates use run_turn rather than direct persistent apply.
New return-commitment creation remains outside compare's existing scope.

## Cost and limits

A normal candidate adds one audit call. A mismatch can add one correction and
one final audit. Existing physical-repair reconciliation uses its one correction
slot. Compare audits both candidates; unchanged prepared selection reuses its
approval. These are additional production calls, not throughput improvements.
Audits limit rows and the complete UTF-8 request to 128 KiB, fail explicitly on
oversize, and do not silently crop evidence. Provider output limits remain the
normal production settings.

The first evidence set reuses two unchanged real historical bad candidates with
fresh native audit/correction calls, then exercises fresh author generation on
the same original state prefixes. Recorded author output is not mislabeled as
new gameplay, and native bridge handoff time is not provider-only latency.

## Recorded evidence and final timing rule

The first historical replay attempt is retained as a failure-quality sample:
the newcomer was rejected after overbroad local/timing checks; the passage
repaired to an incorrect one-day cost. Version B distinguishes canonical Place
changes from local motion, shares the links contract with both authoring paths,
and preserves all prose outside exact issue spans. It fixed both original bad
candidates without changing their narration: one NPC placement, one zero-cost
passage. Each case used three semantic calls plus a normal summary.

Fresh author generation then committed both scenarios. The passage needed one
audit and no semantic correction (five total responses). The newcomer incurred
one unnecessary timing correction and final audit (seven total responses).
An independent new resource fixture used eight responses: shared preview left
the store unchanged; selection made no further model calls, applied one resource
resolution and variation, and reduced ten coins to seven exactly once. Its Author
alternative incurred the same timing false alarm; this cost is retained.

The final deterministic refinement accepts an unknown-time state claim only if
it holds at every canonical checkpoint at or after the established narrative
cursor. Missing/mixed evidence remains unsupported; the cursor does not advance,
and historical/conditional/reported modes are unchanged. All fourteen original
extractions were replayed without new model calls. The two continuing-co-presence
false alarms disappear; initial true defects remain rejected. V1 records retain
their original parser/implicit scope through an evaluation-only compatibility
adapter. This is not a claim of newly measured live-call savings or a statistical
model-quality rate. See reports/2026-10-08/semantic-gate-summary.json; complete raw
requests, responses, state and replay evidence are in the accompanying audit ZIP.
