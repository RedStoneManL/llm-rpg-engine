# S1a: Fact-Graph Substrate + Kernel `apply(world)` Refinement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`. TDD throughout.

**Goal:** Build the shared bitemporal fact-graph substrate (`facts/`) that every S1 content system reads/writes, exposed through a registered `OntologySystem`, and refine the kernel so a system's `apply` can write the shared graph.

**Architecture:** One shared `FactGraph` (entities + typed relation edges + bitemporal facts with supersession + point-in-time queries) lives as the slice of a registered `OntologySystem`. Other systems reach it via `world["systems"]["ontology"]`. The kernel stays game-logic-free: it only changes `apply` to receive the whole `world` (so any system can read/write the shared graph + meta) instead of just its own slice. Pure stdlib — **no networkx** (small-graph algorithms are hand-rolled later; networkx deferred until a real algorithm needs it).

**Tech Stack:** Python 3.12, stdlib + dataclasses. Tests offline with `python3 -m pytest -q`. Reuses kernel from S0.

**Conventions:** TDD per task; `engine.log.get_logger("<pkg>.<mod>")` in each module; commit per task; interpreter `python3` (no venv/`python`). **Git guardrails:** never `git init`/`rm -rf .git`/`checkout --orphan`; never delete/modify `_legacy/` or `docs/`; minimal edits to existing files.

---

## File Structure

- `kernel/projection.py` (modify) — `project` passes `world` to `apply`; `empty_world` unchanged in shape.
- `kernel/contextsystem.py` (modify) — `apply(self, world, event)` signature + docstring.
- `tests/kernel/fakes.py` (modify) — `FakeNoteSystem.apply` reads its slice from `world`.
- `tests/kernel/test_projection.py` (modify) — assertions read `world["systems"]["notes"]`.
- `facts/__init__.py` (new) — package marker.
- `facts/entity.py` (new) — `Entity` dataclass (id, etype, tier, attrs).
- `facts/fact.py` (new) — `Fact` dataclass (bitemporal) + `Relation` dataclass (bitemporal edge).
- `facts/graph.py` (new) — `FactGraph`: entity CRUD + tier; `assert_fact`/`current_facts`/`facts_at`/`fact_history`; `add_relation`/`relations_at`/`neighbors`.
- `systems/__init__.py` (new) — package marker.
- `systems/ontology.py` (new) — `OntologySystem(ContextSystem)`: slice is a `FactGraph`; owns generic events; apply/validate/to_events/inject/recall.
- Tests: `tests/facts/__init__.py`, `tests/facts/test_entity.py`, `tests/facts/test_fact.py`, `tests/facts/test_graph.py`, `tests/systems/__init__.py`, `tests/systems/test_ontology.py`.

---

## Task 1: Kernel `apply(world, event)` refinement

**Files:** modify `kernel/contextsystem.py`, `kernel/projection.py`, `tests/kernel/fakes.py`, `tests/kernel/test_projection.py`.

- [ ] **Step 1 — update the failing tests first.** In `tests/kernel/test_projection.py`, the routing test must assert via the world. Change the body of `test_project_routes_events_to_owner_and_tracks_meta` and `test_project_skips_retracted_and_ignores_unowned_types` so any `state` reference becomes `world["systems"]["notes"]`. (They already read `w["systems"]["notes"]["notes"]` — confirm; if a helper used the bare slice, fix it.) Add one new test:
```python
def test_apply_receives_full_world_so_systems_can_reach_meta():
    r = _reg()
    w = project(r, [kernel_event("note_added", day=3, scene="s9", summary="x")])
    # apply saw meta-bearing world; note stored in its slice
    assert w["systems"]["notes"]["notes"] == ["x"] and w["meta"]["day"] == 3
```

- [ ] **Step 2 — run, expect fail** (FakeNoteSystem.apply still takes the slice): `python3 -m pytest tests/kernel/test_projection.py -q` → FAIL.

- [ ] **Step 3 — change the contract.** In `kernel/contextsystem.py`, change `def apply(self, state: Any, event: dict) -> None:` to:
```python
    def apply(self, world: dict, event: dict) -> None:
        """Fold one owned event into the world. The system's own slice is
        world["systems"][self.name]; the shared fact-graph (if present) is
        world["systems"]["ontology"]. Mutate in place. Must be total over
        already-validated events."""
```

- [ ] **Step 4 — update the driver.** In `kernel/projection.py` `project`, change the dispatch line from `owner.apply(world["systems"][owner.name], ev)` to `owner.apply(world, ev)`.

- [ ] **Step 5 — update the fake.** In `tests/kernel/fakes.py`, change `FakeNoteSystem.apply` to:
```python
    def apply(self, world, event):
        world["systems"][self.name]["notes"].append(event["summary"])
```

- [ ] **Step 6 — run kernel + full suite, expect green:** `python3 -m pytest tests/kernel/ -q` then `python3 -m pytest -q --ignore=tests/test_embed_real.py`. All pass.

- [ ] **Step 7 — commit:** `git add kernel/contextsystem.py kernel/projection.py tests/kernel/fakes.py tests/kernel/test_projection.py && git commit -m "refactor(kernel): apply(world, event) so systems can write the shared fact-graph"`

---

## Task 2: `Entity` and `Fact`/`Relation` dataclasses

**Files:** `facts/__init__.py`, `facts/entity.py`, `facts/fact.py`, `tests/facts/__init__.py`, `tests/facts/test_entity.py`, `tests/facts/test_fact.py`.

- [ ] **Step 1 — package markers:** `mkdir -p facts tests/facts && : > facts/__init__.py && : > tests/facts/__init__.py`

- [ ] **Step 2 — failing tests.** `tests/facts/test_entity.py`:
```python
from facts.entity import Entity

def test_entity_defaults_to_mentioned_tier():
    e = Entity(id="艾拉", etype="Person")
    assert e.tier == "mentioned" and e.attrs == {}

def test_entity_carries_type_tier_attrs():
    e = Entity(id="王都", etype="Place", tier="tracked", attrs={"level": 2})
    assert e.etype == "Place" and e.tier == "tracked" and e.attrs["level"] == 2
```
`tests/facts/test_fact.py`:
```python
from facts.fact import Fact, Relation

def test_fact_is_current_when_no_end():
    f = Fact(subject="艾拉", predicate="trust", value="中", event_time_start=1, ingest_turn=1, source_event="e1")
    assert f.is_current() is True
    f.event_time_end = 5
    assert f.is_current() is False

def test_fact_valid_at_respects_bitemporal_window():
    f = Fact(subject="桥", predicate="status", value="断", event_time_start=5, ingest_turn=9, source_event="e2")
    assert f.valid_at(4) is False and f.valid_at(5) is True and f.valid_at(99) is True
    f.event_time_end = 10
    assert f.valid_at(9) is True and f.valid_at(10) is False

def test_relation_is_bitemporal_like_fact():
    r = Relation(src="剑", rel="held_by", dst="艾拉", event_time_start=2, ingest_turn=2, source_event="e3")
    assert r.is_current() and r.valid_at(2) and not r.valid_at(1)
```

- [ ] **Step 3 — run, expect fail.**

- [ ] **Step 4 — implement `facts/entity.py`:**
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Entity:
    id: str
    etype: str                      # Person | Place | Object | Faction | Thread
    tier: str = "mentioned"         # tracked | mentioned | retired
    attrs: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 5 — implement `facts/fact.py`** (`Fact` and `Relation` share bitemporal logic; keep them separate dataclasses with the same two helpers to stay obvious):
```python
from __future__ import annotations
from dataclasses import dataclass

def _current(end): return end is None
def _valid_at(start, end, day): return day >= start and (end is None or day < end)

@dataclass
class Fact:
    subject: str
    predicate: str
    value: object
    event_time_start: int
    ingest_turn: int
    source_event: str
    event_time_end: int | None = None
    secrecy: str | None = None       # public | restricted | secret (None == unset)
    def is_current(self): return _current(self.event_time_end)
    def valid_at(self, day): return _valid_at(self.event_time_start, self.event_time_end, day)

@dataclass
class Relation:
    src: str
    rel: str                         # held_by | located_in | member_of | ...
    dst: str
    event_time_start: int
    ingest_turn: int
    source_event: str
    event_time_end: int | None = None
    def is_current(self): return _current(self.event_time_end)
    def valid_at(self, day): return _valid_at(self.event_time_start, self.event_time_end, day)
```

- [ ] **Step 6 — run, expect pass.**

- [ ] **Step 7 — commit:** `git add facts/__init__.py facts/entity.py facts/fact.py tests/facts/__init__.py tests/facts/test_entity.py tests/facts/test_fact.py && git commit -m "feat(facts): Entity + bitemporal Fact/Relation dataclasses"`

---

## Task 3: `FactGraph`

**Files:** `facts/graph.py`, `tests/facts/test_graph.py`.

`FactGraph` holds `entities: dict[str, Entity]`, `facts: list[Fact]`, `relations: list[Relation]`. Supersession: `assert_fact(subject, predicate, value, ...)` closes the prior **current** fact for that `(subject, predicate)` by setting its `event_time_end = day` before appending the new one.

- [ ] **Step 1 — failing tests** (`tests/facts/test_graph.py`):
```python
import pytest
from facts.graph import FactGraph
from facts.entity import Entity

def _g():
    g = FactGraph()
    g.add_entity("艾拉", "Person", tier="tracked")
    return g

def test_add_get_entity_and_set_tier():
    g = _g()
    assert g.get_entity("艾拉").tier == "tracked"
    g.set_tier("艾拉", "retired")
    assert g.get_entity("艾拉").tier == "retired"
    assert g.get_entity("nope") is None

def test_assert_fact_supersedes_prior_current():
    g = _g()
    g.assert_fact("艾拉", "trust", "中", day=1, turn=1, source_event="e1")
    g.assert_fact("艾拉", "trust", "依赖", day=5, turn=2, source_event="e2")
    cur = g.current_facts("艾拉")
    assert len(cur) == 1 and cur[0].value == "依赖"
    # history preserved, point-in-time intact
    assert g.value_at("艾拉", "trust", 1) == "中"
    assert g.value_at("艾拉", "trust", 5) == "依赖"
    assert len(g.fact_history("艾拉", "trust")) == 2

def test_different_predicates_coexist():
    g = _g()
    g.assert_fact("艾拉", "trust", "中", day=1, turn=1, source_event="e1")
    g.assert_fact("艾拉", "mood", "警惕", day=1, turn=1, source_event="e1")
    assert {f.predicate for f in g.current_facts("艾拉")} == {"trust", "mood"}

def test_relations_bitemporal_and_neighbors():
    g = _g(); g.add_entity("王都", "Place", tier="tracked")
    g.add_relation("艾拉", "located_in", "王都", day=2, turn=1, source_event="e3")
    assert g.neighbors("艾拉", "located_in", day=2) == ["王都"]
    assert g.neighbors("艾拉", "located_in", day=1) == []
    # moving supersedes the prior location
    g.add_entity("边境城", "Place")
    g.add_relation("艾拉", "located_in", "边境城", day=9, turn=2, source_event="e4")
    assert g.neighbors("艾拉", "located_in", day=9) == ["边境城"]
```

- [ ] **Step 2 — run, expect fail.**

- [ ] **Step 3 — implement `facts/graph.py`.** Required methods (write complete, no placeholders): `add_entity(id, etype, tier="mentioned", **attrs)`, `get_entity(id)`, `set_tier(id, tier)`, `assert_fact(subject, predicate, value, *, day, turn, source_event, secrecy=None)` (closes prior current same-(subject,predicate) by `event_time_end=day`, appends new `Fact`), `current_facts(subject)`, `value_at(subject, predicate, day)` (the value of the fact valid at `day`, else None), `fact_history(subject, predicate)`, `add_relation(src, rel, dst, *, day, turn, source_event)` (supersede prior current same-(src,rel) — i.e. single-valued relations like `located_in`; for multi-valued use the same method, dedupe by (src,rel,dst)), `relations_at(src, rel, day)`, `neighbors(src, rel, day)` (dst ids of relations valid at day). Add `get_logger("facts.graph")` debug lines at assert/supersede.

  NOTE for the implementer: keep `assert_fact`'s supersession **predicate-scoped** (a new `trust` doesn't close `mood`). For relations, supersede scoped to `(src, rel)` so a new `located_in` closes the old location (single-valued); `member_of` can hold multiple — but for S1a, single-supersede-per-(src,rel) is the spec; multi-valued relation handling is deferred to the system that needs it.

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — commit:** `git add facts/graph.py tests/facts/test_graph.py && git commit -m "feat(facts): FactGraph with bitemporal supersession + point-in-time queries"`

---

## Task 4: `OntologySystem` (registered)

**Files:** `systems/__init__.py`, `systems/ontology.py`, `tests/systems/__init__.py`, `tests/systems/test_ontology.py`.

`OntologySystem` is the registered ContextSystem whose **slice is a `FactGraph`**. It owns the generic event-types `entity_created`, `fact_asserted`, `relation_added`, `tier_changed`. Its `apply(world, event)` mutates `world["systems"]["ontology"]` (the graph). It owns the commit sections `entities`, `facts`, `relations` (declared in turn-commit), validates references (subject/src/dst must exist or be introduced this turn — for S1a, just: entity referenced by a fact/relation must already exist in the graph), converts sections to events via `to_events`, injects a compact "known entities" fragment, and recalls entities/facts matching a query substring.

- [ ] **Step 1 — failing tests** (`tests/systems/test_ontology.py`), covering: empty_state is a FactGraph; apply of `entity_created` + `fact_asserted` + `relation_added` mutates the graph; `tier_changed` updates tier; `validate` flags a `facts` decl whose subject entity doesn't exist; `to_events` turns a `facts` section into `fact_asserted` events; registration alongside the kernel works (`Registry().register(OntologySystem())`); end-to-end `project(registry, events)` builds a graph with the asserted facts. Use the kernel's `kernel_event`/`project`/`Registry`. Example core test:
```python
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from facts.graph import FactGraph

def test_project_builds_graph_from_ontology_events():
    r = Registry().register(OntologySystem())
    evs = [
        kernel_event("entity_created", day=1, scene="s1", summary="艾拉登场",
                     deltas={"id": "艾拉", "etype": "Person", "tier": "tracked"}),
        kernel_event("fact_asserted", day=1, scene="s1", summary="信任=中",
                     deltas={"subject": "艾拉", "predicate": "trust", "value": "中"}),
    ]
    w = project(r, evs)
    g = w["systems"]["ontology"]
    assert isinstance(g, FactGraph)
    assert g.get_entity("艾拉").tier == "tracked"
    assert g.value_at("艾拉", "trust", 1) == "中"
```
Add tests: `validate` returns a `dangling_ref` ValidationError when a `facts` decl references a missing subject; `to_events` shape; `tier_changed` apply; `inject` returns a Fragment listing tracked entities; `recall` finds an entity by substring.

- [ ] **Step 2 — run, expect fail.**

- [ ] **Step 3 — implement `systems/ontology.py`.** Complete implementation (no placeholders). Key shape:
```python
from __future__ import annotations
from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event
from facts.graph import FactGraph
from engine.log import get_logger

log = get_logger("systems.ontology")

class OntologySystem(ContextSystem):
    name = "ontology"
    def event_types(self): return {"entity_created", "fact_asserted", "relation_added", "tier_changed"}
    def commit_sections(self): return {"entities", "facts", "relations"}
    def empty_state(self): return FactGraph()

    def apply(self, world, event):
        g = world["systems"][self.name]; d = event.get("deltas", {})
        t = event["type"]
        if t == "entity_created":
            g.add_entity(d["id"], d["etype"], tier=d.get("tier", "mentioned"), **d.get("attrs", {}))
        elif t == "fact_asserted":
            g.assert_fact(d["subject"], d["predicate"], d["value"], day=event["day"],
                          turn=event.get("turn") or 0, source_event=event["id"], secrecy=d.get("secrecy"))
        elif t == "relation_added":
            g.add_relation(d["src"], d["rel"], d["dst"], day=event["day"],
                           turn=event.get("turn") or 0, source_event=event["id"])
        elif t == "tier_changed":
            g.set_tier(d["id"], d["tier"])

    def validate(self, section, decl, world):
        g = world.get("systems", {}).get(self.name)
        errs = []
        # entity refs in facts/relations must already exist OR be introduced in the same commit's "entities"
        ...  # implement: for section=="facts" check each item's subject exists in g (or in pending entities); etc.
        return errs

    def to_events(self, section, decl, *, turn, day, scene):
        out = []
        if section == "entities":
            for e in decl: out.append(kernel_event("entity_created", day=day, scene=scene,
                summary=f"{e['id']} 登场", deltas=e, turn=turn))
        elif section == "facts":
            for f in decl: out.append(kernel_event("fact_asserted", day=day, scene=scene,
                summary=f"{f['subject']}.{f['predicate']}={f['value']}", deltas=f, turn=turn))
        elif section == "relations":
            for r in decl: out.append(kernel_event("relation_added", day=day, scene=scene,
                summary=f"{r['src']} {r['rel']} {r['dst']}", deltas=r, turn=turn))
        return out

    def inject(self, scene, world):
        g = world.get("systems", {}).get(self.name)
        if not g: return None
        tracked = [e.id for e in g.entities.values() if e.tier == "tracked"]
        if not tracked: return None
        return Fragment("ontology", "scene", "已知实体: " + "、".join(tracked))

    def recall(self, query, world):
        g = world.get("systems", {}).get(self.name)
        if not g: return []
        hits = [RecallHit("ontology", 1.0, f"{e.id}({e.etype})") for e in g.entities.values() if query in e.id]
        return hits
```
The implementer must fully implement the `validate` body per the spec in Step 1's tests (dangling subject/src/dst → `ValidationError(section, field, "dangling_ref", hint)`, allowing entities introduced in the same turn-commit's `entities` section — read it from `world` is not possible, so for S1a validate the simple case: subject/src/dst must exist in the current graph; introduced-this-turn cross-section validation is deferred and noted).

- [ ] **Step 4 — run, expect pass.**

- [ ] **Step 5 — full suite green:** `python3 -m pytest -q --ignore=tests/test_embed_real.py`.

- [ ] **Step 6 — commit:** `git add systems/ tests/systems/ && git commit -m "feat(systems): OntologySystem — shared FactGraph as a registered ContextSystem"`

---

## Done criteria for S1a

- `python3 -m pytest -q --ignore=tests/test_embed_real.py` green.
- Kernel `apply(world, event)` lets systems write a shared graph; `OntologySystem` provides that graph and round-trips entities/facts/relations through events→projection.
- `facts/` substrate is pure stdlib, bitemporal, point-in-time-correct, no networkx.
- No game logic in `kernel/`; domain entity/fact logic lives in `facts/` + `systems/ontology.py`.

**Next:** S1b 地点 system (3-tier map: places as entities in the graph + adjacency/containment relations + navigate()), then 角色, 物品, 势力, 认知.
