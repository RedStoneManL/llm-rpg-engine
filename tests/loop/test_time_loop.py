"""Tests for loop.time: current_day, detect_jump, stale_entering_scope, run_catchup."""
from __future__ import annotations

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from loop.time import current_day, detect_jump, JUMP_THRESHOLD


def _reg():
    return (Registry().register(OntologySystem())
            .register(PlaceSystem()).register(CharacterSystem()))


def test_current_day_reads_meta():
    world = project(_reg(), [kernel_event("place_created", day=7, scene="s",
                    summary="x", deltas={"id": "t", "tier": "tracked"}, turn=1)])
    assert current_day(world) == 7


def test_current_day_defaults_to_1_when_empty():
    assert current_day(project(_reg(), [])) == 1


def test_detect_jump_true_on_big_gap():
    events = [
        kernel_event("place_created", day=1, scene="s", summary="x",
                     deltas={"id": "t", "tier": "tracked"}, turn=1),
        kernel_event("entity_moved", day=5, scene="s", summary="到",
                     deltas={"who": "h", "to": "t"}, turn=2),
    ]
    world = project(_reg(), events)
    this_turn = [e for e in events if e["turn"] == 2]
    prev, now, jumped = detect_jump(this_turn, world, all_events=events)
    assert (prev, now, jumped) == (1, 5, True)


def test_detect_jump_false_on_single_day_step():
    events = [
        kernel_event("place_created", day=1, scene="s", summary="x",
                     deltas={"id": "t", "tier": "tracked"}, turn=1),
        kernel_event("entity_moved", day=2, scene="s", summary="到",
                     deltas={"who": "h", "to": "t"}, turn=2),
    ]
    world = project(_reg(), events)
    this_turn = [e for e in events if e["turn"] == 2]
    _, _, jumped = detect_jump(this_turn, world, all_events=events)
    assert jumped is False


# ---------------------------------------------------------------------------
# Task 4: stale_entering_scope
# ---------------------------------------------------------------------------

from loop.time import stale_entering_scope


def _person(pid, day, sketch="人", goal="活着"):
    return kernel_event("character_created", day=day, scene="s",
                        summary="登场",
                        deltas={"id": pid, "tier": "tracked",
                                "sketch": sketch, "goal": goal}, turn=1)


def test_entering_scope_stale_tracked_is_selected():
    world = project(_reg(), [_person("npc", 1)])
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": ["npc"], "day": 5}
    assert stale_entering_scope(world, prev_scene, new_scene, now=5) == ["npc"]


def test_present_last_turn_is_not_entering():
    world = project(_reg(), [_person("npc", 1)])
    prev_scene = {"protagonist": "hero", "present": ["npc"]}
    new_scene = {"protagonist": "hero", "present": ["npc"], "day": 5}
    assert stale_entering_scope(world, prev_scene, new_scene, now=5) == []


def test_fresh_entity_not_selected():
    world = project(_reg(), [_person("npc", 5)])
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": ["npc"], "day": 5}
    assert stale_entering_scope(world, prev_scene, new_scene, now=5) == []


def test_offscreen_entity_never_selected():
    world = project(_reg(), [_person("npc", 1)])
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": [], "day": 9}
    assert stale_entering_scope(world, prev_scene, new_scene, now=9) == []


def test_protagonist_never_selected():
    world = project(_reg(), [_person("hero", 1)])
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": [], "day": 5}
    assert stale_entering_scope(world, prev_scene, new_scene, now=5) == []


# ---------------------------------------------------------------------------
# Task 6: run_catchup hook
# ---------------------------------------------------------------------------

import tempfile
import os

from kernel.events import open_store
from llm.provider import LLMProvider
from systems.cascade import CascadeSystem
from systems.time import TimeSystem
from loop.time import run_catchup


def _full_reg():
    return (Registry().register(OntologySystem()).register(PlaceSystem())
            .register(CharacterSystem()).register(CascadeSystem())
            .register(TimeSystem()))


def _store(reg):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=reg.event_types())


class KeyedCatchup(LLMProvider):
    def __init__(self, by_id):
        self.by_id = by_id
        self.calls = []

    def complete(self, system, user, *, model=None, max_tokens=None):
        return ""

    def complete_json(self, system, user, schema, **kw):
        self.calls.append(user)
        for eid, v in self.by_id.items():
            if eid in user:
                return dict(v, id=eid)
        return {"changed": False}

    def complete_messages(self, messages, *, model=None, max_tokens=None):
        import json as _json
        last_user = next((m.get("content", "") for m in reversed(messages)
                          if m.get("role") == "user"), "")
        self.calls.append(last_user)
        for eid, v in self.by_id.items():
            if eid in last_user:
                return _json.dumps(dict(v, id=eid), ensure_ascii=False)
        return _json.dumps({"changed": False}, ensure_ascii=False)


def test_run_catchup_emits_character_evolved_for_entering_stale():
    reg = _full_reg()
    store = _store(reg)
    store.append(_person("npc", 1))
    # Use time_advanced (day=5) to advance meta.day to 5
    store.append(kernel_event("time_advanced", day=5, scene="s", summary="五天后",
                 deltas={"to_day": 5, "reason": "elapse"}, turn=2))
    world = project(reg, store.iter_events())
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": ["npc"], "id": "s", "day": 5}
    prov = KeyedCatchup({"npc": {"changed": True, "predicate": "mood",
                                 "value": "形容枯槁", "note": "独守五日"}})
    appended = run_catchup(reg, store, world, prev_scene, new_scene, provider=prov)
    types = [e["type"] for e in appended]
    assert "character_evolved" in types
    ev = next(e for e in appended if e["type"] == "character_evolved")
    assert ev["deltas"]["id"] == "npc" and ev["deltas"]["value"] == "形容枯槁"
    # Re-project: drift fact + last_update now current
    w2 = project(reg, store.iter_events())
    assert w2["systems"]["ontology"].value_at("npc", "mood", 5) == "形容枯槁"
    assert w2["systems"]["ontology"].get_entity("npc").attrs["last_update"] == 5


def test_run_catchup_noop_still_stamps_currency():
    reg = _full_reg()
    store = _store(reg)
    store.append(_person("npc", 1))
    store.append(kernel_event("time_advanced", day=5, scene="s", summary="x",
                 deltas={"to_day": 5, "reason": "elapse"}, turn=2))
    world = project(reg, store.iter_events())
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": ["npc"], "id": "s", "day": 5}
    prov = KeyedCatchup({"npc": {"changed": False}})
    appended = run_catchup(reg, store, world, prev_scene, new_scene, provider=prov)
    assert [e["type"] for e in appended] == ["time_advanced"]
    w2 = project(reg, store.iter_events())
    assert w2["systems"]["ontology"].get_entity("npc").attrs["last_update"] == 5


def test_run_catchup_quiet_when_no_entering_stale():
    reg = _full_reg()
    store = _store(reg)
    store.append(_person("npc", 5))
    world = project(reg, store.iter_events())
    scene = {"protagonist": "hero", "present": ["npc"], "id": "s", "day": 5}
    prov = KeyedCatchup({})
    assert run_catchup(reg, store, world, scene, scene, provider=prov) == []
    assert prov.calls == []


def test_run_catchup_budget_caps_calls():
    reg = _full_reg()
    store = _store(reg)
    for i in range(6):
        store.append(_person(f"npc{i}", 1))
    store.append(kernel_event("time_advanced", day=5, scene="s", summary="x",
                 deltas={"to_day": 5, "reason": "e"}, turn=2))
    world = project(reg, store.iter_events())
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": [f"npc{i}" for i in range(6)],
                 "id": "s", "day": 5}
    prov = KeyedCatchup({f"npc{i}": {"changed": False} for i in range(6)})
    import loop.time as tmod
    run_catchup(reg, store, world, prev_scene, new_scene, provider=prov)
    assert len(prov.calls) == tmod.CATCHUP_BUDGET


# ---------------------------------------------------------------------------
# Task 1 (Phase D wiring): run_turn prev_scene threading
# ---------------------------------------------------------------------------

from llm.provider import FakeLLMProvider
from systems.director import DirectorSystem


def _full_reg_with_all():
    """Registry with all systems needed for a full run_turn with catchup."""
    from systems.director import DirectorSystem
    return (Registry()
            .register(OntologySystem())
            .register(PlaceSystem())
            .register(CharacterSystem())
            .register(DirectorSystem())
            .register(CascadeSystem())
            .register(TimeSystem()))


def test_run_turn_with_prev_scene_fires_catchup_for_entering_stale_npc():
    """run_turn(prev_scene=<old scene without npc>) causes catch-up to fire for
    an NPC that was stale (last_update < now) and is now in new_scene["present"].

    This proves the prev_scene threading works: with the bug (prev_scene=scene),
    new_scope - prev_scope = empty => no catch-up. With the fix, entering NPCs
    are detected and catch-up fires.
    """
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    reg = _full_reg_with_all()
    store = _store(reg)

    # Seed: NPC created on day=1 (stale by day=5).
    store.append(_person("inn_keeper", 1))
    # Advance time to day=5 (so now=5, and inn_keeper.last_update=1 < 5 → stale)
    store.append(kernel_event("time_advanced", day=5, scene="s",
                              summary="五天过去",
                              deltas={"to_day": 5, "reason": "elapse"}, turn=1))

    world = project(reg, store.iter_events())

    # Previous scene: inn_keeper NOT in present → she was off-screen.
    prev_scene = {"protagonist": "hero", "present": [], "day": 5,
                  "id": "inn", "location": "inn"}

    # New scene: inn_keeper NOW enters present (the protagonist walks into the inn).
    new_scene = {"protagonist": "hero", "present": ["inn_keeper"], "day": 5,
                 "id": "inn", "location": "inn"}

    # Keyed catchup provider: inn_keeper changed.
    catchup_prov = KeyedCatchup({
        "inn_keeper": {"changed": True, "predicate": "mood",
                       "value": "疲惫", "note": "五日无客"}
    })

    # Minimal narrator commit: nothing special — just a narration.
    narrator_prov = FakeLLMProvider(json_responses=[{
        "narration": "你走进旅馆，掌柜抬起头来。",
        "moves": [], "places": [], "cast": [], "facts": [],
    }])

    result = run_turn(
        reg, store, world, new_scene, "进入旅馆",
        strategy=AuthorStrategy(),
        provider=narrator_prov,
        catchup_provider=catchup_prov,
        prev_scene=prev_scene,   # <-- the new parameter being tested
    )

    # Catch-up should have fired: inn_keeper is entering scope AND stale.
    all_ev_types = [e["type"] for e in store.iter_events()]
    assert "character_evolved" in all_ev_types, (
        f"Expected character_evolved from catch-up; got event types: {all_ev_types}"
    )
    evolved = next(e for e in store.iter_events() if e["type"] == "character_evolved")
    assert evolved["deltas"]["id"] == "inn_keeper"
    assert evolved["deltas"]["value"] == "疲惫"
    # Catchup provider must have been called (not narrator provider)
    assert len(catchup_prov.calls) >= 1


def test_run_turn_without_prev_scene_does_not_fire_catchup():
    """With the default prev_scene=None (or same scene), catch-up must NOT fire
    for an NPC that is already in present, because they are not *entering* scope.

    This is the regression guard: the bug (same scene) vs the fix (None/empty).
    With prev_scene=None, run_turn defaults prev_scope to {}, so technically
    all present NPCs are 'entering'. But for a fresh genesis (day=1, NPC created
    at day=1, last_update=1 == now=1), the staleness gate filters them out.
    """
    from loop.turn import run_turn
    from loop.strategy import AuthorStrategy

    reg = _full_reg_with_all()
    store = _store(reg)

    # NPC created today (day=1): last_update=1, now=1 → NOT stale
    store.append(_person("guard", 1))
    world = project(reg, store.iter_events())

    scene = {"protagonist": "hero", "present": ["guard"], "day": 1,
             "id": "gate", "location": "gate"}

    catchup_prov = KeyedCatchup({"guard": {"changed": True, "predicate": "mood",
                                            "value": "警觉"}})
    narrator_prov = FakeLLMProvider(json_responses=[{
        "narration": "守卫注视着你。",
        "moves": [], "places": [], "cast": [], "facts": [],
    }])

    # No prev_scene passed → defaults to None → empty prev_scope.
    # But guard.last_update=1 == now=1 → NOT stale → no catch-up.
    result = run_turn(
        reg, store, world, scene, "看看守卫",
        strategy=AuthorStrategy(),
        provider=narrator_prov,
        catchup_provider=catchup_prov,
        # prev_scene intentionally omitted
    )

    all_ev_types = [e["type"] for e in store.iter_events()]
    assert "character_evolved" not in all_ev_types, (
        f"Catch-up must NOT fire when NPC is not stale; got: {all_ev_types}"
    )
    assert len(catchup_prov.calls) == 0


def test_play_loop_tracks_prev_scene_so_catchup_fires_on_second_turn(tmp_path):
    """play_loop must track prev_scene across iterations so that when an NPC
    enters scope on turn 2 after being absent in turn 1, catch-up fires.

    Setup: NPC created at day=1, time jumped to day=5 (stale). Turn 1: NPC NOT
    present. Turn 2: NPC enters present. Catch-up should fire on turn 2.
    """
    from app.engine import build_engine, new_game
    from app.play import play_loop
    from systems.time import TimeSystem
    from systems.director import DirectorSystem
    from systems.cascade import CascadeSystem
    from systems.faction import FactionSystem
    from systems.knowledge import KnowledgeSystem
    from systems.object import ObjectSystem

    # Build engine manually with all systems
    from kernel.registry import Registry
    from kernel.events import open_store, kernel_event
    from kernel.projection import project
    from systems.ontology import OntologySystem
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    from app.engine import Engine

    import hashlib
    camp = tmp_path / "camp"
    camp.mkdir(parents=True)

    reg = Registry()
    reg.register(OntologySystem())
    reg.register(PlaceSystem())
    reg.register(CharacterSystem())
    reg.register(ObjectSystem())
    reg.register(FactionSystem())
    reg.register(KnowledgeSystem())
    reg.register(DirectorSystem())
    reg.register(CascadeSystem())
    reg.register(TimeSystem())

    db = camp / "events.db"
    jsonl = camp / "events.jsonl"
    store = open_store(str(db), str(jsonl), reg.event_types())

    # Seed: NPC at day=1, then jump to day=5
    store.append(kernel_event("character_created", day=1, scene="genesis",
                              summary="innkeeper 登场",
                              deltas={"id": "innkeeper", "tier": "tracked",
                                      "sketch": "旅馆掌柜", "goal": "维持生计"}, turn=0))
    store.append(kernel_event("time_advanced", day=5, scene="genesis",
                              summary="五天流逝",
                              deltas={"to_day": 5, "reason": "elapse"}, turn=0))

    world = project(reg, store.iter_events())
    seed = int(hashlib.sha256(b"camp").hexdigest()[:12], 16)
    engine = Engine(registry=reg, store=store, provider=None,
                    embedder=None, world=world, campaign_seed=seed,
                    cascade_provider=None)

    # Turn 1 response: innkeeper NOT in present
    # Turn 2 response: innkeeper enters present
    from loop.strategy import AuthorStrategy
    from loop.turn import REQUIRED_SECTIONS

    # We need narrator to return valid commits. Use FakeLLMProvider with keyed JSON.
    # Patch _build_scene so we control "present" output.
    import app.play as play_mod

    scenes_returned = [
        # Turn 1: innkeeper absent
        {"protagonist": "protagonist", "present": [], "day": 5,
         "id": "inn", "location": "inn"},
        # Turn 2: innkeeper enters present
        {"protagonist": "protagonist", "present": ["innkeeper"], "day": 5,
         "id": "inn", "location": "inn"},
    ]
    scene_call_idx = [0]

    def _fake_build_scene(eng):
        idx = scene_call_idx[0]
        scene_call_idx[0] += 1
        return scenes_returned[min(idx, len(scenes_returned) - 1)]

    # Catchup provider records calls
    catchup_prov = KeyedCatchup({"innkeeper": {"changed": True, "predicate": "mood",
                                                "value": "孤寂", "note": "五日无客"}})
    engine.cascade_provider = catchup_prov

    narrator_resps = [
        {"narration": "第一回合。", "moves": [], "places": [], "cast": [], "facts": [],
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
        {"narration": "第二回合，掌柜在此。", "moves": [], "places": [], "cast": [], "facts": [],
         "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}]},
    ]
    engine.provider = FakeLLMProvider(json_responses=narrator_resps)

    collected = []
    original_build_scene = play_mod._build_scene
    play_mod._build_scene = _fake_build_scene
    try:
        play_loop(engine, inputs=["第一轮", "第二轮", "/quit"],
                  out=collected.append)
    finally:
        play_mod._build_scene = original_build_scene

    all_ev_types = [e["type"] for e in store.iter_events()]
    assert "character_evolved" in all_ev_types, (
        f"Catch-up must fire on turn 2 when innkeeper enters scope; "
        f"got event types: {all_ev_types}"
    )


def test_run_catchup_repair_loop_uses_repaired_result():
    """Bad first response (missing 'changed') + conforming second → repaired result used.
    The repair message names the missing field."""
    import json as _json
    from llm.provider import FakeLLMProvider
    from loop.time import run_catchup

    reg = _full_reg()
    store = _store(reg)
    store.append(_person("npc", 1))
    store.append(kernel_event("time_advanced", day=5, scene="s", summary="x",
                 deltas={"to_day": 5, "reason": "elapse"}, turn=2))
    world = project(reg, store.iter_events())
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": ["npc"], "id": "s", "day": 5}

    # First response: missing "changed" (malformed); second: conforming
    bad  = {"note": "forgot changed"}
    good = {"changed": True, "predicate": "mood", "value": "坚韧"}
    fake = FakeLLMProvider(json_responses=[bad, good])

    appended = run_catchup(reg, store, world, prev_scene, new_scene, provider=fake)

    # Should have used the repaired result
    types = [e["type"] for e in appended]
    assert "character_evolved" in types
    ev = next(e for e in appended if e["type"] == "character_evolved")
    assert ev["deltas"]["value"] == "坚韧"
    # Two calls: initial + 1 repair
    assert len(fake.calls) == 2
    # Repair message named the missing field
    repair_msg = fake.calls[1][1]  # 2nd call's user turn
    assert '"changed"' in repair_msg


def test_run_catchup_blank_populace_mood_not_emitted():
    """B2: a Place catchup with populace_mood='   ' (whitespace-only) must NOT
    emit a populace_shifted event. The mood field is optional — just never blank."""
    from llm.provider import FakeLLMProvider
    from loop.time import run_catchup

    reg = _full_reg()
    store = _store(reg)
    # Create a Place entity (not a Person) stale since day 1
    place_ev = kernel_event(
        "place_created", day=1, scene="s", summary="inn",
        deltas={"id": "inn", "level": 2, "kind": "settlement", "seed": "x",
                "tier": "tracked"},
        turn=1,
    )
    store.append(place_ev)
    store.append(kernel_event("time_advanced", day=5, scene="s", summary="x",
                 deltas={"to_day": 5, "reason": "elapse"}, turn=2))
    world = project(reg, store.iter_events())
    prev_scene = {"protagonist": "hero", "present": []}
    new_scene = {"protagonist": "hero", "present": ["inn"], "id": "s", "day": 5}

    # Place changed, but populace_mood is whitespace-only
    prov = FakeLLMProvider(json_responses=[
        {"changed": True, "state": "荒废", "populace_mood": "   "}
    ])
    appended = run_catchup(reg, store, world, prev_scene, new_scene, provider=prov)

    # place_evolved should be emitted (changed:true, state given)
    types = [e["type"] for e in appended]
    assert "place_evolved" in types
    # populace_shifted must NOT be emitted for a blank/whitespace-only mood
    assert "populace_shifted" not in types
