# P3 — 工具化 / Narrator Tools Implementation Plan

> **STATUS: DRAFT FOR HUMAN REVIEW.** This plan resolves every open question to a *recommended default* so it reads as buildable, but the load-bearing choices are collected in **"DECISIONS FOR HUMAN"** below and flagged inline as **[DECISION-n]**. Do **not** start implementing until the human has signed off on those. Once signed off, an agentic worker implements it task-by-task.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Every task is TDD: write the REAL failing test first, run it, see it FAIL, write the minimal REAL implementation, run it, see it PASS, full-suite gate, then commit exactly the files the task names. No placeholders, no stubbed-out bodies, no "fill this in later". If a step's behavior is unclear, re-read the cited source file before writing code.

**Goal:** Give the narrator model a **PULL** side (spec §1): read-only tools it can *query on demand* — map / characters / factions / recall — wrapped over the existing `FactGraph` + systems, with **fog-of-war** so a tool answer never leaks what the protagonist (or the queried NPC) does not know. Delivered through a provider **tool-use loop** (spec §2.5, OpenAI/GLM function-calling): the model researches by calling tools, the loop executes them and feeds results back, and the model finally emits its turn-commit (**research-then-write**). The keystone invariant (spec §1, §5) is preserved: **tools are READ-ONLY; every write still goes through the validated turn-commit gate.** The whole loop must stay **offline-deterministic-testable** with no network — this is the #1 flagged risk and Task 1–4 exist to nail it before any real tool is wired.

**Architecture:** Three new seams, no kernel change, no new system:

1. **`llm/tools.py` (new) — the tool surface.** A `Tool` dataclass (`name`, `description`, `parameters` JSON-schema, `fn`) and four read-only tool factories that close over `(registry, world, scene)` and return JSON-serializable dicts: `map_query`, `characters_query`, `factions_query`, `recall_query`. Each wraps existing pure helpers (`place.navigate` / `graph.neighbors` / `faction.members_of` / `kernel.recall.recall` + `memory.recall.rank`) — **no new data, no new state**. Fog-of-war is applied here, by routing through `systems.knowledge.knows` + `context.viewpoint.build_viewpoint` so a tool only returns facts the POV agent knows (POV tools) or returns ground truth explicitly tagged `protagonist_knows:false` (DM tools). A `ToolRegistry`/`build_tool_registry(...)` assembles the active set and exposes `schemas()` (the `tools` array sent to the model) + `execute(name, args)` (the dispatcher the loop calls).

2. **`llm/provider.py` — the tool loop.** A new provider method `complete_with_tools(messages, tools, tool_executor, *, model=None, max_tokens=None, max_tool_rounds=N)` and a shared free function `_run_tool_loop(...)` so all three real adapters reuse one loop. It sends `tools` in the OpenAI-style body (a new `_openai_chat_body` kwarg + an Anthropic `tools` shape), inspects the response for `tool_calls`, dispatches each via `tool_executor(name, json_args) -> json_str`, appends `{"role":"tool",...}` (OpenAI) / `tool_result` (Anthropic) messages, and re-posts — looping until the model returns a normal (no-tool-call) message or the `max_tool_rounds` cap is hit (`log.warning` on cap, then a final forced no-tools call). `FakeLLMProvider` gets a SCRIPTED extension (see **[DECISION-2]** / Task 1) so a test can assert "model called `map_query('city')` then emitted commit X" with zero network.

3. **`loop/strategy.py` — wiring into 甲 (and later 丙).** `AuthorStrategy.produce` builds the tool registry from `(registry, world, scene)`, and — when the provider supports tools — calls `complete_with_tools` instead of `complete_messages` on the FRESH turn (research-then-write happens once, before the structured commit; repair rounds stay plain `complete_messages`, no re-research). The assembled PUSH context (recap + storylines + scene + viewpoint) still goes in verbatim; tools are additive PULL.

**Tech Stack:** Python 3.12 stdlib only (no new deps; no `openai`/`anthropic` SDK — `urllib` like the rest of `llm/provider.py`). Reuses S0 kernel (`Registry`), S1 `facts/FactGraph` + `systems/place.py` (`navigate`/containment/adjacency), `systems/character.py`, `systems/faction.py` (`members_of`/`member_rank`), `systems/knowledge.py` (`knows`/`knowers_of`), `context/viewpoint.py` (`build_viewpoint` — POV/guardrail/NPC), `kernel/recall.py` + `memory/recall.py` (existing recall + ranking). Tracing via the existing `get_tracer()` chokepoint in `_do_post`. Logging: `from engine.log import get_logger`. Test binary: `python3`. **Every test offline + deterministic** via `FakeLLMProvider` / the new `ScriptedToolProvider` (NO network, NO live HTTP — adapters tested through `_build_*` request-builders and `_run_tool_loop` driven by a fake `_post` hook).

---

## ⚠️ DECISIONS FOR HUMAN (resolve before building)

These are the load-bearing forks. Each lists the **recommended default** (what the plan is written against) and the alternative. Changing one of these changes the task list, so confirm them first.

### [DECISION-1] Fog of war: two physical entries (POV-tools vs DM-tools) — RECOMMENDED — vs single tool + annotation
- **Spec default & plan default (RECOMMEND):** two physical tool entries. POV tools (`map_query`, `characters_query`, `factions_query`, `recall_query`) return ONLY what the POV agent (protagonist by default, or a named present NPC) knows — sourced through `knows`/`build_viewpoint`. A separate DM tool set (`dm_world_query`, behind a flag) returns ground truth, every unknown fact tagged `protagonist_knows:false`, and is offered to the model ONLY on authoring/structure paths (never on the narration-context path). The fog is enforced by *which tools exist in the schema*, not by trusting the prompt.
- **Alternative (simpler):** a single `query(...)` tool per domain that always returns ground truth but annotates each row with a `knowledge_boundary` flag (`protagonist_knows: true|false`), and we trust the guardrail prompt (the existing `⚠️只约束·勿泄露` machinery) to keep the model from narrating unknown facts.
- **Why I recommend the spec default:** the guardrail prompt already exists for PUSH context and real reasoning models *do* respect it most of the time — but "most of the time" is exactly the leak the spec is trying to design out at the source. Source-side enforcement (the model literally cannot retrieve the secret through a POV tool) is strictly stronger and matches the project philosophy ("autonomy within deterministic guardrails" — make the guardrail structural, not advisory). Cost: two schema entries + a `dm=True` switch on the executor.
- **Trade-off to weigh:** the annotation approach is ~1 fewer task and lets the DM path and narration path share one tool. If you mostly run 甲 (single call that both narrates AND authors structure), the POV/DM split is awkward because *the same call* needs both lenses — see **[DECISION-4]**. **This is the single most important decision in P3.**

### [DECISION-2] Tool-loop offline testability: `ScriptedToolProvider` (RECOMMEND) — make-or-break
- **The risk (human's #1):** if `complete_with_tools` can only be exercised against a live GLM endpoint, we lose offline determinism (命脉). 
- **My concrete answer: YES, it can be made cleanly offline-deterministic, and here is how.** Split the loop into a pure orchestrator `_run_tool_loop(messages, tools, tool_executor, *, post, parse, max_tool_rounds)` where `post(messages, tools) -> dict` is the ONLY I/O seam and `parse(resp) -> (text, tool_calls)` normalizes the provider response shape. Real adapters pass a `post` that calls `_do_post`; tests pass a fake `post`. On top of that, `ScriptedToolProvider(FakeLLMProvider)` implements `complete_with_tools` by replaying a caller-supplied **script**: a list of "assistant turns", each either `{"tool_calls": [{"name","arguments"}...]}` (the loop will execute them against the real `tool_executor` and continue) or `{"content": "...final commit json..."}` (the loop returns it). It records every `(name, arguments)` it was asked to emit AND every tool result the executor returned, in `self.tool_invocations`, so a test asserts the exact call sequence + the commit. **Full interface + example test are in Task 1 — read it; if after Task 1 the fake is NOT clean, STOP and descope to [DECISION-2-fallback].**
- **[DECISION-2-fallback] (descope):** if the scripted fake proves fragile, do NOT ship the loop into the live strategy. Instead ship tools as a NON-loop "context expander": pre-call a fixed set of tools once based on the player input, inject their JSON into the PUSH context, and keep the single `complete_messages` call. This keeps determinism trivially (no loop) at the cost of losing model-driven, iterative research. The plan flags exactly where to cut (Task 7 becomes "inject tool output as context" instead of "wire the loop").

### [DECISION-3] `max_tool_rounds` cap — RECOMMEND 3
- **Default:** `max_tool_rounds = 3` (a round = one model turn that emits tool_calls + our execution of them). After 3 rounds the loop forces ONE final call with `tools` omitted (so the model MUST produce a commit) and `log.warning`s the cap hit. Rationale: research-then-write rarely needs >2 query rounds for one turn; 3 gives headroom while bounding cost/latency (each round = a full reasoning-model call). Make it a method kwarg AND an env override `RPG_MAX_TOOL_ROUNDS` (mirrors `RPG_CASCADE_CONCURRENCY`).
- **Alternatives:** 2 (tighter, cheaper, risks truncating legitimate multi-step lookups) or 5 (looser, for complex investigative turns). The number is cheap to change; confirm the default.

### [DECISION-4] Which tools are POV vs DM — RECOMMEND: all four read tools are POV; DM tools are authoring-only and OFF by default in 甲
- **Default:** `map_query` / `characters_query` / `factions_query` / `recall_query` are **POV** (fog-applied). The DM ground-truth set (`dm_world_query`) is gated behind `dm=True` and offered ONLY where the call's *sole* job is structure/authoring with no narration risk — concretely **丙's structure call** (call 2, which authors the commit for already-frozen prose) and the cascade/digest fleets (which never narrate to the player). In **甲** (one call that narrates AND authors), DM tools are OFF — a single call cannot safely hold both lenses, so 甲 sees only POV tools. 
- **Tension with [DECISION-1]:** this is why the POV/DM split is cleanest under 丙 (prose call = POV tools or none; structure call = DM tools OK) and awkward under 甲. **If the human picks 甲 as the primary strategy, reconsider [DECISION-1]** — under pure 甲, a single annotated tool (the [DECISION-1] alternative) may serve better. P3a wires POV tools into 甲 only (safe under either decision); P3b adds the DM set + 丙 wiring.
- **Alternative:** dual-mode tools (one tool, `pov: bool` arg) — rejected: lets the model flip its own fog off, which defeats source-side enforcement.

### [DECISION-5] Do tools REDUCE the pushed context? — RECOMMEND: NO for P3, revisit in a P4
- **Default:** keep the full PUSH set (recap + storylines + scene + viewpoint) exactly as P1/P2 built it. Tools are PURELY ADDITIVE pull. The spec (§2.5 line 65) muses "P3 一上…甲/丙 可能合一" and §1 frames tools as the trim lever, but trimming PUSH is a *separate, riskier* change (it touches the continuity命脉) and should be its own phase with its own real-model A/B. P3 proves the loop + tools work; it does not also re-balance the context budget.
- **Alternative:** once tools exist, drop the volatile `recall` block from `assemble_context` (it becomes the `recall_query` tool) and/or shrink the viewpoint NPC bundles (the model can `characters_query` an NPC on demand). **Flag:** doing this in P3 risks regressing continuity for an unproven gain; recommend deferring.

---

## Global Constraints

- Python 3.12; branch `app`; **stdlib only — no new dependencies** (no `openai`/`anthropic`/`zhipu` SDK; HTTP via `urllib` exactly as `llm/provider.py` already does).
- Test binary is `python3` (e.g. `python3 -m pytest`).
- Logging: every module uses `from engine.log import get_logger` then `log = get_logger("<dotted.name>")`.
- **Tests mirror source:** `llm/tools.py` → `tests/llm/test_tools.py`; new provider methods → `tests/llm/test_provider.py` (append) and a new `tests/llm/test_tool_loop.py`; `loop/strategy.py` → `tests/loop/test_strategy.py` (append).
- **OFFLINE DETERMINISM IS命脉:** every test runs with NO network and NO live HTTP. Adapters are tested through their `_build_*` request-builders (assert the body carries `tools`) and through `_run_tool_loop` driven by a fake `post`. The end-to-end strategy test uses `ScriptedToolProvider`. If any test needs a live endpoint, it is in the wrong place — fix the seam, don't skip the test.
- **HARD git guardrails:** NO `git init`, NO `git reset`, NO `git checkout`/branch-switch, NO new branches — you are already on `app`. Do NOT edit `engine/`, `_legacy/`, `docs/` (EXCEPT this one plan file), or `data/`. Only commit the exact files each task names.
- The existing **791-test suite** (`python3 -m pytest -q --ignore=tests/test_embed_real.py`) plus all legacy tests MUST stay green at every commit. This plan ADDS tests and is purely additive to source — it must not require modifying any pre-existing test (if a pre-existing test breaks, you changed behavior you shouldn't have; stop and re-read).
- Full-suite gate after each implementation task: `python3 -m pytest -q --ignore=tests/test_embed_real.py`.
- Commit message trailer (every commit): `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Phasing (ship P3a first; P3b is fully gated behind P3a + human review)

- **P3a — loop + fake + minimal POV surface (Tasks 1–7).** The provider tool loop (`_run_tool_loop` + `complete_with_tools` on `ZhipuProvider`/`OpenAIProvider`), the `ScriptedToolProvider` fake (the make-or-break), the `Tool`/`ToolRegistry` scaffold, **two** POV read tools (`map_query` + `recall_query`) with fog applied, and the wiring into `AuthorStrategy` (甲) behind a capability check. **Shippable + fully offline-tested + demonstrable on a real model.** This alone validates [DECISION-2] (determinism) end-to-end.
- **P3b — full surface + DM entry + 丙 + (deferred) context trim (Tasks 8–12).** `characters_query` + `factions_query` POV tools, the DM ground-truth set (`dm_world_query`, [DECISION-1]/[DECISION-4]), `AnthropicProvider.complete_with_tools`, wiring into `HybridStrategy` (丙) structure call, and the env/CLI plumbing (`RPG_MAX_TOOL_ROUNDS`, enabling tools in `app/play.py`). The PUSH-context trim ([DECISION-5]) is explicitly OUT — note it as a future P4.

The split keeps P3a a clean, reviewable, real-model-demoable unit; P3b only starts after the human has seen P3a run and re-confirmed [DECISION-1]/[DECISION-4] (which the P3a real-model run will inform).

---

## Design decisions (load-bearing — referenced by tasks)

### DD1 — One orchestrator, one I/O seam (`_run_tool_loop`)
The loop logic lives ONCE as a free function so all adapters and the fake share it and so the I/O is a single injectable seam:

```python
def _run_tool_loop(messages, tools, tool_executor, *, post, parse,
                   max_tool_rounds, max_total_calls=None):
    """Drive a research-then-write tool loop.

    messages:      mutable conversation (role/content[/tool_calls/tool_call_id]).
    tools:         the provider-shaped `tools` schema array (or None on the
                   final forced call).
    tool_executor: callable(name:str, arguments:dict) -> str (JSON string).
    post(messages, tools) -> dict:  the ONLY network call (real: _do_post;
                   test: a fake returning scripted dicts).
    parse(resp) -> (text:str|None, tool_calls:list[dict]):  normalize the
                   provider response into final-text-or-tool-calls. A tool_call
                   dict is {"id","name","arguments"} (arguments already a dict).
    max_tool_rounds: cap on tool-emitting rounds; on cap, ONE final post with
                   tools=None forces a textual answer. log.warning on cap.

    Returns the final assistant text.
    """
```

Rationale: the make-or-break (DECISION-2) reduces to "can we fake `post`?" — and we can, trivially, because `post` is a plain function. The orchestrator has no `urllib` import; the adapters supply `post`. This also means `_run_tool_loop` itself is unit-tested directly (Task 2) with a hand-written `post` — no provider, no network.

### DD2 — Tool-call normalization (OpenAI/GLM vs Anthropic)
`parse` hides the shape difference. OpenAI/GLM: `resp["choices"][0]["message"]` has optional `tool_calls: [{"id","type":"function","function":{"name","arguments":<json-string>}}]` and `content`. We map to `{"id", "name", "arguments": json.loads(function.arguments or "{}")}`; `finish_reason == "tool_calls"` is the signal. Anthropic (P3b): `resp["content"]` is a block list; `tool_use` blocks carry `{"id","name","input":<dict>}` and `stop_reason == "tool_use"`. Tool RESULTS are appended provider-shaped: OpenAI uses `{"role":"tool","tool_call_id":id,"content":json_str}` after an assistant message echoing the `tool_calls`; Anthropic uses a `user` message with a `tool_result` content block. **Decision:** keep the adapter responsible for building the result-message in the right shape via a small `_append_tool_result(messages, call, result_str)` adapter hook, while `_run_tool_loop` stays shape-agnostic by receiving that hook too. (P3a only needs the OpenAI/GLM shape; the Anthropic hook lands in P3b.)

### DD3 — `tool_executor` is the fog + dispatch boundary
The executor is a closure built by `build_tool_registry(registry, world, scene, *, dm=False) -> ToolRegistry`. `ToolRegistry.execute(name, args) -> str` looks up the `Tool`, calls `tool.fn(**args)` (the fn already closes over world/scene/registry and applies fog), JSON-dumps the result, and — critically — **catches every exception**, returning `{"error": "<msg>"}` as JSON so a bad arg from the model can NEVER crash the turn (it just feeds an error back and the model recovers or proceeds). `dm=False` builds the POV set; `dm=True` ALSO includes the DM ground-truth set. The narration path passes `dm=False`; only authoring-only paths pass `dm=True` ([DECISION-4]).

### DD4 — Fog application is per-tool, sourced from existing helpers (NO new knowledge logic)
- **POV `map_query`:** geography (containment/adjacency/path) is treated as PUBLIC by default — the protagonist can see exits and the local map. BUT a place's *state/detail* facts (e.g. `断桥.是否可通行`) are filtered through `knows(graph, pov_agent, "<place>.<pred>", day)`: only known state is returned; unknown state is omitted entirely (POV) or tagged (DM). **Decision:** structural topology (edges/levels/paths) = public; place *facts* = fog-gated. This matches the spec example (`断桥.是否可通行` is a knowledge fact, the road graph is not a secret).
- **POV `characters_query`:** an NPC's `sketch`/`goal`/location is returned only if the POV agent `knows` the corresponding fact_key, reusing `build_viewpoint`'s exact logic (`knows(graph, pov, "<npc>.sketch", day)` etc.). A character the protagonist has never met returns `{"id":..., "known": false}` (existence may be public if they're co-present in `scene["present"]`, but their facets are gated). `trust:<x>` and `hidden` facets are ALWAYS fog-gated.
- **POV `factions_query`:** membership/rank are gated the same way (`knows(graph, pov, "<member>.rank:<faction>", day)`); only relationships the POV agent knows are surfaced.
- **`recall_query`:** reuses `kernel.recall.recall` + `memory.recall.rank` (existing). Fog: recall hits whose underlying fact the POV agent does not know are dropped from the POV result (DM result keeps them, tagged). For P3a we keep `recall_query` simple: it returns the same ranked hits `assemble_context` would, scoped to POV by filtering hits referencing fog-gated facts.
- **DM tools** return the SAME shapes but with NO filtering and every row carrying `protagonist_knows: <bool>` (computed via `knows`), so the authoring path sees truth + the boundary.

### DD5 — `pov_agent` resolution
Tools take an optional `pov` argument (an entity id). If omitted, the executor defaults `pov = scene["protagonist"]`. The model is told (in tool descriptions) that `pov` may be set to a present NPC id to ask "what does this NPC know" (reusing the §9 NPC-knowledge machinery). The executor validates `pov` is in `scene["present"]` or is the protagonist; an out-of-scene `pov` returns `{"error":"pov not in scene"}` (you cannot query a character who isn't here). This is the source-side enforcement of "POV = who can perceive/recall right now".

### DD6 — Capability check (no `isinstance` on strategy ⇆ provider coupling)
`AuthorStrategy.produce` must work with providers that DON'T implement the loop (e.g. a plain `FakeLLMProvider` in the 791 existing tests). **Decision:** add `LLMProvider.supports_tools(self) -> bool` (default `False`; `True` on the three real adapters and `ScriptedToolProvider`). Strategy calls `complete_with_tools` only when `provider.supports_tools()` AND a non-empty tool registry was built; otherwise it falls back to the existing `complete_messages` path verbatim. This guarantees **every one of the 791 existing tests is untouched** (their `FakeLLMProvider` reports `supports_tools()==False` → old path).

### DD7 — Tracing
`_run_tool_loop` wraps each `post` in the existing `get_tracer().generation(...)` via `_do_post` (real path) — already done, no change. Additionally wrap the WHOLE loop in `get_tracer().span("tool_loop", rounds=...)` inside `complete_with_tools` so Langfuse shows the research arc nested under the turn span. NoopTracer offline → zero overhead (consistent with the rest of the codebase). Tool executions get a `span("tool", name=...)` each.

---

## File Structure

```
llm/
  provider.py          # EDIT: + supports_tools(); + complete_with_tools() on ABC (NotImplementedError),
                        #        Zhipu/OpenAI (P3a) & Anthropic (P3b); + _run_tool_loop(); + _append_tool_result
                        #        adapter hook; + _openai_chat_body gains optional tools=; ScriptedToolProvider class.
  tools.py             # NEW: Tool dataclass, ToolRegistry, build_tool_registry(), the four tool fns + fog helpers.
loop/
  strategy.py          # EDIT: AuthorStrategy (P3a) & HybridStrategy (P3b) call complete_with_tools when supported.
app/
  engine.py            # EDIT (P3b): resolve RPG_MAX_TOOL_ROUNDS / tool-enable flag onto Engine.
  play.py              # EDIT (P3b): pass tool-enable through run_turn wiring (no behavior change when off).

tests/llm/
  test_tools.py        # NEW: tool surface + fog-of-war unit tests (offline; FactGraph fixtures).
  test_tool_loop.py    # NEW: _run_tool_loop orchestrator + ScriptedToolProvider tests (fake post, NO network).
  test_provider.py     # APPEND: supports_tools default/override; _build body carries tools; parse() shapes.
tests/loop/
  test_strategy.py     # APPEND: AuthorStrategy research-then-write via ScriptedToolProvider; fallback when unsupported.
```

No new package dirs; `llm/` and `loop/` already exist with `__init__.py`. `tests/llm/__init__.py` and `tests/loop/__init__.py` already exist.

---

## Task 1: `ScriptedToolProvider` + `supports_tools()` — the offline-determinism keystone (TDD FIRST)

> Build the FAKE before the loop, so the determinism property is locked in by a test from the very first commit. This is the make-or-break ([DECISION-2]). The provider's real `complete_with_tools` does not exist yet — this task gives the ABC the method (raising `NotImplementedError`), the `supports_tools()` capability hook, and a deterministic scripted fake that drives a *real* `tool_executor`.

**Files:** `llm/provider.py` (edit), `tests/llm/test_tool_loop.py` (new).

- [ ] **Step 1: Write the failing tests.** Create `tests/llm/test_tool_loop.py`:

```python
"""P3 Task 1: ScriptedToolProvider — offline-deterministic tool-loop fake (NO network)."""
from __future__ import annotations

import json
import pytest


class _RecordingExecutor:
    """A trivial tool_executor: maps name->canned-json, records every call."""
    def __init__(self, table: dict[str, dict]):
        self._table = table
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return json.dumps(self._table.get(name, {"error": f"no tool {name}"}),
                          ensure_ascii=False)


def test_supports_tools_default_false():
    from llm.provider import FakeLLMProvider
    assert FakeLLMProvider().supports_tools() is False


def test_scripted_provider_supports_tools_true():
    from llm.provider import ScriptedToolProvider
    assert ScriptedToolProvider(script=[{"content": "x"}]).supports_tools() is True


def test_scripted_provider_replays_tool_then_final():
    """A script of [tool_calls round, final content] must: execute the tool via the
    REAL executor, feed its result back, then return the final content — and record
    the exact (name, arguments) sequence the model emitted."""
    from llm.provider import ScriptedToolProvider

    script = [
        {"tool_calls": [{"name": "map_query", "arguments": {"q": "city"}}]},
        {"content": '{"narration": "done", "moves": []}'},
    ]
    executor = _RecordingExecutor({"map_query": {"places": ["city", "gate"]}})
    prov = ScriptedToolProvider(script=script)

    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "look around"}]
    final = prov.complete_with_tools(
        messages, tools=[{"type": "function",
                          "function": {"name": "map_query", "parameters": {}}}],
        tool_executor=executor, max_tool_rounds=3)

    assert final == '{"narration": "done", "moves": []}'
    # The executor really ran with the scripted args:
    assert executor.calls == [("map_query", {"q": "city"})]
    # And the provider recorded the model->tool invocations deterministically:
    assert prov.tool_invocations == [("map_query", {"q": "city"})]


def test_scripted_provider_multi_round_sequence():
    """Two tool rounds then final — asserts ordered multi-call research."""
    from llm.provider import ScriptedToolProvider
    script = [
        {"tool_calls": [{"name": "map_query", "arguments": {"q": "city"}}]},
        {"tool_calls": [{"name": "recall_query", "arguments": {"q": "桥"}}]},
        {"content": '{"narration": "ok"}'},
    ]
    executor = _RecordingExecutor({"map_query": {"ok": 1}, "recall_query": {"ok": 2}})
    prov = ScriptedToolProvider(script=script)
    out = prov.complete_with_tools([{"role": "user", "content": "go"}],
                                   tools=[], tool_executor=executor, max_tool_rounds=3)
    assert out == '{"narration": "ok"}'
    assert [c[0] for c in executor.calls] == ["map_query", "recall_query"]


def test_scripted_provider_respects_max_tool_rounds():
    """If the script keeps asking for tools past the cap, the provider stops and
    returns the LAST content it can (forced-final), and logs — never loops forever."""
    from llm.provider import ScriptedToolProvider
    # 5 tool rounds scripted, cap = 2 → must not execute more than the cap allows,
    # and must terminate with the forced-final content.
    script = [{"tool_calls": [{"name": "map_query", "arguments": {}}]}] * 5
    script.append({"content": "FINAL"})
    executor = _RecordingExecutor({"map_query": {"ok": 1}})
    prov = ScriptedToolProvider(script=script)
    out = prov.complete_with_tools([{"role": "user", "content": "go"}],
                                   tools=[], tool_executor=executor, max_tool_rounds=2)
    assert out == "FINAL"
    assert len(executor.calls) == 2  # capped
```

- [ ] **Step 2: Run test to verify it fails.** `python3 -m pytest tests/llm/test_tool_loop.py -q` → FAIL (`ImportError: cannot import name 'ScriptedToolProvider'` / `AttributeError: supports_tools`).

- [ ] **Step 3: Write minimal implementation.** In `llm/provider.py`:
  - On `LLMProvider` (ABC) add:
    ```python
    def supports_tools(self) -> bool:
        """Whether this provider implements complete_with_tools (the tool loop)."""
        return False

    def complete_with_tools(self, messages: list[dict], tools: list[dict],
                            tool_executor, *, model: str | None = None,
                            max_tokens: int | None = None,
                            max_tool_rounds: int = 3) -> str:
        """Research-then-write tool loop: see _run_tool_loop. Default: unsupported."""
        raise NotImplementedError
    ```
  - Add `ScriptedToolProvider` (a `FakeLLMProvider` subclass) that replays `script` against the real `tool_executor`, capped by `max_tool_rounds`, recording `self.tool_invocations`:
    ```python
    class ScriptedToolProvider(FakeLLMProvider):
        """Offline-deterministic tool-loop fake. `script` is a list of assistant
        turns: {"tool_calls":[{"name","arguments"}...]} (executed, then continue)
        or {"content": "<final>"} (returned). Records (name, arguments) the model
        emitted in self.tool_invocations. NO network. See plan DECISION-2."""
        def __init__(self, *, script: list[dict], **kw):
            super().__init__(**kw)
            self._script = list(script)
            self.tool_invocations: list[tuple[str, dict]] = []

        def supports_tools(self) -> bool:
            return True

        def complete_with_tools(self, messages, tools, tool_executor, *,
                                model=None, max_tokens=None, max_tool_rounds=3):
            rounds = 0
            for turn in self._script:
                if "content" in turn:
                    return turn["content"]
                if rounds >= max_tool_rounds:
                    break  # cap hit: fall through to forced-final
                for call in turn.get("tool_calls", []):
                    name, args = call["name"], call.get("arguments", {})
                    self.tool_invocations.append((name, args))
                    tool_executor(name, args)  # drive the REAL executor (records/side-effect-free)
                rounds += 1
            # Forced-final: return the first content turn if any, else the last text.
            for turn in self._script:
                if "content" in turn:
                    return turn["content"]
            return ""
    ```
  > NB: the scripted fake does not thread tool *results* back into `messages` (it doesn't need to — the script already encodes what the model "decided"). It DOES run the real executor so fog/dispatch bugs surface. This is the whole determinism trick: the model's branching is fixed by the script; the tool *effects* are real.

- [ ] **Step 4: Run test to verify it passes.** `python3 -m pytest tests/llm/test_tool_loop.py -q` → PASS.

- [ ] **Step 5: Full-suite gate + commit.** `python3 -m pytest -q --ignore=tests/test_embed_real.py` → 791+4 pass. Commit `llm/provider.py` + `tests/llm/test_tool_loop.py`.
  ```
  P3a Task 1: ScriptedToolProvider + supports_tools() — offline tool-loop fake

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

---

## Task 2: `_run_tool_loop` orchestrator — pure, network-free, `post`/`parse` seams (TDD)

> The real loop logic, with the single I/O seam (`post`) hand-faked in the test. No provider, no urllib. This proves the orchestrator independently of any adapter.

**Files:** `llm/provider.py` (edit), `tests/llm/test_tool_loop.py` (append).

- [ ] **Step 1: Write the failing tests.** Append to `tests/llm/test_tool_loop.py`:

```python
def test_run_tool_loop_executes_and_feeds_back():
    """post returns a tool_calls response once, then a final-text response.
    _run_tool_loop must execute the tool via tool_executor, append the result
    to messages, re-post, and return the final text."""
    from llm.provider import _run_tool_loop

    posts: list[tuple] = []
    responses = iter([
        # round 0: model asks for a tool
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "map_query",
                                         "arguments": '{"q": "city"}'}}]}}]},
        # round 1: model answers
        {"choices": [{"finish_reason": "stop",
                      "message": {"content": "FINAL"}}]},
    ])

    def post(messages, tools):
        posts.append((len(messages), tools is not None))
        return next(responses)

    def parse(resp):
        msg = resp["choices"][0]["message"]
        if resp["choices"][0].get("finish_reason") == "tool_calls":
            calls = [{"id": c["id"], "name": c["function"]["name"],
                      "arguments": __import__("json").loads(c["function"]["arguments"])}
                     for c in msg["tool_calls"]]
            return None, calls
        return msg.get("content"), []

    seen = []
    def executor(name, arguments):
        seen.append((name, arguments))
        return '{"places": ["city"]}'

    def append_result(messages, call, result_str):
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": call["id"], "type": "function",
                                         "function": {"name": call["name"], "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_str})

    messages = [{"role": "user", "content": "look"}]
    out = _run_tool_loop(messages, tools=[{"x": 1}], tool_executor=executor,
                         post=post, parse=parse, append_result=append_result,
                         max_tool_rounds=3)
    assert out == "FINAL"
    assert seen == [("map_query", {"q": "city"})]
    assert len(posts) == 2                 # one research post + one final post
    assert posts[1][1] is True             # tools still offered on the 2nd (under cap)


def test_run_tool_loop_cap_forces_final_without_tools():
    """When the model keeps requesting tools past max_tool_rounds, the loop does
    ONE final post with tools=None (forcing a textual answer) and returns it."""
    from llm.provider import _run_tool_loop

    tool_resp = {"choices": [{"finish_reason": "tool_calls", "message": {
        "tool_calls": [{"id": "c", "type": "function",
                        "function": {"name": "map_query", "arguments": "{}"}}]}}]}
    final_resp = {"choices": [{"finish_reason": "stop",
                               "message": {"content": "FORCED"}}]}
    calls_tools_flag: list[bool] = []
    state = {"n": 0}

    def post(messages, tools):
        calls_tools_flag.append(tools is not None)
        # Always ask for a tool until tools is None (the forced-final call).
        if tools is None:
            return final_resp
        state["n"] += 1
        return tool_resp

    def parse(resp):
        msg = resp["choices"][0]["message"]
        if resp["choices"][0].get("finish_reason") == "tool_calls":
            return None, [{"id": "c", "name": "map_query", "arguments": {}}]
        return msg.get("content"), []

    def executor(name, arguments):
        return "{}"

    def append_result(messages, call, result_str):
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_str})

    out = _run_tool_loop([{"role": "user", "content": "go"}], tools=[{"x": 1}],
                         tool_executor=executor, post=post, parse=parse,
                         append_result=append_result, max_tool_rounds=2)
    assert out == "FORCED"
    assert calls_tools_flag[-1] is False   # final call omits tools
    assert state["n"] == 2                  # exactly max_tool_rounds tool rounds
```

- [ ] **Step 2: Run test to verify it fails.** `python3 -m pytest tests/llm/test_tool_loop.py -k run_tool_loop -q` → FAIL (`cannot import name '_run_tool_loop'`).

- [ ] **Step 3: Write minimal implementation.** In `llm/provider.py`, add `_run_tool_loop` per DD1: loop up to `max_tool_rounds`; each iteration `post(messages, tools)`, `parse` → if `tool_calls`, run each via `tool_executor`, `append_result(messages, call, result)`, continue; else return the text. On exceeding the cap, do ONE `post(messages, None)` (tools omitted), `parse`, return its text. `log.warning("tool loop hit max_tool_rounds=%d; forcing final", max_tool_rounds)` on cap. Wrap each tool execution in `with get_tracer().span("tool", name=call["name"]):`.

- [ ] **Step 4: Run test to verify it passes.** `python3 -m pytest tests/llm/test_tool_loop.py -q` → PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Full-suite gate + commit.** Gate green. Commit `llm/provider.py` + `tests/llm/test_tool_loop.py`.
  ```
  P3a Task 2: _run_tool_loop orchestrator with injectable post/parse seams

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

---

## Task 3: `Tool` + `ToolRegistry` + `build_tool_registry` scaffold (no fog yet) (TDD)

> The dispatcher + schema surface, with two trivial pass-through tools so the registry is testable before fog logic. Fog lands in Task 4–5.

**Files:** `llm/tools.py` (new), `tests/llm/test_tools.py` (new).

- [ ] **Step 1: Write the failing tests.** Create `tests/llm/test_tools.py`:

```python
"""P3 Task 3: Tool / ToolRegistry scaffold (offline)."""
from __future__ import annotations

import json
import pytest

from kernel.registry import Registry
from kernel.projection import empty_world
from systems.ontology import OntologySystem
from systems.place import PlaceSystem


def _reg():
    r = Registry(); r.register(OntologySystem()); r.register(PlaceSystem()); return r


def _scene():
    return {"protagonist": "hero", "present": ["hero"], "day": 1, "location": "town"}


def test_tool_dataclass_schema_shape():
    from llm.tools import Tool
    t = Tool(name="map_query", description="d",
             parameters={"type": "object", "properties": {}}, fn=lambda: {})
    s = t.schema()
    assert s["type"] == "function"
    assert s["function"]["name"] == "map_query"
    assert "parameters" in s["function"]


def test_registry_schemas_and_execute():
    from llm.tools import Tool, ToolRegistry
    reg = ToolRegistry([Tool(name="echo", description="d",
                             parameters={"type": "object"},
                             fn=lambda **kw: {"got": kw})])
    schemas = reg.schemas()
    assert isinstance(schemas, list) and schemas[0]["function"]["name"] == "echo"
    out = reg.execute("echo", {"a": 1})
    assert json.loads(out) == {"got": {"a": 1}}


def test_registry_execute_unknown_tool_returns_error_json():
    from llm.tools import ToolRegistry
    reg = ToolRegistry([])
    out = reg.execute("nope", {})
    assert "error" in json.loads(out)


def test_registry_execute_catches_tool_exception():
    """A throwing tool must NEVER crash the turn — execute returns {"error":...}."""
    from llm.tools import Tool, ToolRegistry
    def boom(**kw):
        raise ValueError("bad arg")
    reg = ToolRegistry([Tool(name="boom", description="d",
                             parameters={"type": "object"}, fn=boom)])
    out = reg.execute("boom", {})
    assert "error" in json.loads(out)


def test_build_tool_registry_returns_named_tools():
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene())
    names = {s["function"]["name"] for s in reg.schemas()}
    # P3a minimal surface (Task 5/6 add the rest):
    assert "map_query" in names
    assert "recall_query" in names
```

- [ ] **Step 2: Run test to verify it fails.** `python3 -m pytest tests/llm/test_tools.py -q` → FAIL (no `llm.tools`).

- [ ] **Step 3: Write minimal implementation.** Create `llm/tools.py` with `from engine.log import get_logger`, `log = get_logger("llm.tools")`:
  - `@dataclass Tool` with `name/description/parameters/fn` and `schema()` returning the OpenAI function shape.
  - `ToolRegistry`: holds `list[Tool]`, `schemas()` → `[t.schema() for t]`, `execute(name, args)` → look up by name (error JSON if unknown), call `fn(**args)` inside `try/except Exception` (log.warning + return `{"error": str(exc)}` JSON), `json.dumps(result, ensure_ascii=False)`.
  - `build_tool_registry(registry, world, scene, *, dm=False)` → for now register two real-but-thin tools: `map_query` and `recall_query`, each a closure over `(registry, world, scene)`. Implement them minimally (e.g. `map_query` returns `{"current": <protagonist location>, "exits": [...]}` from `graph.neighbors`; `recall_query` returns `kernel_recall(registry, q, world)` texts). Fog is Task 4–5 — here they just return data so the scaffold is exercised. Keep return values JSON-serializable.

- [ ] **Step 4: Run test to verify it passes.** `python3 -m pytest tests/llm/test_tools.py -q` → PASS.

- [ ] **Step 5: Full-suite gate + commit.** Gate green. Commit `llm/tools.py` + `tests/llm/test_tools.py`.
  ```
  P3a Task 3: Tool / ToolRegistry scaffold + build_tool_registry (map_query, recall_query)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

---

## Task 4: Fog-of-war for `map_query` (POV) — `knows`-gated place facts (TDD)

> First real fog. Topology public; place *facts* gated through `systems.knowledge.knows`. [DECISION-1]/[DECISION-4]: POV tool, default `pov = protagonist`.

**Files:** `llm/tools.py` (edit), `tests/llm/test_tools.py` (append).

- [ ] **Step 1: Write the failing tests.** Append to `tests/llm/test_tools.py`. Build a FactGraph fixture with two places (`city`, `gate`) linked, a `gate.是否可通行` fact, and a `knowledge_set` granting the protagonist that fact only in one variant:

```python
def _world_with_map(knows_gate: bool):
    from kernel.projection import project
    from kernel.events import kernel_event
    r = _reg()
    evs = [
        kernel_event("place_created", day=1, scene="g", summary="city",
                     deltas={"id": "city", "level": 2, "kind": "settlement", "seed": "城"}, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="gate",
                     deltas={"id": "gate", "level": 3, "kind": "venue", "seed": "门",
                             "parent": "city"}, turn=1),
        kernel_event("place_linked", day=1, scene="g", summary="link",
                     deltas={"a": "city", "b": "gate", "travel_cost": 1}, turn=1),
        kernel_event("entity_moved", day=1, scene="g", summary="hero@city",
                     deltas={"who": "hero", "to": "city"}, turn=1),
        # ground-truth place fact (always in graph):
        kernel_event("place_created", day=1, scene="g", summary="hero ent",
                     deltas={"id": "hero", "level": 1, "kind": "settlement", "seed": "x"}, turn=1),
    ]
    if knows_gate:
        evs.append(kernel_event("knowledge_set", day=1, scene="g", summary="knows",
                                deltas={"knower": "hero", "fact_key": "gate.是否可通行",
                                        "value": "可通行"}, turn=1))
    # assert the ground-truth gate fact directly via the graph after projection:
    w = project(r, iter(evs))
    g = w["systems"]["ontology"]
    g.assert_fact("gate", "是否可通行", "其实已塌", day=1, turn=1, source_event="seed")
    return r, w


def test_map_query_returns_topology_public():
    """Exits/containment are public regardless of knowledge."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=False)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("map_query", {"q": "city"}))
    # exits visible (public topology):
    assert "gate" in json.dumps(out, ensure_ascii=False)


def test_map_query_hides_unknown_place_fact():
    """Protagonist does NOT know gate.是否可通行 → the fact must NOT appear."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=False)
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("map_query", {"q": "gate"})
    assert "其实已塌" not in out      # ground truth never leaks to POV
    assert "可通行" not in out


def test_map_query_shows_known_place_fact():
    """When the protagonist KNOWS the fact, the BELIEVED value appears (not truth)."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=True)
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("map_query", {"q": "gate"})
    assert "可通行" in out            # believed value surfaces
    assert "其实已塌" not in out      # divergent ground truth still hidden


def test_map_query_pov_not_in_scene_errors():
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=True)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("map_query", {"q": "gate", "pov": "stranger"}))
    assert "error" in out
```

- [ ] **Step 2: Run test to verify it fails.** `python3 -m pytest tests/llm/test_tools.py -k map_query -q` → FAIL (fog not implemented; ground truth leaks).

- [ ] **Step 3: Write minimal implementation.** In `llm/tools.py`, rewrite `map_query`'s closure (per DD4/DD5):
  - Resolve `pov = args.get("pov") or scene["protagonist"]`; validate `pov == protagonist or pov in scene["present"]` else raise/return error (DD5 — let `ToolRegistry.execute` convert raised errors to JSON, OR return `{"error":...}` directly; pick one and be consistent — recommend returning the dict, executor passes it through).
  - Topology: from the matched place(s) (substring on id/seed, like `PlaceSystem.recall`) emit `contained_by` parent, `adjacent_to` exits + costs, `level`/`kind` — all public.
  - Place facts: for each current non-reserved fact on the place (predicate not in `{knows:*, rank:*, group:*, trust:*}`), include it ONLY if `knows(graph, pov, f"{place}.{predicate}", day)` is not None, and emit the BELIEVED value (`knows(...)`), never `graph.value_at` (truth). Unknown facts are omitted (POV).
  - `import` `knows` from `systems.knowledge` and `navigate` from `systems.place`.

- [ ] **Step 4: Run test to verify it passes.** `python3 -m pytest tests/llm/test_tools.py -q` → PASS.

- [ ] **Step 5: Full-suite gate + commit.** Gate green. Commit `llm/tools.py` + `tests/llm/test_tools.py`.
  ```
  P3a Task 4: map_query fog-of-war — public topology, knows-gated place facts

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

---

## Task 5: Fog-of-war for `recall_query` (POV) + `navigate` path support in `map_query` (TDD)

> Round out the two P3a tools: `recall_query` filtered to POV; `map_query` gains an optional `path_to` arg using `place.navigate`.

**Files:** `llm/tools.py` (edit), `tests/llm/test_tools.py` (append).

- [ ] **Step 1: Write the failing tests.** Append tests asserting: (a) `map_query({"q":"city","path_to":"gate"})` returns `{"path":["city","gate"],"total_cost":1}` (delegates to `navigate`); (b) `recall_query({"q":"门"})` returns hit texts for matching entities; (c) a recall hit whose underlying fact the protagonist does NOT know is dropped from the POV result. (Construct the fixture so one recallable entity carries a fog-gated fact.) Mirror the fixture style from Task 4.

- [ ] **Step 2: Run test to verify it fails.** FAIL (`path_to` unhandled / recall not fog-filtered).

- [ ] **Step 3: Write minimal implementation.** In `llm/tools.py`:
  - `map_query`: if `args.get("path_to")`, return `navigate(graph, <pov current location>, path_to, day)` merged into the result.
  - `recall_query`: call `kernel_recall(registry, args["q"], world)`; build `{"hits": [h.text, ...]}`; drop any hit whose `ref["id"]`'s relevant fact is fog-gated for `pov` (for P3a, the simple rule: a Person/Place hit is kept; a hit derived from a `knows:`-type fact is included only if `pov` knows it). Keep it minimal but assert the drop in the test.
  - Document in each tool's `description` that `pov` defaults to the protagonist and may be a present NPC ([DECISION-4]/DD5).

- [ ] **Step 4: Run test to verify it passes.** PASS.

- [ ] **Step 5: Full-suite gate + commit.** Gate green. Commit the two files.
  ```
  P3a Task 5: recall_query POV fog + map_query navigate path

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

---

## Task 6: `ZhipuProvider`/`OpenAIProvider.complete_with_tools` — real adapters over `_run_tool_loop` (TDD, offline via fake `post`)

> Wire the real GLM/OpenAI adapters to `_run_tool_loop`, with the OpenAI/GLM `parse` + `append_result` shapes (DD2). Tested OFFLINE: assert `_build_*` carries `tools`; drive `complete_with_tools` with a monkeypatched module-level `_do_post` so NO network is hit.

**Files:** `llm/provider.py` (edit), `tests/llm/test_provider.py` (append).

- [ ] **Step 1: Write the failing tests.** Append to `tests/llm/test_provider.py`:
  - `test_supports_tools_true_on_real_adapters`: `ZhipuProvider(...).supports_tools() is True` (and OpenAI).
  - `test_openai_chat_body_carries_tools`: a body-builder test — `_openai_chat_body(model, msgs, mt, tools=[{...}])` puts `tools` in the body.
  - `test_zhipu_complete_with_tools_offline` (the key one): monkeypatch `llm.provider._do_post` with a fake returning a tool_calls response then a final-text response (same two-response pattern as Task 2), pass a recording `tool_executor`, assert the final text + that the executor ran with the parsed args + that `_do_post` was called twice. NO real HTTP.

```python
def test_zhipu_complete_with_tools_offline(monkeypatch):
    import json
    from llm import provider as P
    responses = iter([
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "map_query",
                                         "arguments": '{"q": "city"}'}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "DONE"}}]},
    ])
    calls = {"n": 0}
    def fake_post(url, headers, body, timeout=300, *, max_retries=4):
        calls["n"] += 1
        return next(responses)
    monkeypatch.setattr(P, "_do_post", fake_post)

    seen = []
    def executor(name, arguments):
        seen.append((name, arguments)); return '{"ok": 1}'

    prov = P.ZhipuProvider(model="glm-4.7", api_key="k")
    out = prov.complete_with_tools(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "look"}],
        tools=[{"type": "function", "function": {"name": "map_query", "parameters": {}}}],
        tool_executor=executor, max_tool_rounds=3)
    assert out == "DONE"
    assert seen == [("map_query", {"q": "city"})]
    assert calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails.** FAIL (`complete_with_tools` raises `NotImplementedError`; `supports_tools` False; `_openai_chat_body` rejects `tools`).

- [ ] **Step 3: Write minimal implementation.** In `llm/provider.py`:
  - `_openai_chat_body(model, messages, max_tokens, tools=None)` → add `body["tools"] = tools` when non-None.
  - Module-level `_openai_parse(resp)` and `_openai_append_result(messages, call, result_str)` per DD2.
  - On `OpenAIProvider` and `ZhipuProvider`: `supports_tools()` → `True`; `complete_with_tools(...)` builds url/headers, defines `post = lambda msgs, tls: _do_post(url, headers, _openai_chat_body(model, msgs, mt, tools=tls))`, then `return _run_tool_loop(messages, tools, tool_executor, post=post, parse=_openai_parse, append_result=_openai_append_result, max_tool_rounds=max_tool_rounds)`, all wrapped in `with get_tracer().span("tool_loop"):` (DD7).

- [ ] **Step 4: Run test to verify it passes.** PASS.

- [ ] **Step 5: Full-suite gate + commit.** Gate green. Commit `llm/provider.py` + `tests/llm/test_provider.py`.
  ```
  P3a Task 6: Zhipu/OpenAI complete_with_tools via _run_tool_loop (offline-tested)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

---

## Task 7: Wire tools into `AuthorStrategy` (甲) — research-then-write, capability-gated (TDD) — END OF P3a

> The payoff: 甲 builds the POV tool registry and uses the loop when the provider supports it; otherwise unchanged. End-to-end offline via `ScriptedToolProvider`. After this task, P3a is shippable and demoable on a real model.

**Files:** `loop/strategy.py` (edit), `tests/loop/test_strategy.py` (append).

- [ ] **Step 1: Write the failing tests.** Append to `tests/loop/test_strategy.py`:

```python
def test_author_strategy_uses_tool_loop_when_supported():
    """With a tool-capable provider, 甲 researches (calls a tool) then emits the
    commit — asserted deterministically via ScriptedToolProvider."""
    from loop.strategy import AuthorStrategy
    from llm.provider import ScriptedToolProvider

    registry = _make_registry()
    world = _make_world(registry)
    scene = _make_scene()  # protagonist present
    script = [
        {"tool_calls": [{"name": "map_query", "arguments": {"q": "town"}}]},
        {"content": '{"narration": "勘察后前行", "moves": []}'},
    ]
    provider = ScriptedToolProvider(script=script)
    strat = AuthorStrategy()
    commit = strat.produce(registry, world, scene, "四处看看", provider=provider)

    assert commit.narration == "勘察后前行"
    # 甲 actually drove a tool research round:
    assert ("map_query", {"q": "town"}) in provider.tool_invocations


def test_author_strategy_falls_back_when_tools_unsupported():
    """A plain FakeLLMProvider (supports_tools()==False) must use the OLD
    complete_messages path verbatim — guarantees the 791 suite is untouched."""
    from loop.strategy import AuthorStrategy
    from llm.provider import FakeLLMProvider
    provider = FakeLLMProvider(json_responses=[{"narration": "no tools", "moves": []}])
    strat = AuthorStrategy()
    commit = strat.produce(_make_registry(), _make_world(_make_registry()),
                           _make_scene(), "走", provider=provider)
    assert commit.narration == "no tools"
    assert provider.supports_tools() is False
```

  (`_make_registry/_make_world/_make_scene` already exist in this test file; ensure `_make_scene` lists the protagonist in `present` so tool `pov` validation passes.)

- [ ] **Step 2: Run test to verify it fails.** FAIL (甲 doesn't build/use tools).

- [ ] **Step 3: Write minimal implementation.** In `loop/strategy.py`, in `AuthorStrategy.produce`, on the FRESH-turn branch (`repair is None or self._messages is None`), after building `self._messages`:
  ```python
  from llm.tools import build_tool_registry  # at module top
  ...
  if repair is None and provider.supports_tools():
      tool_reg = build_tool_registry(registry, world, scene)   # POV set (dm=False)
      schemas = tool_reg.schemas()
      if schemas:
          import os
          rounds = int(os.environ.get("RPG_MAX_TOOL_ROUNDS", "3"))
          raw = provider.complete_with_tools(
              self._messages, schemas, tool_reg.execute, max_tool_rounds=rounds)
          self._messages.append({"role": "assistant", "content": raw})
          data = _parse_json_object(raw) or {"narration": raw}
          return TurnCommit.from_dict(data)
  # else: existing complete_messages path (unchanged)
  raw = provider.complete_messages(self._messages)
  ...
  ```
  Repair rounds keep the existing `complete_messages` path (no re-research — research-then-write happens once). Keep all existing behavior for non-tool providers byte-for-byte.

- [ ] **Step 4: Run test to verify it passes.** PASS.

- [ ] **Step 5: Full-suite gate + commit.** `python3 -m pytest -q --ignore=tests/test_embed_real.py` → all green (791 + P3a additions). Commit `loop/strategy.py` + `tests/loop/test_strategy.py`.
  ```
  P3a Task 7: AuthorStrategy research-then-write via complete_with_tools (capability-gated)

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

> **P3a COMPLETE.** Run a real-model smoke (`/tmp/verify_p3a.py`, NOT committed): build an engine with a real GLM provider, run a turn whose input invites a lookup ("回想我对城门的了解"), confirm a `tool_loop` span fires and the narration respects fog. **Then pause for human review of [DECISION-1]/[DECISION-4] before P3b.**

---

## Task 8 (P3b): `characters_query` + `factions_query` POV tools (TDD)

**Files:** `llm/tools.py` (edit), `tests/llm/test_tools.py` (append).
- [ ] Tests: `characters_query({"id":"npc"})` returns `sketch/goal/location` only for facets the protagonist `knows`; an unmet NPC returns `{"known": false}`; `trust:`/`hidden` always gated. `factions_query({"faction":"guild"})` returns members/ranks only as known by `pov` (gate via `knows(graph, pov, f"{member}.rank:{faction}", day)`); reuse `members_of`/`member_rank`. Add both to `build_tool_registry`'s POV set.
- [ ] Impl mirrors DD4; import `members_of`/`member_rank` from `systems.faction`. Reuse `build_viewpoint` where a present-NPC bundle is the natural shape.
- [ ] Gate + commit. `P3b Task 8: characters_query + factions_query POV tools`.

## Task 9 (P3b): DM ground-truth tool set (`dm_world_query`), `dm=True` ([DECISION-1]/[DECISION-4]) (TDD)

**Files:** `llm/tools.py` (edit), `tests/llm/test_tools.py` (append).
- [ ] Tests: `build_tool_registry(..., dm=True)` schema includes `dm_world_query`; `build_tool_registry(...)` (default) does NOT. `dm_world_query` returns ground truth (`graph.value_at`) for places/characters/factions with each fact tagged `protagonist_knows: <bool>` (via `knows`). Assert a fact the protagonist does NOT know is RETURNED but tagged `protagonist_knows: false` (the opposite of the POV tool — truth + boundary, for authoring).
- [ ] Impl: add the DM tool factory; `build_tool_registry` appends it only when `dm=True`.
- [ ] Gate + commit. `P3b Task 9: DM ground-truth dm_world_query (dm=True, authoring-only)`.

## Task 10 (P3b): `AnthropicProvider.complete_with_tools` (Anthropic tool shape) (TDD, offline)

**Files:** `llm/provider.py` (edit), `tests/llm/test_provider.py` (append).
- [ ] Tests: `AnthropicProvider.supports_tools() is True`; body-builder carries `tools`; `complete_with_tools` offline via monkeypatched `_do_post` returning a `tool_use` block response then a text response (Anthropic shapes per DD2); assert final text + executor ran.
- [ ] Impl: `_anthropic_parse` (reads `content` blocks, `stop_reason=="tool_use"`) + `_anthropic_append_result` (`user` message w/ `tool_result` block); body adds `tools` (Anthropic schema: `{"name","description","input_schema"}` — note the field is `input_schema`, NOT `parameters`; add a tiny `Tool.anthropic_schema()` or convert in the adapter). Reuse `_run_tool_loop`.
- [ ] Gate + commit. `P3b Task 10: AnthropicProvider.complete_with_tools`.

## Task 11 (P3b): Wire tools into `HybridStrategy` (丙) structure call; DM tools allowed there ([DECISION-4]) (TDD)

**Files:** `loop/strategy.py` (edit), `tests/loop/test_strategy.py` (append).
- [ ] Tests: with a tool-capable provider, 丙's CALL 2 (structure authoring of frozen prose) uses `complete_with_tools` built with `dm=True` (authoring-only path → DM tools OK); prose call (call 1) stays plain `complete` OR uses POV tools only — pick per [DECISION-4] and assert. Frozen-prose + repair-continues-structure invariants from the existing `test_hybrid_strategy_freezes_prose_and_structures_separately` MUST still hold (do not break it).
- [ ] Impl: in `HybridStrategy.produce`, build a `dm=True` registry for the structure call when `provider.supports_tools()`; keep call-1 prose path POV-only or tool-free per decision.
- [ ] Gate + commit. `P3b Task 11: HybridStrategy structure call uses tools (dm authoring set)`.

## Task 12 (P3b): env/CLI plumbing — `RPG_MAX_TOOL_ROUNDS` + tool-enable flag (TDD)

**Files:** `app/engine.py` (edit), `app/play.py` (edit), `tests/app/test_engine.py` (append — mirror existing app tests).
- [ ] Tests: `RPG_MAX_TOOL_ROUNDS` resolves onto the Engine/loop (default 3); tools are effectively no-ops when the provider is `FakeLLMProvider` (existing app tests stay green — assert a turn still runs with the fake). Confirm NO behavior change in the default fake path.
- [ ] Impl: read the env onto `Engine` (like `cascade_provider`); thread it where the strategy reads it (or keep the strategy reading the env directly as in Task 7 — then this task is just docs + a tiny `Engine` field for visibility). Keep `app/play.py` change minimal (the strategy already self-activates via `supports_tools()`).
- [ ] Gate + commit. `P3b Task 12: RPG_MAX_TOOL_ROUNDS env + tool-enable plumbing`.

> **P3b COMPLETE.** Real-model verify (`/tmp/verify_p3b.py`): a turn where the protagonist investigates an NPC's hidden allegiance — confirm the POV `characters_query` hides it in narration while (under 丙) the DM tool exposes it to the structure authoring. **OUT OF SCOPE (future P4):** trimming the PUSH context ([DECISION-5]) — recap/storylines/viewpoint stay fully pushed.

---

## Self-Review

Run after the last P3a task (and again after P3b):

- [ ] **Determinism (命脉) holds:** every new test runs with NO network. Confirm `grep -rn "urlopen\|http" tests/llm/test_tool_loop.py tests/llm/test_tools.py` finds NOTHING; the only `_do_post` reference in tests is a `monkeypatch.setattr`. `ScriptedToolProvider` drives the real `tool_executor` (fog bugs surface) while fixing the model's branching.
- [ ] **791 suite untouched:** `python3 -m pytest -q --ignore=tests/test_embed_real.py` green; the diff to pre-existing tests is ZERO (P3 is additive). `FakeLLMProvider.supports_tools()` is `False` → every existing strategy/turn test takes the old `complete_messages` path unchanged (DD6).
- [ ] **Read-only keystone:** `grep -n "assert_fact\|add_entity\|add_relation\|store.append\|kernel_event" llm/tools.py` finds NOTHING — tools never mutate state or emit events; ALL writes still go through the turn-commit gate (spec §1/§5).
- [ ] **Fog enforced at the source:** POV tools call `knows(...)` and emit the BELIEVED value, never `graph.value_at` (truth). `grep -n "value_at" llm/tools.py` appears ONLY inside the `dm_world_query` factory (DM path), never in a POV tool.
- [ ] **Cap + safety:** `_run_tool_loop` `log.warning`s on `max_tool_rounds` and forces a final tools-omitted call (Task 2 test asserts it). `ToolRegistry.execute` catches every exception → a bad model arg can NEVER crash a turn (Task 3 test asserts it).
- [ ] **One I/O seam:** `_run_tool_loop` has no `urllib` import; adapters inject `post`. The Anthropic vs OpenAI shape difference lives entirely in `parse`/`append_result` (DD2), not in the orchestrator.
- [ ] **Logging/convention:** `llm/tools.py` uses `from engine.log import get_logger`. Tests mirror source paths.
- [ ] **Git guardrails:** no `engine/`/`_legacy/`/`data/`/`docs` edits (except this plan); no branch ops; commits name exactly their files with the Co-Authored-By trailer.

## Execution Handoff

- Implement P3a (Tasks 1–7) first; STOP after Task 7 for the real-model smoke + human re-confirmation of [DECISION-1]/[DECISION-4].
- P3b (Tasks 8–12) only after sign-off. If at Task 1 the `ScriptedToolProvider` is NOT clean/deterministic, invoke [DECISION-2-fallback] (descope the loop to a one-shot context expander) and re-plan Task 7 accordingly.
- Open questions for the human are ALL in "DECISIONS FOR HUMAN" — do not silently resolve them differently during implementation.
