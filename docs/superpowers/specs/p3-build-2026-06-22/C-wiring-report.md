# P3a Task 7 — Narrator Tools Wiring Report

## What was changed

### `loop/strategy.py`

Two changes:

1. Added imports at the top of the module:
   - `import os` (for `RPG_MAX_TOOL_ROUNDS` env var)
   - `from llm.tools import build_tool_registry`

2. In `AuthorStrategy.produce`, immediately after `self._messages` is assembled
   (just before the final `log.debug` / original `provider.complete_messages` call),
   inserted a DD6 capability gate:

   ```python
   if repair is None and provider.supports_tools():
       tool_reg = build_tool_registry(registry, world, scene)  # POV set (dm=False)
       schemas = tool_reg.schemas()
       if schemas:
           rounds = int(os.environ.get("RPG_MAX_TOOL_ROUNDS", "3"))
           raw = provider.complete_with_tools(
               self._messages, schemas, tool_reg.execute,
               max_tool_rounds=rounds,
           )
           self._messages.append({"role": "assistant", "content": raw})
           data = _parse_json_object(raw) or {"narration": raw}
           return TurnCommit.from_dict(data)
   # existing complete_messages path follows (unchanged)
   ```

   The gate fires ONLY when ALL three conditions hold:
   - `repair is None` — fresh turn (not a repair round)
   - `provider.supports_tools()` — provider declares loop support (True only on
     ZhipuProvider, OpenAIProvider, ScriptedToolProvider; False on FakeLLMProvider)
   - `schemas` is non-empty — the POV tool registry built at least one tool

   Repair turns always fall through to the existing `complete_messages` path.

### `tests/loop/test_strategy.py` (append)

Two new tests appended:

- `test_author_strategy_uses_tool_loop_when_supported`: uses `ScriptedToolProvider`
  scripted with one `map_query` tool call then a final commit JSON. Asserts the
  commit narration is correct AND that the tool was invoked (via `tool_invocations`).

- `test_author_strategy_falls_back_when_tools_unsupported`: uses plain
  `FakeLLMProvider` (`supports_tools()==False`). Asserts the existing
  `complete_messages` path runs unchanged.

## HybridStrategy (丙) scope

Task 7 is scoped to AuthorStrategy (甲) only. HybridStrategy (丙) wiring is
deferred to P3b Task 11 per the plan. `HybridStrategy` was NOT modified.

## Invariants verified

- **DD6 gate**: `FakeLLMProvider.supports_tools()` returns `False` → all 1180
  pre-existing tests take the old `complete_messages` path byte-for-byte unchanged.
- **Read-only**: `llm/tools.py` contains no `assert_fact`/`add_entity`/`add_relation`
  calls; all writes still go through the validated turn-commit gate in `produce_turn`.
- **Tools additive**: the full PUSH context (recap + storylines + scene + viewpoint)
  is assembled as before and passed in `self._messages`; tools are strictly additive pull.
- **Repair turns unchanged**: the `repair is None` guard ensures re-research never
  happens on repair rounds.
