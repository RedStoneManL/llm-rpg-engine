# Decisions needed (托管 batch done — resolve on return)

All 9 playtest fixes (#1–#9) implemented, opus final review = READY (0 critical/important),
full suite 1505 green, live-smoke validated. Below = the genuine forks I did NOT decide for you.

## D1 — Stream the DM narration? (the remaining latency lever)
Done already (cheap wins): #9 verbosity dial (default **medium** → shorter turns) + #8 modular
repair (no full re-write). The BIG remaining perceived-latency win is STREAMING the narration
(print it as it's generated). Substantial change: `llm/provider.py::_do_post` is non-streaming
(blocks for the whole completion); streaming needs an SSE request path + the play loop printing
chunks live. Pairs with the #4 spinner. **Want streaming? (I'll spec+build if yes.)**

## D2 — Re-push the updated engine to the public repo?
The public `llm-rpg-engine` snapshot is STALE — predates BOTH debug-mode (viewer/--debug) AND all
9 playtest fixes. To make public v0.1 current = re-snapshot (`git archive HEAD`) + push. Outward-
facing/irreversible → your call. **Re-push now, or leave public as-is?**

## D3 — Richer world history? (minor)
#2's intro uses the frame's `central_conflict` as the backdrop. A dedicated multi-event "过往历史"
(a few authored historical beats) is richer but a small new gen step. **Good enough, or fuller?**

## D4 — Protagonist: engine-authored vs player-defined? (minor)
#6b has the engine AUTHOR the protagonist (name/身世/goal) to fit the world (matches your "写出来").
Alternative: a session-zero where YOU define your character. **Keep engine-authored, or add PC creation?**

(Advisory impl-notes, NOT your call — see ledger: M2 empty-required-section repair path narrowed;
M3 reroll doesn't print progress; M4 sanitizer leaves rare OSC/DCS escape tails.)
