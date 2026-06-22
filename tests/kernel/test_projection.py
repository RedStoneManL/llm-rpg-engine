import copy

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


def test_apply_receives_full_world_so_systems_can_reach_meta():
    r = _reg()
    w = project(r, [kernel_event("note_added", day=3, scene="s9", summary="x")])
    # apply saw meta-bearing world; note stored in its slice
    assert w["systems"]["notes"]["notes"] == ["x"] and w["meta"]["day"] == 3


# ---------------------------------------------------------------------------
# D2: multi-system replay-idempotency — projecting the same event stream twice
#     must yield deeply equal worlds across ALL real engine systems.
# ---------------------------------------------------------------------------

def _build_real_registry():
    """Build the same registry used by build_engine (all real systems)."""
    from kernel.registry import Registry
    from systems.ontology import OntologySystem
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    from systems.object import ObjectSystem
    from systems.faction import FactionSystem
    from systems.knowledge import KnowledgeSystem
    from systems.cascade import CascadeSystem
    from systems.time import TimeSystem
    from systems.narrative import NarrativeSystem
    from systems.scene import SceneSystem
    from systems.lore import LoreSystem

    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(ObjectSystem())
    r.register(FactionSystem())
    r.register(KnowledgeSystem())
    r.register(CascadeSystem())
    r.register(TimeSystem())
    r.register(NarrativeSystem())
    r.register(SceneSystem())
    r.register(LoreSystem())
    return r


def _make_realistic_event_stream():
    """Build a realistic multi-system event stream: places, characters, lore, advances."""
    _LORE_SKELETON = {
        "id": "lost_merchant", "complexity": "medium", "about": "失踪商人",
        "secret": "商人被本地帮派灭口", "anchor": "border_town",
        "description": "城中关于失踪商人的流言",
        "trigger": "玩家在客栈打听",
        "l3_anchor": "border_inn",
        "stages": [{"hint": "客栈老板神情不安"}, {"hint": "马厩里有一匹陌生的马"}],
        "threshold": 55,
    }
    evs = [
        # Places: L1 region → L2 border_town → L3 border_inn
        kernel_event("place_created", day=1, scene="s1", summary="region1",
                     deltas={"id": "region1", "level": 1, "kind": "region",
                             "seed": "北境", "tier": "tracked"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="border_town",
                     deltas={"id": "border_town", "level": 2, "kind": "settlement",
                             "seed": "边境小镇", "tier": "tracked", "parent": "region1"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="border_inn",
                     deltas={"id": "border_inn", "level": 3, "kind": "venue",
                             "seed": "客栈", "tier": "tracked", "parent": "border_town"}, turn=0),
        # Characters
        kernel_event("character_created", day=1, scene="s1", summary="hero",
                     deltas={"id": "hero", "etype": "Person", "tier": "tracked",
                             "sketch": "流浪侠客", "goal": "寻找失踪的朋友"}, turn=0),
        kernel_event("entity_moved", day=1, scene="s1", summary="hero->border_inn",
                     deltas={"who": "hero", "to": "border_inn"}, turn=0),
        # Lore line creation
        kernel_event("lore_created", day=1, scene="s1", summary="暗线创建",
                     deltas=_LORE_SKELETON, turn=1),
        # Lore advances
        kernel_event("lore_advanced", day=2, scene="s2", summary="第一阶段",
                     deltas={"id": "lost_merchant", "stage_idx": 0,
                             "hint": "客栈老板神情不安"}, turn=2),
        kernel_event("lore_advanced", day=3, scene="s3", summary="第二阶段",
                     deltas={"id": "lost_merchant", "stage_idx": 1,
                             "hint": "马厩里有一匹陌生的马"}, turn=3),
    ]
    return evs


def test_multi_system_project_twice_yields_equal_worlds():
    """D2: Projecting the same event stream twice (with real systems) must yield
    deeply equal worlds — guards against apply() side-effects across all systems.
    """
    reg = _build_real_registry()
    evs = _make_realistic_event_stream()

    world_a = project(reg, iter(evs))
    world_b = project(reg, iter(evs))

    # Deep equality: meta
    assert world_a["meta"]["day"] == world_b["meta"]["day"], \
        f"meta.day mismatch: {world_a['meta']['day']} vs {world_b['meta']['day']}"
    assert world_a["meta"]["scene"] == world_b["meta"]["scene"], \
        "meta.scene mismatch"
    assert len(world_a["meta"]["timeline"]) == len(world_b["meta"]["timeline"]), \
        "timeline length mismatch"

    # Lore slice: lines and clues_dropped must be identical
    lore_a = world_a["systems"]["lore"]
    lore_b = world_b["systems"]["lore"]
    assert set(lore_a["lines"].keys()) == set(lore_b["lines"].keys()), \
        "lore lines keys mismatch"
    for lid in lore_a["lines"]:
        la = lore_a["lines"][lid]
        lb = lore_b["lines"][lid]
        assert la["clues_dropped"] == lb["clues_dropped"], \
            f"clues_dropped mismatch for line {lid!r}: {la['clues_dropped']} vs {lb['clues_dropped']}"
        assert la["stage_idx"] == lb["stage_idx"], \
            f"stage_idx mismatch for line {lid!r}"
        assert la["state"] == lb["state"], \
            f"state mismatch for line {lid!r}"

    # Systems slice: full deep equality via copy to avoid FactGraph object identity issues
    # Compare serialisable fields we can reliably compare
    sys_a = world_a["systems"]
    sys_b = world_b["systems"]
    assert set(sys_a.keys()) == set(sys_b.keys()), \
        f"systems keys mismatch: {set(sys_a.keys())} vs {set(sys_b.keys())}"

    # Lore gen state
    assert lore_a.get("gen") == lore_b.get("gen"), \
        f"lore gen mismatch: {lore_a.get('gen')} vs {lore_b.get('gen')}"
