# S1c: 角色 (Character) System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. `- [ ]` steps, TDD throughout.

**Goal:** A registered `CharacterSystem`: Persons as entities in the shared `FactGraph`, with the **anti-脸谱 prose-primary card** (a required free-form "who is this person" sketch + a required current goal + *optional* facets) and evolution/relationship state — all stored as **bitemporal facts**, so arc history + point-in-time come free via supersession.

**Architecture:** Follows the system pattern from S1b: owns its event-types/sections, writes the shared graph `world["systems"]["ontology"]`, requires `OntologySystem` registered. **Everything about a character except identity (id/etype/tier) is a Fact on the Person entity** — `sketch`, `goal`, optional `past`/`hidden`, and evolving `trust`/`mood`/relationship predicates. Reflection (S2) later supersedes `sketch`/arc facts; this system just records. **机械处严, 创作处松:** validate requires id+sketch+goal and that refs exist, but NEVER requires facets (depth is optional — "纯粹之人" is valid). Staleness detection (active-but-unchanged) is an S4/check concern — NOT here.

**Tech Stack:** Python 3.12 stdlib; reuses S0 kernel + S1a `facts/`/`OntologySystem`. Offline tests, `python3 -m pytest -q` (full: `--ignore=tests/test_embed_real.py`).

**Conventions:** TDD per task; `get_logger("systems.character")`; commit per task; `python3`. **Git guardrails:** never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; do NOT touch `kernel/`.

---

## File Structure
- `systems/character.py` (new) — `CharacterSystem`.
- `tests/systems/test_character.py` (new).

---

## Task 1: `CharacterSystem` core (events, apply, validate, to_events)

`CharacterSystem(ContextSystem)`: `name="character"`; `event_types={"character_created","character_evolved","relationship_changed"}`; `commit_sections={"cast"}`; `empty_state()` returns `{}` (Persons live in the shared graph). `apply` reads `g=world["systems"]["ontology"]`.

Storage model (all bitemporal facts on the Person entity, predicate-scoped supersession):
- `character_created` deltas `{id, sketch, goal, tier?, past?, hidden?}` → `g.add_entity(id,"Person",tier)` + `assert_fact(id,"sketch",sketch)`, `assert_fact(id,"goal",goal)`, and **only if present**, `assert_fact(id,"past",past)` / `assert_fact(id,"hidden",hidden)`.
- `character_evolved` deltas `{id, predicate, value}` → `assert_fact(id, predicate, value)` (e.g. predicate `mood`/`goal`/`sketch`). Supersedes prior value of that predicate (arc preserved in history).
- `relationship_changed` deltas `{id, toward, value}` → `assert_fact(id, f"trust:{toward}", value)` (trust of `id` toward entity `toward`).

- [ ] **Step 1 — failing tests** `tests/systems/test_character.py` (register `OntologySystem()` + `CharacterSystem()`; drive via `project(...)`/`kernel_event`). Cover:
  - `character_created` (full: id+sketch+goal+past+hidden+tier) → Person entity at right tier; `value_at(id,"sketch",day)`, `"goal"`, `"past"`, `"hidden"` all set.
  - `character_created` **minimal** (only id+sketch+goal, the "纯粹之人" case) → entity created; `past`/`hidden` facts absent (`value_at` → None); NO error.
  - `character_evolved` (predicate="mood",value="哀恸") → `value_at(id,"mood",day)`; a later evolve supersedes (point-in-time: old day → old value, new day → new value, `fact_history` length 2).
  - `relationship_changed` (id, toward="主角", value="敌对") → `value_at(id,"trust:主角",day)=="敌对"`.
  - `validate("cast",[...])`: `character_created` missing `sketch` → `ValidationError(code="missing", field contains "sketch")`; missing `goal` → missing; a `character_evolved`/`relationship_changed` whose `id` entity doesn't exist → `dangling_ref`. **A minimal create with only id+sketch+goal → NO errors** (facets not required — this is the anti-脸谱 guarantee, assert it explicitly).
  - `to_events("cast",[...])` maps each item to the right event by an `op` field: `{"op":"create",...}`→character_created, `{"op":"evolve",...}`→character_evolved, `{"op":"relationship",...}`→relationship_changed.
- [ ] **Step 2 — run, fail.**
- [ ] **Step 3 — implement `systems/character.py`.** `validate` requires `id`+`sketch`+`goal` for create items (`op=="create"` or no `op` defaults to create), checks subject existence for evolve/relationship; **must not require past/hidden**. `to_events` dispatches on `item["op"]` (default "create"). Add `get_logger`. Docstring: requires OntologySystem; documents the "机械处严创作处松" validation stance + that staleness/reflection are external.
- [ ] **Step 4 — run, pass; full suite green.**
- [ ] **Step 5 — commit:** `git add systems/character.py tests/systems/test_character.py && git commit -m "feat(systems): CharacterSystem — Persons + prose-primary card + evolution facts"`

---

## Task 2: `inject()` — present-character cards

- [ ] **Step 1 — failing tests:** with characters 艾拉 (sketch+goal+current mood) and 主角 present in the scene (`scene={"present":["艾拉"], "day":D}`), `CharacterSystem().inject(scene, world)` returns a `Fragment(system="character", layer="scene", ...)` whose text contains 艾拉's current `sketch` and current `mood`/`goal` (the *current* fact values at `day`). Characters NOT in `scene["present"]` are omitted. Empty/absent `present` → returns `None`.
- [ ] **Step 2 — run, fail.**
- [ ] **Step 3 — implement** `CharacterSystem.inject(self, scene, world)`: `g=world["systems"]["ontology"]`; for each id in `scene.get("present",[])` that is a Person entity, render `f"{id}：{sketch} | 目标：{goal} | 此刻：{mood或'—'}"` using `g.value_at(id, pred, day)`. Join present cards; layer `"scene"`. None if nothing present.
- [ ] **Step 4 — run, pass; full suite green.**
- [ ] **Step 5 — commit:** `git add systems/character.py tests/systems/test_character.py && git commit -m "feat(systems): CharacterSystem.inject — present-character cards (current facts)"`

---

## Done criteria for S1c
- Full suite green.
- Persons round-trip through events→shared graph as bitemporal facts; minimal "纯粹之人" creation passes validation (no facet pressure); evolution supersedes with history intact; inject surfaces present-character current cards.
- No game logic in `kernel/`.

**Next:** S1d 物品 (Object slice: items as Object entities + `held_by`/`located_in` + `item_transferred`), then S1e 势力 (templated Faction), S1f 认知 (knows/believes + audience + broadcast/endowment).
