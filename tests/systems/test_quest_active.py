"""T2: LoreSystem absorbs 明-side — quests commit section + 明账 inject.

Tests cover:
- surface op on a 暗 line → quest_surfaced event → state 明, surfaced_turn set
- advance op on a 明 line → quest_advanced event → summary updated (明账 content)
- advance op on a 暗 line → validation error (bug-guard)
- resolve op on a 暗 line → validation error (bug-guard)
- resolve op on a 明 line → quest_resolved event → state 了结 + resolved recorded
- 明账 inject: 明 lines present, 暗 lines absent
- validate: missing op / unknown op / missing id / unknown id → errors
"""
from __future__ import annotations

import pytest

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
    "id": "quest_01",
    "complexity": "simple",
    "about": "测试任务线",
    "secret": "隐情内容",
    "anchor": "town_a",
    "description": "任务描述",
    "trigger": "触发条件",
    "l3_anchor": "town_a_market",
    "stages": [{"hint": "线索A"}, {"hint": "线索B"}],
    "threshold": 50,
}


def _make_world_with_dark_line(reg=None, line_id="quest_01"):
    """Create a world with one 暗 line."""
    r = reg or _reg()
    skeleton = {**_BASE_SKELETON, "id": line_id, "state": "暗"}
    w = project(r, [kernel_event("lore_created", day=1, scene="s1",
                                 summary="创建暗线", deltas=skeleton, turn=1)])
    return r, w, [kernel_event("lore_created", day=1, scene="s1",
                               summary="创建暗线", deltas=skeleton, turn=1)]


def _make_world_with_ming_line(reg=None, line_id="quest_01"):
    """Create a world with one 明 line (surfaced from 暗)."""
    r, _, evts_dark = _make_world_with_dark_line(reg=reg, line_id=line_id)
    surf_ev = kernel_event("quest_surfaced", day=1, scene="s1",
                           summary="浮现", deltas={"id": line_id}, turn=2)
    w2 = project(r, evts_dark + [surf_ev])
    return r, w2, evts_dark + [surf_ev]


# ---------------------------------------------------------------------------
# commit_sections / event_types registration
# ---------------------------------------------------------------------------

def test_lore_system_has_quests_commit_section():
    """LoreSystem.commit_sections() must include 'quests'."""
    sys = LoreSystem()
    assert "quests" in sys.commit_sections()


def test_lore_system_has_quest_event_types():
    """LoreSystem.event_types() must include quest_surfaced, quest_advanced, quest_resolved."""
    sys = LoreSystem()
    et = sys.event_types()
    assert "quest_surfaced" in et
    assert "quest_advanced" in et
    assert "quest_resolved" in et
    # Existing types still present
    assert "lore_created" in et
    assert "lore_advanced" in et


# ---------------------------------------------------------------------------
# surface op: 暗 → 明
# ---------------------------------------------------------------------------

def test_surface_dark_line_sets_state_ming():
    """surface op on a 暗 line → state becomes '明'."""
    r, _, base_evts = _make_world_with_dark_line()
    w2 = project(r, base_evts + [kernel_event("quest_surfaced", day=1, scene="s1",
                                               summary="浮现", deltas={"id": "quest_01"}, turn=2)])
    lines = w2["systems"]["lore"]["lines"]
    assert lines["quest_01"]["state"] == "明"


def test_surface_dark_line_sets_surfaced_turn():
    """surface op → surfaced_turn is recorded."""
    r, _, base_evts = _make_world_with_dark_line()
    # After surfacing at turn=2
    w2 = project(r, base_evts + [kernel_event("quest_surfaced", day=1, scene="s1",
                                               summary="浮现", deltas={"id": "quest_01"}, turn=2)])
    ln = w2["systems"]["lore"]["lines"]["quest_01"]
    assert ln["state"] == "明"
    assert ln["surfaced_turn"] == 2


def test_surface_via_validate_and_to_events():
    """validate + to_events for 'quests' surface op generates quest_surfaced event."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    decl = [{"op": "surface", "id": "quest_01"}]
    errs = sys.validate("quests", decl, w)
    assert errs == [], f"unexpected errors: {errs}"
    evts = sys.to_events("quests", decl, turn=3, day=1, scene="s2")
    assert len(evts) == 1
    ev = evts[0]
    assert ev["type"] == "quest_surfaced"
    assert ev["deltas"]["id"] == "quest_01"


# ---------------------------------------------------------------------------
# advance op on 明 line
# ---------------------------------------------------------------------------

def test_advance_ming_line_updates_summary():
    """advance op on a 明 line → line summary updated."""
    r, _, base_evts = _make_world_with_dark_line()
    # Surface first, then advance
    w_adv = project(r, base_evts + [
        kernel_event("quest_surfaced", day=1, scene="s1",
                     summary="浮现", deltas={"id": "quest_01"}, turn=2),
        kernel_event("quest_advanced", day=1, scene="s2",
                     summary="推进", deltas={"id": "quest_01",
                                             "summary": "玩家找到了关键证据"}, turn=3),
    ])
    ln = w_adv["systems"]["lore"]["lines"]["quest_01"]
    assert ln["state"] == "明"
    assert ln["summary"] == "玩家找到了关键证据"


def test_advance_ming_via_validate_and_to_events():
    """validate + to_events for advance on a 明 line generates quest_advanced event."""
    r, _, base_evts = _make_world_with_dark_line()
    w_ming = project(r, base_evts + [kernel_event("quest_surfaced", day=1, scene="s1",
                                                   summary="浮现", deltas={"id": "quest_01"}, turn=2)])
    sys = LoreSystem()
    decl = [{"op": "advance", "id": "quest_01", "summary": "新进展"}]
    errs = sys.validate("quests", decl, w_ming)
    assert errs == [], f"unexpected errors: {errs}"
    evts = sys.to_events("quests", decl, turn=3, day=1, scene="s2")
    assert len(evts) == 1
    ev = evts[0]
    assert ev["type"] == "quest_advanced"
    assert ev["deltas"]["id"] == "quest_01"
    assert ev["deltas"]["summary"] == "新进展"


# ---------------------------------------------------------------------------
# Bug-guard: advance/resolve on a 暗 line → validation error
# ---------------------------------------------------------------------------

def test_advance_dark_line_is_validation_error():
    """advance op on a 暗 line → validation error (bug-guard: narrator cannot advance 暗 lines)."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    decl = [{"op": "advance", "id": "quest_01", "summary": "不应该推进暗线"}]
    errs = sys.validate("quests", decl, w)
    assert len(errs) > 0, "Expected validation error for advance on 暗 line"
    assert any(e.code != "" for e in errs)
    # Error should mention the state constraint
    hints = " ".join(e.hint for e in errs)
    assert "明" in hints or "暗" in hints or "advance" in hints or "推进" in hints


def test_resolve_dark_line_is_validation_error():
    """resolve op on a 暗 line → validation error (bug-guard)."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    decl = [{"op": "resolve", "id": "quest_01", "summary": "不应该收束暗线"}]
    errs = sys.validate("quests", decl, w)
    assert len(errs) > 0, "Expected validation error for resolve on 暗 line"
    hints = " ".join(e.hint for e in errs)
    assert "明" in hints or "暗" in hints or "resolve" in hints or "收束" in hints


# ---------------------------------------------------------------------------
# resolve op
# ---------------------------------------------------------------------------

def test_resolve_ming_line_sets_state_liujie():
    """resolve op on a 明 line → state becomes '了结'."""
    r, _, base_evts = _make_world_with_dark_line()
    w_res = project(r, base_evts + [
        kernel_event("quest_surfaced", day=1, scene="s1",
                     summary="浮现", deltas={"id": "quest_01"}, turn=2),
        kernel_event("quest_resolved", day=1, scene="s3",
                     summary="收束", deltas={"id": "quest_01",
                                             "summary": "玩家成功解决了任务",
                                             "by": "player"}, turn=4),
    ])
    ln = w_res["systems"]["lore"]["lines"]["quest_01"]
    assert ln["state"] == "了结"
    assert ln["resolved"]["by"] == "player"
    assert ln["resolved"]["summary"] == "玩家成功解决了任务"


def test_resolve_via_validate_and_to_events():
    """validate + to_events for resolve on a 明 line generates quest_resolved event with by=player."""
    r, _, base_evts = _make_world_with_dark_line()
    w_ming = project(r, base_evts + [kernel_event("quest_surfaced", day=1, scene="s1",
                                                   summary="浮现", deltas={"id": "quest_01"}, turn=2)])
    sys = LoreSystem()
    decl = [{"op": "resolve", "id": "quest_01", "summary": "任务完成"}]
    errs = sys.validate("quests", decl, w_ming)
    assert errs == [], f"unexpected errors: {errs}"
    evts = sys.to_events("quests", decl, turn=4, day=1, scene="s3")
    assert len(evts) == 1
    ev = evts[0]
    assert ev["type"] == "quest_resolved"
    assert ev["deltas"]["id"] == "quest_01"
    assert ev["deltas"].get("by") == "player"


# ---------------------------------------------------------------------------
# 明账 inject
# ---------------------------------------------------------------------------

def test_inject_renders_ming_lines():
    """inject fragment contains 明 line's summary; 暗 lines absent."""
    r, _, base_evts = _make_world_with_dark_line()
    # Surface to 明 + advance with summary
    w_ming = project(r, base_evts + [
        kernel_event("quest_surfaced", day=1, scene="s1",
                     summary="浮现", deltas={"id": "quest_01"}, turn=2),
        kernel_event("quest_advanced", day=1, scene="s2",
                     summary="推进", deltas={"id": "quest_01",
                                             "summary": "追踪神秘商人的下落"}, turn=3),
    ])
    sys = LoreSystem()
    frag = sys.inject({}, w_ming)
    # There should be a fragment (at minimum from the 明账 block)
    # We need to check that the inject doesn't return None when there are 明 lines
    # and that it includes the summary
    lines = w_ming["systems"]["lore"]["lines"]
    ming_lines = [ln for ln in lines.values() if ln.get("state") == "明"]
    assert len(ming_lines) == 1
    # If there are 明 lines, inject should return something containing the summary
    assert frag is not None
    assert "追踪神秘商人的下落" in frag.text


def test_inject_dark_line_not_in_mingzhang():
    """A 暗 line should NOT appear in the 明账 inject block."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    # Inject with only a 暗 line — 明账 portion should not render the line
    # (暗 lines show up via ambient clue dump, not 明账)
    frag = sys.inject({}, w)
    # The 明账 block specifically should not list quest_01 as a明 quest
    # (the fragment may still exist if there are ambient clues, but
    #  the 明账 section specifically should not contain quest_01 summary)
    lines = w["systems"]["lore"]["lines"]
    dark_line = lines["quest_01"]
    assert dark_line["state"] == "暗"
    # If frag exists, it should NOT contain 任务·明账 listing for this line
    # (the 暗 line has no summary in 明账)
    assert "任务·明账" not in (frag.text if frag else "")


def test_inject_only_ming_lines_in_mingzhang():
    """When there's one 明 and one 暗 line, only the 明 appears in the 明账 block."""
    r = _reg()
    # Create two lines: one 暗, one that will be surfaced to 明
    ev1 = kernel_event("lore_created", day=1, scene="s1", summary="创建暗线1",
                       deltas={**_BASE_SKELETON, "id": "quest_dark", "state": "暗"}, turn=1)
    ev2 = kernel_event("lore_created", day=1, scene="s1", summary="创建线2",
                       deltas={**_BASE_SKELETON, "id": "quest_ming", "state": "暗"}, turn=1)
    ev3 = kernel_event("quest_surfaced", day=1, scene="s2", summary="浮现",
                       deltas={"id": "quest_ming"}, turn=2)
    ev4 = kernel_event("quest_advanced", day=1, scene="s2", summary="推进",
                       deltas={"id": "quest_ming", "summary": "明线摘要内容"}, turn=2)
    w = project(r, [ev1, ev2, ev3, ev4])

    sys = LoreSystem()
    frag = sys.inject({}, w)
    assert frag is not None
    # 明 line's summary in the fragment
    assert "明线摘要内容" in frag.text
    # 暗 line id should NOT appear in 明账 context (it has no summary)
    # The 明账 block should only list 明 lines
    assert "quest_dark" not in frag.text


# ---------------------------------------------------------------------------
# validate: structural errors
# ---------------------------------------------------------------------------

def test_validate_missing_op():
    """Missing op → validation error."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    decl = [{"id": "quest_01"}]  # no op
    errs = sys.validate("quests", decl, w)
    assert any(e.code == "bad_enum" or "op" in e.field for e in errs), \
        f"Expected op error, got: {errs}"


def test_validate_unknown_op():
    """Unknown op → validation error."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    decl = [{"op": "destroy", "id": "quest_01"}]
    errs = sys.validate("quests", decl, w)
    assert any(e.code == "bad_enum" or "op" in e.field for e in errs), \
        f"Expected op error, got: {errs}"


def test_validate_missing_id():
    """Missing id → validation error."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    decl = [{"op": "surface"}]  # no id
    errs = sys.validate("quests", decl, w)
    assert any("id" in e.field or e.code == "missing" for e in errs), \
        f"Expected id error, got: {errs}"


def test_validate_unknown_id():
    """Unknown id (not in world lines) → validation error."""
    r, w, _ = _make_world_with_dark_line()
    sys = LoreSystem()
    decl = [{"op": "surface", "id": "nonexistent_id"}]
    errs = sys.validate("quests", decl, w)
    assert len(errs) > 0, f"Expected error for unknown id, got: {errs}"


def test_validate_surface_on_ming_line_is_error():
    """surface op on a 明 line → validation error (can only surface 暗 lines)."""
    r, _, base_evts = _make_world_with_dark_line()
    w_ming = project(r, base_evts + [kernel_event("quest_surfaced", day=1, scene="s1",
                                                   summary="浮现", deltas={"id": "quest_01"}, turn=2)])
    sys = LoreSystem()
    decl = [{"op": "surface", "id": "quest_01"}]
    errs = sys.validate("quests", decl, w_ming)
    assert len(errs) > 0, "Expected error: cannot surface a 明 line"
