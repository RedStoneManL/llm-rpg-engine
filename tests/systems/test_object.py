"""Tests for ObjectSystem — Task 1 (TDD: items + possession + inventory inject)."""
from __future__ import annotations

import pytest
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import ValidationError, Fragment
from systems.ontology import OntologySystem
from systems.object import ObjectSystem
from facts.graph import FactGraph


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reg():
    return Registry().register(OntologySystem()).register(ObjectSystem())


def _ev(typ, day=1, scene="s1", **deltas):
    return kernel_event(typ, day=day, scene=scene, summary=f"{typ}", deltas=deltas)


def _world():
    """Empty world with both systems registered."""
    r = _reg()
    return project(r, [])


# ---------------------------------------------------------------------------
# apply — object_created
# ---------------------------------------------------------------------------

def test_object_created_makes_entity():
    r = _reg()
    evs = [_ev("object_created", id="神剑", tier="tracked", material="秘银")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("神剑")
    assert e is not None
    assert e.etype == "Object"


def test_object_created_stores_attrs():
    r = _reg()
    evs = [_ev("object_created", id="神剑", tier="tracked", material="秘银", weight=5)]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("神剑")
    assert e.attrs.get("material") == "秘银"
    assert e.attrs.get("weight") == 5


def test_object_created_default_tier():
    r = _reg()
    evs = [_ev("object_created", id="普通石头")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("普通石头")
    assert e is not None
    assert e.tier == "mentioned"


def test_object_created_custom_tier():
    r = _reg()
    evs = [_ev("object_created", id="神剑", tier="tracked")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("神剑")
    assert e.tier == "tracked"


# ---------------------------------------------------------------------------
# apply — item_transferred (possession / held_by)
# ---------------------------------------------------------------------------

def test_item_transferred_sets_held_by():
    r = _reg()
    evs = [
        _ev("object_created", id="神剑", tier="tracked"),
        _ev("item_transferred", day=1, item="神剑", to="主角"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    holders = g.neighbors("神剑", "held_by", day=1)
    assert holders == ["主角"]


def test_item_transferred_second_transfer_supersedes():
    """A second transfer replaces the previous holder (single-valued)."""
    r = _reg()
    evs = [
        _ev("object_created", id="神剑", tier="tracked"),
        _ev("item_transferred", day=1, item="神剑", to="主角"),
        _ev("item_transferred", day=5, item="神剑", to="反派"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.neighbors("神剑", "held_by", day=1) == ["主角"]
    assert g.neighbors("神剑", "held_by", day=5) == ["反派"]


def test_item_transferred_later_supersedes_earlier():
    """After transfer, the new holder is the only neighbor at later day."""
    r = _reg()
    evs = [
        _ev("object_created", id="神剑", tier="tracked"),
        _ev("item_transferred", day=1, item="神剑", to="主角"),
        _ev("item_transferred", day=5, item="神剑", to="反派"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    # At day 5, only the new holder
    holders_at_5 = g.neighbors("神剑", "held_by", day=5)
    assert holders_at_5 == ["反派"]
    assert "主角" not in holders_at_5


# ---------------------------------------------------------------------------
# validate — "items" section
# ---------------------------------------------------------------------------

def _world_with_item_and_person():
    g = FactGraph()
    g.add_entity("神剑", "Object")
    g.add_entity("主角", "Person")
    return {"systems": {"ontology": g, "object": {}}}


def test_validate_item_transferred_missing_item_entity():
    """item_transferred whose 'item' entity is missing → dangling_ref."""
    os = ObjectSystem()
    w = _world_with_item_and_person()
    errs = os.validate("items", [{"op": "transfer", "item": "不存在的剑", "to": "主角"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_item_transferred_missing_to_entity():
    """item_transferred whose 'to' entity is missing → dangling_ref."""
    os = ObjectSystem()
    w = _world_with_item_and_person()
    errs = os.validate("items", [{"op": "transfer", "item": "神剑", "to": "不存在的人"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_item_created_missing_id():
    """object_created missing id → missing error."""
    os = ObjectSystem()
    w = _world_with_item_and_person()
    errs = os.validate("items", [{"op": "create", "material": "木"}], w)
    assert any(e.code == "missing" for e in errs)


def test_validate_valid_transfer_no_errors():
    os = ObjectSystem()
    w = _world_with_item_and_person()
    errs = os.validate("items", [{"op": "transfer", "item": "神剑", "to": "主角"}], w)
    assert errs == []


def test_validate_valid_create_no_errors():
    os = ObjectSystem()
    w = _world_with_item_and_person()
    errs = os.validate("items", [{"op": "create", "id": "新剑", "material": "铁"}], w)
    assert errs == []


# ---------------------------------------------------------------------------
# to_events — "items" section
# ---------------------------------------------------------------------------

def test_to_events_create_op():
    os = ObjectSystem()
    decl = [{"op": "create", "id": "神剑", "tier": "tracked", "material": "秘银"}]
    evs = os.to_events("items", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "object_created"
    assert evs[0]["deltas"]["id"] == "神剑"


def test_to_events_transfer_op():
    os = ObjectSystem()
    decl = [{"op": "transfer", "item": "神剑", "to": "主角"}]
    evs = os.to_events("items", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "item_transferred"
    assert evs[0]["deltas"]["item"] == "神剑"
    assert evs[0]["deltas"]["to"] == "主角"


def test_to_events_unknown_section_empty():
    os = ObjectSystem()
    decl = [{"op": "create", "id": "神剑"}]
    evs = os.to_events("unknown_section", decl, turn=1, day=1, scene="s1")
    assert evs == []


# ---------------------------------------------------------------------------
# inject — inventory Fragment for protagonist
# ---------------------------------------------------------------------------

def _world_with_inventory():
    r = _reg()
    evs = [
        _ev("object_created", id="神剑", tier="tracked"),
        _ev("object_created", id="魔法戒指", tier="tracked"),
        _ev("object_created", id="普通石头", tier="mentioned"),
        _ev("item_transferred", day=1, item="神剑", to="主角"),
        _ev("item_transferred", day=1, item="魔法戒指", to="主角"),
        _ev("item_transferred", day=1, item="普通石头", to="反派"),
    ]
    return project(r, evs)


def test_inject_returns_fragment_with_held_items():
    os = ObjectSystem()
    w = _world_with_inventory()
    scene = {"protagonist": "主角", "day": 1}
    frag = os.inject(scene, w)
    assert isinstance(frag, Fragment)
    assert frag.system == "object"
    assert frag.layer == "scene"
    assert "神剑" in frag.text
    assert "魔法戒指" in frag.text


def test_inject_excludes_items_held_by_others():
    os = ObjectSystem()
    w = _world_with_inventory()
    scene = {"protagonist": "主角", "day": 1}
    frag = os.inject(scene, w)
    assert "普通石头" not in frag.text


def test_inject_no_items_returns_none():
    """Protagonist with no items → None."""
    r = _reg()
    evs = [_ev("object_created", id="神剑", tier="tracked")]
    w = project(r, evs)
    os = ObjectSystem()
    scene = {"protagonist": "主角", "day": 1}
    result = os.inject(scene, w)
    assert result is None


def test_inject_no_protagonist_returns_none():
    os = ObjectSystem()
    w = _world_with_inventory()
    scene = {"day": 1}
    result = os.inject(scene, w)
    assert result is None


def test_inject_transferred_away_not_shown():
    """Item transferred away before query day should not appear."""
    r = _reg()
    evs = [
        _ev("object_created", id="神剑", tier="tracked"),
        _ev("item_transferred", day=1, item="神剑", to="主角"),
        _ev("item_transferred", day=5, item="神剑", to="反派"),
    ]
    w = project(r, evs)
    os = ObjectSystem()
    # At day 5, 主角 no longer holds 神剑
    scene = {"protagonist": "主角", "day": 5}
    result = os.inject(scene, w)
    assert result is None or "神剑" not in result.text


# ---------------------------------------------------------------------------
# NEW: validate — missing required fields yield code="missing"
# ---------------------------------------------------------------------------


class TestValidateMissingRequiredFields:
    """validate() must catch every field that apply() reads via bare subscript."""

    def _os(self):
        return ObjectSystem()

    def _empty_world(self):
        return {"systems": {"ontology": FactGraph()}}

    # ---- create op --------------------------------------------------------

    def test_create_missing_id_yields_missing(self):
        """op=create without 'id' → ValidationError code='missing' at [0].id."""
        errs = self._os().validate("items", [{"op": "create", "material": "铁"}], self._empty_world())
        codes = [(e.field, e.code) for e in errs]
        assert ("[0].id", "missing") in codes

    def test_create_id_wrong_type_yields_missing(self):
        """op=create with id=123 (not str) → missing error."""
        errs = self._os().validate("items", [{"op": "create", "id": 123}], self._empty_world())
        assert any(e.field == "[0].id" and e.code == "missing" for e in errs)

    def test_create_valid_id_no_missing_error(self):
        """op=create with valid string id → no missing errors."""
        errs = self._os().validate("items", [{"op": "create", "id": "新剑"}], self._empty_world())
        assert not any(e.code == "missing" for e in errs)

    # ---- transfer op — 'item' field ---------------------------------------

    def test_transfer_missing_item_key_yields_missing(self):
        """op=transfer without 'item' key → code='missing' at [0].item."""
        errs = self._os().validate("items", [{"op": "transfer", "to": "主角"}], self._empty_world())
        assert any(e.field == "[0].item" and e.code == "missing" for e in errs)

    def test_transfer_item_none_yields_missing(self):
        """op=transfer with item=None → code='missing' at [0].item."""
        errs = self._os().validate("items", [{"op": "transfer", "item": None, "to": "主角"}], self._empty_world())
        assert any(e.field == "[0].item" and e.code == "missing" for e in errs)

    def test_transfer_item_wrong_type_yields_missing(self):
        """op=transfer with item=42 → code='missing'."""
        errs = self._os().validate("items", [{"op": "transfer", "item": 42, "to": "主角"}], self._empty_world())
        assert any(e.field == "[0].item" and e.code == "missing" for e in errs)

    # ---- transfer op — 'to' field -----------------------------------------

    def test_transfer_missing_to_key_yields_missing(self):
        """op=transfer without 'to' key → code='missing' at [0].to."""
        errs = self._os().validate("items", [{"op": "transfer", "item": "神剑"}], self._empty_world())
        assert any(e.field == "[0].to" and e.code == "missing" for e in errs)

    def test_transfer_to_none_yields_missing(self):
        """op=transfer with to=None → code='missing' at [0].to."""
        errs = self._os().validate("items", [{"op": "transfer", "item": "神剑", "to": None}], self._empty_world())
        assert any(e.field == "[0].to" and e.code == "missing" for e in errs)

    def test_transfer_to_wrong_type_yields_missing(self):
        """op=transfer with to=99 → code='missing'."""
        errs = self._os().validate("items", [{"op": "transfer", "item": "神剑", "to": 99}], self._empty_world())
        assert any(e.field == "[0].to" and e.code == "missing" for e in errs)

    def test_transfer_both_missing_two_errors(self):
        """op=transfer with neither 'item' nor 'to' → errors for both fields."""
        errs = self._os().validate("items", [{"op": "transfer"}], self._empty_world())
        fields_with_missing = {e.field for e in errs if e.code == "missing"}
        assert "[0].item" in fields_with_missing
        assert "[0].to" in fields_with_missing

    # ---- multi-item: second item's index is reported correctly ------------

    def test_second_item_missing_field_reports_index_1(self):
        """Missing field in second item → field path starts with [1]."""
        g = FactGraph()
        g.add_entity("已知剑", "Object")
        g.add_entity("已知人", "Person")
        world = {"systems": {"ontology": g}}
        decl = [
            {"op": "transfer", "item": "已知剑", "to": "已知人"},   # valid
            {"op": "transfer", "to": "已知人"},                      # missing 'item'
        ]
        errs = self._os().validate("items", decl, world)
        assert any(e.field == "[1].item" and e.code == "missing" for e in errs)


# ---------------------------------------------------------------------------
# NEW: complete valid decl validates clean and round-trips through to_events/apply
# ---------------------------------------------------------------------------


class TestValidToEventsApplyRoundtrip:
    """A fully-formed decl: validate=[], to_events produces events, apply works."""

    def test_create_roundtrip(self):
        os = ObjectSystem()
        decl = [{"op": "create", "id": "圣剑", "tier": "tracked", "material": "秘银"}]
        world = {"systems": {"ontology": FactGraph()}}

        errs = os.validate("items", decl, world)
        assert errs == [], f"Unexpected errors: {errs}"

        evs = os.to_events("items", decl, turn=1, day=1, scene="s1")
        assert len(evs) == 1
        assert evs[0]["type"] == "object_created"

        for ev in evs:
            os.apply(world, ev)
        g: FactGraph = world["systems"]["ontology"]
        assert g.get_entity("圣剑") is not None

    def test_transfer_roundtrip(self):
        os = ObjectSystem()
        g = FactGraph()
        g.add_entity("圣剑", "Object")
        g.add_entity("勇者", "Person")
        world = {"systems": {"ontology": g}}

        decl = [{"op": "transfer", "item": "圣剑", "to": "勇者"}]

        errs = os.validate("items", decl, world)
        assert errs == [], f"Unexpected errors: {errs}"

        evs = os.to_events("items", decl, turn=1, day=1, scene="s1")
        assert len(evs) == 1
        assert evs[0]["type"] == "item_transferred"

        for ev in evs:
            os.apply(world, ev)
        holders = g.neighbors("圣剑", "held_by", day=1)
        assert "勇者" in holders


# ---------------------------------------------------------------------------
# NEW: defensive apply — malformed events must not raise
# ---------------------------------------------------------------------------


class TestDefensiveApply:
    """apply() on events missing required deltas must log+skip, never raise."""

    def _world(self):
        return {"systems": {"ontology": FactGraph()}}

    def test_object_created_missing_id_does_not_raise(self):
        """apply(object_created) with no 'id' in deltas → silent skip, no KeyError."""
        os = ObjectSystem()
        w = self._world()
        ev = kernel_event("object_created", day=1, scene="s1", summary="bad",
                          deltas={"tier": "mentioned"})  # 'id' absent
        os.apply(w, ev)  # must not raise
        # Nothing should have been created
        g: FactGraph = w["systems"]["ontology"]
        assert len(g.entities) == 0

    def test_item_transferred_missing_item_does_not_raise(self):
        """apply(item_transferred) with no 'item' → silent skip, no KeyError."""
        os = ObjectSystem()
        w = self._world()
        ev = kernel_event("item_transferred", day=1, scene="s1", summary="bad",
                          deltas={"to": "勇者"})  # 'item' absent
        os.apply(w, ev)  # must not raise

    def test_item_transferred_missing_to_does_not_raise(self):
        """apply(item_transferred) with no 'to' → silent skip, no KeyError."""
        os = ObjectSystem()
        w = self._world()
        ev = kernel_event("item_transferred", day=1, scene="s1", summary="bad",
                          deltas={"item": "神剑"})  # 'to' absent
        os.apply(w, ev)  # must not raise

    def test_item_transferred_empty_deltas_does_not_raise(self):
        """apply(item_transferred) with completely empty deltas → silent skip."""
        os = ObjectSystem()
        w = self._world()
        ev = kernel_event("item_transferred", day=1, scene="s1", summary="bad", deltas={})
        os.apply(w, ev)  # must not raise


# ---------------------------------------------------------------------------
# NEW: defensive to_events — malformed decl must not raise
# ---------------------------------------------------------------------------


class TestDefensiveToEvents:
    """to_events() on malformed decl items must not raise."""

    def test_create_missing_id_does_not_raise(self):
        """to_events with op=create missing 'id' → no exception, returns event."""
        os = ObjectSystem()
        decl = [{"op": "create", "material": "铁"}]  # no 'id'
        evs = os.to_events("items", decl, turn=1, day=1, scene="s1")
        assert isinstance(evs, list)

    def test_transfer_missing_item_and_to_does_not_raise(self):
        """to_events with op=transfer missing both 'item' and 'to' → no exception."""
        os = ObjectSystem()
        decl = [{"op": "transfer"}]
        evs = os.to_events("items", decl, turn=1, day=1, scene="s1")
        assert isinstance(evs, list)
