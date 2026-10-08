# Authoritative scene history for pacing

`kernel.scene_history.iter_scene_history` folds surviving events into active
scene visits. Director pacing, thread due scores and thread-followup checks now
share this history. Event envelopes still describe source context; a delayed
summary of an old scene does not create a new visit.

Before the first surviving `scene_advanced`, the fold uses projection's legacy
day-gated envelope fallback. After that boundary, only explicit boundaries move
the active scene. An explicit older-day boundary still changes scene, while
the maximum day remains unchanged. Retracted events contribute neither visits,
fire recency nor tension. Replaying a prefix or retracting all boundaries
restores the appropriate fallback without changing original events.

Ordinals count changes of active scene, including repeated visits, rather than
distinct labels or numeric suffixes. RNG, cadence, probability tables and
thresholds are unchanged. Corrected ordinal inputs can legitimately change a
deterministic random outcome.

Compatibility: old streams with decreasing-day envelope annotations now follow
projection instead of counting those annotations as visits. The fold expects
complete event envelopes (day, scene, summary, type). Historical thread
`last_advanced_scene` fields are retained; this change does not reconstruct an
incorrectly stored recency label from event position.

## Recorded evidence

The original ferry arrival trace contains 191 events. Raw envelope changes
previously produced ordinal 15, last-fire age 10 and current scene s4. The
authoritative fold produces ordinal 6, age 3 and current scene s6, matching
projection at every prefix. Rewind-prefix and all-boundaries-retracted replays
also match projection. This is deterministic replay of actual model gameplay,
not newly generated dialogue. All four recorded threads are dormant, so both
versions have empty active-thread scores; no observed active-thread scheduling
improvement is claimed.

A fresh native Astra continuation used the real Author/play loop, research tool,
semantic audit and callbacks: one committed action, four provider responses,
one map tool, one audit, no repair or summary. The actual director invocation
saw ordinal 6 / s6 / age 3, using the unchanged seed and turn salt. Its natural
roll .14 against .42 opened a dormant thread; the subsequent location boundary
advanced to s7. This is not a statistical pacing-quality claim. The old fixture's
missing passage edge remains: extraction did not emit a separate connectivity
claim, so successful movement auditing does not demonstrate complete coverage.
