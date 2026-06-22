"""tests.systems.test_lore_endgame — Task 1: LoreSystem new event types.

Tests:
  - quest_world_resolved → state=了结, resolved.by=world_rescue, pending_finale cleared
  - quest_catastrophe    → state=了结, resolved.by=catastrophe, pending_finale cleared
  - both are replay-safe (idempotent: re-applying does not crash, stays 了结)
  - NO `status` field on lines (state is the sole lifecycle field)
"""
from __future__ import annotations

import pytest

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(LoreSystem())
    return r


_COMPLEX_SKELETON = {
    "id": "dark_complex",
    "complexity": "complex",
    "about": "神秘失踪事件",
    "secret": "背后是邪教",
    "anchor": "town1",
    "description": "镇上神秘失踪",
    "trigger": "调查失踪",
    "l3_anchor": "town1_market",
    "stages": [{"hint": "h1"}, {"hint": "h2"}, {"hint": "h3"},
               {"hint": "h4"}, {"hint": "h5"}],
    "threshold": 0,
}


def _create_event():
    return kernel_event("lore_created", day=1, scene="s1", summary="create",
                        deltas=_COMPLEX_SKELETON, turn=1)


def _finale_event():
    """Mark the line as pending_finale."""
    return kernel_event("quest_finale_due", day=10, scene="s1", summary="finale",
                        deltas={"id": "dark_complex"}, turn=5)


def _world_resolved_event(by="world_rescue"):
    return kernel_event(
        "quest_world_resolved", day=12, scene="s1",
        summary="世界自行了结",
        deltas={"id": "dark_complex", "by": by, "summary": "外力介入，事态平息"},
        turn=6,
    )


def _catastrophe_event():
    return kernel_event(
        "quest_catastrophe", day=12, scene="s1",
        summary="终局灾难",
        deltas={"id": "dark_complex", "summary": "失控，波及region1", "anchor": "region1"},
        turn=6,
    )


# ---------------------------------------------------------------------------
# quest_world_resolved
# ---------------------------------------------------------------------------

class TestQuestWorldResolved:
    def test_state_becomes_liujie(self):
        r = _reg()
        w = project(r, [_create_event(), _world_resolved_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["state"] == "了结"

    def test_no_status_field_on_liujie_line(self):
        """state is the single lifecycle truth — no `status` key should be set."""
        r = _reg()
        w = project(r, [_create_event(), _world_resolved_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert "status" not in ln, f"status field must be absent; got {ln.get('status')!r}"

    def test_resolved_by_is_world_rescue(self):
        r = _reg()
        w = project(r, [_create_event(), _world_resolved_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["resolved"]["by"] == "world_rescue"

    def test_resolved_summary_is_set(self):
        r = _reg()
        w = project(r, [_create_event(), _world_resolved_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["resolved"]["summary"] == "外力介入，事态平息"

    def test_pending_finale_cleared(self):
        r = _reg()
        w = project(r, [_create_event(), _finale_event(), _world_resolved_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        # pending_finale should be falsy (False, None, or key absent)
        assert not ln.get("pending_finale")

    def test_default_by_is_world_rescue_when_not_in_deltas(self):
        """If 'by' not in deltas, default should be 'world_rescue'."""
        r = _reg()
        ev = kernel_event(
            "quest_world_resolved", day=12, scene="s1", summary="x",
            deltas={"id": "dark_complex", "summary": "test"},
            turn=6,
        )
        w = project(r, [_create_event(), ev])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["resolved"]["by"] == "world_rescue"

    def test_by_world_rescue_finale_preserved(self):
        """Custom by value (e.g. 'world_rescue:finale') is preserved."""
        r = _reg()
        w = project(r, [_create_event(), _world_resolved_event(by="world_rescue:finale")])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["resolved"]["by"] == "world_rescue:finale"

    def test_idempotent_replay_no_crash(self):
        """Re-applying quest_world_resolved to an already 了结 line → no crash."""
        r = _reg()
        events = [_create_event(), _world_resolved_event(), _world_resolved_event()]
        w = project(r, events)  # must not raise
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["state"] == "了结"

    def test_idempotent_stays_liujie(self):
        """After duplicate event, state is still 了结 (not changed to something else)."""
        r = _reg()
        events = [
            _create_event(),
            _world_resolved_event(by="world_rescue"),
            _world_resolved_event(by="world_rescue:finale"),  # second apply should be no-op
        ]
        w = project(r, events)
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["state"] == "了结"
        # The first by should stick (idempotent: second is no-op)
        assert ln["resolved"]["by"] == "world_rescue"


# ---------------------------------------------------------------------------
# quest_catastrophe
# ---------------------------------------------------------------------------

class TestQuestCatastrophe:
    def test_state_becomes_liujie(self):
        r = _reg()
        w = project(r, [_create_event(), _catastrophe_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["state"] == "了结"

    def test_no_status_field_on_liujie_line(self):
        """state is the single lifecycle truth — no `status` key should be set."""
        r = _reg()
        w = project(r, [_create_event(), _catastrophe_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert "status" not in ln, f"status field must be absent; got {ln.get('status')!r}"

    def test_resolved_by_is_catastrophe(self):
        r = _reg()
        w = project(r, [_create_event(), _catastrophe_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["resolved"]["by"] == "catastrophe"

    def test_resolved_summary_is_set(self):
        r = _reg()
        w = project(r, [_create_event(), _catastrophe_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["resolved"]["summary"] == "失控，波及region1"

    def test_pending_finale_cleared(self):
        r = _reg()
        w = project(r, [_create_event(), _finale_event(), _catastrophe_event()])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert not ln.get("pending_finale")

    def test_default_by_is_catastrophe_when_not_in_deltas(self):
        """If 'by' not in deltas, default should be 'catastrophe'."""
        r = _reg()
        ev = kernel_event(
            "quest_catastrophe", day=12, scene="s1", summary="x",
            deltas={"id": "dark_complex", "summary": "test", "anchor": "region1"},
            turn=6,
        )
        w = project(r, [_create_event(), ev])
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["resolved"]["by"] == "catastrophe"

    def test_idempotent_replay_no_crash(self):
        """Re-applying quest_catastrophe to an already 了结 line → no crash."""
        r = _reg()
        events = [_create_event(), _catastrophe_event(), _catastrophe_event()]
        w = project(r, events)  # must not raise
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["state"] == "了结"

    def test_idempotent_stays_liujie(self):
        r = _reg()
        events = [
            _create_event(),
            _catastrophe_event(),
            _catastrophe_event(),
        ]
        w = project(r, events)
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["state"] == "了结"

    def test_cross_idempotent_world_resolved_then_catastrophe(self):
        """world_resolved then catastrophe → second is no-op, stays 了结 with first's by."""
        r = _reg()
        events = [_create_event(), _world_resolved_event(), _catastrophe_event()]
        w = project(r, events)
        ln = w["systems"]["lore"]["lines"]["dark_complex"]
        assert ln["state"] == "了结"
        # First event wins; catastrophe is no-op
        assert ln["resolved"]["by"] == "world_rescue"


# ---------------------------------------------------------------------------
# event_types registration
# ---------------------------------------------------------------------------

class TestEventTypesRegistration:
    def test_quest_world_resolved_in_event_types(self):
        ls = LoreSystem()
        assert "quest_world_resolved" in ls.event_types()

    def test_quest_catastrophe_in_event_types(self):
        ls = LoreSystem()
        assert "quest_catastrophe" in ls.event_types()
