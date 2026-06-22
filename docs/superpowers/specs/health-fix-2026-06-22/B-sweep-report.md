# B-sweep dead-code removal report — 2026-06-22

Base commit: `0c7ae44`

---

## B1 — Mode A disclosure infrastructure

### B1.1 `loop/lore_disclosure.py::index_fragment`

**Verified dead:** grep shows `index_fragment` only in `loop/lore_disclosure.py` (definition) and
`tests/loop/test_lore_disclosure_A.py` (tests). Zero production callers in `loop/`, `app/`,
`systems/`, `kernel/`, `context/`, `engine/`.

**Action:** Deleted lines 126–182 (the function body + A-mode section comment header). Kept
`station_push_fragment` and `_l2_ancestor` intact.

**LOC removed:** 58 lines (lore_disclosure.py)

**Tests removed:** `tests/loop/test_lore_disclosure_A.py` — 380 lines.

---

### B1.2 `llm/lore_tools.py`

**Verified dead:** Imported only by `tests/llm/test_lore_tools.py` and one comment in
`test_lore_disclosure_A.py`. Zero production imports.

**Action:** File deleted (61 lines).

**Tests removed:** `tests/llm/test_lore_tools.py` — 251 lines.

---

### B1.3 `llm/tools.py`

**Verified dead:** Imported only by `llm/lore_tools.py` (deleted above) and
`tests/llm/test_lore_tools.py` (deleted above). Zero production imports.

**Action:** File deleted (73 lines).

---

### B1.4 `llm/provider.py` — dead tool-loop infra

**Verified dead (grep repo-wide):**
- `_run_tool_loop`: defined in provider.py; called only by `OpenAIProvider.complete_with_tools`
  and `ZhipuProvider.complete_with_tools` (both deleted here) and tests.
- `ScriptedToolProvider`: defined in provider.py; imported/used only in `tests/llm/test_tool_loop.py`.
- `supports_tools` / `complete_with_tools` overrides on OpenAI/ZhipuProvider: called only by tests.

**Action:**
- Deleted `_run_tool_loop` (~lines 197–234, 38 lines).
- Deleted `ScriptedToolProvider` (~lines 241–272, 32 lines).
- Deleted `OpenAIProvider.supports_tools` + `complete_with_tools` (~lines 433–452, 20 lines).
- Deleted `ZhipuProvider.supports_tools` + `complete_with_tools` (~lines 510–529, 20 lines).

**Base `LLMProvider.supports_tools` / `complete_with_tools` (lines 116–125):** After removing all
four concrete overrides and all callers, grep found ZERO non-test, non-definition occurrences.
Decision per spec: **REMOVED** both base methods (10 lines). They were never overridden or called
in production after the above deletions.

**Also removed:** unused `_MIN_THREADS` constant in `loop/director.py` (1 line, left dangling after
`seed_threads` deletion).

**Kept intact:** `_openai_chat_body`, `_openai_parse`, `_openai_append_result` (serve
`complete_messages`); `complete`, `complete_messages`, `complete_json`, `FakeLLMProvider`,
`make_provider`, `AnthropicProvider`, `_do_post`, `_parse_json_object`, `_record_usage`.

**Total LOC removed from provider.py:** ~120 lines.

**Tests removed from `tests/llm/test_tool_loop.py`:** 185 lines (entire file deleted).

**Tests removed from `tests/llm/test_provider.py`:** Removed only:
- Section comment `# Task 6 (P3a): ...`
- `test_supports_tools_true_on_zhipu`
- `test_supports_tools_true_on_openai`
- `test_zhipu_complete_with_tools_offline`
- `test_openai_append_result_echoes_real_arguments` (invoked `complete_with_tools` via
  `ZhipuProvider`; removed because the method no longer exists; the lock it tested is now
  moot since the tool loop is deleted)
- `test_openai_complete_with_tools_offline`

Kept: all `FakeLLMProvider`, `TestMakeProvider`, `TestOpenAIProviderRequestBuilding`,
`TestZhipuProviderRequestBuilding`, `TestAnthropicProviderRequestBuilding`,
`test_do_post_retries_on_read_timeout`, `test_openai_chat_body_carries_tools`,
`test_openai_chat_body_no_tools_key_when_none` — 120 lines removed.

---

## B2 — Retired StorySystem

### B2.1 `systems/story.py`

**Verified dead:** `StorySystem` / `systems.story` / `from systems.story` — grep shows only the
definition file itself. Not imported in `app/engine.py` (build_engine), or anywhere in `loop/`,
`systems/`, `kernel/`, `context/`, `app/`.

**Action:** File deleted (235 lines).

### B2.2 `tests/systems/test_story_system.py`

**Finding:** File header reads
`"Tests for LoreSystem quests section — replaces retired StorySystem tests (T3)."` The file
imports `LoreSystem` and `OntologySystem` — NOT `StorySystem`. All test functions test `LoreSystem`.

**Action:** **RENAMED** to `tests/systems/test_lore_active.py` (kept content intact). No StorySystem
reference existed; renaming removes the lie in the filename.

---

## B3 — Dead director helper

### B3.1 `loop/director.py::seed_threads`

**Verified dead:** grep shows `seed_threads` only in `loop/director.py` (definition) and
`tests/loop/test_director_loop.py` (tests, lines 164–188). Not called by `run_director`,
`_handle_dormant`, or any production path.

**Action:** Deleted lines 43–79 (the function, 37 lines) + `_MIN_THREADS` constant (now unused, 1 line).

**Tests removed from `tests/loop/test_director_loop.py`:** Removed:
- `from loop.director import seed_threads` import
- `from engine.oracle import Oracle, load_table` import (only used by seed_threads tests)
- `_seed_tables()` helper
- `test_seed_threads_distinct_traits_and_archetypes`
- `test_seed_threads_deterministic`
- Orphaned `from kernel.contextsystem import ContextSystem  # noqa: F401` (clarity import only,
  no remaining usage)

Total: ~31 lines removed from test_director_loop.py. All other director tests kept.

---

## Summary

| Target | Status | LOC removed |
|--------|--------|-------------|
| B1.1 index_fragment | DELETED | 58 |
| B1.2 llm/lore_tools.py | DELETED | 61 |
| B1.3 llm/tools.py | DELETED | 73 |
| B1.4 provider.py tool-loop infra | DELETED | ~120 |
| B2.1 systems/story.py | DELETED | 235 |
| B2.2 test_story_system.py | RENAMED → test_lore_active.py | 0 (kept) |
| B3.1 seed_threads | DELETED | 38 |
| Dead tests (5 files / sections) | DELETED | ~967 |
| **Total** | | **~1812 lines** |

Base provider tool methods decision: **REMOVED** (no live overrides or callers remained).
test_story_system.py finding: was already repurposed to test LoreSystem — **RENAMED** to test_lore_active.py.

## Test result

```
1151 passed, 1 deselected in 52.65s   (0 failures, 0 errors)
```

Down from 1197 (pre-sweep) — reduction of 46 tests, matching the 5 deleted test files/sections.
