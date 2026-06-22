# Scene Progression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `meta.scene` advance (a new scene when the protagonist's location changes or the day advances), so recap tiers and the director's scene ordinal becomes real.

**Architecture:** A small `SceneSystem` owns a harness-authored `scene_advanced` event; `run_turn` detects the boundary after each turn (compare protagonist location + day before/after) and emits `scene_advanced` with the next monotonic id, so the NEXT turn opens the new scene. `meta.scene` rides projection (`= ev["scene"]`); the recap (`NarrativeSystem`) and director auto-benefit with no changes.

**Tech Stack:** Python 3.12, pytest (offline/deterministic, `FakeLLMProvider`). Run: `cd /root/rpg-engine-app && PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`.

**Spec:** `docs/superpowers/specs/2026-06-20-scene-progression-design.md`

## Global Constraints

- Python 3.12; interpreter is `python3` (NOT `python`). Run: `cd /root/rpg-engine-app && PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`.
- Offline/deterministic tests only — no network. Langfuse stays no-op.
- Baseline before this plan: **825 passed, 1 deselected**. No task may reduce the passing count except by explicit, justified test updates a task names.
- Scene ids: genesis stays `"genesis"` (scene 1, untouched in `new_game`); advanced scenes are `"s2"`, `"s3"`, … (monotonic unique). The scene string is OPAQUE to consumers (recap bucket key / director distinct-count) — only uniqueness + change-on-boundary matter.
- Boundary rule: a new scene when (protagonist `located_in` place changed) OR (`meta.day` changed) between a turn's start and end.
- `meta.scene` is set by `kernel/projection.py:24` (`meta["scene"] = ev["scene"]`) for EVERY event — so stamp `scene_advanced` with `scene=<new id>` and `meta.scene` flows automatically; `SceneSystem.apply` only tracks `meta.scene_no` (the int counter) + `meta.scene_anchor`.
- HARD git guardrails: stay on branch `app`; NEVER `git init`/`reset --hard`/`rebase`/`checkout --orphan`/branch-switch; never delete `_legacy/` or `docs/`. Commit only the files each task names. Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- NEVER print/commit `.env.local`.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `systems/scene.py` (new) | `SceneSystem`: owns `scene_advanced`; `apply` tracks `meta.scene_no` + `meta.scene_anchor` (meta.scene rides projection). | 1 |
| `app/engine.py` (modify) | Register `SceneSystem` in `build_engine`. | 1 |
| `tests/systems/test_scene_system.py` (new) | Unit tests for `SceneSystem`. | 1 |
| `loop/turn.py` (modify) | `_protagonist_location` helper; capture prev loc/day; after the fleet, detect the boundary and emit `scene_advanced`. | 2 |
| `tests/loop/test_scene_progression.py` (new) | Boundary detection + the recap-tiering payoff (distinct buckets on a multi-scene run). | 2 |

---

### Task 1: `SceneSystem` + registration

**Files:**
- Create: `systems/scene.py`
- Modify: `app/engine.py` (register in `build_engine`, after `NarrativeSystem()` at line ~102)
- Test: `tests/systems/test_scene_system.py`

**Interfaces:**
- Consumes: `kernel.contextsystem.ContextSystem`.
- Produces: `SceneSystem` with `name="scene"`, `requires()={"ontology"}`, `event_types()={"scene_advanced"}`, `commit_sections()=set()`, `apply(world, event)` setting `world["meta"]["scene_no"]` + `world["meta"]["scene_anchor"]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/systems/test_scene_system.py`:

```python
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.scene import SceneSystem


def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(SceneSystem())
    return r


def test_scene_system_owns_event_no_commit_section():
    ss = SceneSystem()
    assert ss.event_types() == {"scene_advanced"}
    assert ss.commit_sections() == set()
    assert "ontology" in ss.requires()


def test_scene_advanced_sets_meta_scene_and_counter_and_anchor():
    r = _reg()
    world = project(r, [
        kernel_event("scene_advanced", day=3, scene="s2",
                     summary="场景推进→s2",
                     deltas={"scene_id": "s2", "scene_no": 2,
                             "location": "canglang_ridge", "day": 3},
                     turn=1),
    ])
    # meta.scene flows from projection (ev["scene"]); counter+anchor from apply
    assert world["meta"]["scene"] == "s2"
    assert world["meta"]["scene_no"] == 2
    assert world["meta"]["scene_anchor"] == {"location": "canglang_ridge", "day": 3}


def test_scene_advanced_sequence_keeps_latest():
    r = _reg()
    world = project(r, [
        kernel_event("scene_advanced", day=1, scene="s2",
                     deltas={"scene_id": "s2", "scene_no": 2, "location": "a", "day": 1},
                     summary="→s2", turn=1),
        kernel_event("scene_advanced", day=2, scene="s3",
                     deltas={"scene_id": "s3", "scene_no": 3, "location": "b", "day": 2},
                     summary="→s3", turn=2),
    ])
    assert world["meta"]["scene"] == "s3"
    assert world["meta"]["scene_no"] == 3
    assert world["meta"]["scene_anchor"] == {"location": "b", "day": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/systems/test_scene_system.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'systems.scene'`.

- [ ] **Step 3: Write the implementation**

Create `systems/scene.py`:

```python
"""systems.scene — SceneSystem: owns the scene_advanced event.

Harness-authored only (no commit section). loop/turn.run_turn detects a scene
boundary (protagonist location changed OR day changed) after a turn and appends
a scene_advanced event carrying the next monotonic scene id, so the NEXT turn
opens the new scene.

meta.scene itself rides kernel projection (meta["scene"] = ev["scene"]); apply()
only tracks meta.scene_no (the int counter, for computing the next id) and
meta.scene_anchor (where/when the current scene began). Rewind-safe: both fold
from events, so /rewind that retracts scene_advanced events reverts the counter.
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem
from engine.log import get_logger

log = get_logger("systems.scene")


class SceneSystem(ContextSystem):
    """Owns scene_advanced. No commit section (harness-authored)."""

    name = "scene"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"scene_advanced"}

    def commit_sections(self) -> set[str]:
        return set()

    def empty_state(self) -> dict:
        return {}

    def apply(self, world: dict, event: dict) -> None:
        if event["type"] != "scene_advanced":
            return
        d = event.get("deltas", {})
        meta = world["meta"]
        # meta.scene is set by projection from event["scene"] (= the new id).
        meta["scene_no"] = d.get("scene_no", meta.get("scene_no") or 1)
        meta["scene_anchor"] = {"location": d.get("location"), "day": d.get("day")}
        log.debug("scene_advanced -> scene_no=%s anchor=%s",
                  meta["scene_no"], meta["scene_anchor"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/systems/test_scene_system.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Register the system**

In `app/engine.py`, add the import near the other system imports (after `from systems.narrative import NarrativeSystem`, line ~38):

```python
from systems.scene import SceneSystem
```

And register it in `build_engine` after `registry.register(NarrativeSystem())` (line ~102):

```python
    registry.register(SceneSystem())
```

- [ ] **Step 6: Run the full suite**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`
Expected: all green; passing count = 825 + 3 new = 828 (1 deselected). If a test breaks, investigate (registering a new harness-authored system should be inert to existing tests).

- [ ] **Step 7: Commit**

```bash
git add systems/scene.py app/engine.py tests/systems/test_scene_system.py
git commit -m "feat(scene): SceneSystem owns scene_advanced (counter + anchor); registered

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Boundary detection in `run_turn` + the recap-tiering payoff

**Files:**
- Modify: `loop/turn.py`
- Test: `tests/loop/test_scene_progression.py`

**Interfaces:**
- Consumes: `SceneSystem` (Task 1, owns `scene_advanced`); `kernel.events.kernel_event` (already imported in turn.py? — it imports `project`, `validate_commit`; add `from kernel.events import kernel_event` if absent).
- Produces: `loop.turn._protagonist_location(world, protagonist) -> str | None`; `run_turn` emits `scene_advanced` at the end of a turn when the boundary rule fires.

**Context — current `run_turn` end (loop/turn.py ~286-312):** after the `catchup` try/except block it builds and returns `TurnResult(narration=..., world=new_world, ...)`. The detection goes between the catchup block and the `return`.

- [ ] **Step 1: Write the failing tests**

Create `tests/loop/test_scene_progression.py`:

```python
import os
import tempfile

from kernel.registry import Registry
from kernel.projection import empty_world
from kernel.events import open_store
from loop.turn import run_turn, REQUIRED_SECTIONS, _protagonist_location
from loop.strategy import AuthorStrategy
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.time import TimeSystem
from systems.scene import SceneSystem


def _registry():
    r = Registry()
    for s in (OntologySystem(), PlaceSystem(), CharacterSystem(),
              TimeSystem(), SceneSystem()):
        r.register(s)
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=registry.event_types())


def _seed(store):
    """Minimal genesis: a starting place + protagonist located there, scene s1."""
    from kernel.events import kernel_event
    from kernel.projection import project
    r = _registry()
    for ev in [
        kernel_event("place_created", day=1, scene="s1", summary="start",
                     deltas={"id": "town", "level": 2, "kind": "settlement", "seed": "x"}, turn=0),
        kernel_event("character_created", day=1, scene="s1", summary="hero",
                     deltas={"id": "hero", "tier": "tracked", "sketch": "a", "goal": "b"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="arrive",
                     deltas={"who": "hero", "to": "town"}, turn=0),
    ]:
        store.append(ev)
    return r, project(r, store.iter_events())


def _scene(world):
    return {"protagonist": "hero", "present": [],
            "day": world["meta"].get("day") or 1,
            "id": world["meta"].get("scene") or "s1",
            "location": "town"}


def _no_change_commit(narr="原地。"):
    return {"narration": narr,
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "原地"}],
            "reasons": {"moves": "未动", "places": "无", "cast": "无", "facts": "无"}}


def test_protagonist_location_helper():
    r, world = _seed(_store(_registry()))
    assert _protagonist_location(world, "hero") == "town"
    assert _protagonist_location(world, "nobody") is None


def test_no_boundary_keeps_scene():
    store = _store(_registry())
    r, world = _seed(store)
    res = run_turn(r, store, world, _scene(world), "看看四周",
                   strategy=AuthorStrategy(),
                   provider=FakeLLMProvider(json_responses=[_no_change_commit()]),
                   required_sections=REQUIRED_SECTIONS)
    assert res.world["meta"]["scene"] == "s1"  # no move, no day change → same scene


def test_location_change_advances_scene():
    store = _store(_registry())
    r, world = _seed(store)
    move_commit = {"narration": "我走到市集。",
                   "places": [{"id": "market", "level": 2, "kind": "settlement", "seed": "集市"}],
                   "moves": [{"who": "hero", "to": "market"}],
                   "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "几步路"}],
                   "reasons": {"cast": "无", "facts": "无"}}
    res = run_turn(r, store, world, _scene(world), "去市集",
                   strategy=AuthorStrategy(),
                   provider=FakeLLMProvider(json_responses=[move_commit]),
                   required_sections=REQUIRED_SECTIONS)
    assert res.world["meta"]["scene"] == "s2"          # location changed → new scene
    assert res.world["meta"]["scene_no"] == 2
    assert res.world["meta"]["scene_anchor"]["location"] == "market"


def test_day_change_advances_scene():
    store = _store(_registry())
    r, world = _seed(store)
    overnight = {"narration": "一夜过去。",
                 "clock": [{"advance": True, "days": 1, "bands": 0, "reason": "宿了一夜"}],
                 "reasons": {"moves": "未动", "places": "无", "cast": "无", "facts": "无"}}
    res = run_turn(r, store, world, _scene(world), "睡一觉",
                   strategy=AuthorStrategy(),
                   provider=FakeLLMProvider(json_responses=[overnight]),
                   required_sections=REQUIRED_SECTIONS)
    assert res.world["meta"]["scene"] == "s2"          # day advanced (same place) → new scene
    assert res.world["meta"]["day"] == 2


def test_multi_scene_run_creates_distinct_recap_buckets():
    """The payoff: distinct scenes => the recap (NarrativeSystem) buckets them
    separately, instead of one ever-growing bucket. Proves scene-progression
    unblocks recap tiering."""
    store = _store(_registry())
    r, world = _seed(store)
    scene = _scene(world)
    # 3 turns, each moving to a fresh place => 3 scene boundaries.
    commits = []
    for i in range(1, 4):
        commits.append({"narration": f"第{i}站的见闻。",
                        "places": [{"id": f"place{i}", "level": 2, "kind": "settlement", "seed": "x"}],
                        "moves": [{"who": "hero", "to": f"place{i}"}],
                        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "赶路"}],
                        "reasons": {"cast": "无", "facts": "无"}})
    provider = FakeLLMProvider(json_responses=commits)
    prev_scene = None
    for c in commits:
        res = run_turn(r, store, world, scene, "继续",
                       strategy=AuthorStrategy(), provider=provider,
                       required_sections=REQUIRED_SECTIONS, prev_scene=prev_scene)
        world = res.world
        prev_scene = scene
        scene = _scene(world)
    buckets = world["systems"]["narrative"]["scenes"]
    distinct = {b["scene"] for b in buckets}
    # With static scene this would be 1; scene-progression yields multiple.
    assert len(distinct) >= 2, f"expected multiple recap buckets, got {distinct}"
```

Note: `test_multi_scene_run_creates_distinct_recap_buckets` relies on `digest_fleet` recording `narration_recorded` per turn keyed by the turn's scene; with a real `FakeLLMProvider` (no recap_provider passed) the fleet still records raw narration, so distinct scenes ⇒ distinct buckets. If the fleet needs a provider to record narration, pass `cascade_provider=FakeLLMProvider(responses=["概要"])` to `run_turn` in this test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/loop/test_scene_progression.py -q`
Expected: FAIL — `_protagonist_location` not importable; scene stays `s1` (no boundary detection yet).

- [ ] **Step 3: Write the implementation**

Edit `loop/turn.py`.

(a) Ensure `kernel_event` is imported (top of file, with the other kernel imports):

```python
from kernel.events import kernel_event
```

(b) Add the helper just below `_next_turn` (after its return):

```python
def _protagonist_location(world: dict, protagonist: str | None) -> str | None:
    """Current place id the protagonist is located_in (or None)."""
    g = world.get("systems", {}).get("ontology")
    if g is None or not protagonist:
        return None
    day = world.get("meta", {}).get("day") or 1
    locs = g.neighbors(protagonist, "located_in", day)
    return locs[0] if locs else None
```

(c) In `run_turn`, capture the pre-turn anchor. Just after `turn_num_before = _next_turn(store)` (and inside/near the top of the `with get_tracer().span("turn", ...)` block, before `produce_turn`), add:

```python
        protagonist = scene.get("protagonist")
        prev_loc = _protagonist_location(world, protagonist)
        prev_day = (world.get("meta", {}) or {}).get("day")
```

(d) After the `catchup` try/except block and BEFORE `return TurnResult(...)`, add the boundary detection:

```python
        # Scene progression: a new scene begins when the protagonist's location
        # changed or the day advanced this turn. Emit scene_advanced LAST so this
        # turn's events stay in the old scene and the NEXT turn opens the new one.
        new_loc = _protagonist_location(new_world, protagonist)
        new_day = (new_world.get("meta", {}) or {}).get("day")
        if (new_loc != prev_loc) or (new_day != prev_day):
            cur_no = (world.get("meta", {}) or {}).get("scene_no") or 1
            new_no = cur_no + 1
            new_scene_id = f"s{new_no}"
            try:
                store.append(kernel_event(
                    "scene_advanced", day=new_day or 1, scene=new_scene_id,
                    summary=f"场景推进→{new_scene_id}",
                    deltas={"scene_id": new_scene_id, "scene_no": new_no,
                            "location": new_loc, "day": new_day},
                    turn=turn_num_before,
                ))
                new_world = project(registry, store.iter_events())
                log.debug("run_turn: scene advanced -> %s (loc %s->%s, day %s->%s)",
                          new_scene_id, prev_loc, new_loc, prev_day, new_day)
            except Exception:
                log.exception("run_turn: scene_advanced failed (non-fatal)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest tests/loop/test_scene_progression.py -q`
Expected: PASS (5 tests). If `test_multi_scene_run_creates_distinct_recap_buckets` fails because the fleet didn't record narration, add `cascade_provider=FakeLLMProvider(responses=["概要"])` to its `run_turn` calls per the note in Step 1.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=/root/rpg-engine-app python3 -m pytest -q`
Expected: all green; count = 828 + 5 = 833 (1 deselected). Existing tests that drive `run_turn` and assert on `meta.scene` or exact event counts may shift (a turn that moves/changes-day now appends one `scene_advanced` event, and `meta.scene` may become `s2`). For each such failure, decide: if the test asserts the OLD static-scene behavior, update it to the correct new value (scene now advances — that's the feature); if it reveals a real logic problem, investigate. Name every test you change and why in your report.

- [ ] **Step 6: Commit**

```bash
git add loop/turn.py tests/loop/test_scene_progression.py
# plus any existing test files you legitimately updated in Step 5:
git commit -m "feat(scene): advance meta.scene on location/day boundary in run_turn

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Out of Scope / Follow-ups
- **Cascade scene-subtree fix** — `loop/cascade.py:_scene_subtree` treats the scene string as a place id (pre-existing bug; not regressed by counter ids). Real fix = thread the protagonist's actual location to `run_cascade` for the subtree while still stamping events with the scene id. Separate change.
- **Level-2-area boundary granularity** — v1 keys "location change" on raw place id; could coarsen to level-2-area change if too granular in dungeons.
- **Director ordinal** — already auto-benefits (distinct scene strings → real `scene_ordinal`); the `salt=next_turn` workaround stays (harmless, still needed for same-scene multi-turn variation).

## Self-Review
- **Spec coverage:** §1 boundary rule → Task 2 detection. §2 unique counter + SceneSystem → Task 1. §3 run_turn detection/emit → Task 2. §4 `_build_scene` reads meta.scene (unchanged — already `meta.get("scene")`). §5 recap/director auto-benefit → Task 2 payoff test. Out-of-scope items match the spec.
- **Type consistency:** `scene_advanced` deltas `{scene_id, scene_no, location, day}` identical in Task 1 (apply reads scene_no/location/day) and Task 2 (emit). `_protagonist_location(world, protagonist)->str|None` used in helper + both prev/new in run_turn.
- **No placeholders:** every step has full code + exact commands + expected output.
