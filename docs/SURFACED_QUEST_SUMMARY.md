# Preserve the summary when a clue becomes an active line

The real umbrella scene wrote a `quest_surfaced` event with the summary
“询问寻伞告示，得知蓝伞的来历及昨夜最后出现的位置。” The event converter
retained it, but the projection only changed the line's state. Four subsequent
actual narrator requests consequently displayed `（暂无摘要）` in the active
ledger.

A valid dark-to-active transition now copies a supplied nonblank string summary.
Missing, blank or malformed values leave existing data unchanged. Invalid-state
and duplicate transitions retain their prior guard. Replaying existing events
recovers the summary already stored in them; event bytes are not rewritten.

No summary is synthesized from hidden lore, its secret or future stages. The
existing active-ledger visibility policy is unchanged. The observed source
summary only described information already disclosed to the player.

[The native follow-up](reports/2026-10-08/native-surface-summary-after.json)
reprojected the actual first-turn event prefix and ran one fixed player question
through real play_loop. Two fresh native Astra responses classified the input
and delivered the reply. The actual prompt now contains the supplied summary;
the narrator recalled the disclosed blue umbrella, daughter and wooden rack
without inventing a player agreement or revealing the hidden culprit/location.

This confirms the small data path, not universal prose fidelity. Authored future
hints are not automatically canonical facts; dark stages intentionally stop
after surfacing under the original player-driven design. Those are separate
questions. The old fixture's optional clue-history field was also mislabeled
`clue` instead of `hint`; current stage disclosure still worked, and that fixture
mistake is not attributed to production code. No full unit suite was run.
