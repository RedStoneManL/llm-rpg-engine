# Recall already-known facts with their actual source

The ferry playthrough exposed a gap in the POV research tool: a query for
“白鹭二号” returned no hits although the same actor's map query returned the
announcement they had heard. The recall fan-out searched entity descriptions
and topology, but did not supply generic actor-known fact candidates.

`recall_query` now appends a bounded substring-matched lane of `actor_belief`
records from the querying actor's own valid `knows:` facts. Each record keeps
the original belief value and its knowledge-event source, grant turn and day.
It explicitly remains a possibly stale or false belief. The normal POV topic
filter and hidden/internal exclusions apply; other characters' knowledge is
not included. The actor must be an existing visible Person. Malformed records
are omitted with partial-coverage metadata. The lane holds at most 16 complete
records and 12,000 JSON characters, without truncating values or provenance.
Existing recall lanes are unchanged. Source IDs are retained from the graph;
this reader does not independently authenticate an arbitrary imported event.

This distinction matters because a POV projection can replace canonical truth
with a belief while retaining the truth fact's source. The new lane reads the
original actor knowledge record rather than attributing the substituted value
to that unrelated source. Current public truth is not folded into personal
memory. Separately, the ambient tool description now explicitly mentions
public notices and schedules, including during a named-NPC conversation.
That lookup still does not establish that the NPC knows or has said the fact.
The public-data permissions and executor are unchanged.

The exact original tool request on the actual pre-T7 event prefix now returns
one belief sourced to `ev_685715d7a132`, matching the original `knowledge_set`
actor, key, value, turn and day. The distinct public assertion has source
`ev_9e815c046b23` and is not used as the memory source. This is a deterministic
production-tool replay, not a new model turn or evidence that prose will
always remain consistent.

A separate fresh native Astra turn reused the original T6 action and recorded
pre-T6 state with a fresh Author context and the real tool executor. It chose
`ambient_query("班次")`, retrieved the existing 赤鹭九号/午后 schedule alongside
older beliefs and a pending notice, and then made an unproductive empty ambient
query. Its delivered answer used 赤鹭九号/午后 and visibly replaced the pending
notice. The canonical schedule and absent keeper's old belief stayed unchanged;
the player gained the new schedule through the actual conversation. One action
committed through five native responses, two tool calls and a normal scene
summary, with no repair. The player did not buy a ticket. The boarding platform was
already described in the prior player-visible scene; its use for this sailing
was newly confirmed in the scene, not a field returned by the schedule tool.

This sample exercised the public-record description, not the new recall lane:
the latter is demonstrated by the exact tool replay above. These two changes
are not an ablation, and one fresh response cannot establish a general semantic
success rate. The old third-boat invention remains in the preserved before
trace. Full synthetic requests/results are archived separately; the compact
[evidence report](reports/2026-10-08/known-fact-recall-after.json) includes source
and state checks. No DS call or broad offline test campaign was used.

The copied seed retains database-serialized top-level secrecy strings. The
prefix matches original event content after decoding that storage field and
excluding sequence metadata; it is not a byte-identical event copy or a
secrecy-boundary test. Independent review confirmed the initial narrator
messages and player action match the original case.
