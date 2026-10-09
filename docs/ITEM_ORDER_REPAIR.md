# Conserved cross-item ordering and custody intervals

The foreground semantic gate can repair a narrow mismatch between narration
and independently grouped item declarations without rewriting correct prose.
It offers original row indexes, never a general `items` editor.

## Eligibility and conservation

- Only the built-in ObjectSystem's minimal create/transfer chains qualify.
- At least two objects must be implicated by the audited issue quotes; each
  matching quote is bound to its exact span and occurrence.
- Existing holders and every explicit handoff need positive public packet
  evidence, including repeated-handoff multiplicity. Known identity alone does
  not expose a hidden custody relation.
- Source-less initialization requires an authenticated new candidate creation,
  its first initialization, and positive public custody evidence. Redeclaring
  an existing object cannot erase its prior custody.
- Materialization, custom/opaque declarations, resource actions, movement,
  changed scene time/place, opening-text-only policy, immutable prose and
  relevant open return commitments stay outside this lane.
- The proposal is a complete permutation: each original row appears exactly
  once, every object's subsequence is unchanged, and ineligible rows keep their
  positions. It cannot also edit movement or passage declarations.
- The entire proposal is revalidated without repair fallback. Candidate source
  indexes are mapped through the exact permutation, not discarded. The admitted
  physical endpoint and whole-turn custody certificates must remain equal.
- A reordered proposal always receives a fresh full semantic extraction and
  deterministic audit. Earlier interpretations are not reused for this pass.

## Positive custody evidence

Public transition snapshots can omit an invisible custody excursion. Therefore
interval support is tracked privately after **every projected event**, including
those omitted from public transitions. Losing visibility or changing holders
ends a continuous run. Returning to the same holder does not restore that run.

Only positive `{item, holder, first, last}` ranges over public checkpoint
positions leave the host. Hidden holders, hidden event counts and private run
markers are not exported. Missing, outdated or incomplete certificates fail
closed; endpoint equality is not evidence of continuous possession.

Two narrow narrative cases use these intervals:

1. An exact co-quoted handoff's positive source-custody qualifier may retain its
   supported anchor without advancing the narrative cursor. The full bound
   interval must be certified; the actual handoff still obeys normal chronology.
2. A completed positive possession assertion with unknown intermediate timing
   may be bracketed by the nearest non-overlapping, exactly bound receipt and
   release of the same item. The receipt must already be supported, the incoming
   cursor must lie inside the bracket, and the **entire** bracket must have both
   event-level certification and supported public checkpoints. The point stays
   unspecified and does not advance the cursor. Both handoffs remain audited.

Overlapping quotes, missing endpoints, repeated unbound handoffs, uncertain or
historical assertions, negative possession, hidden excursions and cursor rewind
cannot use these exceptions. Custody proves neither ownership nor consent.

## Verification and limitations

The five focused suites under `tests/loop/` cover `item_order_repair`,
`item_order_gate`, `coclaimed_custody_preconditions`, `event_custody_intervals`, and
`bracketed_custody`. They use offline responses; integration tests exercise real
registry validation, preview, gate approval and store-write rejection.

A separate captured live-model candidate regression verifies the original
proposal, an isolated model-generated index permutation, a fresh isolated final
audit and an actual commit in an independent campaign copy. Historical gameplay
is not rewritten. Model extraction can still omit or misclassify claims; passing
coverage checks does not establish perfect semantic recall. Full-suite baseline
failures are reported separately from focused successes.
