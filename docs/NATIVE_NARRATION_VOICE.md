# Light-novel voice without invented player thoughts

The eight-action native episode repeatedly inserted protagonist judgments such
as “很好。跑腿终于有了句号。” The isekai pack explicitly made inner quips a
driver of every turn, despite `reference/narrative-style.md` preferring scene
and NPC detail over protagonist monologue.

The pack voice and its matching variation now use natural dialogue, NPC
behavior and situational contrast. Humor is optional; player-expressed thoughts
can continue, but unexpressed judgments, motives and feelings stay with the
player. There is no phrase blacklist, output rewriting or added model call.
Other packs and explicit player style overrides retain their existing behavior.

## Actual native episode

[The recorded result](reports/2026-10-08/native-voice-after.json) covers six
adaptive inputs from a fresh independent player: one clarification-only input
and five committed story turns, with eleven real native Astra responses.
The story offered a puppet demonstration, answered how the wooden duck works,
and let the player choose a limited role observing a boat trial. All five
delivered prose responses used dialogue and visible behavior without invented
protagonist evaluations. Humor remained in the puppeteer's explanation that
the duck could enter water but was still learning to get out.

The first input exposed an unrelated real failure: “第三天中午前” triggered a
date clarification. That result is retained; the existing parser's Chinese
ordinal and before-boundary limitations are not fixed by this change. Ordinary
mutation-based fallback dark records also remain a separate issue.

This small stochastic native episode is not a matched before/after accuracy
measurement, DS verification, long-term continuity test or latency benchmark.
No unit-test campaign was used as a substitute for gameplay. The data-only
change received independent review and JSON/content checks.
