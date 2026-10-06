# S0 Microkernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the event-sourced microkernel: a `ContextSystem` registry + five registry-dispatched drivers (projection / validation / assembler / recall / digest) + a turn-commit envelope + an observability layer (Langfuse tracing + debug dump) — the foundation every S1 system plugs into.

**Architecture:** The kernel owns only generic mechanism and routing; it holds no game logic. Each `ContextSystem` declares the event-types and turn-commit sections it owns, plus hooks (`apply`/`validate`/`to_events`/`inject`/`recall`/`digest_extract`). Drivers fan out / route to the owning system by event-type or section. Reuses the existing `engine/` primitives (`EventStore`, `engine.recall`, `engine.log`) rather than rebuilding them; `engine/projection.py`'s domain logic stays put and is migrated into systems during S1.

**Tech Stack:** Python 3.12, stdlib + `dataclasses` + `typing`; `langfuse` (optional, lazy-imported, no-op fallback offline); pytest. Every kernel module is offline-testable with a deterministic `FakeNoteSystem`; no network in tests.

**Conventions (mandatory):** TDD per task; every module gets a `get_logger("kernel.<mod>")` debug logger (mirrors `engine/log.py`); tests use `from kernel.X import Y`; the autouse `_hermetic_rpg_env` fixture in `tests/conftest.py` already clears `RPG_*`. Git guardrails: never `git init` / `rm -rf .git` / `checkout --orphan`; never delete `_legacy/` or `docs/`; minimal incremental edits to existing files.

---

## File Structure

New package `kernel/` (sibling of `engine/`):

- `kernel/__init__.py` — empty package marker.
- `kernel/contextsystem.py` — `ContextSystem` base class + data types (`ValidationError`, `Fragment`, `RecallHit`). The contract every system implements.
- `kernel/turncommit.py` — `TurnCommit` envelope (narration + per-section declarations).
- `kernel/registry.py` — `Registry`: holds systems, maps event-type→system and commit-section→system, rejects collisions.
- `kernel/events.py` — `kernel_event(...)` (registry-agnostic event builder) + `open_store(registry, ...)` (an `EventStore` whose allowed event-types come from the registry, not the closed frozenset).
- `kernel/projection.py` — `project(registry, events)`: generic fold dispatching `apply` to owners.
- `kernel/validation.py` — `validate_commit(registry, commit, world)` + `build_repair_request(errors)`.
- `kernel/assembler.py` — `assemble(registry, scene, world)`: layered (stable→scene→volatile) `inject` fan-out.
- `kernel/recall.py` — `recall(registry, query, world, k)`: `recall` fan-out + score-sort.
- `kernel/digest.py` — `digest_extract(registry, prose, world)`: `digest_extract` fan-out → `TurnCommit`.
- `kernel/observability.py` — `get_tracer()` (`LangfuseTracer` | `NoopTracer`) + `dump(label, payload)` debug helper.

Edited existing files (minimal, backward-compatible):
- `engine/schema.py` — `validate_event(ev, allowed_types=None)` gains an optional override.
- `engine/store.py` — `EventStore(..., allowed_types=None)` threads the override into `append`.
- `requirements.txt` — add `langfuse`.

Tests under `tests/kernel/`:
- `tests/kernel/__init__.py`, `tests/kernel/fakes.py` (the `FakeNoteSystem`), and one `test_*.py` per module above.

---

## Task 1: Scaffold `kernel/` package + core data types + turn-commit

**Files:**
- Create: `kernel/__init__.py` (empty)
- Create: `kernel/contextsystem.py`
- Create: `kernel/turncommit.py`
- Create: `tests/kernel/__init__.py` (empty)
- Test: `tests/kernel/test_contextsystem.py`

- [ ] **Step 1: Create empty package markers**

```bash
mkdir -p kernel tests/kernel
: > kernel/__init__.py
: > tests/kernel/__init__.py
```

- [ ] **Step 2: Write the failing test**

`tests/kernel/test_contextsystem.py`:
```python
from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.turncommit import TurnCommit


def test_base_system_defaults_are_inert():
    s = ContextSystem()
    assert s.event_types() == set()
    assert s.commit_sections() == set()
    assert s.empty_state() == {}
    assert s.validate("x", None, {}) == []
    assert s.to_events("x", None, turn=1, day=1, scene="s1") == []
    assert s.inject({}, {}) is None
    assert s.recall("q", {}) == []
    assert s.digest_extract("prose", {}) == {}


def test_dataclasses_carry_fields():
    e = ValidationError(section="cast", field="[0].who", code="missing", hint="needs who")
    assert e.section == "cast" and e.code == "missing"
    f = Fragment(system="notes", layer="scene", text="hi", affordance="can note")
    assert f.layer == "scene" and f.affordance == "can note"
    h = RecallHit(system="notes", score=0.9, text="t", ref={"id": 1})
    assert h.score == 0.9 and h.ref["id"] == 1


def test_turncommit_from_dict_splits_narration_and_sections():
    tc = TurnCommit.from_dict({"narration": "你推开门", "cast": [{"who": "Ela"}]})
    assert tc.narration == "你推开门"
    assert tc.sections == {"cast": [{"who": "Ela"}]}
    assert TurnCommit().sections == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_contextsystem.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.contextsystem'`

- [ ] **Step 4: Implement `kernel/contextsystem.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.log import get_logger

log = get_logger("kernel.contextsystem")


@dataclass
class ValidationError:
    section: str          # turn-commit section the error is in
    field: str            # dotted path within the section, e.g. "[0].who"
    code: str             # "missing" | "dangling_ref" | "bad_enum" | "unknown_section" | ...
    hint: str             # preset, LLM-facing repair instruction


@dataclass
class Fragment:
    """A system's contribution to the assembled context."""
    system: str
    layer: str            # "stable" | "scene" | "volatile"
    text: str             # rendered context text
    affordance: str = ""  # "what you can declare this turn"


@dataclass
class RecallHit:
    system: str
    score: float
    text: str
    ref: dict = field(default_factory=dict)


class ContextSystem:
    """Base contract for a pluggable system. Subclasses override only what they need;
    every hook has an inert default so the kernel can call it unconditionally."""

    name: str = "unnamed"

    # --- ownership declarations -------------------------------------------
    def event_types(self) -> set[str]:
        return set()

    def commit_sections(self) -> set[str]:
        return set()

    # --- projection -------------------------------------------------------
    def empty_state(self) -> Any:
        return {}

    def apply(self, state: Any, event: dict) -> None:
        """Fold one owned event into this system's state slice (mutate in place)."""

    # --- write path (turn-commit -> events) -------------------------------
    def validate(self, section: str, decl: Any, world: dict) -> list[ValidationError]:
        return []

    def to_events(self, section: str, decl: Any, *, turn: int, day: int, scene: str) -> list[dict]:
        return []

    # --- read path --------------------------------------------------------
    def inject(self, scene: dict, world: dict) -> Fragment | None:
        return None

    def recall(self, query: str, world: dict) -> list[RecallHit]:
        return []

    # --- digest (strategy 乙) --------------------------------------------
    def digest_extract(self, prose: str, world: dict) -> dict:
        """Return {section_name: decl} extracted from narration prose."""
        return {}
```

- [ ] **Step 5: Implement `kernel/turncommit.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnCommit:
    """The structured output of a turn. `narration` is the player-facing prose;
    `sections` maps each owning system's section name to its declaration."""
    narration: str = ""
    sections: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "TurnCommit":
        d = dict(d)
        narration = d.pop("narration", "")
        return cls(narration=narration, sections=d)

    def to_dict(self) -> dict:
        return {"narration": self.narration, **self.sections}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_contextsystem.py -q`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git -C /root/rpg-engine-app add kernel/__init__.py kernel/contextsystem.py kernel/turncommit.py tests/kernel/__init__.py tests/kernel/test_contextsystem.py
git -C /root/rpg-engine-app commit -m "feat(kernel): ContextSystem contract + TurnCommit envelope"
```

---

## Task 2: Decentralize event-type validation (reuse EventStore)

The kernel lets each system declare its own event-types, so the closed `EVENT_TYPES` frozenset must become an optional override. Backward-compatible: existing callers pass nothing and still get the frozenset.

**Files:**
- Modify: `engine/schema.py` (the `validate_event` signature + the type check)
- Modify: `engine/store.py` (`EventStore.__init__` + `append`)
- Test: `tests/kernel/test_events.py`
- Create: `kernel/events.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_events.py`:
```python
import pytest

from engine.schema import validate_event
from kernel.events import kernel_event, open_store


def test_validate_event_accepts_custom_types():
    ev = kernel_event("place_created", day=1, scene="s1", summary="王都·酒馆")
    # default frozenset rejects the new type
    with pytest.raises(ValueError):
        validate_event(ev)
    # but an explicit allow-set accepts it
    validate_event(ev, allowed_types={"place_created"})


def test_open_store_appends_registry_typed_events(tmp_path):
    store = open_store(tmp_path / "events.db", tmp_path / "events.jsonl",
                       allowed_types={"place_created", "note_added"})
    seq = store.append(kernel_event("place_created", day=1, scene="s1", summary="王都"))
    assert seq == 1
    got = list(store.iter_events())
    assert got[0]["type"] == "place_created" and got[0]["summary"] == "王都"
    store.close()


def test_open_store_still_rejects_unknown_type(tmp_path):
    store = open_store(tmp_path / "e.db", tmp_path / "e.jsonl", allowed_types={"place_created"})
    with pytest.raises(ValueError):
        store.append(kernel_event("not_registered", day=1, scene="s1", summary="x"))
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_events.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.events'`

- [ ] **Step 3: Edit `engine/schema.py`** — make the type check overridable

Replace the `validate_event` function (lines 28-39) with:
```python
def validate_event(ev, allowed_types=None):
    types = EVENT_TYPES if allowed_types is None else allowed_types
    for k in _REQUIRED:
        if k not in ev:
            raise ValueError(f"event missing required field: {k}")
    if ev["type"] not in types:
        raise ValueError(f"unknown event type: {ev['type']!r}")
    if not isinstance(ev["day"], int):
        raise ValueError("day must be an int")
    if not isinstance(ev["actors"], list):
        raise ValueError("actors must be a list")
    if not str(ev.get("summary", "")).strip():
        raise ValueError("summary must be a non-empty string")
```

- [ ] **Step 4: Edit `engine/store.py`** — thread `allowed_types` through

In `EventStore.__init__` (line 25) change the signature and store the attribute:
```python
    def __init__(self, db_path, jsonl_path, allowed_types=None):
        self.db_path = Path(db_path)
        self.jsonl_path = Path(jsonl_path)
        self.allowed_types = allowed_types
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
```
In `append` (line 35) change the validate call:
```python
        validate_event(ev, self.allowed_types)
```

- [ ] **Step 5: Implement `kernel/events.py`**

```python
from __future__ import annotations

import uuid

from engine.store import EventStore


def kernel_event(type, day, scene, summary, *, actors=None, deltas=None,
                 thread_refs=None, chunk_ids=None, secrecy=None, roll=None,
                 turn=None, id=None) -> dict:
    """Build an event dict without the closed-set check (the store enforces the
    registry's allow-set instead). Same shape as engine.schema.make_event."""
    return {
        "id": id or f"ev_{uuid.uuid4().hex[:12]}",
        "type": type, "day": day, "scene": scene, "arc": None,
        "actors": list(actors or []), "summary": summary,
        "deltas": dict(deltas or {}), "thread_refs": list(thread_refs or []),
        "chunk_ids": list(chunk_ids or []), "secrecy": secrecy, "roll": roll,
        "turn": turn, "retracted": False,
    }


def open_store(db_path, jsonl_path, allowed_types) -> EventStore:
    """An EventStore that accepts exactly the registry's declared event-types."""
    return EventStore(db_path, jsonl_path, allowed_types=set(allowed_types))
```

- [ ] **Step 6: Run kernel tests AND the existing suite (no regression)**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_events.py tests/test_store.py tests/test_schema.py -q`
Expected: PASS (kernel events pass; existing store/schema tests still pass — the override defaults to the frozenset).

- [ ] **Step 7: Commit**

```bash
git -C /root/rpg-engine-app add engine/schema.py engine/store.py kernel/events.py tests/kernel/test_events.py
git -C /root/rpg-engine-app commit -m "feat(kernel): registry-overridable event-type validation + kernel_event/open_store"
```

---

## Task 3: The `Registry`

**Files:**
- Create: `kernel/registry.py`
- Test: `tests/kernel/test_registry.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_registry.py`:
```python
import pytest

from kernel.contextsystem import ContextSystem
from kernel.registry import Registry


class _A(ContextSystem):
    name = "a"
    def event_types(self): return {"a_made"}
    def commit_sections(self): return {"a"}


class _B(ContextSystem):
    name = "b"
    def event_types(self): return {"b_made"}
    def commit_sections(self): return {"b"}


class _CollideEvent(ContextSystem):
    name = "c"
    def event_types(self): return {"a_made"}  # collides with _A


def test_register_and_lookup():
    r = Registry().register(_A()).register(_B())
    assert {s.name for s in r.systems} == {"a", "b"}
    assert r.event_types() == {"a_made", "b_made"}
    assert r.owner_of_event("a_made").name == "a"
    assert r.owner_of_section("b").name == "b"
    assert r.owner_of_event("nope") is None
    assert r.owner_of_section("nope") is None


def test_event_type_collision_rejected():
    r = Registry().register(_A())
    with pytest.raises(ValueError, match="a_made"):
        r.register(_CollideEvent())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.registry'`

- [ ] **Step 3: Implement `kernel/registry.py`**

```python
from __future__ import annotations

from kernel.contextsystem import ContextSystem
from engine.log import get_logger

log = get_logger("kernel.registry")


class Registry:
    """Holds registered ContextSystems and routes by event-type / commit-section.
    Each event-type and each commit-section may have exactly one owner."""

    def __init__(self):
        self._systems: list[ContextSystem] = []
        self._by_event: dict[str, ContextSystem] = {}
        self._by_section: dict[str, ContextSystem] = {}

    def register(self, system: ContextSystem) -> "Registry":
        for et in system.event_types():
            if et in self._by_event:
                raise ValueError(
                    f"event type {et!r} already owned by {self._by_event[et].name!r}")
            self._by_event[et] = system
        for sec in system.commit_sections():
            if sec in self._by_section:
                raise ValueError(
                    f"commit section {sec!r} already owned by {self._by_section[sec].name!r}")
            self._by_section[sec] = system
        self._systems.append(system)
        log.debug("registered system=%s events=%s sections=%s",
                  system.name, sorted(system.event_types()), sorted(system.commit_sections()))
        return self

    @property
    def systems(self) -> list[ContextSystem]:
        return list(self._systems)

    def event_types(self) -> set[str]:
        return set(self._by_event)

    def owner_of_event(self, etype: str) -> ContextSystem | None:
        return self._by_event.get(etype)

    def owner_of_section(self, section: str) -> ContextSystem | None:
        return self._by_section.get(section)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_registry.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git -C /root/rpg-engine-app add kernel/registry.py tests/kernel/test_registry.py
git -C /root/rpg-engine-app commit -m "feat(kernel): Registry with event-type/section routing + collision guard"
```

---

## Task 4: `FakeNoteSystem` test fixture

A minimal real `ContextSystem` used to exercise every driver. Lives in tests (it is not production code).

**Files:**
- Create: `tests/kernel/fakes.py`
- Test: `tests/kernel/test_fakes.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_fakes.py`:
```python
from kernel.registry import Registry
from tests.kernel.fakes import FakeNoteSystem


def test_fake_note_system_roundtrips_through_registry():
    s = FakeNoteSystem()
    r = Registry().register(s)
    assert r.owner_of_event("note_added") is s
    assert r.owner_of_section("notes") is s
    state = s.empty_state()
    evs = s.to_events("notes", [{"text": "门开了"}], turn=1, day=1, scene="s1")
    assert evs[0]["type"] == "note_added"
    s.apply(state, evs[0])
    assert state["notes"] == ["门开了"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_fakes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.kernel.fakes'`

- [ ] **Step 3: Implement `tests/kernel/fakes.py`**

```python
from __future__ import annotations

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event


class FakeNoteSystem(ContextSystem):
    """A toy system owning a 'notes' section / 'note_added' event. Each note is
    a {'text': str}. Used to drive kernel tests with no game logic."""

    name = "notes"

    def event_types(self): return {"note_added"}
    def commit_sections(self): return {"notes"}
    def empty_state(self): return {"notes": []}

    def apply(self, state, event):
        state["notes"].append(event["summary"])

    def validate(self, section, decl, world):
        errs = []
        for i, n in enumerate(decl or []):
            if not (isinstance(n, dict) and str(n.get("text", "")).strip()):
                errs.append(ValidationError("notes", f"[{i}].text", "missing",
                                            "每条 note 需要非空 text"))
        return errs

    def to_events(self, section, decl, *, turn, day, scene):
        return [kernel_event("note_added", day, scene, n["text"], turn=turn)
                for n in (decl or [])]

    def inject(self, scene, world):
        notes = world.get("systems", {}).get("notes", {}).get("notes", [])
        return Fragment("notes", "scene", "Notes: " + "; ".join(notes),
                        affordance="notes:[{text}] — 记一条便签")

    def recall(self, query, world):
        notes = world.get("systems", {}).get("notes", {}).get("notes", [])
        return [RecallHit("notes", 1.0, n) for n in notes if query in n]

    def digest_extract(self, prose, world):
        return {"notes": [{"text": prose[:24]}]} if prose.strip() else {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_fakes.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git -C /root/rpg-engine-app add tests/kernel/fakes.py tests/kernel/test_fakes.py
git -C /root/rpg-engine-app commit -m "test(kernel): FakeNoteSystem driver fixture"
```

---

## Task 5: Projection driver

Generic fold: maintain kernel-level `meta` (day/scene/timeline) and dispatch each event to its owner's `apply`.

**Files:**
- Create: `kernel/projection.py`
- Test: `tests/kernel/test_projection.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_projection.py`:
```python
from kernel.registry import Registry
from kernel.events import kernel_event
from kernel.projection import project, empty_world
from tests.kernel.fakes import FakeNoteSystem


def _reg():
    return Registry().register(FakeNoteSystem())


def test_empty_world_has_meta_and_per_system_slices():
    w = empty_world(_reg())
    assert w["meta"]["day"] is None and w["meta"]["timeline"] == []
    assert w["systems"]["notes"] == {"notes": []}


def test_project_routes_events_to_owner_and_tracks_meta():
    r = _reg()
    evs = [
        kernel_event("note_added", day=1, scene="s1", summary="第一条"),
        kernel_event("note_added", day=2, scene="s2", summary="第二条"),
    ]
    w = project(r, evs)
    assert w["systems"]["notes"]["notes"] == ["第一条", "第二条"]
    assert w["meta"]["day"] == 2 and w["meta"]["scene"] == "s2"
    assert len(w["meta"]["timeline"]) == 2


def test_project_skips_retracted_and_ignores_unowned_types():
    r = _reg()
    e1 = kernel_event("note_added", day=1, scene="s1", summary="留")
    e2 = kernel_event("note_added", day=1, scene="s1", summary="撤"); e2["retracted"] = True
    e3 = kernel_event("orphan_type", day=1, scene="s1", summary="无主")  # no owner
    w = project(r, [e1, e2, e3])
    assert w["systems"]["notes"]["notes"] == ["留"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_projection.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.projection'`

- [ ] **Step 3: Implement `kernel/projection.py`**

```python
from __future__ import annotations

from kernel.registry import Registry
from engine.log import get_logger

log = get_logger("kernel.projection")


def empty_world(registry: Registry) -> dict:
    return {
        "meta": {"day": None, "scene": None, "timeline": []},
        "systems": {s.name: s.empty_state() for s in registry.systems},
    }


def project(registry: Registry, events) -> dict:
    """Fold events into a world: kernel-level meta + each system's slice."""
    world = empty_world(registry)
    n = 0
    for ev in events:
        if ev.get("retracted"):
            continue
        world["meta"]["day"] = ev["day"]
        world["meta"]["scene"] = ev["scene"]
        world["meta"]["timeline"].append(
            {"day": ev["day"], "scene": ev["scene"], "summary": ev["summary"]})
        owner = registry.owner_of_event(ev["type"])
        if owner is None:
            log.debug("no owner for event type=%s id=%s (ignored)", ev["type"], ev.get("id"))
            continue
        owner.apply(world["systems"][owner.name], ev)
        n += 1
    log.debug("project folded %d events across %d systems", n, len(registry.systems))
    return world
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_projection.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git -C /root/rpg-engine-app add kernel/projection.py tests/kernel/test_projection.py
git -C /root/rpg-engine-app commit -m "feat(kernel): registry-dispatched projection driver"
```

---

## Task 6: Validation driver + repair-request builder

Dispatch each turn-commit section to its owner's `validate`; an unowned section is itself an error. `build_repair_request` renders the preset hints for the LLM (the repair *loop* is S4; this is the pure pieces).

**Files:**
- Create: `kernel/validation.py`
- Test: `tests/kernel/test_validation.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_validation.py`:
```python
from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from kernel.validation import validate_commit, build_repair_request
from kernel.contextsystem import ValidationError
from tests.kernel.fakes import FakeNoteSystem


def _reg():
    return Registry().register(FakeNoteSystem())


def test_valid_commit_has_no_errors():
    tc = TurnCommit.from_dict({"narration": "x", "notes": [{"text": "ok"}]})
    assert validate_commit(_reg(), tc, world={}) == []


def test_missing_field_surfaces_owner_error():
    tc = TurnCommit.from_dict({"notes": [{"text": ""}, {"text": "good"}]})
    errs = validate_commit(_reg(), tc, world={})
    assert len(errs) == 1 and errs[0].code == "missing" and errs[0].field == "[0].text"


def test_unknown_section_is_an_error():
    tc = TurnCommit.from_dict({"weather": {"rain": True}})
    errs = validate_commit(_reg(), tc, world={})
    assert len(errs) == 1 and errs[0].code == "unknown_section" and errs[0].section == "weather"


def test_build_repair_request_renders_hints():
    errs = [ValidationError("notes", "[0].text", "missing", "每条 note 需要非空 text")]
    msg = build_repair_request(errs)
    assert "notes" in msg and "[0].text" in msg and "需要非空 text" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_validation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.validation'`

- [ ] **Step 3: Implement `kernel/validation.py`**

```python
from __future__ import annotations

from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from kernel.contextsystem import ValidationError
from engine.log import get_logger

log = get_logger("kernel.validation")


def validate_commit(registry: Registry, commit: TurnCommit, world: dict) -> list[ValidationError]:
    """Dispatch each section to its owning system. Unowned section => error."""
    errors: list[ValidationError] = []
    for section, decl in commit.sections.items():
        owner = registry.owner_of_section(section)
        if owner is None:
            errors.append(ValidationError(section, "", "unknown_section",
                                          f"没有系统拥有段 {section!r};删掉或改用已知段"))
            continue
        errors.extend(owner.validate(section, decl, world))
    log.debug("validate_commit sections=%d errors=%d", len(commit.sections), len(errors))
    return errors


def build_repair_request(errors: list[ValidationError]) -> str:
    """Render a compact, LLM-facing repair instruction grouped by section."""
    by_section: dict[str, list[ValidationError]] = {}
    for e in errors:
        by_section.setdefault(e.section, []).append(e)
    lines = ["turn-commit 校验未过,只修正以下字段后重发:"]
    for section, errs in by_section.items():
        lines.append(f"[{section}]")
        for e in errs:
            loc = f"{section}{e.field}" if e.field else section
            lines.append(f"  - {loc} ({e.code}): {e.hint}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_validation.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git -C /root/rpg-engine-app add kernel/validation.py tests/kernel/test_validation.py
git -C /root/rpg-engine-app commit -m "feat(kernel): validation driver + repair-request builder"
```

---

## Task 7: Context-assembler driver (layered fan-out)

Collect each system's `inject` fragment and order them stable→scene→volatile (the cache-friendly layering of spec §3.2).

**Files:**
- Create: `kernel/assembler.py`
- Test: `tests/kernel/test_assembler.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_assembler.py`:
```python
from kernel.registry import Registry
from kernel.contextsystem import ContextSystem, Fragment
from kernel.assembler import assemble, render
from tests.kernel.fakes import FakeNoteSystem


class _Stable(ContextSystem):
    name = "rules"
    def inject(self, scene, world):
        return Fragment("rules", "stable", "宪法", affordance="")


def test_assemble_orders_layers_stable_first():
    r = Registry().register(FakeNoteSystem()).register(_Stable())
    world = {"systems": {"notes": {"notes": ["n1"]}}}
    frags = assemble(r, scene={}, world=world)
    assert [f.layer for f in frags] == ["stable", "scene"]
    assert frags[0].system == "rules"


def test_systems_returning_none_are_skipped():
    r = Registry().register(ContextSystem())  # base inject() -> None
    assert assemble(r, scene={}, world={}) == []


def test_render_emits_layer_headers_and_affordances():
    r = Registry().register(FakeNoteSystem())
    world = {"systems": {"notes": {"notes": ["n1"]}}}
    text = render(assemble(r, scene={}, world=world))
    assert "Notes: n1" in text and "记一条便签" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_assembler.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.assembler'`

- [ ] **Step 3: Implement `kernel/assembler.py`**

```python
from __future__ import annotations

from kernel.registry import Registry
from kernel.contextsystem import Fragment
from engine.log import get_logger

log = get_logger("kernel.assembler")

LAYER_ORDER = ("stable", "scene", "volatile")


def assemble(registry: Registry, scene: dict, world: dict) -> list[Fragment]:
    """Gather each system's fragment, ordered stable->scene->volatile (cache-friendly)."""
    frags: list[Fragment] = []
    for s in registry.systems:
        f = s.inject(scene, world)
        if f is not None:
            frags.append(f)
    frags.sort(key=lambda f: LAYER_ORDER.index(f.layer) if f.layer in LAYER_ORDER else len(LAYER_ORDER))
    log.debug("assemble produced %d fragments", len(frags))
    return frags


def render(frags: list[Fragment]) -> str:
    """Flatten fragments to context text, grouped by layer, affordances appended."""
    out: list[str] = []
    last_layer = None
    affordances: list[str] = []
    for f in frags:
        if f.layer != last_layer:
            out.append(f"## [{f.layer}]")
            last_layer = f.layer
        out.append(f.text)
        if f.affordance:
            affordances.append(f.affordance)
    if affordances:
        out.append("## [affordance · 本轮可声明]")
        out.extend(affordances)
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_assembler.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git -C /root/rpg-engine-app add kernel/assembler.py tests/kernel/test_assembler.py
git -C /root/rpg-engine-app commit -m "feat(kernel): layered context-assembler driver"
```

---

## Task 8: Recall driver (fan-out + score-sort)

**Files:**
- Create: `kernel/recall.py`
- Test: `tests/kernel/test_recall.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_recall.py`:
```python
from kernel.registry import Registry
from kernel.contextsystem import ContextSystem, RecallHit
from kernel.recall import recall
from tests.kernel.fakes import FakeNoteSystem


class _Other(ContextSystem):
    name = "other"
    def recall(self, query, world):
        return [RecallHit("other", 0.5, "low"), RecallHit("other", 2.0, "high")]


def test_recall_fans_out_and_sorts_by_score_desc():
    r = Registry().register(FakeNoteSystem()).register(_Other())
    world = {"systems": {"notes": {"notes": ["匹配的门", "无关"]}}}
    hits = recall(r, query="门", world=world)
    assert hits[0].score == 2.0
    assert any(h.system == "notes" and "门" in h.text for h in hits)


def test_recall_k_truncates():
    r = Registry().register(_Other())
    assert len(recall(r, query="x", world={}, k=1)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_recall.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.recall'`

- [ ] **Step 3: Implement `kernel/recall.py`**

```python
from __future__ import annotations

from kernel.registry import Registry
from kernel.contextsystem import RecallHit
from engine.log import get_logger

log = get_logger("kernel.recall")


def recall(registry: Registry, query: str, world: dict, k: int | None = None) -> list[RecallHit]:
    """Fan out the query to every system's recall(), merge, sort by score desc."""
    hits: list[RecallHit] = []
    for s in registry.systems:
        hits.extend(s.recall(query, world))
    hits.sort(key=lambda h: h.score, reverse=True)
    log.debug("recall query=%r hits=%d k=%s", query, len(hits), k)
    return hits[:k] if k else hits
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_recall.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git -C /root/rpg-engine-app add kernel/recall.py tests/kernel/test_recall.py
git -C /root/rpg-engine-app commit -m "feat(kernel): recall fan-out driver"
```

---

## Task 9: Digest driver (prose → turn-commit, strategy 乙)

**Files:**
- Create: `kernel/digest.py`
- Test: `tests/kernel/test_digest.py`

- [ ] **Step 1: Write the failing test**

`tests/kernel/test_digest.py`:
```python
from kernel.registry import Registry
from kernel.digest import digest_extract
from kernel.turncommit import TurnCommit
from tests.kernel.fakes import FakeNoteSystem


def test_digest_merges_section_decls_from_systems():
    r = Registry().register(FakeNoteSystem())
    tc = digest_extract(r, prose="你推开门走了进去", world={})
    assert isinstance(tc, TurnCommit)
    assert tc.sections["notes"] == [{"text": "你推开门走了进去"}]


def test_digest_empty_prose_yields_no_sections():
    r = Registry().register(FakeNoteSystem())
    assert digest_extract(r, prose="   ", world={}).sections == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_digest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.digest'`

- [ ] **Step 3: Implement `kernel/digest.py`**

```python
from __future__ import annotations

from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from engine.log import get_logger

log = get_logger("kernel.digest")


def digest_extract(registry: Registry, prose: str, world: dict) -> TurnCommit:
    """Ask every system to extract its turn-commit sections from narration prose.
    (Used by strategy 乙; the LLM-backed extractor is a system concern, the kernel
    only fans out and merges.)"""
    sections: dict = {}
    for s in registry.systems:
        for name, decl in s.digest_extract(prose, world).items():
            sections[name] = decl
    log.debug("digest_extract sections=%s", sorted(sections))
    return TurnCommit(narration=prose, sections=sections)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_digest.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git -C /root/rpg-engine-app add kernel/digest.py tests/kernel/test_digest.py
git -C /root/rpg-engine-app commit -m "feat(kernel): digest fan-out driver (prose -> turn-commit)"
```

---

## Task 10: Observability layer (Langfuse tracer + debug dump)

Every LLM call (added in later subprojects) wraps in `tracer.span(...)`; offline/tests use a no-op tracer (no network). `dump()` mirrors the `RPG_DEBUG` convention for inspecting assembled context / turn-commit.

**Files:**
- Modify: `requirements.txt` (add `langfuse`)
- Create: `kernel/observability.py`
- Test: `tests/kernel/test_observability.py`

- [ ] **Step 1: Add the dependency**

Append `langfuse` to `requirements.txt` (one line). Do NOT install in tests; the module lazy-imports it only when creds are present.

- [ ] **Step 2: Write the failing test**

`tests/kernel/test_observability.py`:
```python
import logging

from kernel.observability import get_tracer, NoopTracer, dump


def test_default_tracer_is_noop_without_creds(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    t = get_tracer()
    assert isinstance(t, NoopTracer)


def test_noop_span_is_a_usable_contextmanager():
    t = NoopTracer()
    with t.span("turn", turn=1) as sp:
        assert sp is None  # no-op yields nothing, never raises


def test_dump_logs_only_when_debug(monkeypatch, caplog):
    monkeypatch.setenv("RPG_DEBUG", "1")
    with caplog.at_level(logging.DEBUG, logger="rpg.kernel.observability"):
        dump("turn-commit", {"narration": "hi"})
    assert any("turn-commit" in r.message for r in caplog.records)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_observability.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel.observability'`

- [ ] **Step 4: Implement `kernel/observability.py`**

```python
from __future__ import annotations

import json
import os
from contextlib import contextmanager

from engine.log import get_logger

log = get_logger("kernel.observability")


class NoopTracer:
    """Tracer used offline / in tests: every method is inert."""

    @contextmanager
    def span(self, name, **attrs):
        yield None

    def event(self, name, **attrs):
        pass


class LangfuseTracer:
    """Thin wrapper over the Langfuse SDK. Constructed only when creds exist;
    langfuse is imported lazily so the dependency is optional at runtime."""

    def __init__(self):
        from langfuse import Langfuse  # lazy: only when creds present
        self._lf = Langfuse()  # reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST from env

    @contextmanager
    def span(self, name, **attrs):
        span = self._lf.start_span(name=name, input=attrs or None)
        try:
            yield span
        finally:
            try:
                span.end()
            except Exception:
                log.debug("langfuse span end failed for %s", name)

    def event(self, name, **attrs):
        try:
            self._lf.create_event(name=name, metadata=attrs or None)
        except Exception:
            log.debug("langfuse event failed for %s", name)


def get_tracer():
    """LangfuseTracer when LANGFUSE_PUBLIC_KEY is set and the SDK imports; else NoopTracer."""
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        try:
            return LangfuseTracer()
        except Exception as e:  # missing SDK / bad config -> degrade gracefully
            log.debug("Langfuse unavailable (%s); falling back to NoopTracer", e)
    return NoopTracer()


def dump(label: str, payload) -> None:
    """Debug-dump a kernel artifact (assembled context, turn-commit, validation)
    to the rpg debug log. Only emits when RPG_DEBUG / RPG_LOG_LEVEL=DEBUG."""
    try:
        body = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        body = repr(payload)
    log.debug("DUMP %s: %s", label, body)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/rpg-engine-app && python3 -m pytest tests/kernel/test_observability.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Run the FULL suite (kernel + existing engine, no regressions)**

Run: `cd /root/rpg-engine-app && python3 -m pytest -q`
Expected: PASS — all `tests/kernel/*` pass and the pre-existing `tests/test_*` still pass (Task 2's edits are backward-compatible).

- [ ] **Step 7: Commit**

```bash
git -C /root/rpg-engine-app add requirements.txt kernel/observability.py tests/kernel/test_observability.py
git -C /root/rpg-engine-app commit -m "feat(kernel): observability layer (Langfuse tracer + debug dump)"
```

---

## Done criteria for S0

- `python3 -m pytest -q` green (new `tests/kernel/*` + unchanged `tests/test_*`).
- A registry of systems can: append registry-typed events (`open_store`), project state (`project`), validate a turn-commit + build a repair request (`validate_commit`/`build_repair_request`), assemble layered context (`assemble`/`render`), recall (`recall`), and digest prose→turn-commit (`digest_extract`); every LLM-call site can wrap in `get_tracer().span(...)` and dump artifacts via `dump(...)`.
- No game logic in `kernel/` — only routing + mechanism. `engine/` untouched except the two backward-compatible edits.

**Next subproject:** S1 — implement the first real `ContextSystem`s (地点 + 角色) against this kernel to validate the interface, then migrate the rest.
