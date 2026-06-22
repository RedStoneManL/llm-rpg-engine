from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import Fragment
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.lore import LoreSystem


def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(LoreSystem())
    return r


_SKELETON = {
    "id": "missing_caravan", "complexity": "medium", "about": "商队失踪",
    "secret": "商队首领其实卷款潜逃", "anchor": "qingshi_town",
    "description": "集市上关于失踪商队的窃窃私语",
    "trigger": "玩家在集市打听商队/货物/失踪的人",
    "l3_anchor": "qingshi_market",
    "stages": [{"hint": "集市上有人在打听商队的下落"},
               {"hint": "城门记录显示商队从未出城"},
               {"hint": "首领的空宅里翻出一张烧剩的地契"}],
    "threshold": 60,
}


def test_lore_system_ownership():
    ls = LoreSystem()
    # T1 events still present; T2 adds quest_* events
    assert {"lore_created", "lore_advanced"}.issubset(ls.event_types())
    # T2: 'quests' commit section added
    assert "quests" in ls.commit_sections()
    assert "ontology" in ls.requires()
    assert ls.empty_state() == {"lines": {}, "gen": {}}


def test_lore_created_builds_line():
    r = _reg()
    w = project(r, [kernel_event("lore_created", day=1, scene="s1",
                                 summary="x", deltas=_SKELETON, turn=1)])
    ln = w["systems"]["lore"]["lines"]["missing_caravan"]
    assert ln["complexity"] == "medium"
    assert ln["about"] == "商队失踪"
    assert ln["anchor"] == "qingshi_town"
    assert ln["threshold"] == 60
    assert ln["stage_idx"] == -1
    assert "status" not in ln, "status must not be present; state is the lifecycle field"
    assert ln["state"] == "暗"
    assert ln["clues_dropped"] == []
    assert len(ln["stages"]) == 3


def test_lore_advanced_sets_stage_and_appends_clue():
    r = _reg()
    w = project(r, [
        kernel_event("lore_created", day=1, scene="s1", summary="x", deltas=_SKELETON, turn=1),
        kernel_event("lore_advanced", day=1, scene="s1", summary="adv",
                     deltas={"id": "missing_caravan", "stage_idx": 0,
                             "hint": "集市上有人在打听商队的下落"}, turn=2),
        kernel_event("lore_advanced", day=2, scene="s1", summary="adv",
                     deltas={"id": "missing_caravan", "stage_idx": 1,
                             "hint": "城门记录显示商队从未出城"}, turn=3),
    ])
    ln = w["systems"]["lore"]["lines"]["missing_caravan"]
    assert ln["stage_idx"] == 1
    assert ln["clues_dropped"] == ["集市上有人在打听商队的下落", "城门记录显示商队从未出城"]


def test_lore_advanced_unknown_line_is_ignored():
    r = _reg()
    w = project(r, [kernel_event("lore_advanced", day=1, scene="s1", summary="x",
                                 deltas={"id": "nope", "stage_idx": 0, "hint": "h"}, turn=1)])
    assert w["systems"]["lore"]["lines"] == {}


def test_inject_surfaces_active_clues():
    # T5 migration: inject no longer dumps 暗 clue text (superseded by station_push_fragment).
    # inject now returns None for 暗-only worlds; ambient clues are delivered by the strategy
    # via station_push_fragment.  station_push_fragment IS tested in test_lore_disclosure_B.py
    # and test_quest_disclosure.py.
    # This test now verifies: (a) inject returns None for 暗-only worlds, AND
    # (b) station_push_fragment provides the clue text for the same world.
    from loop.lore_disclosure import station_push_fragment as _spf
    r = _reg()
    w = project(r, [
        kernel_event("place_created", day=1, scene="s1", summary="p",
                     deltas={"id": "qingshi_town", "level": 2, "kind": "settlement", "seed": "x"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="qingshi_market",
                     deltas={"id": "qingshi_market", "level": 3, "kind": "venue",
                             "parent": "qingshi_town"}, turn=0),
        kernel_event("character_created", day=1, scene="s1", summary="h",
                     deltas={"id": "hero", "tier": "tracked", "sketch": "a", "goal": "b"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="m",
                     deltas={"who": "hero", "to": "qingshi_market"}, turn=0),
        kernel_event("lore_created", day=1, scene="s1", summary="x", deltas=_SKELETON, turn=1),
        kernel_event("lore_advanced", day=1, scene="s1", summary="adv",
                     deltas={"id": "missing_caravan", "stage_idx": 0,
                             "hint": "集市上有人在打听商队的下落"}, turn=2),
    ])
    # T5: inject returns None for 暗-only worlds (no 明 lines = no 明账)
    frag = LoreSystem().inject({"protagonist": "hero"}, w)
    assert frag is None, "T5: inject must return None for 暗-only worlds"
    # The clue is delivered via station_push_fragment (暗 ambient path)
    scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "qingshi_market"}
    ambient = _spf(r, w, scene)
    assert ambient is not None, "station_push_fragment must return clue text at the venue"
    assert "集市上有人在打听商队的下落" in ambient


def test_inject_none_when_no_clues():
    r = _reg()
    w = project(r, [
        kernel_event("place_created", day=1, scene="s1", summary="p",
                     deltas={"id": "qingshi_town", "level": 2, "kind": "settlement", "seed": "x"}, turn=0),
        kernel_event("character_created", day=1, scene="s1", summary="h",
                     deltas={"id": "hero", "tier": "tracked", "sketch": "a", "goal": "b"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="m",
                     deltas={"who": "hero", "to": "qingshi_town"}, turn=0),
        kernel_event("lore_created", day=1, scene="s1", summary="x", deltas=_SKELETON, turn=1),
    ])
    # line exists but has dropped no clue yet
    assert LoreSystem().inject({"protagonist": "hero"}, w) is None


def test_inject_filters_out_other_location():
    """A line anchored at a different location is filtered out even with a dropped clue."""
    r = _reg()
    far_skeleton = {**_SKELETON, "id": "far_mystery", "anchor": "far_town"}
    w = project(r, [
        kernel_event("place_created", day=1, scene="s1", summary="p",
                     deltas={"id": "qingshi_town", "level": 2, "kind": "settlement", "seed": "x"}, turn=0),
        kernel_event("character_created", day=1, scene="s1", summary="h",
                     deltas={"id": "hero", "tier": "tracked", "sketch": "a", "goal": "b"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="m",
                     deltas={"who": "hero", "to": "qingshi_town"}, turn=0),
        kernel_event("lore_created", day=1, scene="s1", summary="x", deltas=far_skeleton, turn=1),
        kernel_event("lore_advanced", day=1, scene="s1", summary="adv",
                     deltas={"id": "far_mystery", "stage_idx": 0,
                             "hint": "远处有怪事发生"}, turn=2),
    ])
    # protagonist is at qingshi_town; line is anchored at far_town → filtered out
    assert LoreSystem().inject({"protagonist": "hero"}, w) is None


# ---------------------------------------------------------------------------
# A3 fix: lore_advanced apply dedup guard on clues_dropped
# ---------------------------------------------------------------------------

def test_lore_advanced_apply_dedup_same_hint_not_doubled():
    """A3: applying the same lore_advanced event twice must NOT double-count the hint.

    This simulates a retract-replay or any other path that calls apply() twice
    with the same event. The hint should appear exactly once in clues_dropped.
    """
    r = _reg()
    adv_event = kernel_event(
        "lore_advanced", day=1, scene="s1", summary="adv",
        deltas={"id": "missing_caravan", "stage_idx": 0,
                "hint": "集市上有人在打听商队的下落"},
        turn=2,
    )
    # Project once normally (create + one advance)
    w = project(r, [
        kernel_event("lore_created", day=1, scene="s1", summary="x", deltas=_SKELETON, turn=1),
        adv_event,
    ])
    ln = w["systems"]["lore"]["lines"]["missing_caravan"]
    assert ln["clues_dropped"].count("集市上有人在打听商队的下落") == 1, \
        "After first apply: clue should appear exactly once"

    # Directly call apply a second time on the same state (simulate re-apply)
    ls = LoreSystem()
    ls.apply(w, adv_event)
    ln_after = w["systems"]["lore"]["lines"]["missing_caravan"]
    count = ln_after["clues_dropped"].count("集市上有人在打听商队的下落")
    assert count == 1, \
        f"After second apply of same event: clue appeared {count} time(s); expected 1 (dedup guard)"


def test_lore_advanced_different_hints_both_stored():
    """A3 guard must not suppress distinct hints — two different clues should both appear."""
    r = _reg()
    w = project(r, [
        kernel_event("lore_created", day=1, scene="s1", summary="x", deltas=_SKELETON, turn=1),
        kernel_event("lore_advanced", day=1, scene="s1", summary="adv1",
                     deltas={"id": "missing_caravan", "stage_idx": 0,
                             "hint": "集市上有人在打听商队的下落"}, turn=2),
        kernel_event("lore_advanced", day=2, scene="s1", summary="adv2",
                     deltas={"id": "missing_caravan", "stage_idx": 1,
                             "hint": "城门记录显示商队从未出城"}, turn=3),
    ])
    ln = w["systems"]["lore"]["lines"]["missing_caravan"]
    assert "集市上有人在打听商队的下落" in ln["clues_dropped"]
    assert "城门记录显示商队从未出城" in ln["clues_dropped"]
    assert len(ln["clues_dropped"]) == 2, \
        f"Expected exactly 2 distinct clues; got {ln['clues_dropped']}"
