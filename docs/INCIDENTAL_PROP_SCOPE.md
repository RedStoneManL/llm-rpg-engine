# Incidental NPC props in the physical audit

The physical extractor retains a narrow `incidental_prop` claim instead of forcing every background prop into an Object ledger. A successful exemption is `out_of_scope`, never a supported canonical custody fact. It does not create an object, establish historical ownership, or authorize later transactions.

The original `background_only` branch requires the model to identify an ordinary untracked prop used only in an already-present NPC's background gesture. It reports an exact object phrase and explicit relevance (`background_only`, `task_relevant`, or `uncertain`), while considering the full current candidate and actual player input. Relevance and reference extraction are probabilistic; quoted text proves provenance, not entailment.

For that background-only branch, host checks allow the exemption only for positive possession, an unbound item, a bound existing non-player Person, explicit before/after timing, and canonical co-presence both before the turn and at that checkpoint. Transfers, tracked/candidate Objects, player/Place/unknown holders, negative claims, uncertain timing/relevance, and newly placed candidate NPCs cannot use it. A literal match against a visible/candidate Object's ID or name conservatively prevents exemption; it is not semantic synonym recognition.

Any explicit items/quests/promises declaration, Object creation, fact/relation write involving an Object, current host return-commitment request (including reaffirmation), or newly staged actor commitment disables the background-only exemption for the whole turn. This deliberately retains some false positives. An active objective expressed only in natural language still relies on the model's relevance interpretation; the host does not claim to prove irrelevance.

Resource status comes from this action's actual staged `resources_resolved` event or sealed comparison prefix. It carries actor, turn, status and source ID, not amounts or balances. Only `none` permits the exemption. `spent`, `insufficient`, missing/stale/malformed metadata, and unknown status keep the claim in the ordinary audit. An actor without configured resources receives an explicit host no-resources proof, rechecked at consumption. Projected `last_resolution` is never used as current-action evidence. The proof is authenticated by staging/selection; its reader is not an event-store membership checker or a sandbox against arbitrary host Python.

The resource/return-request fields are bound by semantic approval and comparison fingerprints. Comparison preparation version 2 rejects retained older payloads explicitly. Existing accounting, repair allowances and model-call counts are unchanged. The exemption is conservative for opening/direct/legacy callers without a current resource proof; it does not solve every decorative-prop case.

A later, distinct [secondary presentation carrier](SECONDARY_CARRIER_SCOPE.md) role permits a bounded item-only footprint backed by independently tracked content custody and handoffs. It does not broaden the original background-only branch or certify carrier custody.

## Evaluation boundary

The bounded matrix uses one fresh native extraction per fixed candidate: actual decorative coin prose, actual unsupported bell handoff, actual glove first-placement/handoff ambiguity, and a labelled synthetic untracked-key pickup. Original author text is fixed; no redraw or repair-until-pass loop. Structured host-guard mutations are separate from model results. This is audit-granularity evaluation, not a new complete gameplay episode or a statistical estimate of error rates.
