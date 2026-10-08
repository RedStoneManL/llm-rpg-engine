# Retiring the orphan mutation-to-quest fallback

Real native play created dark quest rows from returning to the inn, stepping
into the courtyard and inspecting an undamaged item. They were truthful state
changes, but supplied no evidence of an unresolved objective.

The old fallback copied a mutation summary into `quest_created` with only an
ID and dark state. Unified lore requires anchors for ambient disclosure and
stages for advancement; these records had neither, nor a complexity-derived
lifespan. They accumulated without a normal consumer. They also did not occupy
ordinary density quotas, so they are not evidence that legitimate scenes were
being crowded out.

`digest_fleet` no longer makes that automatic write or its extra projection.
`backstop_quests` remains a no-op compatibility symbol for existing callers.
Historical `quest_created` registration/projection is retained unchanged.
Explicit narrator quests, authored lore, normal stage progression and director
events retain their existing producers and consumers.

This retires an invalid fallback contract; it does not restore detection of
omitted objectives. A future recovery path would need source evidence of an
unresolved objective and a deliberate consumption route. Increasing a score
threshold or adding anchors to ordinary movement would not supply that evidence.

Removing a formerly emitted event can change later turn-derived random seeds.
Actual-play checks therefore examine coherent authored-line behavior rather
than demand identical old random events.

## Actual authored-line play

[Five adaptive native actions](reports/2026-10-08/native-authored-lore-after.json)
used a complete anchored authored line with its first clue already available.
The normal context path exposed that clue while keeping its hidden cause out
of player prose. The player asked about the missing umbrella, then explicitly
declined searching and chose to rest. The NPC accepted; a normal director event
brought a theater company, whose members moved their own scenery. The player
chose a seat and watched a complete play through its curtain call and lunch.
The final clock was day 1/noon, matching the requested morning of rest.

Eleven actual native Astra responses supplied five classifications, five
narrations and normal density generation. The resulting stream contains three
authored `lore_created` events, two `lore_advanced`, one `quest_surfaced` and one
`director_fired`, with no generic `quest_created`. The independent player found
no clear continuity error and felt the refusal was respected, while noting the
director's arrival immediately after asking for rest felt slightly arranged.

This was one stochastic native episode, not DS validation or a quantitative
causal comparison. The initial clue was seeded, so this does not demonstrate
autonomous initial discovery. A setup error before model calls and a mistaken
resume attempt after a tool transport failure are preserved separately; the
original session actually survived, and the duplicate attempt wrote no events
or model responses. The game context stayed continuous.
