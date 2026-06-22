"""T1: state field on lore lines — lore_created stores state from deltas, defaults to '暗'."""
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem


def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(LoreSystem())
    return r


_BASE_SKELETON = {
    "id": "test_line", "complexity": "simple", "about": "测试线",
    "secret": "隐情", "anchor": "town",
    "description": "描述", "trigger": "触发",
    "l3_anchor": "town_market",
    "stages": [{"hint": "线索A"}, {"hint": "线索B"}],
    "threshold": 50,
}


def test_lore_created_with_explicit_state_ming():
    """lore_created with state:'明' → line stored with state '明'."""
    r = _reg()
    skeleton = {**_BASE_SKELETON, "state": "明"}
    w = project(r, [kernel_event("lore_created", day=1, scene="s1",
                                 summary="x", deltas=skeleton, turn=1)])
    ln = w["systems"]["lore"]["lines"]["test_line"]
    assert ln["state"] == "明"


def test_lore_created_with_explicit_state_an():
    """lore_created with state:'暗' → line stored with state '暗'."""
    r = _reg()
    skeleton = {**_BASE_SKELETON, "state": "暗"}
    w = project(r, [kernel_event("lore_created", day=1, scene="s1",
                                 summary="x", deltas=skeleton, turn=1)])
    ln = w["systems"]["lore"]["lines"]["test_line"]
    assert ln["state"] == "暗"


def test_lore_created_with_explicit_state_liujie():
    """lore_created with state:'了结' → line stored with state '了结'."""
    r = _reg()
    skeleton = {**_BASE_SKELETON, "state": "了结"}
    w = project(r, [kernel_event("lore_created", day=1, scene="s1",
                                 summary="x", deltas=skeleton, turn=1)])
    ln = w["systems"]["lore"]["lines"]["test_line"]
    assert ln["state"] == "了结"


def test_lore_created_without_state_defaults_to_an():
    """lore_created with no state field → defaults to '暗' (backward compat)."""
    r = _reg()
    skeleton = {**_BASE_SKELETON}  # no state key
    assert "state" not in skeleton
    w = project(r, [kernel_event("lore_created", day=1, scene="s1",
                                 summary="x", deltas=skeleton, turn=1)])
    ln = w["systems"]["lore"]["lines"]["test_line"]
    assert ln["state"] == "暗"


def test_lore_created_existing_tests_unaffected():
    """Existing lore lines behave as before (stage_idx -1, state 暗); no `status` field."""
    r = _reg()
    w = project(r, [kernel_event("lore_created", day=1, scene="s1",
                                 summary="x", deltas=_BASE_SKELETON, turn=1)])
    ln = w["systems"]["lore"]["lines"]["test_line"]
    assert ln["stage_idx"] == -1
    assert "status" not in ln, "status must be absent; state is the lifecycle field"
    assert ln["clues_dropped"] == []
    # state defaults to 暗 (the single lifecycle source of truth)
    assert ln["state"] == "暗"
