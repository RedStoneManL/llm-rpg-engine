# S3: Context Assembler + POV/Guardrail Viewpoint Bundles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. `- [ ]` steps, TDD.

**Goal:** Assemble the per-turn context the main narrator sees: cache-layered (stable/scene/volatile) per-system `inject` fragments + ranked recall (S2) + the **POV/guardrail viewpoint bundle** (protagonist sees only what they know; scene-relevant facts they DON'T know become constrain-don't-reveal guardrails; present-NPC knowledge bundles). god-truth reaches the player only via explicit OOC; no dramatic-irony.

**Architecture:** A `context/` package. The viewpoint bundler is pure over the shared graph using 认知 `knows`/`knowers_of`. The assembler composes the kernel's `assemble`/`render` (per-system fragments, already layer-sorted) + ranked recall candidates (S2 `rank`) + the viewpoint bundle into one rendered context string with the cache layering of design §3.2. Also closes M4: add a `recall` hook to the content systems so the kernel recall driver returns useful candidates. All offline (FakeEmbedder / no LLM needed for assembly itself).

**Tech Stack:** Python 3.12 stdlib; reuses kernel (`assemble`/`render`/`recall`), `memory.recall.rank`/`embed_query`, `engine.embed`, 认知 `knows`/`knowers_of`. Offline tests `python3 -m pytest -q --ignore=tests/test_embed_real.py`.

**Conventions/Guardrails:** TDD per task; `get_logger`; commit per task; `python3`. Never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; do NOT touch `kernel/`. New package `context/`.

---

## File Structure
- `context/__init__.py`, `context/viewpoint.py`, `context/assembler.py` (new).
- Possibly small edits to `systems/character.py`/`systems/place.py` to add a `recall()` hook (M4) — minimal, additive.
- Tests: `tests/context/__init__.py` + `test_viewpoint.py` + `test_assembler.py`; extend `tests/systems/*` if adding recall.

---

## Task 1: Viewpoint bundler (POV / guardrail / NPC bundles)
`context/viewpoint.py`: `build_viewpoint(graph, *, protagonist, present, day, candidate_fact_keys) -> dict` returning:
- `pov`: `{fact_key: value}` for fact_keys the protagonist KNOWS (`knows(graph, protagonist, fk, day)` not None) — writable.
- `guardrail`: `{fact_key: truth}` for candidate fact_keys the protagonist does NOT know but that have a ground-truth value in the graph — these are "constrain, never reveal". (truth = the current graph value, looked up by interpreting fact_key as `subject.predicate` if it contains a dot, else skipped.)
- `npc`: `{npc: {fact_key: value_npc_knows}}` for each id in `present` (excluding protagonist) over the candidate fact_keys they know.
- [ ] Step1 failing tests `tests/context/test_viewpoint.py`: set up knowledge (protagonist knows `桥.status`; an NPC knows a secret protagonist doesn't; a candidate truth protagonist doesn't know). Assert pov contains only protagonist-known; guardrail contains the unknown-but-true one; npc bundle reflects the NPC's knowledge. Step2 fail. Step3 implement (import `knows` from `systems.knowledge`; ground-truth lookup via `graph.value_at(subject, predicate, day)` when fact_key splits on `.`). Step4 pass. Step5 commit `feat(context): viewpoint bundler (POV/guardrail/NPC)`.

## Task 2: Per-system `recall` hooks (close M4)
Add `recall(self, query, world)` to `CharacterSystem` (match persons by sketch/goal substring → RecallHit) and `PlaceSystem` (match places by id/seed substring). Keep simple substring/contains matching (semantic ranking happens in the assembler via S2). 
- [ ] Step1 failing tests (extend `tests/systems/test_character.py`/`test_place.py`): character recall finds a person whose sketch contains the query; place recall finds a place whose seed/id contains the query. Step2 fail. Step3 implement minimal `recall`. Step4 pass + full suite green. Step5 commit `feat(systems): per-system recall hooks (character/place) — closes M4`.

## Task 3: Context assembler
`context/assembler.py`: `assemble_context(registry, world, scene, *, query=None, embedder=None, k=6) -> str`:
1. kernel `assemble(registry, scene, world)` → layer-sorted fragments.
2. if `query`: kernel `recall(registry, query, world)` → candidates; rank with `memory.recall.rank` (compute recency from `world["meta"]["day"]`, importance default if absent, relevance via `embed_query`/FakeEmbedder) → top-k recall block.
3. `build_viewpoint(...)` from `scene` (protagonist/present/day) + candidate fact_keys (derive from current knowledge facts in the graph) → render POV facts (scene layer), guardrail facts tagged "⚠️只约束·勿泄露" (scene layer), NPC bundles.
4. Compose into cache layers: render fragments via kernel `render`, then append the recall block (volatile) + viewpoint (scene) in the right order; return one string. Keep stable→scene→volatile ordering.
- [ ] Step1 failing tests `tests/context/test_assembler.py` (register ontology+place+character+knowledge; build a small world; FakeEmbedder): assembled context contains the place exits (from PlaceSystem.inject), a present-character card (CharacterSystem.inject), a recalled item when `query` matches, the protagonist's POV fact, and the guardrail marker for an unknown-but-true fact; ordering is stable-before-scene-before-volatile. Step2 fail. Step3 implement. Step4 pass + full suite green. Step5 commit `feat(context): assemble_context — layered fragments + ranked recall + viewpoint`.

---

## Done criteria for S3
- Full suite green. `assemble_context` produces a cache-layered context combining per-system inject + ranked recall + POV/guardrail/NPC bundles; protagonist POV is the writable lens, unknown-but-true facts are guardrail-only. M4 closed (character/place recall).
- No game logic in `kernel/`; offline (no LLM/network in tests).

**Next:** S4 — agent loop + backstage fleet + TurnStrategy 甲/乙 + compare + validation-repair loop, wiring `LLMProvider` (decided) and `assemble_context`; then S5 CLI v1.
