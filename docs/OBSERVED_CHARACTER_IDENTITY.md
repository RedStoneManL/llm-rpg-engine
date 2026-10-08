# Remember an actually introduced character after leaving

In the actual ferry source-revisit episode, the player spoke to a newly created
dispatcher, left for the inn, and asked about returning to him. The next
`characters_query("")` omitted his ID. The player had learned the ferry schedule,
not a facet on the dispatcher himself. The retained introduction passage did
not make him discoverable. The final narration did not create a duplicate NPC;
the demonstrated failure was retrieval, not duplicate identity.

Old introduction snapshots are not enough to grant identity visibility. They
associate new, POV-visible creations with a published scene; a public off-scene
creation can satisfy that old rule. No old snapshot is silently upgraded.

For new source events, the host may attach `observed_identity` to an introduction
person. This requires a validated new Person creation, the same single canonical
physical location as the actor at publication, an actual committed narration
link, and a declared name/role found verbatim with matching boundaries in the
retained published passage. Duplicate or colliding safe labels suppress the
marker. The marker stores the historical location and exact label offsets.
Author and Hybrid guidance asks for the visible literal label when introducing
a person; an unnamed dispatcher can use a role label without inventing a true
name. A missing, ambiguous or truncated-away label grants no marker.

The actor-owned reader validates the complete source and marker. Only explicitly
marked identities become eligible; a generic creation, a summary, another
actor's record or a current location cannot manufacture the historical grant.
The normal POV view first filters its facts and relations as before, then adds
marker-only identities with empty attributes and no current facts or edges.
This preserves an ID and type, not current whereabouts, relationships, inventory,
private goals or sketches. Existing independent visibility paths are unchanged.

`characters_query` can match that ID and its stored published label, and can
return up to four whole introduction sources within a 12,000-character optional
lane. Coverage marks omission. A provenance-only identity cannot be matched
through a private profile. Facets retain their own knowledge gates. The original
passage can mention several people: neither the label nor the passage proves
speaker attribution, a true name, NPC hearing, or any inferred personality trait.
This is structured publication provenance, not a general semantic proof that
every phrase entails a character fact.

The first fresh-model verification attempt exposed a separate prerequisite
failure: its prose introduced the dispatcher and its cast declaration supplied
the exact role label, but `moves` was empty and the character had no canonical
location. The host correctly issued no observed-identity marker. That one-turn
trace is retained unchanged. Author/Hybrid moves guidance and the newcomer
example now explicitly require actual initial placement; mentioning a person
or talking remotely does not place them beside the actor. This is guidance,
not a new validator that proves prose and movement always agree. A separate
fresh branch verifies the combined change rather than rewriting that failure.

In that second branch, three real actions committed through fourteen fresh
Astra responses, eleven model-chosen tool calls and three normal summaries,
without repair. The first action reused the actual pre-creation input; an
independent player chose the subsequent departure and return. The new dispatcher
was placed at `bridge` and introduced literally as 发船管事. After the actor left
for the inn, the fresh narrator autonomously queried `ferry_dispatcher` and
received the original actor-owned introduction. The final response referred to
the same person with `cast: []`; the first knowledge facet on that NPC arrived
only during this later conversation. No duplicate person was created.

Four separate production-tool reads on the real departed-state prefix confirm
empty-query and role-label discovery, absence of private-profile search matches,
and an identity-only graph entry with no attributes, facts or incident edges.
These reads are not model responses or new gameplay actions. An independent
audit checked the published text link, label offsets, source ownership and
unchanged source hashes. Both branches use the exact original 86-event JSONL
prefix, without retrofitting observation markers into the old save.

The [compact evidence](reports/2026-10-08/observed-character-identity.json) keeps
the original omission, attempt A's missing-location failure, and attempt B's
result distinct. This is a single successful continuation of a combined change,
not a causal ablation or a broad identity guarantee. The player still could not
obtain ticketing details or a concrete next step; information pacing remains an
observed separate issue. No DS calls or broad offline test suite were used.
