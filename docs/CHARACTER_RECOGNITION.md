# Recognize characters through any already-known facet

In the actual ferry continuation, the player met Xu Lan in the market and
learned two facts about her timetable answers and directions. After leaving
for the bridge, `characters_query(q="ferry")` nevertheless returned her exact
ID with `known:false`. The ID and knowledge had not disappeared: the tool's
early recognition gate considered only known sketch and goal fields, contrary
to its documented any-facet rule.

Recognition now accepts a non-null, currently valid knowledge fact owned by
the resolved querying actor under that character's exact ID prefix. False,
zero and empty-string beliefs still count; null means unknown. The character
must already have passed the normal POV entity filter. Each returned facet
retains its separate knowledge gate and hidden/internal exclusions. Knowing
one answer does not unlock a private goal or character profile.

The precise original tool call was replayed against the real pre-T6 event
prefix, not the later state containing another newly created character. It now
returns Xu Lan's ID plus the same two learned answer facets, without revealing
a name, sketch or goal. This is a deterministic production-tool read, not a
new model turn. It does not add an introduction/name index, create knowledge,
make all created NPCs public, or repair generic public-fact recall coverage.

[The evidence](reports/2026-10-08/character-recognition-after.json) separates
that exact tool replay from the subsequent fresh-model turn. The original
tool-enabled process lost its executor after committed T6: its T7 classifier
had completed, but its next narration request had no response or receipt.
A new Author/tool segment resumed only the pending player input from committed
T6. Four real native Astra responses produced one turn and a normal summary.
The model chose character, recall and map queries using the original executor;
the reply answered ticket/return questions and waited without buying a ticket.
It queried co-present Zhou rather than Xu Lan, so it is integration evidence,
not additional direct coverage of the repaired Xu Lan gate.

All prior outputs, including the unfinished request, are retained. Two natural
summaries kept the old timetable and pending-confirmation sources distinct.
Generic fact recall still missed already-known information, and the earlier
model invented a third boat without retrieving the fixture's public schedule.
Those remain separate findings. No full unit suite or DS calls were used.
