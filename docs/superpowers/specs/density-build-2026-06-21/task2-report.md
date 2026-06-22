# Task 2 Report: generate_lore_batch

## Status
DONE — all tests green, full suite green.

## Files changed
- `loop/density.py` — added `GEN_THRESHOLD = 50`, `_LIFESPAN_DEFAULTS`, `_make_skeleton_id`, `generate_lore_batch`
- `tests/loop/test_density_generate.py` — 11 new tests (TDD: wrote failing tests first)

## Implementation summary
`generate_lore_batch` builds a system+user prompt containing town kind/flavor, venues list, existing_abouts to avoid, and per-spec complexity+stage_count. It calls `provider.complete_json(system, user, schema)` once with a schema that requests `{"lines":[{about,secret,description,trigger,l3_anchor,stages:[{hint}]}]}`. Engine then:
1. Parses `raw["lines"]` (also accepts bare list).
2. Per skeleton: checks required model fields present+non-empty, truncates stages to spec stage_count, coerces l3_anchor to venues[0] if not in venues, drops if 0 stages or missing about/description/trigger/l3_anchor/stages.
3. Injects engine-decided fields: id (deterministic sha256), complexity (from spec), anchor (town_id), threshold (50). Does NOT set lifespan_days (left to create_lore_line/LoreSystem defaults).
4. Returns list of valid skeletons (may be shorter than specs, or [] if all bad or error).

Fault tolerance: provider is None → []; complete_json raises → [] (logged); malformed response → []; never raises.

## Test coverage (11 cases)
- happy path: 2 specs (simple/2, complex/5) → 2 skeletons with all required keys, correct complexity/anchor/threshold/stage lengths/l3_anchor
- deterministic ids: same inputs → same ids across two calls; ids unique
- provider=None → []
- provider raises → [] (via _RaisingProvider stub)
- bad l3_anchor → coerced to venues[0]
- missing `about` (empty) → that skeleton dropped, other kept
- 0 stages → skeleton dropped
- missing `description` → skeleton dropped
- model over-produces stages → truncated to stage_count
- empty venues → no crash, l3_anchor kept as-is
- GEN_THRESHOLD == 50

## Test counts
Baseline: 1008 passed. After Task 2: 1019 passed (11 added), 1 deselected.

## Concerns
None. lifespan_days intentionally omitted from generated skeletons per spec (create_lore_line/LoreSystem assigns per-complexity defaults at projection time).

## Fix pass

Status: DONE
Commit: 1639570

Covering tests: 14 passed in 0.08s
Full suite: 1022 passed, 1 deselected in 45.97s (was 1019 passed)

- I1: deleted dead `_LIFESPAN_DEFAULTS` constant; added comment in skeleton dict explaining lifespan_days intentional omission.
- I2: pre-filter malformed specs before prompt-building (missing stage_count/complexity skipped); per-skeleton loop body wrapped in try/except Exception: continue — function never raises.
- m3: added `test_generate_lore_batch_provider_raises_runtime_error` — RuntimeError from complete_json also returns [].
- m4: added `test_generate_lore_batch_fewer_stages_than_spec` — 1 model stage for stage_count=3 spec → skeleton kept, stages length 1, not padded.
- m5: replaced one-shot collision fallback with while-loop counter; final id guaranteed unique in seen_ids before add.
- m6: strengthened `test_generate_lore_batch_empty_venues` — asserts len==2 and result[0]["l3_anchor"] equals model-returned value (no coercion with empty venues).
