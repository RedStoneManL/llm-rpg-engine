# S4a: Core Agent Loop (TurnStrategy 甲 + turn pipeline + repair loop) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. `- [ ]` steps, TDD.

**Goal:** Make a single turn runnable end-to-end: assemble context → main-LLM produces a `TurnCommit` (strategy 甲) → validate + repair loop (N=3) → explode to events → append → re-project. Offline-tested with `FakeLLMProvider`; real provider only for live play (S5).

**Architecture:** `loop/` package. `TurnStrategy` ABC; `AuthorStrategy(甲)` produces the full `TurnCommit` from one main-LLM call over `assemble_context(...)`. `run_turn(...)` is the pipeline: produce → `kernel.validation.validate_commit` → on errors `build_repair_request` and re-produce (feedback fed to the strategy) up to N=3 → final fallback drops still-failing sections (keep valid, log) → for each section `owner.to_events(...)` → `EventStore.append` → `kernel.projection.project`. Returns the player-facing narration + new world. No game logic in `kernel/`; the loop orchestrates registry + systems + provider + assembler.

**Tech Stack:** Python 3.12 stdlib; reuses kernel (registry/projection/validation/events), `context.assembler.assemble_context`, `kernel.turncommit.TurnCommit`, `llm.provider` (FakeLLMProvider), `engine.embed`. Offline tests `python3 -m pytest -q --ignore=tests/test_embed_real.py`.

**Conventions/Guardrails:** TDD per task; `get_logger("loop.*")`; commit per task; `python3`. Never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; do NOT touch `kernel/`/`facts/`/`systems/`/`llm/`/`memory/`/`context/` except minimal additive use. New package `loop/`.

---

## File Structure
- `loop/__init__.py`, `loop/strategy.py` (`TurnStrategy` ABC + `AuthorStrategy`), `loop/turn.py` (`run_turn` + `TurnResult`).
- Tests: `tests/loop/__init__.py` + `test_strategy.py` + `test_turn.py`.

---

## Task 1: `TurnStrategy` ABC + `AuthorStrategy(甲)`
`TurnStrategy` ABC: `produce(self, registry, world, scene, player_input, *, provider, embedder=None, repair=None) -> TurnCommit`. `AuthorStrategy`:
1. `ctx = assemble_context(registry, world, scene, query=player_input, embedder=embedder)`.
2. Build `system` prompt (a short DM constitution: narrate in protagonist POV; respect ⚠️guardrail facts—never reveal; output JSON `{narration, <commit sections>}`). `user` = ctx + player_input + (if `repair`: the repair instruction).
3. `data = provider.complete_json(system, user, schema=TURNCOMMIT_SCHEMA)`; return `TurnCommit.from_dict(data)`.
`TURNCOMMIT_SCHEMA` = permissive ({narration: str, plus optional section keys}); real checking is `validate_commit`.
- [ ] Step1 failing tests `tests/loop/test_strategy.py` (FakeLLMProvider with a canned JSON turn-commit): `AuthorStrategy().produce(...)` returns a `TurnCommit` with the canned narration + sections; when `repair="..."` is passed, the repair text appears in the user prompt the fake recorded. Step2 fail. Step3 implement. Step4 pass. Step5 commit `feat(loop): TurnStrategy ABC + AuthorStrategy (甲)`.

## Task 2: `run_turn` pipeline + repair loop
`loop/turn.py`: `@dataclass TurnResult{narration, world, commit, events, repair_attempts, dropped_sections}`. `run_turn(registry, store, world, scene, player_input, *, strategy, provider, embedder=None, max_repairs=3) -> TurnResult`:
1. `commit = strategy.produce(...)`.
2. `errors = validate_commit(registry, commit, world)`; while errors and attempts < max_repairs: `commit = strategy.produce(..., repair=build_repair_request(errors))`; re-validate; attempts++.
3. If errors remain: **drop** the sections that still have errors from `commit.sections` (record `dropped_sections`, log a warning) — keep the valid ones.
4. For each section in `commit.sections`: `owner = registry.owner_of_section(section)`; `events += owner.to_events(section, decl, turn=<next>, day=<world meta day or scene day>, scene=<scene id>)`.
5. `for ev in events: store.append(ev)`; `world = project(registry, store.iter_events())`.
6. Return `TurnResult(narration=commit.narration, world=world, commit=commit, events=events, repair_attempts=attempts, dropped_sections=...)`.
- [ ] Step1 failing tests `tests/loop/test_turn.py` (register ontology+place+character+knowledge; `open_store` w/ registry.event_types(); FakeLLMProvider): (a) a valid canned commit → events appended, world reflects them (e.g. a created entity present), narration returned, repair_attempts==0; (b) a commit that's invalid first (e.g. a `facts` ref to a missing entity) then a fake that returns a fixed commit on the repair call → repair_attempts≥1, final world valid; (c) a commit that stays invalid for all attempts → that section is in `dropped_sections`, valid sections still applied. Step2 fail. Step3 implement (turn counter = max existing event turn + 1 or scene-provided). Step4 pass + full suite green. Step5 commit `feat(loop): run_turn pipeline + validate/repair loop (N=3) + drop-fallback`.

## Task 3: End-to-end smoke (a real multi-turn sequence offline)
- [ ] Step1 failing test `tests/loop/test_turn.py::test_two_turn_sequence`: with a `FakeLLMProvider` scripted to return two different turn-commits across two `run_turn` calls (turn 1 creates a place + moves protagonist there + a character; turn 2 evolves the character + asserts a fact), assert after turn 2: the world has the entities/facts from both turns, `value_at` reflects turn-2 evolution, and the protagonist's `located_in` is correct (point-in-time). This proves the loop accrues state across turns via the event log. Step2 fail. Step3 — should pass with Tasks 1–2 (if not, fix). Step4 pass + full suite green. Step5 commit `test(loop): two-turn end-to-end sequence (offline)`.

---

## Done criteria for S4a
- Full suite green. A turn runs end-to-end offline (FakeLLMProvider): assemble → produce → validate/repair(N=3)/drop → events → append → project; state accrues across turns via the event log.
- No game logic in `kernel/`; no network in tests.

**Next:** S4b (乙 ExtractStrategy + compare mode + backstage fleet importance/reflection); then S5 CLI v1 (real `make_provider`).
