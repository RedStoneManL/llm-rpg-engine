# Keep explicitly public NPC beliefs attributable

Generic POV filtering intentionally removes other actors' private knowledge.
The context assembler then tried to build NPC knowledge bundles from that
filtered graph, losing even knowledge-as-fact rows explicitly marked public.
Ordinary NPC knowledge does not itself authorize disclosure; restoring the
whole graph would violate the existing privacy contract.

The assembler now adds a narrow separate evidence packet from canonical
`knows:` facts whose own secrecy is exactly `public`. The NPC must be a Person
at the actor's one canonical location, and the topic entity must already be in
the actor's normal visible graph. It preserves that public row's actual belief
and source-event identifier, including stale beliefs, rather than substituting
world truth or another private belief. No knowledge is granted merely by
rendering context; actual dialogue still records what the player learned.

The packet is limited to 16 complete records and 6000 serialized JSON characters.
Oversized rows are omitted whole and partial coverage is indicated; values and
source IDs are never cut. Malformed source metadata, structured/nonfinite values
and exact hidden-entity-ID values are excluded. Public free text remains trusted
as public content; this is not semantic redaction of embedded aliases or IDs.
The source ID is retained, not independently resolved against event history.

Generic POV views, NPC-POV tool restrictions, unknown-truth guardrails and
unmarked/restricted/secret knowledge grants are unchanged. Ordinary
`knowledge_set` does not gain a new sharing default. This does not solve all
NPC disclosure policy or future authored-clue grounding.

## Actual native comparison

[Four independent one-action cases](reports/2026-10-08/native-public-npc-evidence.json)
asked an NPC what they personally saw on an old timetable. One NPC had an
explicit public old belief; the other had explicit scenario evidence of never
seeing the timetable. A separate private canary remained backstage.

Before, the informed NPC said he had seen it but could not recall the details;
his old belief was absent from the request. After, the same input produced
“青桐三号，酉初”, matching that public old belief. The player learned it through
the spoken reply, and the newer objective timetable was not overwritten.
The uninformed NPC declined to guess both before and after. The secret canary
was absent from all four cases' requests and responses.

Eight fresh native Astra completions supplied classifications and narrations
through real play_loop, without repairs. This is a small controlled sample,
not a general accuracy/privacy guarantee, DS result or latency measurement.
Independent static review covered the new read boundary; no full unit suite
was run or substituted for the actual interaction evidence.
