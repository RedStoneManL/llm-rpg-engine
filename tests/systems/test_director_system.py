"""Tests for DirectorSystem (Phase B1)."""
from __future__ import annotations

from kernel.registry import Registry
from kernel.projection import project, empty_world
from kernel.events import kernel_event
from kernel.contextsystem import Fragment
from systems.ontology import OntologySystem
from systems.director import DirectorSystem


def _reg():
    return Registry().register(OntologySystem()).register(DirectorSystem())


def test_director_owns_event_types():
    ds = DirectorSystem()
    assert ds.name == "director"
    # B1 core types + B2 thread types + replay-safe consumption watermark event
    assert ds.event_types() == {
        "campaign_seeded", "oracle_roll", "director_fired",
        "thread_open", "thread_advance", "directive_consumed",
    }
    # B1 emits events directly (harness-authored), so it owns no commit sections.
    assert ds.commit_sections() == set()


def test_director_registers_without_requires_cycle():
    reg = _reg()
    assert "director" in {s.name for s in reg.systems}
    assert "director_fired" in reg.event_types()
    assert reg.owner_of_event("oracle_roll").name == "director"


def test_empty_state_is_pending_queue():
    ds = DirectorSystem()
    st = ds.empty_state()
    assert st == {"pending": [], "consumed_through_turn": 0, "threads": {}}


def _fired_event(turn, scene="s1", day=1, **extra):
    deltas = {
        "type": "front_stage",
        "magnitude": "big",
        "valence": None,
        "event_type": "危机",
        "event_hint": "遇到危险/被追杀/突发威胁",
        "twist": "另有目的",
        "twist_hint": "对方动机不单纯",
    }
    deltas.update(extra)
    return kernel_event("director_fired", day=day, scene=scene,
                        summary="突发:危机(另有目的)", deltas=deltas, turn=turn)


def test_campaign_seeded_apply_sets_meta_seed():
    reg = _reg()
    ev = kernel_event("campaign_seeded", day=1, scene="genesis",
                      summary="campaign seed", deltas={"campaign_seed": 123456}, turn=0)
    world = project(reg, [ev])
    assert world["meta"]["campaign_seed"] == 123456


def test_director_fired_apply_enqueues_directive():
    reg = _reg()
    world = project(reg, [_fired_event(turn=3)])
    slice_ = world["systems"]["director"]
    assert len(slice_["pending"]) == 1
    d = slice_["pending"][0]
    assert d["event_type"] == "危机" and d["twist"] == "另有目的"
    assert d["magnitude"] == "big" and d["type"] == "front_stage"
    assert d["turn"] == 3 and d["consumed"] is False


def test_oracle_roll_apply_is_audit_only():
    reg = _reg()
    ev = kernel_event("oracle_roll", day=1, scene="s1",
                      summary="暗骰 roll=0.20 prob=0.30",
                      deltas={"prob": 0.30, "roll": 0.20}, turn=2)
    world = project(reg, [ev])
    # audit-only: no pending directive, slice unchanged from empty
    assert world["systems"]["director"]["pending"] == []


def test_inject_renders_pending_directive():
    ds = DirectorSystem()
    world = {"meta": {}, "systems": {"director": {
        "pending": [{
            "type": "front_stage", "magnitude": "big", "valence": None,
            "event_type": "危机", "event_hint": "遇到危险/被追杀/突发威胁",
            "twist": "另有目的", "twist_hint": "对方动机不单纯",
            "turn": 3, "scene": "s1", "consumed": False,
        }],
        "consumed_through_turn": 0,
    }}}
    frag = ds.inject({"protagonist": "hero", "day": 1}, world)
    assert isinstance(frag, Fragment)
    assert frag.system == "director" and frag.layer == "scene"
    # The directive names the drawn seed so the narrator can weave it in.
    assert "危机" in frag.text and "另有目的" in frag.text
    assert "big" in frag.text


def test_inject_skips_consumed_directive():
    ds = DirectorSystem()
    world = {"meta": {}, "systems": {"director": {
        "pending": [{
            "type": "front_stage", "magnitude": "small", "valence": None,
            "event_type": "机遇", "event_hint": "h", "twist": "无反转",
            "twist_hint": "h2", "turn": 1, "scene": "s1", "consumed": True,
        }],
        "consumed_through_turn": 1,
    }}}
    assert ds.inject({"protagonist": "hero", "day": 1}, world) is None


def test_inject_none_when_no_pending():
    ds = DirectorSystem()
    world = {"meta": {}, "systems": {"director": {"pending": [], "consumed_through_turn": 0}}}
    assert ds.inject({"protagonist": "hero", "day": 1}, world) is None


def test_directive_surfaces_through_assemble_context():
    """assemble_context iterates every system's inject(); the directive must
    appear in the assembled string with no assembler edits."""
    from context.assembler import assemble_context
    from systems.place import PlaceSystem

    reg = (Registry().register(OntologySystem())
           .register(PlaceSystem()).register(DirectorSystem()))
    world = project(reg, [_fired_event(turn=3)])
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "s1", "location": "s1"}
    ctx = assemble_context(reg, world, scene)
    assert "导演·暗骰" in ctx
    assert "危机" in ctx and "另有目的" in ctx


def test_director_owns_thread_events_in_b2():
    ds = DirectorSystem()
    assert {"thread_open", "thread_advance"} <= ds.event_types()


def test_thread_open_projects_into_thread_store():
    reg = _reg()
    ev = kernel_event("thread_open", day=1, scene="s1", summary="暗线",
                      deltas={"id": "th_revenge", "status": "活跃", "speed": "中",
                              "dormant": True, "trait": "城府极深", "archetype": "复仇宿敌",
                              "event_type": "阴谋线", "last_advanced_scene": "s1"}, turn=2)
    world = project(reg, [ev])
    threads = world["systems"]["director"]["threads"]
    assert "th_revenge" in threads
    assert threads["th_revenge"]["dormant"] is True
    assert threads["th_revenge"]["trait"] == "城府极深"


def test_thread_advance_updates_last_advanced_scene():
    reg = _reg()
    world = project(reg, [
        kernel_event("thread_open", day=1, scene="s1", summary="暗线",
                     deltas={"id": "th1", "status": "活跃", "speed": "快",
                             "dormant": False, "trait": "毒舌", "archetype": "身世之谜",
                             "last_advanced_scene": "s1"}, turn=1),
        kernel_event("thread_advance", day=2, scene="s3", summary="推进",
                     deltas={"id": "th1", "last_advanced_scene": "s3"}, turn=4),
    ])
    assert world["systems"]["director"]["threads"]["th1"]["last_advanced_scene"] == "s3"


def test_thread_surface_directive_is_injected():
    reg = _reg()
    world = project(reg, [
        kernel_event("thread_open", day=1, scene="s1", summary="暗线",
                     deltas={"id": "th_x", "status": "活跃", "speed": "中",
                             "dormant": True, "trait": "深不可测", "archetype": "复仇宿敌",
                             "event_type": "阴谋线", "last_advanced_scene": "s1"}, turn=1),
        kernel_event("thread_advance", day=2, scene="s4", summary="暗线浮现:th_x",
                     deltas={"id": "th_x", "last_advanced_scene": "s4",
                             "surface": True}, turn=5),
    ])
    ds = DirectorSystem()
    frag = ds.inject({"protagonist": "hero", "day": 2}, world)
    assert frag is not None
    assert "暗线" in frag.text and "复仇宿敌" in frag.text
