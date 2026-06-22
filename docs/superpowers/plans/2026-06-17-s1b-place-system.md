# S1b: 地点 (Place) System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. `- [ ]` steps, TDD throughout.

**Goal:** A registered `PlaceSystem` that models the three-tier map as Place entities + containment/adjacency relations in the shared `FactGraph`, with the three primitives (create / link / materialize) + entity movement, plus `navigate()` pathfinding and an "exits" context fragment.

**Architecture (the system pattern, established here):** A content system owns its own event-types + commit-sections and, in `apply(world, event)`, **writes the shared graph** `world["systems"]["ontology"]` (a `FactGraph` from S1a). It therefore **requires `OntologySystem` to be registered**. Places are `Entity(etype="Place")` with attrs `level`/`kind`/`seed`/`detail`; containment = `contained_by` relations; adjacency = `adjacent_to` relations carrying a `travel_cost`; an actor's location = a single-valued `located_in` relation (the FactGraph already supersedes single-valued `(src,rel)` relations, so moving auto-closes the old location). Pure stdlib; `navigate()` is Dijkstra over adjacency (no networkx). LLM-driven decisions (when to materialize, cost ladder, location-staleness) belong to the S4 loop — this system only provides the mechanisms.

**Tech Stack:** Python 3.12 stdlib; reuses S0 kernel + S1a `facts/`/`OntologySystem`. Tests offline, `python3 -m pytest -q` (full suite: add `--ignore=tests/test_embed_real.py`).

**Conventions:** TDD per task; `get_logger("systems.place")`; commit per task; `python3`. **Git guardrails:** never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; minimal edits to existing files.

---

## File Structure
- `facts/fact.py` (modify) — add `attrs: dict` to `Relation` (for `travel_cost`); keep `Fact` unchanged.
- `facts/graph.py` (modify) — `add_relation(..., **attrs)` stores attrs on the `Relation`; add `relation_attrs_at(src, rel, day)` helper returning `[(dst, attrs)]`.
- `systems/place.py` (new) — `PlaceSystem` + module-level `navigate(graph, src, dst, day)`.
- `tests/facts/test_relation_attrs.py` (new) — relation attrs round-trip.
- `tests/systems/test_place.py` (new) — full PlaceSystem behavior.

---

## Task 1: Relation carries `attrs` (for travel_cost)

- [ ] **Step 1 — failing test** `tests/facts/test_relation_attrs.py`: build a `FactGraph`, `add_relation("A","adjacent_to","B", day=1, turn=1, source_event="e", travel_cost=2)`; assert `relation_attrs_at("A","adjacent_to",1) == [("B", {"travel_cost": 2})]`; assert a relation with no attrs yields `("X", {})`.
- [ ] **Step 2 — run, fail.**
- [ ] **Step 3 — implement:** in `facts/fact.py` add `attrs: dict = field(default_factory=dict)` to `Relation` (import `field`). In `facts/graph.py` `add_relation` add `**attrs` param and pass `attrs=dict(attrs)` into `Relation(...)`; add `relation_attrs_at(self, src, rel, day)` returning `[(r.dst, r.attrs) for r in self.relations_at(src, rel, day)]`.
- [ ] **Step 4 — run, pass.**
- [ ] **Step 5 — commit:** `git add facts/fact.py facts/graph.py tests/facts/test_relation_attrs.py && git commit -m "feat(facts): relations carry attrs (travel_cost)"`

---

## Task 2: `PlaceSystem` core (events, apply, validate, to_events)

`PlaceSystem(ContextSystem)`: `name="place"`; `event_types={"place_created","place_linked","place_materialized","entity_moved"}`; `commit_sections={"places","moves"}`; `empty_state()` returns `{}` (places live in the shared graph). All `apply` paths read `g = world["systems"]["ontology"]`.

- [ ] **Step 1 — failing tests** `tests/systems/test_place.py` (register BOTH systems: `Registry().register(OntologySystem()).register(PlaceSystem())`). Cover, via `project(...)` over `kernel_event`s:
  - `place_created` (deltas `{id,level,kind,seed,tier,detail,parent}`) creates a `Place` entity with those attrs and, when `parent` given, a `contained_by` relation child→parent.
  - `place_materialized` (deltas `{id}`) flips the entity's `attrs["detail"]` to `"full"`.
  - `place_linked` (deltas `{a,b,travel_cost}`) adds `adjacent_to` BOTH directions with the cost.
  - `entity_moved` (deltas `{who,to}`) adds `located_in`; a second move supersedes the first (point-in-time: `neighbors(who,"located_in",later_day)==[new]`).
  - `validate("places",[{...}],world)`: missing `id` → `ValidationError(code="missing")`; bad `level` (not 1/2/3) → `bad_enum`; `parent` that doesn't exist in graph → `dangling_ref` (cross-section deferred, same as ontology).
  - `validate("moves",[{who,to}],world)`: `to` place not in graph → `dangling_ref`.
  - `to_events("places",[...])` → `place_created` events; `to_events("moves",[...])` → `entity_moved` events.
- [ ] **Step 2 — run, fail.**
- [ ] **Step 3 — implement `systems/place.py`** (`apply` writes the shared graph; `place_created` sets attrs `level/kind/seed/detail` and optional `contained_by`; `place_linked` adds symmetric `adjacent_to` with `travel_cost` attr; `place_materialized` sets `detail="full"`; `entity_moved` adds `located_in`). `validate` per the tests (`KIND` allowed set = `{"settlement","wilderness","dungeon","venue","region"}`; levels `{1,2,3}`). `to_events` mirrors OntologySystem's style. Add `get_logger`. Document that the system requires `OntologySystem` registered.
- [ ] **Step 4 — run, pass; then full suite green.**
- [ ] **Step 5 — commit:** `git add systems/place.py tests/systems/test_place.py && git commit -m "feat(systems): PlaceSystem — places/containment/adjacency/movement on the shared graph"`

---

## Task 3: `navigate()` — Dijkstra over adjacency

- [ ] **Step 1 — failing tests** (in `tests/systems/test_place.py`): build a small map (王都—(1)—暗黑森林—(3)—边境城; 王都—(5)—边境城) via `place_linked`; `navigate(g, "王都", "边境城", day)` returns the least-cost path `{"path": ["王都","暗黑森林","边境城"], "total_cost": 4}` (4 < direct 5). Same node → `{"path":[x],"total_cost":0}`. Unreachable → `{"path": [], "total_cost": None}`.
- [ ] **Step 2 — run, fail.**
- [ ] **Step 3 — implement** module-level `navigate(graph, src, dst, day)` in `systems/place.py`: Dijkstra over `adjacent_to` edges using `graph.relation_attrs_at(node,"adjacent_to",day)` for neighbors+costs (default cost 1 if absent). Pure stdlib (`heapq`). (Multi-level containment ascend/descend routing is DEFERRED — note it; same-graph adjacency routing suffices for S1b.)
- [ ] **Step 4 — run, pass.**
- [ ] **Step 5 — commit:** `git add systems/place.py tests/systems/test_place.py && git commit -m "feat(systems): navigate() Dijkstra pathfinding over adjacency"`

---

## Task 4: `inject()` — current location + 出口表

- [ ] **Step 1 — failing tests:** with the protagonist `located_in` 王都 and 王都 adjacent to 暗黑森林(1)/边境城(5), `PlaceSystem().inject(scene={"protagonist":"主角","day":D}, world)` returns a `Fragment(system="place", layer="scene", ...)` whose text names the current place 王都 and lists exits "暗黑森林(1日)"、"边境城(5日)". If protagonist has no location → returns `None`.
- [ ] **Step 2 — run, fail.**
- [ ] **Step 3 — implement** `PlaceSystem.inject(self, scene, world)`: read `g=world["systems"]["ontology"]`, `who=scene.get("protagonist")`, `day=scene.get("day")`; `loc=g.neighbors(who,"located_in",day)`; if none → None; render current place (id + its `kind`) + exits from `g.relation_attrs_at(loc0,"adjacent_to",day)` as `名(cost日)`, joined. Layer `"scene"`. Affordance string listing the exits as `move` targets.
- [ ] **Step 4 — run, pass; full suite green.**
- [ ] **Step 5 — commit:** `git add systems/place.py tests/systems/test_place.py && git commit -m "feat(systems): PlaceSystem.inject — current location + exits affordance"`

---

## Done criteria for S1b
- Full suite green (`--ignore=tests/test_embed_real.py`).
- Places + containment + adjacency(+cost) + movement round-trip through events→shared graph; `navigate()` finds least-cost paths; `inject()` surfaces current location + exits.
- No game logic in `kernel/`; PlaceSystem writes only the shared graph + its own (empty) slice.

**Next:** S1c 角色 (Character) system — Persons as graph entities with the anti-脸谱 prose-primary card + evolution facts; then 物品/势力/认知.
