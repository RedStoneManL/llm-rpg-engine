# Same-turn paragraph interpretation reuse

A local repair used to trigger a completely new extraction of every paragraph.
Recorded play showed an unchanged paragraph acquiring a new transfer assertion
and another unchanged quotation receiving a different epistemic classification.
V8 can preserve unaffected interpretations during one `finalize_candidate` call.
It never preserves their physical verdicts or authorizes state changes.

## Narrow eligibility

The extraction covers the existing nonblank-line spans (normally paragraphs).
Each coverage row now declares `context_span_ids` and `context_complete` for
references, speaker/report/temporal framing, relevance and absence of critical
claims. Dependency completeness is a model attestation, not a host proof.

Reuse requires exactly one validated, newline-free patch inside one span. The
host verifies the complete before/after text, offsets and unchanged other spans.
Actor, input/context, revision, policy, source state, ordered effect sections,
preparation binding and every non-prose packet field must remain identical.
The binding is checked again when consuming the plan.

Changed paragraphs and their transitive reverse dependants are re-extracted.
Unknown/incomplete dependencies, global dependence reaching every span, changed
state/evidence, multiple or cross-line patches, old captures without the new
metadata and opening identity bindings use the existing full terminal audit.
Old captures are never upgraded by inventing dependency fields.

## Final extraction

The model sees the complete corrected prose and bounded packet, but returns
claims and coverage only for affected spans. It must attest contained impact;
global/unknown impact or incomplete affected coverage rejects the candidate.
A freshly interpreted paragraph may reference unchanged retained context. That
direction alone does not invalidate the retained paragraph; newly affected
retained interpretations require global/unknown impact.

The host merges only raw interpretations, assigns collision-free claim IDs,
checks complete combined coverage, and runs every claim through the ordinary
strict parser and global temporal comparator again. Materialization origins are
freshly assessed by the same terminal call. The local capture is not a stored
cross-turn cache or a reusable publication credential.

The call allowance is unchanged: one first audit, at most one correction and one
terminal audit. Once a selective terminal call begins, rejection does not trigger
a third audit. This change reduces unrelated reinterpretation, not model-call
count. Unchanged sentences inside a changed paragraph can still be reinterpreted.

## Recorded native check

The fixed real Author proposal from the materialization episode was processed
once with fresh Astra auditors/correction. No Author was redrawn and no events
were published. The first v8 extraction found eight claims and the existing
wood-chip issue. Its actual dependencies caused s2 and s3 to be re-extracted;
five raw claims in s0/s1/s4 were retained. All seven final claims received fresh
verdicts and the source origin was freshly assessed. The final candidate passed
in three calls, the same call count as the corresponding old audit/correction
phase. This is one observed trace, not a success-rate or latency comparison.

The repair changed the wood-chip sentence to say that the NPC had not confirmed
its position, while the original fact/knowledge effects remained more definite.
The physical auditor did not adjudicate that information-strength difference.
Source support for relevant untracked scene details and preservation of such
meaning during repair remain separate work; this cache does not excuse the gap.
