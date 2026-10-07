# Backstop quest sources must be gameplay effects

The repaired T14 native replay correctly narrated that the bell remained with
the player, yet its later backstop created a hidden quest from the unchanged
clock.reason text claiming a handover. This was deterministic event consumption,
not a second model misunderstanding.

backstop_quests previously ranked every new event. Unrecognized event types
received importance1 plus1 for any deltas, meeting the substantive-event
threshold. A no-op clock was first in the stream, won a tie, and its free-form
reason was copied verbatim into the quest summary.

The fallback now checks eligibility before ranking. Supported typed mutations
are item transfer, movement, fact assertion, relation addition, relationship
change, non-arc character evolution, knowledge acquisition, and an actual
finite before/after resource change with outcome spent. Required identifiers
and owner-required values must be present; false/zero values are not treated as
missing. Clocks, narration/summary storage, provenance, rolls, director/quest
control, reflection arcs, retracted rows, and unknown types cannot seed this
fallback. Creation/materialization and other unsupported types stay excluded
until explicitly supported.

This is a conservative event-consumer gate, not another world validator. Most
mutation events do not carry old values, so the gate does not claim every fact
assignment materially differs from its previous value. It assumes its normal
caller supplies committed owner-applied events. Global importance scoring,
clock.reason, threshold, duplicate suppression, active-quest suppression, and
intentional quest-generation paths are unchanged.

## Recorded trace comparison

The [evidence](reports/2026-10-07/backstop-real-trace-after.json) uses identical
recorded streams before and after the code change:

- Repaired T14: old gate returned a quest seeded by “时间未推进：柜台前一次交接…”;
  new gate returned no quest. The source events and rationale were untouched.
- Genuine T16 return: old gate selected the clock rationale; new gate selected
  the real item transfer “jade_seal 转移至 keeper”. Canonical holder was keeper.
  The original stress run had this hook disabled; this result is explicitly
  replay of its real event stream through the consumer, not a new live game.

The importance module's file hash is identical before/after. No model/API call,
fresh gameplay, or offline unit-test campaign was involved. Independent code
inspection found no blocker. Eligibility is not a guarantee that every selected
event makes an interesting quest or that all other metadata consumers are safe.
