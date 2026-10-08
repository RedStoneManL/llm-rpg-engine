# Semantic consistency: observed causes and enforcement boundaries

This ledger groups actual failures by mechanism, rather than treating each
story-specific symptom as a new prompt exception. Names in the reports identify
fixtures; production rules must not depend on those names.

| Actual failure | Current intervention | What remains |
| --- | --- | --- |
| New NPC speaks on site, but cast creation has no moves | Explicit creation/movement guidance; host requires real placement and delivered introduction before granting identity provenance | A model can still miss a claim; canonical assertions now pass the shared gate |
| Secured gangplank is narrated, but no map route exists | Document existing links schema; new native arrival emits the route | The shared gate checks extracted passage claims; extraction itself remains fallible |
| Invalid handover is removed during repair, but prose still says it happened | Conditional narration rewrite plus shared ordered custody/transfer audit, even when proposed effects are already empty | Model extraction can still miss a handoff; custody does not prove ownership or consent |
| Show-only prose triggers an interval-custody false alarm | Positive whole-primary-turn custody certificate across every preview event; explicit extracted interval timing | Historical intervals and uncertified/uncertain claims remain unsupported; original extra calls are retained |
| Historical summary selects an old active scene and inflates pacing | SceneSystem owns active scene after a boundary; previews and pacing/thread consumers follow authoritative history | Old stored thread recency labels and provenance are not rewritten |
| Temporary passage remains navigable after physical closure | Explicit close event, ordered state checks, typed endpoints and commit preflight are implemented | Omitted closure remains a semantic detection problem; closing does not establish movement legality |

## Shared pre-publication check

The first increment covers current location/co-presence and passage connectivity
through ordered physical preview and staged publication:

1. Extract bounded typed assertions from candidate prose with exact source spans,
   canonical references where available, and present/past/conditional/reported
   distinctions. Candidate prose is untrusted data, not world truth.
2. Compare those claims to approved canonical state and transitions. Verify spans,
   IDs, types and coverage deterministically. Missing/redacted/unbound support is
   unknown, not evidence of absence and not a passing claim.
3. Allow a bounded repair through existing validated authoring or narration-only
   rewriting. Do not turn extracted prose into effects automatically or invent
   player choices to make prose true.
4. Re-audit the repaired candidate before publication. Reject exhausted critical
   contradictions/unsupported completed actions, while leaving unrelated prose
   outside the explicitly covered claim types.

Candidate-created NPC labels need a separate bounded binding list because an
unplaced newcomer is absent from the existing local-state packet. Such a list
must not grant observed identity, location or private profile knowledge.

Normal, prepared and compare candidates must use the same gate. Any host-only
approval must bind source revision, actor, actual input, complete prose/effects
and resolved outcomes; model JSON cannot supply that approval. Replay of old
saved events is not a new publication and must remain compatible.

This adds a model judgment, not a deterministic proof of natural-language
meaning. A normal candidate costs one audit call; a failed audit can require a
repair and second audit. Preserve all attempts and report model calls/latency
separately from state invariants. The intended first evidence uses the real
newcomer and gangplank failures, followed by fresh native generation on their
original state prefixes. No generic free-fact/trust rewrite or keyword blacklist
is part of this first increment. Background narration, summaries and arbitrary
NPC knowledge remain outside the foreground physical scope.
