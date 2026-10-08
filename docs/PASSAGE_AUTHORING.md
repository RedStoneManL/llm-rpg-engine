# Record usable passages through the existing links section

The native ferry arrival described a secured gangplank to a newly created
landing. Its structured output recorded docking as facts and knowledge, but no
navigation edge. The production map tool correctly returned `path: []` and
`total_cost: null`. A fact, containment relationship, or an entity move does not
create traversable adjacency.

Author and Hybrid's structured-output instructions omitted the implemented
`links` section. Both now describe `{a, b, travel_cost}` using canonical Place
IDs, including same-turn places. Existing PlaceSystem creates both directions.
Cost is in days, defaults to one, and must explicitly be zero for a short local
passage. The guidance records only a passage actually established and still
usable at turn end; it does not infer connectivity from mention, containment,
co-location, or a possible route. It also does not move the player.

No validator, navigation algorithm, event schema, or movement permission is
changed. This is a prompt-contract correction, not guaranteed semantic capture.

## Temporary-route limit

The existing links operation can add/update a connection but cannot close one.
A boarding connection withdrawn before a turn ends must not be newly recorded
as still open. A connection recorded while docked can nevertheless become stale
on a later departure: that lifecycle remains a separate, prioritized issue.
Current arrival-edge verification must not be read as proof that the entire
journey's changing topology is supported.

## Actual native result

The original recorded T14 waiting action was replayed against the exact 180-event
pre-arrival prefix with fresh GPT-6 Astra responses and normal callbacks. Three
responses included one normal summary, with no research-tool or repair call.
The model generated a new landing and a cost-zero bidirectional link, while
keeping moves empty and the player aboard. A subsequent production map read
returned the deck-to-landing route with total_cost=0, compared with the original
empty path/null cost. The generated landing ID differs between these single
stochastic branches. No response content or route was injected by the moderator.

The normal historical summary also left active scene s6 intact, providing a
fresh follow-on observation for the already-published scene authority fix.
