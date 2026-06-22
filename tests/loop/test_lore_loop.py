import os
import tempfile

from kernel.registry import Registry
from kernel.projection import project, empty_world
from kernel.events import open_store, kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem
from loop.lore import create_lore_line, run_lore


def _reg(with_lore=True):
    r = Registry()
    r.register(OntologySystem())
    if with_lore:
        r.register(LoreSystem())
    return r


def _reg_full(with_lore=True):
    """Registry with Place + Character systems, needed for dormancy (★6).

    Dormancy requires resolving the protagonist's L2 town via the ontology
    graph, which in turn requires place_created and entity_moved events to be
    accepted by the store.  PlaceSystem owns place_created/entity_moved;
    CharacterSystem owns character_created.
    """
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    if with_lore:
        r.register(LoreSystem())
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=registry.event_types())


_SK = {"id": "l1", "complexity": "simple", "about": "x", "anchor": "town",
       "description": "镇上关于神秘失踪的传言", "trigger": "玩家向居民打听失踪事件",
       "l3_anchor": "town_market",
       "stages": [{"hint": "clue-a"}, {"hint": "clue-b"}, {"hint": "clue-c"}],
       "threshold": 100}  # threshold 100 → d100 always <= 100 → always advances


def _seed_protagonist_in_anchor(store, anchor_town="town"):
    """Seed a protagonist (tracked Person) located_in the anchor town.

    Required by dormancy (★6): simple/medium 暗 lines are frozen unless the
    protagonist is in the line's anchor town.  Call this after creating a
    store with _reg_full() so that place_created / character_created /
    entity_moved event types are in allowed_types.
    """
    store.append(kernel_event(
        "place_created", day=1, scene="s1", summary=anchor_town,
        deltas={"id": anchor_town, "level": 2, "kind": "settlement",
                "seed": anchor_town, "tier": "tracked"},
        turn=0,
    ))
    store.append(kernel_event(
        "character_created", day=1, scene="s1", summary="hero",
        deltas={"id": "hero", "tier": "tracked", "sketch": "a", "goal": "b"},
        turn=0,
    ))
    store.append(kernel_event(
        "entity_moved", day=1, scene="s1", summary="move",
        deltas={"who": "hero", "to": anchor_town},
        turn=0,
    ))


def test_create_lore_line_appends_event():
    r = _reg(); store = _store(r)
    ev = create_lore_line(store, _SK, day=1, scene="s1", turn=1)
    assert ev["type"] == "lore_created"
    assert ev["deltas"]["id"] == "l1"
    w = project(r, store.iter_events())
    assert "l1" in w["systems"]["lore"]["lines"]


def test_create_lore_line_rejects_missing_field():
    r = _reg(); store = _store(r)
    import pytest
    with pytest.raises(ValueError):
        create_lore_line(store, {"id": "x"}, day=1, scene="s1", turn=1)


def test_run_lore_advances_when_threshold_passes():
    # Uses _reg_full + protagonist in anchor town so dormancy (★6) doesn't freeze the line.
    r = _reg_full(); store = _store(r)
    _seed_protagonist_in_anchor(store, "town")
    create_lore_line(store, _SK, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    appended = run_lore(r, store, w)
    assert len(appended) == 1
    assert appended[0]["type"] == "lore_advanced"
    assert appended[0]["deltas"]["stage_idx"] == 0
    assert appended[0]["deltas"]["hint"] == "clue-a"


def test_run_lore_never_advances_when_threshold_zero():
    r = _reg(); store = _store(r)
    sk = {**_SK, "id": "l0", "threshold": 0}  # d100 in 1..100, never <= 0
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    assert run_lore(r, store, w) == []


def test_run_lore_stops_at_last_stage():
    # Uses _reg_full + protagonist in anchor town so dormancy (★6) doesn't freeze the line.
    r = _reg_full(); store = _store(r)
    _seed_protagonist_in_anchor(store, "town")
    create_lore_line(store, _SK, day=1, scene="s1", turn=1)
    # advance 3 stages (0,1,2) over 3 calls, then it should stop (no stage 3)
    w = project(r, store.iter_events())
    for _ in range(3):
        run_lore(r, store, w)
        w = project(r, store.iter_events())
    ln = w["systems"]["lore"]["lines"]["l1"]
    assert ln["stage_idx"] == 2  # capped at last stage
    assert run_lore(r, store, w) == []  # no further advance
    assert ln["clues_dropped"] == ["clue-a", "clue-b", "clue-c"]


def test_run_lore_replay_deterministic():
    """Same events + same world → same roll outcome (seeded)."""
    r = _reg(); store = _store(r)
    sk = {**_SK, "id": "lr", "threshold": 50}
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    out1 = run_lore(r, store, w)
    # Re-projecting and running again from the SAME pre-roll state would re-roll
    # the same seed; instead assert determinism via two fresh stores with identical input.
    r2 = _reg(); store2 = _store(r2)
    create_lore_line(store2, sk, day=1, scene="s1", turn=1)
    w2 = project(r2, store2.iter_events())
    out2 = run_lore(r2, store2, w2)
    assert [e["deltas"].get("stage_idx") for e in out1] == [e["deltas"].get("stage_idx") for e in out2]


def test_run_lore_noop_without_loresystem():
    r = _reg(with_lore=False); store = _store(r)
    # No LoreSystem registered → guard makes run_lore a clean no-op.
    w = empty_world(r)
    assert run_lore(r, store, w) == []


def test_run_lore_idempotent_on_stale_world():
    """Calling run_lore twice on the same (stale, not re-projected) world returns [] the second time."""
    # Uses _reg_full + protagonist in anchor town so dormancy (★6) doesn't freeze the line.
    r = _reg_full(); store = _store(r)
    _seed_protagonist_in_anchor(store, "town")
    sk = {**_SK, "threshold": 100}  # always advances
    create_lore_line(store, sk, day=1, scene="s1", turn=1)
    w = project(r, store.iter_events())
    first = run_lore(r, store, w)
    assert len(first) == 1  # stage 0 advanced
    # second call: world is stale (still shows stage_idx=-1), but the store already has lore_advanced
    second = run_lore(r, store, w)
    assert second == []  # idempotency guard: no duplicate advance


def test_run_turn_advances_lore_end_to_end():
    from loop.turn import run_turn, REQUIRED_SECTIONS
    from loop.strategy import AuthorStrategy
    from llm.provider import FakeLLMProvider
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    from systems.time import TimeSystem

    r = Registry()
    for s in (OntologySystem(), PlaceSystem(), CharacterSystem(), TimeSystem(), LoreSystem()):
        r.register(s)
    store = _store(r)
    # seed a protagonist + place
    for ev in [
        kernel_event("place_created", day=1, scene="s1", summary="p",
                     deltas={"id": "town", "level": 2, "kind": "settlement", "seed": "x"}, turn=0),
        kernel_event("character_created", day=1, scene="s1", summary="h",
                     deltas={"id": "hero", "tier": "tracked", "sketch": "a", "goal": "b"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="m",
                     deltas={"who": "hero", "to": "town"}, turn=0),
    ]:
        store.append(ev)
    create_lore_line(store, _SK, day=1, scene="s1", turn=0)
    world = project(r, store.iter_events())
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "town"}
    commit = {"narration": "无事。",
              "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "原地"}],
              "reasons": {"moves": "未动", "places": "无", "cast": "无", "facts": "无"}}
    res = run_turn(r, store, world, scene, "观察",
                   strategy=AuthorStrategy(),
                   provider=FakeLLMProvider(json_responses=[commit, commit]),
                   required_sections=REQUIRED_SECTIONS)
    ln = res.world["systems"]["lore"]["lines"]["l1"]
    assert ln["stage_idx"] >= 0  # the threshold-100 line advanced at least one stage this turn
    assert ln["clues_dropped"]   # and dropped a clue


# ---------------------------------------------------------------------------
# jit_resequence tests (Site 4: complete_structured refactor)
# ---------------------------------------------------------------------------

from loop.lore import jit_resequence
from llm.provider import FakeLLMProvider


def _simple_world():
    """Minimal world dict for jit_resequence tests."""
    return {"meta": {"day": 3}, "systems": {"lore": {}, "ontology": {}}}


def test_jit_resequence_conforming_response_returns_stages():
    """A conforming first response → new stages returned, 1 LLM call."""
    line = {"id": "l1", "about": "失踪事件", "secret": "村长所为",
            "clues_dropped": [], "stage_idx": 0,
            "stages": [{"hint": "原阶段1"}, {"hint": "原阶段2"}]}
    good = {"stages": [{"hint": "新阶段A"}, {"hint": "新阶段B"}]}
    fake = FakeLLMProvider(json_responses=[good])
    result = jit_resequence(line, _simple_world(), fake)
    assert result == [{"hint": "新阶段A"}, {"hint": "新阶段B"}]
    assert len(fake.calls) == 1


def test_jit_resequence_repair_loop_uses_repaired_result():
    """Bad first response (missing stages) + conforming second → repaired stages used.
    Repair message names the missing field."""
    line = {"id": "l1", "about": "失踪事件", "secret": "村长所为",
            "clues_dropped": [], "stage_idx": 0,
            "stages": [{"hint": "原阶段1"}, {"hint": "原阶段2"}]}
    bad  = {"note": "wrong"}          # missing "stages"
    good = {"stages": [{"hint": "修复阶段"}]}
    fake = FakeLLMProvider(json_responses=[bad, good])
    result = jit_resequence(line, _simple_world(), fake)
    assert result == [{"hint": "修复阶段"}]
    # Two calls: initial + 1 repair
    assert len(fake.calls) == 2
    # Repair message names the missing field
    repair_msg = fake.calls[1][1]  # 2nd call's user turn
    assert '"stages"' in repair_msg


def test_jit_resequence_fallback_when_all_attempts_fail():
    """Never-conforming response → fallback to remaining original stages."""
    line = {"id": "l1", "about": "x", "secret": "y",
            "clues_dropped": [], "stage_idx": 0,
            "stages": [{"hint": "orig1"}, {"hint": "orig2"}, {"hint": "orig3"}]}
    bad = {"note": "always wrong"}
    fake = FakeLLMProvider(json_responses=[bad])  # cycles the bad response
    result = jit_resequence(line, _simple_world(), fake)
    # Fallback: remaining from stage_idx+1 onwards = orig2, orig3
    assert result == [{"hint": "orig2"}, {"hint": "orig3"}]


def test_jit_validate_collects_all_stage_errors():
    """B1: a response with TWO bad stages yields a repair message naming BOTH bad stages,
    not just the first one (early-return bug was: stage 2 named, stage 3 never mentioned).
    With max_repairs=1 this matters — the single repair must name every broken stage
    so the model can fix them all at once."""
    line = {"id": "l1", "about": "x", "secret": "y",
            "clues_dropped": [], "stage_idx": 0,
            "stages": [{"hint": "orig1"}, {"hint": "orig2"}]}
    # Two bad stages: stage 1 has wrong type, stage 3 is missing hint
    bad_two_stages = {"stages": [{"note": "no hint here"}, {"hint": "good"}, {}]}
    good = {"stages": [{"hint": "修复A"}, {"hint": "修复B"}]}
    fake = FakeLLMProvider(json_responses=[bad_two_stages, good])
    result = jit_resequence(line, _simple_world(), fake)
    assert result == [{"hint": "修复A"}, {"hint": "修复B"}]
    assert len(fake.calls) == 2
    repair_msg = fake.calls[1][1]  # 2nd call's user turn (the repair feedback)
    # Must name BOTH bad stages (stage 1 and stage 3), not just stage 1
    assert "stage 1" in repair_msg
    assert "stage 3" in repair_msg
