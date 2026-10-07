# Typed identity evidence for scene summaries

An actual native-model scene summary called the protagonist“小满”, although
小满 was the bell's name. The summarizer saw second-person prose and the named
bell but received no bound protagonist identity. Its raw scene text did not
supply the protagonist's name林舟.

The existing scene-summary and recompression calls now receive a bounded
identity packet alongside unchanged source prose/summaries. This adds no model
call. Each passage is matched to an exact, unique narration event, then to one
later host player-input source with the same positive turn. Its original actor
is used; the current actor is never substituted. Missing, ambiguous, malformed,
or legacy-unavailable provenance remains unknown.

The host replays the event prefix through that source before applying historical
POV visibility. Filtering today's graph by an old day is insufficient because
same-day renames and entity attributes can change. The packet contains typed
IDs and eligible names, not player inputs, arbitrary facts, private attributes,
knowledge rows, or hidden destinations.

Name eligibility is checked against actual historical canonical public values
or a published name-boundary match. A POV belief inheriting a public secrecy
flag does not make that belief public. Non-actor entities must themselves have
a published safe-name/ID mention; private input references alone do not expose
them. Names are never clipped into invented aliases. Actor binding and typed
names are attribution hints, not evidence of new actions or NPC knowledge.

Per-bucket/per-passage grouping and original-text offsets preserve attribution.
Recompression covers both the prior overview's source buckets and the new ones,
with explicit bucket markers even when scene IDs repeat. Source metadata keeps
only needed temporal provenance, not location. The serialized packet is capped
at 12,000 characters; passage/entity/name omissions and unknown bindings remain
explicit. The packet cannot guarantee complete identity coverage for every
large or legacy recap.

## Actual native verification

The [recorded evidence](reports/2026-10-07/native-summary-identity-after.json)
retains the wrong original output and the final fresh outputs. The first final
call used the exact original raw scene, augmented by the reviewed identity
packet. It summarized “林舟…收好铜铃‘小满’”. The second called the actual production
recompression function with that result and the recorded next-scene summary;
it again kept林舟 as the actor and小满 as the bell while preserving pending
bridge/route uncertainty.

The two implementation-file hashes were frozen throughout these final two
completions. Four earlier completed native outputs are retained separately as
pre-final iterations; they were not relabeled as final results. A queued
pre-review request produced no response and is not counted as a completion.

This was a focused native-model check of the two production functions, not a
new gameplay run or a claim that the normal recap fanout threshold fired. No
DeepSeek calls or offline test campaign were run. Independent code inspection
covered the privacy/provenance fixes; one successful native sample per function
does not establish universal factual or summary fidelity. Cast-schema repair
friction and broader prose/effect reconciliation remain separate work.
