# S1e: 认知 (Knowledge) System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. `- [ ]` steps, TDD.

**Goal:** A registered `KnowledgeSystem` modeling information asymmetry: **knows = bitemporal facts** on the knower (`knows:{fact_key}` → believed value), granted by three ops (told / broadcast / endowment), with audience resolution over factions (S1d `members_of`) and places (occupants). This is the DATA + grant mechanisms + query helpers; the POV/guardrail *writing* model is assembled later in S3.

**Architecture & model (read carefully):**
- A knower's knowledge of a topic is a Fact `(subject=knower, predicate="knows:{fact_key}", value=believed_value)`. Bitemporal supersession (from S1a) gives point-in-time belief + re-learning for free. **Stale/false belief = a `knows` fact whose value differs from the shared-graph truth** — no separate `believes` structure needed (one mechanism).
- **`fact_key`** is a stable topic id the LLM/systems choose, e.g. `"桥.status"` or `"反派身份"`. Knowing is acquired-state (sticky), never auto-derived. **Public facts are NOT modeled** (no grant → assume-known by all; the assembler treats unmodeled as public).
- **Three grant ops** (declared in the `knowledge` commit section, each item carries `op`):
  - `told` `{knower, fact_key, value, via}` → ONE `knowledge_set` event.
  - `endowment` `{knower, grants:[{fact_key,value}]}` → one `knowledge_set` per grant (batch grant; the LLM *judgement* of what a new character plausibly knows is an S4 concern — here it's just the batch-grant mechanism).
  - `broadcast` `{fact_key, value, audience}` → ONE `knowledge_broadcast` event (audience spec in deltas). **apply resolves the audience at the event's day** (because resolution needs the graph): `audience={faction, min_rank?, group?}` → `systems.faction.members_of(...)`; `audience={place}` → occupants = entities with a current `located_in`→place relation. Each resolved knower gets a `knows` fact. (Containment-subtree place audience is DEFERRED — direct occupants only.)
- **Events:** `knowledge_set` (atomic: one knower learns one fact_key=value) and `knowledge_broadcast` (audience-resolved at apply). **Section:** `knowledge`.

**Architecture pattern:** same as other systems (own events/sections; write shared graph `world["systems"]["ontology"]`; require `OntologySystem` registered; for broadcast also requires `FactionSystem`'s helpers — import `from systems.faction import members_of`).

**Tech Stack:** Python 3.12 stdlib; reuses S0 + S1a–d. Offline tests, `python3 -m pytest -q --ignore=tests/test_embed_real.py`.

**Conventions/Guardrails:** TDD per task; `get_logger("systems.knowledge")`; commit per task; `python3`. Never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; do NOT touch `kernel/` or other systems. Create only `systems/knowledge.py` + `tests/systems/test_knowledge.py`.

---

## Task 1: `KnowledgeSystem` core — `knowledge_set` + queries

`KnowledgeSystem`: `name="knowledge"`; `event_types={"knowledge_set","knowledge_broadcast"}`; `commit_sections={"knowledge"}`; `empty_state()={}`.
- apply `knowledge_set` deltas `{knower, fact_key, value, via?}` → `g.assert_fact(knower, f"knows:{fact_key}", value, day,turn,source_event)`.
- Module query fns: `knows(graph, knower, fact_key, day) -> value|None` (= `value_at(knower, f"knows:{fact_key}", day)`); `knowers_of(graph, fact_key, day) -> list[str]` (knowers whose `knows:{fact_key}` is current/valid at day).

- [ ] **Step 1 — failing tests** `tests/systems/test_knowledge.py` (register OntologySystem+KnowledgeSystem, plus CharacterSystem/FactionSystem where needed): a `knowledge_set` makes the knower's `knows:桥.status` fact (`knows(g,knower,"桥.status",day)==value`); re-learning a different value supersedes (point-in-time: old day old belief, new day new belief = stale-belief works); `knows` returns None when ungranted; `knowers_of(g,"桥.status",day)` lists current knowers; `validate("knowledge",...)` flags a `told` whose `knower` entity is missing → `dangling_ref`; `to_events` maps a `told` item to one `knowledge_set` and an `endowment` item to N `knowledge_set`s.
- [ ] **Step 2 — fail. Step 3 — implement** core (`apply` for knowledge_set; `to_events` for `told`+`endowment`; `validate` ref checks; query fns). Defer broadcast to_events/apply to Task 2 (leave a clear TODO that Task 2 fills). **Step 4 — pass + full suite green. Step 5 — commit** `git add systems/knowledge.py tests/systems/test_knowledge.py && git commit -m "feat(systems): KnowledgeSystem core — knows-as-facts + told/endowment grants"`

---

## Task 2: Broadcast + audience resolution

- apply `knowledge_broadcast` deltas `{fact_key, value, audience}` → resolve audience at `event["day"]`:
  - if `audience` has `faction`: `members = members_of(g, audience["faction"], day, min_rank=audience.get("min_rank"), group=audience.get("group"))`.
  - elif `audience` has `place`: `members = [e for e in <entities> if place in g.neighbors(e, "located_in", day)]` (direct occupants; subtree DEFERRED).
  - for each member: `g.assert_fact(member, f"knows:{fact_key}", value, day, turn, source_event)`.
- `to_events`: a `broadcast` item `{fact_key,value,audience}` → ONE `knowledge_broadcast` event (audience in deltas).

- [ ] **Step 1 — failing tests:** set up a faction with 3 members at ranks; `broadcast` with `audience={faction:F, min_rank:"资深"}` → only senior members end up knowing (`knows(g,member,fact_key,day)` set for seniors, None for juniors). A `place` audience: two entities `located_in` 王都, one elsewhere → only the two occupants learn it. `to_events` produces a single `knowledge_broadcast`.
- [ ] **Step 2 — fail. Step 3 — implement** broadcast apply (import `members_of` from `systems.faction`; for place occupants, iterate `g.entities` and check `located_in`). **Step 4 — pass + full suite green. Step 5 — commit** `git add systems/knowledge.py tests/systems/test_knowledge.py && git commit -m "feat(systems): KnowledgeSystem broadcast — faction/place audience resolution"`

---

## Done criteria for S1e (and S1 complete)
- Full suite green. knows-as-facts round-trips (told/endowment/broadcast); stale belief = divergent `knows` value (point-in-time correct); audience resolves over faction(rank/group) + place occupants; `knows`/`knowers_of` queries work; public facts simply ungranted.
- No game logic in `kernel/`. All five S1 systems (ontology/place/character/object/faction/knowledge) registerable on the kernel.

**Next (S1 boundary):** holistic review of the systems layer, then S2 (recall + scoring + reflection — build offline with engine `FakeEmbedder`; real fastembed deferred).
