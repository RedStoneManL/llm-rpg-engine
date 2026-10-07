# Absolute clock endpoints

The 16-turn stress benchmark exposed a representation error: from day 1
下午 (band 2), a request to rest until the next morning was represented as
`days=1,bands=2`, which correctly executes as day 3 morning. The engine's
arithmetic was correct; the generated duration double-counted the overnight jump.

A clock declaration can now give an absolute endpoint:

```json
{"advance":true,"target":{"day":2,"band":0},"reason":"休息到次日清晨"}
```

Bands retain the existing calendar: 晨=0, 中午=1, 下午=2, 夜晚=3. `target`
is mutually exclusive with either `days` or `bands`, even when zero. A future
target requires `advance:true`; an unchanged endpoint requires `advance:false`.
Earlier endpoints, booleans in numeric fields, and malformed targets are
repairable validation errors. Reason remains required.

The host converts the target against the pre-turn canonical clock. Day 1
下午 → day 2 晨 produces `days=0,bands=2`. Item/return preview, event stamps,
and actual application use the same normalization. Original proposals are not
mutated. Persisted `clock_advanced` events keep their existing delta format;
legacy duration declarations and historical replay remain supported.

An already-resolved `wait_until` is validated by the time path independently of
registered resources. Resource intent's current day now comes from canonical
world state rather than a possibly stale scene view. The existing resource
classifier may still supply a wait endpoint; resource-free turns gain no extra
classifier call. Clear endpoints can instead be authored directly in `clock`.

## Evidence and limits

Offline tests cover the observed overnight arithmetic case with and without
resources, mutually exclusive forms, malformed/past endpoints, direct apply,
item history, exact return-fulfillment time, reopen/replay, and rewind. Prompt contracts expose
the absolute representation in both authoring strategies.

This is an endpoint representation and validation improvement, not proof that a
model correctly interprets every natural-language wait, conditional statement,
or future promise. A model can still choose a wrong but well-formed endpoint,
use an incorrect legacy duration, or write inconsistent prose. No live
DeepSeek after-test was run for this increment because the environment's
provider network path remains blocked by proxy HTTP 403.
