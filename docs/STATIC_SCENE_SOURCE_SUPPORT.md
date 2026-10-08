# Optional prior-fact support for static scene descriptions

The physical audit can now cite an actor-visible, pre-action Place fact when an untracked static detail persists at the same Place and day/band. This supplements the existing typed audit; it does not require every new observation or historical description to occur in a fact catalog.

## Contract

- A shared bounded reader authenticates the original fact event, actor, Place, complete value, and digest. It reuses the existing visibility rules and materialization source format. The catalog contains at most 24 complete values of at most 2048 characters; absence is not evidence that a detail never existed.
- The optional `scene_state` interpretation requires a complete, non-null source reference, digest, and unique exact quotation. Invalid explicit bindings fail. Empty catalogs do not advertise this interpretation kind.
- Only the old value supplies positive evidence. The validated preview’s same-slot after value can qualify or contradict continuity, but cannot establish a candidate-only historical fact. A changed compound fact may preserve one substatement; assessing that entailment remains a model judgment.
- Endpoint source attestation does not consume a canonical narrative position. Its original `after` evidence anchor remains recorded; the action cursor stays where the last supported physical claim left it. This prevents a static description embedded in an action paragraph from prematurely consuming later transfers.
- Accepted source judgments are labelled `source_attested`, distinct from deterministic typed `supported`. Every terminal audit produces fresh source judgments, including when paragraph interpretations are reused. A cached interpretation never carries a cached truth verdict.
- Taking, receiving, transfer, tracked Objects, player custody, Person placement, passages, and resource/task effects retain their existing contracts. This source lane does not establish ownership, permission, successful inspection, or anyone's knowledge.

Ordinary new scenery, observation limits, same-turn inspection summaries, and older narration outside the catalog stay within the original authoring/audit scope. They receive neither source attestation nor a new missing-evidence rejection merely because the catalog lacks them. An extractor can still omit or misclassify a relevant claim; this is not a general semantic truth guarantee.

## Evidence and regression

The motivating real crank-removal candidate mentions wood chips remaining where an earlier visible fact established them. A first unpublished implementation (v9) overreached: the fixed glove inspection candidate acquired eight extra rejections for descriptions or limits outside the fact catalog, although its original unsupported glove handoff was the only earlier issue. Those six original model responses and the regression are preserved.

The narrowed v10 contract removes mandatory static coverage and all-null source claims. Verification uses fresh native Astra extraction of fixed recorded or explicitly constructed candidates, not a new full gameplay episode or DS run. Detailed per-case outcomes are recorded separately; source classification and continuity entailment remain probabilistic even when source identity is host-verified.

The [fixed-candidate results](reports/2026-10-08/static-scene-source-support.json) record six fresh Astra responses: original chips retained with source attestation; glove and bell unsupported handoffs still rejected; explicit after-state contradiction rejected with its prior binding retained; observation-only inspection and candidate-only new scenery passed without source attestation. No action was committed. The exact glove candidate's eight added v9 source errors disappeared in this one v10 sample. Native bridge wall time totalled about 684 seconds; this is orchestration time, not production provider latency. The optional source assessment shares the existing audit call rather than adding another call.

A later real-play integration exposed a chronology bug: a source-attested detail in the first paragraph consumed the final action checkpoint and incorrectly rejected subsequent ordered placements. The [captured correction](reports/2026-10-08/source-cursor-separation.json) replays the exact initial and terminal extractions. Source attestation retains its endpoint evidence check but consumes no physical action position. Neither the old failed action nor the saved world was rewritten; true action-order and source-veto failures remain rejected.
