# Structured-LLM Refactor Report — 2026-06-21

Converts 5 single-shot `complete_json` call sites to `complete_structured` (validate→name-errors→repair loop, max_repairs=1 at all sites). Base commit: `50c93b0`.

---

## Site 1 — `loop/cascade.py` `_node_verdict`

**What changed:**
- Added `from llm.structured import complete_structured` import.
- Added module-level `_node_validate(obj)` validator: checks `evolve` is a boolean; when `evolve=true`, requires non-empty `state` and/or `populace_mood` (Chinese).
- Replaced `provider.complete_json(_NODE_SYSTEM, user, _NODE_SCHEMA)` with `complete_structured(provider, system=_NODE_SYSTEM, user=user, validate=_node_validate, max_repairs=1, schema_reminder=..., log_label="cascade")`.
- On `errors` or non-dict `obj`, falls back to `{}` — same behavior as the old `isinstance(result, dict)` guard. Harness still injects `id` unconditionally.

**Tests updated:**
- `KeyedFakeProvider` gained `complete_messages` method (keyed on place_id in last user message, returns JSON string).
- `NoIdProvider`, `BadProv`, `RaisingProvider` (inner classes in 3 test functions) each gained `complete_messages` override matching their `complete_json` behavior.
- **New test:** `test_node_verdict_repair_loop_uses_repaired_result` — feeds `(bad={note:oops}, good={evolve:true, state:修复后状态})`, asserts verdict uses repaired result, 2 LLM calls, `id` injected.

**Fallback nuance:** `complete_messages` raises inside `ThreadPoolExecutor` workers → `fut.result()` re-raises → caught by the `except Exception as exc` in `_vertical_bfs` → node dropped + logged. Same behavior as before (complete_json raising was the old path).

---

## Site 2 — `loop/fleet.py` `summarize_scene`

**What changed:**
- Added `from llm.structured import complete_structured` import.
- Added module-level `_summary_validate(obj)` validator: requires `summary` to be a non-empty string.
- Replaced `provider.complete_json(_SUMMARIZE_SYSTEM, user, _SUMMARY_SCHEMA)` with `complete_structured(...)` using `_summary_validate`, `max_repairs=1`, `log_label="summarize"`.
- On `errors` or empty summary, logs warning and returns `None` — identical fallback as before.

**Tests updated:**
- No existing tests changed (existing `FakeLLMProvider(json_responses=[...])` still works because `FakeLLMProvider.complete_messages` cycles `json_responses` and returns conforming JSON → 1 call, no repair needed).
- `test_digest_summarizes_only_when_scene_ages_out` asserts `len(cheap.calls) == 1` — still holds.
- **New test:** `test_summarize_scene_repair_loop_uses_repaired_result` — feeds `(bad={note:oops}, good={summary:这是摘要})`, asserts result event has correct summary, 2 calls, repair message names `"summary"`.

---

## Site 3 — `loop/fleet.py` recap recompress (~line 316 in `digest_fleet`)

**What changed:**
- Reuses same `_summary_validate` and `complete_structured` import from Site 2.
- Replaced `recap_provider.complete_json(_RECOMPRESS_SYSTEM, user_rc, _SUMMARY_SCHEMA)` with `complete_structured(recap_provider, system=_RECOMPRESS_SYSTEM, user=user_rc, validate=_summary_validate, max_repairs=1, schema_reminder=..., log_label="recap")`.
- Guard changed from `if rc_summary:` to `if rc_summary and not rc_errors:` — conformance required before emitting `recap_recompressed`.

**Tests updated:**
- No existing tests changed.
- **New test:** `test_recompress_repair_loop_uses_repaired_result` — seeds RECAP_SUMMARY_FANOUT summarized scenes + extra narration to push aged-out→summarize→recompress. Feeds `(summarize_resp conforming, bad_rc={note:wrong}, good_rc={summary:总概要修复后})`. Asserts `recap_recompressed` event has correct `super_summary`, 3 total calls on recap_provider.

---

## Site 4 — `loop/lore.py` `jit_resequence`

**What changed:**
- Added `from llm.structured import complete_structured` import.
- Added module-level `_jit_validate(obj)` validator: checks response is a dict, `stages` is a non-empty list, each stage has a non-empty `hint` string. Reports exact stage number on per-stage failure.
- Replaced `provider.complete_json(_JIT_SYSTEM, user, schema)` (and the inline `schema` dict) with `complete_structured(provider, system=_JIT_SYSTEM, user=user, validate=_jit_validate, max_repairs=1, schema_reminder=..., log_label="jit")`.
- On `errors` or non-dict `obj`, falls back to `remaining` (original remaining stages) — same as before.

**Tests added (3 new):**
- `test_jit_resequence_conforming_response_returns_stages` — 1 call, new stages returned.
- `test_jit_resequence_repair_loop_uses_repaired_result` — `(bad={note:wrong}, good={stages:[{hint:修复阶段}]})`, 2 calls, repair message names `"stages"`.
- `test_jit_resequence_fallback_when_all_attempts_fail` — always-bad response cycles, falls back to original remaining stages.

**No existing lore_loop tests touched** (`run_lore` and `create_lore_line` tests don't go through `complete_json` at all).

---

## Site 5 — `loop/time.py` catch-up loop (`run_catchup`)

**What changed:**
- Added `from llm.structured import complete_structured` import.
- Added `_catchup_validate(kind)` factory (kind-parameterized): checks `changed` is a boolean; when `changed=true`, for Person requires non-empty `predicate` + `value`, for Place requires non-empty `state`. Returns a closure.
- Replaced `cp.complete_json(_CATCHUP_SYSTEM, _catchup_prompt(...), _CATCHUP_SCHEMA)` + `if not isinstance(raw, dict)` block with `complete_structured(cp, ...)` + `raw = obj if (isinstance(obj, dict) and not errors) else {}`.
- Harness still injects `id` unconditionally; existing `if raw.get("changed") and lightweight_validate(...)` logic unchanged.

**Tests updated:**
- `KeyedCatchup` gained `complete_messages` method: extracts last user message from messages list, records in `self.calls` as text string (matching existing call count assertions), returns JSON-serialized keyed response.
- `test_run_catchup_budget_caps_calls` still asserts `len(prov.calls) == CATCHUP_BUDGET` — conforming responses mean 1 call each, still holds.
- `test_run_turn_with_prev_scene_fires_catchup_for_entering_stale_npc` and `test_play_loop_tracks_prev_scene_...` continue to work: keyed conforming responses need no repair.
- **New test:** `test_run_catchup_repair_loop_uses_repaired_result` — feeds `(bad={note:forgot changed}, good={changed:true, predicate:mood, value:坚韧})`, asserts `character_evolved` with correct `value`, 2 calls, repair message names `"changed"`.

---

## Test Summary

| File | Before | After | New repair tests |
|------|--------|-------|-----------------|
| tests/loop/test_cascade_loop.py | 34 | 35 | 1 |
| tests/loop/test_fleet.py | 10 | 12 | 2 |
| tests/loop/test_lore_loop.py | 9 | 12 | 3 |
| tests/loop/test_time_loop.py | 16 | 17 | 1 |
| tests/llm/ | 9 | 9 | 0 (already existed) |
| **Full suite** | ~1060 | **1081 passed** | **7 new** |

---

## Concerns / Notes

1. `KeyedFakeProvider` and `KeyedCatchup` both still implement `complete_json` — existing tests that call those methods directly (e.g., the `RaisingProvider` path in cascade which tests thread executor exception handling) continue to work because the exception is raised from `complete_messages`, not `complete_json`.

2. The recompress test (Site 3) required seeding `RECAP_SUMMARY_FANOUT` already-summarized scenes, not `FANOUT-1`, because the `aged_out_scene` gate fires only when the oldest bucket has no raw texts and is the `(len(buckets) - RAW_SCENES)`-th scene. This is correct behavior; the test seeds exactly enough to trigger the aged-out→summarize→recompress chain.

3. All sites use `max_repairs=1` (cost-bounded: per-child/entity calls, one cheap attempt). The primitive supports up to `max_repairs+1 = 2` total LLM calls per invocation.

4. No changes to `_legacy/`, `llm/structured.py`, `llm/provider.py`, or any system/projection files.

---

## Fix pass

**Base commit:** `10f7d76`

| Finding | Status | Note |
|---------|--------|------|
| B1 `loop/lore.py` `_jit_validate` | FIXED | Accumulates all per-stage errors into a list instead of early-returning on the first bad stage; mirrors `density._validate_gen_lines` pattern. |
| B2 `loop/time.py` blank populace_mood | FIXED | Guards emission with `isinstance(pm, str) and pm.strip()`; uses stripped value; field stays optional in validator. |
| B3 `tests/loop/test_fleet.py` store leak | FIXED | Wrapped `test_recompress_repair_loop_uses_repaired_result` body in try/finally calling `store.close()`, matching all sibling tests. |
| Q1 `tests/loop/test_cascade_loop.py` assert repair names field | FIXED | Added `assert '"evolve"' in fake.calls[1][1]`; removed dead `bad`/`good` string locals. |
| Q2 `loop/cascade.py` place-level failure log | FIXED | Added `log.warning("cascade: node %s did not conform: …", place_id, …)` after `complete_structured` when `errors` is non-empty. |
| Q3 `llm/structured.py` dead list branch | FIXED | Changed `isinstance(obj, (dict, list))` to `isinstance(obj, dict)`; error text already says "object". |

**New tests added:**
- `tests/loop/test_lore_loop.py::test_jit_validate_collects_all_stage_errors` — B1 proof: two bad stages in one response → repair message names stage 1 AND stage 3; repaired result used.
- `tests/loop/test_time_loop.py::test_run_catchup_blank_populace_mood_not_emitted` — B2 proof: Place catchup with `populace_mood="   "` emits `place_evolved` but NOT `populace_shifted`.

**Covering tests:** 166 passed in 2.79s
**Full suite:** 1083 passed, 1 deselected in 46.44s (was 1081 — 2 new tests)
