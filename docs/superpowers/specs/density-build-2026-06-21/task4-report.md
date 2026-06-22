# Task 4 Report: End-to-End Offline Validation + Demo Script

> 2026-06-21. Capstone of density-generation feature.

## Status

DONE. 13 e2e tests added (all green). Demo script written. Full suite: 1054 passed.

## Dual FakeLLMProvider Wiring

The critical design choice: **separate** FakeLLMProvider instances for narrator vs cascade.

`AuthorStrategy.produce` calls `provider.complete_messages()` for the narrator commit.
`generate_lore_batch` calls `cascade_provider.complete_json()` for the skeleton batch.

Both `complete_messages` and `complete_json` on `FakeLLMProvider` consume from the same
`_json_responses` list and advance `_json_idx`. If a single provider is used for both,
the call-count of every backstage hook (digest_fleet, run_director, run_cascade,
run_catchup, run_lore, _run_demote_on_leave, run_density) must be known precisely and
pre-loaded into `json_responses` — fragile and breaks whenever a new hook is added.

**Solution:** `narrator_fake = FakeLLMProvider(json_responses=[narrator_commit, ...])`
for `provider`; `cascade_fake = FakeLLMProvider(json_responses=[batch_response, ...])`
for `cascade_provider`. The cascade fake only sees `complete_json` calls from
`generate_lore_batch`; the narrator fake only sees `complete_messages` from
`AuthorStrategy.produce`. No cross-contamination.

The test wiring:
```python
result = run_turn(..., provider=narrator_fake, cascade_provider=cascade_fake, ...)
```
`run_turn` passes `(cascade_provider or provider)` to `run_density`, so:
- Normal case: cascade_fake receives the batch call.
- Fault-tolerance test (cascade=None): narrator_fake fallback receives the batch call
  but its response (a narrator commit dict) is not a valid skeleton batch ->
  `generate_lore_batch` returns [] -> seeded with 0 lines (graceful).

## Pinned Determinism Values

SEED=20260621, town=青石镇, density=0.3, BASE=10, target=round(0.3*10)=3 slots.

Oracle rolls (via `Oracle(scene_seed(SEED, f'density:{town}:{n}', 0)).d100()`):
- slot 0: d100=14 -> simple
- slot 1: d100=78 -> medium
- slot 2: d100=85 -> medium

`EXPECTED_COMPLEXITIES = ['simple', 'medium', 'medium']`

Refresh timing (density=0.3, roll threshold < density*100 = 30.0):
- day=4: roll=30 (not < 30.0) -> density_refreshed emitted, no new line
- day=7: roll=45 -> no spawn
- day=16: roll=11 -> first spawn

The refresh test asserts only that `density_refreshed` fires and `last_refresh_day`
advances (not that a new line spawned), because the pinned seed's first spawn is at
day=16 which would require many clock advances. The "spawn" case is covered by
`TestRegionless.test_with_l1_region_higher_density_more_lines` which uses density=0.9
(target=9 lines vs 3 for 0.3), proving the density attribute is read from L1.

## Brew Detection

`run_lore` is already called inside `run_turn` backstage. With threshold=50,
`gen_青石镇_c90820b1` (simple) rolls d100=18 at next_turn=2 -> passes.
`TestBrew.test_run_lore_advances_generated_lines` calls `run_lore` explicitly after T1
and checks either `stage_idx` increased or `clues_dropped` grew. A fallback assertion
also accepts lines that already advanced inside T1's backstage hooks.

## Pipeline Gaps Found

None. Tasks 1-3 were complete and the e2e tests passed on first run, confirming
the pipeline composes correctly end-to-end:
- `run_turn` hook calls `run_density` with `(cascade_provider or provider)`
- `run_density` calls `generate_lore_batch` -> `create_lore_line` -> `lore_created`
- `LoreSystem.apply` sets `gen[town].seeded=True` / `last_refresh_day`
- `run_lore` inside `run_turn` advances generated暗 lines on subsequent turns
- `station_push_fragment` returns fragment including generated line `[id]`
- `density_refreshed` emitted after `REFRESH_INTERVAL_DAYS` elapsed

## Fix pass

DONE. Commit: see git log. Covering-test result: 14 passed in 1.88s. Full-suite result: 1055 passed, 1 deselected in 49.33s.

- F1: Replaced hollow `any_advanced or any_in_world` with pinned assertion on named line `gen_青石镇_c90820b1`; baseline captured immediately after T1 (stage_idx=-1, confirmed), explicit run_lore must advance it to stage_idx=0 and drop a clue. Would fail if run_lore skipped gen_ lines or threshold gate broke.
- F2: `_RaisingProvider` now has `complete_messages(*args,**kw)` that also raises, making it a uniformly-failing stub; removed double-instantiation (single `raising` instance shared between `eng.cascade_provider` and `run_turn` kwarg).
- F3: New test `test_refresh_spawn_creates_new_line` — seeded at day=1, T2 advances 4 days to day=5 (pinned: Oracle roll=7 < 30 → spawned=True); uses distinct `about` for refresh batch → new id; asserts `lore_created` event at day=5 exists, id not in seeded_ids, town line count increases by 1. The `if spawned:` branch in `_run_density_inner` now executes.
- F4: Folds into F1 — snapshot taken right after T1 by line id, no timing ambiguity.
- F5: Added assertion that `gen_青石镇_c90820b1` (l3_anchor=市集 == current venue) appears specifically in the 「就在此処」 L1 section of the fragment, not only the L0 section.

## Test File

`tests/loop/test_density_e2e.py` — 14 tests across 8 classes (A-H):
- A: AutoSeed (4 tests) — seeding, valid fields, pinned complexity, no double-seeding
- B: Brew (1 test) — run_lore advances generated lines
- C: Ambient (1 test) — station_push_fragment includes gen_ ids
- D: Determinism (1 test) — same seed -> same ids and complexities across two runs
- E: Regionless (2 tests) — no L1 degrades gracefully; L1 at 0.9 gives more lines
- F: FaultTolerance (2 tests) — cascade raises -> no crash; cascade=None -> no crash
- G: Refresh (1 test) — density_refreshed emitted + last_refresh_day advances
- H: FullPipeline (1 test) — 3-turn multi-step composition

## Demo Script

`docs/superpowers/specs/density-build-2026-06-21/demo.py`

World: 幽港镇 (region-less, no L1) + 4 venues (渔港码头/盐商会馆/渡口茶摊/镇守庙).
6 turns, protagonist starts outside at 官道岔口, enters on T1 (triggers seeding),
advances days across turns to trigger refresh check by T5.
Uses the real GLM provider for both narrator and cascade (one model, two roles).
Per-turn output: generated 暗 lines (id/complexity/l3_anchor/stages/state/clues),
ambient station_push_fragment, narration snippet, seeding/refresh markers.
