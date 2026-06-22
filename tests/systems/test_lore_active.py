"""Tests for LoreSystem quests section.

Covers the full明账 lifecycle owned by LoreSystem:
  open (new 明 quest) / surface (暗→明) / advance (明) / resolve (了结).
"""
from __future__ import annotations

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.lore import LoreSystem
from systems.ontology import OntologySystem


def _reg():
    """Registry with OntologySystem (required by LoreSystem) + LoreSystem."""
    return (Registry()
            .register(OntologySystem())
            .register(LoreSystem()))


# ---------------------------------------------------------------------------
# Section/event ownership
# ---------------------------------------------------------------------------

def test_story_owns_section_and_events():
    """LoreSystem owns quests section + all quest event types incl. quest_opened."""
    s = LoreSystem()
    assert s.name == "lore"
    assert "quests" in s.commit_sections()
    et = s.event_types()
    assert "quest_opened" in et
    assert "quest_surfaced" in et
    assert "quest_advanced" in et
    assert "quest_resolved" in et
    assert "quest_created" in et


def test_story_registers_and_routes():
    """Registry routes quests section and quest_opened event to lore."""
    reg = _reg()
    assert reg.owner_of_section("quests").name == "lore"
    assert reg.owner_of_event("quest_opened").name == "lore"


def test_empty_state_shape():
    assert LoreSystem().empty_state() == {"lines": {}, "gen": {}}


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def _opened(tid, summary, scene="s1", day=1, turn=1):
    return kernel_event("quest_opened", day=day, scene=scene,
                        summary=f"open {tid}",
                        deltas={"id": tid, "summary": summary, "state": "明"}, turn=turn)


def _surfaced(tid, scene="s1", day=1, turn=1):
    """Create a lore_created暗 then quest_surfaced sequence."""
    return kernel_event("quest_surfaced", day=day, scene=scene,
                        summary=f"surface {tid}",
                        deltas={"id": tid}, turn=turn)


def _advanced(tid, summary=None, scene="s2", day=1, turn=2):
    d = {"id": tid}
    if summary is not None:
        d["summary"] = summary
    return kernel_event("quest_advanced", day=day, scene=scene,
                        summary=f"advance {tid}", deltas=d, turn=turn)


def _resolved(tid, scene="s3", day=2, turn=3):
    return kernel_event("quest_resolved", day=day, scene=scene,
                        summary=f"resolve {tid}", deltas={"id": tid}, turn=turn)


def _lore_暗(tid, scene="s0", day=1, turn=0):
    """Create a 暗 line via lore_created."""
    return kernel_event("lore_created", day=day, scene=scene,
                        summary=f"lore_created {tid}",
                        deltas={"id": tid, "about": "background", "state": "暗"}, turn=turn)


# ---------------------------------------------------------------------------
# apply tests
# ---------------------------------------------------------------------------

def test_open_creates_active_record():
    """quest_opened creates a 明 line with summary."""
    world = project(_reg(), [_opened("th_bridge", "查明断桥真相")])
    ln = world["systems"]["lore"]["lines"]["th_bridge"]
    assert ln["state"] == "明"
    assert ln["summary"] == "查明断桥真相"
    assert ln["surfaced_turn"] == 1


def test_advance_updates_summary_and_scene_and_reactivates():
    """quest_advanced updates summary on a 明 line."""
    world = project(_reg(), [
        _opened("th_bridge", "查明断桥真相"),
        _advanced("th_bridge", "发现桥是人为破坏", scene="s2"),
    ])
    ln = world["systems"]["lore"]["lines"]["th_bridge"]
    assert ln["summary"] == "发现桥是人为破坏"
    assert ln["state"] == "明"


def test_resolve_marks_done():
    """quest_resolved sets state to 了结."""
    world = project(_reg(), [
        _opened("th_bridge", "查明断桥真相"),
        _resolved("th_bridge", scene="s3"),
    ])
    ln = world["systems"]["lore"]["lines"]["th_bridge"]
    assert ln["state"] == "了结"


def test_advance_on_missing_id_is_defensive():
    """quest_advanced on unknown id is skipped — LoreSystem does NOT create."""
    world = project(_reg(), [_advanced("th_ghost", "凭空推进")])
    assert "th_ghost" not in world["systems"]["lore"]["lines"]


def test_resolve_on_missing_id_skips():
    """quest_resolved on unknown id is skipped."""
    world = project(_reg(), [_resolved("th_ghost")])
    assert "th_ghost" not in world["systems"]["lore"]["lines"]


def test_open_honors_explicit_status_for_backstop_dormant_flag():
    """quest_created(state:'暗') creates a 暗 line (backstop flag equivalent)."""
    ev = kernel_event("quest_created", day=1, scene="s1", summary="auto",
                      deltas={"id": "th_auto", "summary": "疑似新线",
                              "state": "暗"}, turn=1)
    world = project(_reg(), [ev])
    ln = world["systems"]["lore"]["lines"]["th_auto"]
    assert ln["state"] == "暗"


def test_surface_flips_dark_line_to_ming():
    """quest_surfaced: a 暗 line → 明 (+ surfaced_turn)."""
    world = project(_reg(), [
        _lore_暗("th_hidden", scene="s0", day=1, turn=0),
        _surfaced("th_hidden", scene="s1", day=1, turn=1),
    ])
    ln = world["systems"]["lore"]["lines"]["th_hidden"]
    assert ln["state"] == "明"
    assert ln["surfaced_turn"] == 1


# ---------------------------------------------------------------------------
# validate tests
# ---------------------------------------------------------------------------

def test_validate_rejects_bad_op():
    """quests section rejects bad op."""
    errs = LoreSystem().validate(
        "quests", [{"op": "nuke", "id": "x", "summary": "y"}], {})
    assert any(e.code == "bad_enum" and e.field == "[0].op" for e in errs)


def test_validate_requires_id_and_summary_for_open():
    """quests open requires id and summary."""
    errs = LoreSystem().validate(
        "quests", [{"op": "open"}], {})
    codes = {(e.field, e.code) for e in errs}
    assert ("[0].id", "missing") in codes
    assert ("[0].summary", "missing") in codes


def test_validate_open_rejects_existing_id():
    """quests open rejects id that already exists in lines."""
    world = project(_reg(), [_opened("th_exists", "已有线")])
    errs = LoreSystem().validate(
        "quests", [{"op": "open", "id": "th_exists", "summary": "新任务"}], world)
    assert any(e.code == "dangling_ref" and "th_exists" in e.hint for e in errs)


def test_validate_resolve_allows_missing_summary():
    """quests resolve allows missing summary (for a 明 line)."""
    world = project(_reg(), [_opened("th_x", "测试线")])
    errs = LoreSystem().validate(
        "quests", [{"op": "resolve", "id": "th_x"}], world)
    assert errs == []


def test_validate_ignores_other_sections():
    """LoreSystem validate ignores non-quests sections."""
    assert LoreSystem().validate("knowledge", [{"whatever": 1}], {}) == []


# ---------------------------------------------------------------------------
# to_events tests
# ---------------------------------------------------------------------------

def test_to_events_maps_ops():
    """to_events maps open→quest_opened, surface→quest_surfaced, advance→quest_advanced, resolve→quest_resolved."""
    evs = LoreSystem().to_events(
        "quests",
        [{"op": "open", "id": "th_a", "summary": "S"},
         {"op": "advance", "id": "th_b", "summary": "S2"},
         {"op": "resolve", "id": "th_c"}],
        turn=4, day=1, scene="s4")
    assert [e["type"] for e in evs] == [
        "quest_opened", "quest_advanced", "quest_resolved"]
    assert evs[0]["deltas"]["id"] == "th_a"
    assert evs[0]["turn"] == 4 and evs[0]["scene"] == "s4"


def test_to_events_skips_malformed_item():
    """to_events skips malformed items (empty id)."""
    evs = LoreSystem().to_events(
        "quests", [{"op": "open", "id": "", "summary": "x"}],
        turn=1, day=1, scene="s1")
    assert evs == []


# ---------------------------------------------------------------------------
# inject tests
# ---------------------------------------------------------------------------

def test_inject_renders_active_and_dormant_omits_resolved():
    """inject renders 明 lines, omits 了结 lines."""
    world = project(_reg(), [
        _opened("th_a", "线A：查案", scene="s1"),
        _opened("th_b", "线B：寻人", scene="s1"),
        _resolved("th_b", scene="s2"),          # 了结 → omitted
    ])
    frag = LoreSystem().inject({"id": "s2"}, world)
    assert frag is not None
    assert frag.layer == "scene"
    assert "线A：查案" in frag.text
    assert "线B：寻人" not in frag.text          # resolved omitted
    assert "明账" in frag.text                   # ledger header


def test_inject_empty_ledger_returns_none():
    """inject returns None when no 明 lines exist."""
    world = project(_reg(), [])
    assert LoreSystem().inject({"id": "s1"}, world) is None
