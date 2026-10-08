# Explicit passage lifecycle

A usable gangplank must stop appearing in navigation after it is withdrawn.
This increment extends the existing links contract with typed close events;
it does not infer events from narrative keywords or arbitrary character moves.

- `links: [{a, b, travel_cost}]` retains the existing open/update behavior.
  Explicit `op: "open"` has the same effect.
- `links: [{op: "close", a, b}]` emits `place_unlinked`. It closes every current
  adjacency record for precisely that endpoint pair, in both directions.
  Historical rows and opening sources remain; unrelated exits remain open.
- Reopening emits new records with a new source event. One pair represents one
  aggregate bidirectional passage; independently closable parallel routes are
  outside this contract.

## Host checks

New declarations require distinct Place endpoints, supported operations and
nonnegative integer travel costs (excluding booleans). Close omits cost and
requires an existing edge at that point in the ordered declarations. Closing
unknown endpoints cannot auto-create Places. Creation sections precede dependent
item/link operations, so same-turn new places work independent of JSON key order.

The same state checks run against ordered private projection and new events at
the atomic publication boundary. Generic new `adjacent_to` relations must use
links instead. Re-declaring an actively linked Place as a different type is
rejected. Old event replay remains permissive; an absent historical closure is
harmless, and existing default-open events retain their shape.

If structural repair changes links, the existing physical-outcome signature
requires narration reconciliation. The approved packet contains only local,
POV-visible passage endpoints and costs. Rewriting can change text, not effects;
invalid or empty reconciliation rejects the turn. This does not detect omitted
links in otherwise valid original prose; the shared semantic check proposed in
SEMANTIC_CONSISTENCY_LEDGER.md addresses that separate gap.

## Deliberate boundaries

Closing a passage does not move a character, erase their knowledge, or prove
that all moves are reachable. Existing moves validation is not a path-permission
system. Route visibility is the existing POV/endpoint contract, not a new hidden
route metadata schema.

The graph is day-granular. Open and close on the same day retain history and
source events, but the final relation interval is empty for that day. Earlier
same-day availability is recovered by replaying the earlier event prefix, not
by querying day=1 on the final graph. No band/turn temporal API is added here.

The real arrival save was used for direct checks of ordered close/reopen,
malformed endpoints, no-mint closing, private outcome reconciliation, and direct
invalid publication without a revision change. These are deterministic contract
checks, not substitutes for native gameplay evidence.

## Actual native episode

A fresh independent player continued the exact completed arrival save. Across
three committed actions, nine native GPT-6 Astra responses, five research-tool
calls and one normal summary, the player disembarked, asked about the vessel's
plans, and voluntarily handed the crew a basket. Disembarkation kept the route
open for remaining passengers. Only after the last passenger left did the
narration describe withdrawal of the gangplank and departure, accompanied by a
`place_unlinked` event. The subsequent production map query from shore to ship
returned an empty path/null cost, and both historical adjacency records had
ended. The player stayed ashore, the deckhand stayed aboard, and the ticket
remained with the player. No repair was needed in this episode.

The independent player found the actions coherent and voluntary, while noting
that consecutive on-the-spot scheduling decisions felt convenient. This remains
an experience limitation, not a silently removed failed result. Native bridge
handoff time is recorded separately and is not production-provider latency.
