# Semantic consistency: observed causes and enforcement boundaries

This ledger groups actual failures by mechanism, rather than treating each
story-specific symptom as a new prompt exception. Names in the reports identify
fixtures; production rules must not depend on those names.

| Actual failure | Current intervention | What remains |
| --- | --- | --- |
| New NPC speaks on site, but cast creation has no moves | Explicit creation/movement guidance; host requires real placement and delivered introduction before granting identity provenance | A legal empty moves section still passes structural validation despite the prose |
| Secured gangplank is narrated, but no map route exists | Document existing links schema; new native arrival emits the route | The author can still omit links; a model-generated successful sample is not a guarantee |
| Invalid handover is removed during repair, but prose still says it happened | Conditional narration-only rewrite after changed physical effects | Original prose-only effects do not trigger this condition; rewritten prose can still be inaccurate |
| Historical summary selects an old active scene | SceneSystem owns active scene after a boundary; previews/director use projected context | Raw-label pacing consumers remain separate; old stored provenance is not rewritten |
| Temporary passage remains navigable after physical closure | Explicit close event, ordered state checks, typed endpoints and commit preflight are implemented | Omitted closure remains a semantic detection problem; closing does not establish movement legality |

## Proposed shared pre-publication check

The first increment should cover current location/co-presence and passage
connectivity. It should reuse ordered physical preview and staged publication:

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
