# Physical item claims in the shared publication gate

The existing gate now extracts physical possession and positive completed
handoffs, alongside location and passage claims. This uses the same bounded
POV outcome packet, one extraction, one permitted correction and re-audit,
and host-bound normal/prepared/compare publication approval.

The source is canonical `held_by` and ordered `item_transferred` preview events,
not free-form ownership facts or candidate prose. Per-transition snapshots carry
the same filtered custody rows as the initial/final states. The extractor sees
publicly eligible Object bindings and visible Person/Place holders; hidden
destinations are not exposed to improve matching.

- Physical possession is not legal ownership, consent, payment or permission.
- A completed handoff needs a recorded change between two visible, distinct
  holders. A quoted source endpoint may be omitted, but canonical evidence may
  not be invented. Matching follows narrative/event order.
- A→B→A contains two transfers even though its final holder equals its initial
  holder. Continuous A possession alone supplies no transfer event.
- Creation and first placement can support final possession, never a handoff
  from an unknown prior holder.
- Missing/redacted custody is unknown, not proof of no holder. A negative
  possession claim about one holder requires explicit different-holder evidence.
- Point possession requires a visible checkpoint or the stronger positive
  whole-primary-turn certificate below. A current/completed assertion with an
  unknown point within this primary turn can use that same certificate without
  inventing a time or relabeling itself `throughout`. Equal endpoints alone are
  insufficient. Missing item/holder identity, uncertain scope, historical claims,
  and absent/ambiguous certificates are not resolved by this rule.
- Explicit `throughout` timing covers this entire primary turn only. The host
  intersects initial unique visible custody with the POV-filtered state after
  every preview event and the final state. Only surviving positive pairs are
  exposed. A visible or hidden excursion, visibility loss, or missing intermediate
  custody permanently removes certification; no hidden endpoints, checkpoint
  counts, or failed-pair reasons are exported. This cannot certify past history.
  A supported interval leaves the narrative cursor unchanged; uncertain claims
  stay uncertain and are not upgraded automatically.
- Showing, offering, promising, quoting and reporting do not automatically
  establish a completed transfer. Pure absence of a handoff remains outside the
  positive-transfer schema.

Item corrections can replace only exact issue spans in narration. They cannot
add/remove transfers or alter other approved effects to fit the story. Item
holder references cannot authorize the separate missing-new-NPC/Place repair
permissions. This preserves player intent as intent, not proof of success.

Earlier item-claim additions changed the extraction schema and comparator policy,
invalidating old in-memory approvals. The later unknown-point certificate reuse
changes comparator policy while retaining the v12 extraction schema. Historical events remain replayable. Trusted manual writes
retain their documented boundary. Model extraction can still omit or misclassify
claims; this is bounded semantic coverage, not a natural-language guarantee.

## Observed iteration

The frozen v3 trial used 13 fresh Astra responses: a recorded real DS T14 stale
handover candidate plus fresh correction/audit/summary (4), a new show-without-
giving action (6), and a new give-inspect-return action (3). The latter generated
and committed both player→keeper and keeper→player transfers with no repair,
despite ending with the same holder. The historical bad candidate was corrected
without adding an item effect.

The first show-only candidate was already factually consistent, but its phrase
about keeping the item throughout triggered the then-unsupported interval
boundary, costing two unnecessary calls and a wording change. That failure is
retained. v4 adds the positive whole-turn certificate rather than a phrase
exception. Replaying the frozen extractions does not relabel their uncertainty
as success; the revised contract requires a fresh extraction.

The final v4 replay of that exact original show candidate used two fresh
responses (audit and normal summary), committed with byte-identical narration,
zero repair and zero transfer. This is fresh verification of recorded author
output, not another independent author-generation sample. Targeted host probes
on the recorded initial world reject whole-turn certification for visible and
hidden round trips, a Place-holder round trip, and new-item initial placement.
Five frozen v3 extractions retain their original verdicts under deterministic
replay after updating only the schema identifier. No DeepSeek request or broad
offline suite was run. Native handoff elapsed time is not production latency.

Residual evidence: the normal callback summarized historical scene s6 as the
bell being locked in the keeper’s cabinet, repeating old prose while canonical
custody remains player. This foreground gate does not reconcile historical
narration or summaries; their assertions must not be treated as current truth.


## Unknown point inside a certified primary turn

A later real repair attempt described holding a rough blank while trial-fitting,
removing and shaving it. Both native audits correctly identified no transfer but
selected `completed/unknown` custody. The host already certified that exact
Object/actor pair for the whole primary turn, yet its unknown-point branch refused
to use the evidence. The shared certificate comparator now serves both interval
and unknown-point queries after the original mode, scope and reference gates.
It preserves the raw claim and current narrative cursor. A certificate for A
supports positive A or negative B for a distinct known holder B, contradicts
negative A or positive B, and proves
neither when missing. It never establishes a handoff, permission, ownership, or
custody before/after the primary turn. The original rejected attempt remains
uncommitted; replay evidence is distinct from subsequent actual play.

The [captured replay and producer boundaries](reports/2026-10-08/certified-point-custody.json)
record the unchanged native assertions and distinguish constructed proof controls
from actual gameplay. They do not turn the prior failed attempt into a success.
