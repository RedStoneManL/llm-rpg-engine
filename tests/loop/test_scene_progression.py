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


def _seed(store, r=None):
    """Minimal genesis: a starting place + protagonist located there, scene s1."""
    from kernel.events import kernel_event
    from kernel.projection import project
    if r is None:
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
            "location": _protagonist_location(world, "hero") or "town"}


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


def _registry_full():
    """Registry with NarrativeSystem included for recap bucket tests."""
    from systems.director import DirectorSystem
    from systems.cascade import CascadeSystem
    from systems.lore import LoreSystem
    from systems.narrative import NarrativeSystem
    r = Registry()
    for s in (OntologySystem(), PlaceSystem(), CharacterSystem(),
              TimeSystem(), DirectorSystem(), CascadeSystem(),
              LoreSystem(), NarrativeSystem(), SceneSystem()):
        r.register(s)
    return r


def test_multi_scene_run_creates_distinct_recap_buckets():
    """The payoff: distinct scenes => the recap (NarrativeSystem) buckets them
    separately, instead of one ever-growing bucket. Proves scene-progression
    unblocks recap tiering."""
    r = _registry_full()
    store = _store(r)
    r, world = _seed(store, r)
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
                       required_sections=REQUIRED_SECTIONS, prev_scene=prev_scene,
                       cascade_provider=FakeLLMProvider(responses=["概要"]))
        world = res.world
        prev_scene = scene
        scene = _scene(world)
    buckets = world["systems"]["narrative"]["scenes"]
    distinct = {b["scene"] for b in buckets}
    # With static scene this would be 1; scene-progression yields multiple.
    assert len(distinct) >= 2, f"expected multiple recap buckets, got {distinct}"
