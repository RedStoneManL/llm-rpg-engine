"""Tests for CharacterSystem — Tasks 1 and 2."""
from __future__ import annotations

import pytest
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from kernel.contextsystem import ValidationError, Fragment
from systems.ontology import OntologySystem
from systems.character import CharacterSystem
from facts.graph import FactGraph


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reg():
    return Registry().register(OntologySystem()).register(CharacterSystem())


def _ev(typ, day=1, scene="s1", **deltas):
    return kernel_event(typ, day=day, scene=scene, summary=f"{typ}", deltas=deltas)


def _world():
    """Empty world with both systems registered."""
    r = _reg()
    return project(r, [])


# ---------------------------------------------------------------------------
# Task 1: apply — character_created (full)
# ---------------------------------------------------------------------------

def test_character_created_full_entity_in_graph():
    r = _reg()
    evs = [_ev("character_created", id="艾拉", sketch="一位沉默的弓手", goal="寻找失踪的弟弟",
               past="曾是王国侍卫", hidden="内心藏着秘密", tier="tracked")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("艾拉")
    assert e is not None
    assert e.etype == "Person"
    assert e.tier == "tracked"


def test_character_created_full_facts_set():
    r = _reg()
    day = 1
    evs = [_ev("character_created", day=day, id="艾拉", sketch="一位沉默的弓手",
               goal="寻找失踪的弟弟", past="曾是王国侍卫", hidden="内心藏着秘密", tier="tracked")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.value_at("艾拉", "sketch", day) == "一位沉默的弓手"
    assert g.value_at("艾拉", "goal", day) == "寻找失踪的弟弟"
    assert g.value_at("艾拉", "past", day) == "曾是王国侍卫"
    assert g.value_at("艾拉", "hidden", day) == "内心藏着秘密"


# ---------------------------------------------------------------------------
# Task 1: apply — character_created (minimal, the "纯粹之人" case)
# ---------------------------------------------------------------------------

def test_character_created_minimal_no_error():
    """A minimal create with only id+sketch+goal must succeed with no errors."""
    r = _reg()
    evs = [_ev("character_created", id="纯粹之人", sketch="一个简单的人", goal="活下去")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    e = g.get_entity("纯粹之人")
    assert e is not None
    assert e.etype == "Person"


def test_character_created_minimal_past_hidden_absent():
    """past/hidden facts must be absent (value_at → None) in minimal create."""
    r = _reg()
    day = 1
    evs = [_ev("character_created", day=day, id="纯粹之人", sketch="一个简单的人", goal="活下去")]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.value_at("纯粹之人", "past", day) is None
    assert g.value_at("纯粹之人", "hidden", day) is None


# ---------------------------------------------------------------------------
# Task 1: apply — character_evolved
# ---------------------------------------------------------------------------

def test_character_evolved_sets_predicate():
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟"),
        _ev("character_evolved", day=2, id="艾拉", predicate="mood", value="哀恸"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.value_at("艾拉", "mood", 2) == "哀恸"


def test_character_evolved_supersedes_prior_value():
    """A second evolve supersedes; point-in-time resolution works; history length == 2."""
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟"),
        _ev("character_evolved", day=2, id="艾拉", predicate="mood", value="哀恸"),
        _ev("character_evolved", day=5, id="艾拉", predicate="mood", value="平静"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.value_at("艾拉", "mood", 2) == "哀恸"
    assert g.value_at("艾拉", "mood", 5) == "平静"
    assert len(g.fact_history("艾拉", "mood")) == 2


# ---------------------------------------------------------------------------
# Task 1: apply — relationship_changed
# ---------------------------------------------------------------------------

def test_relationship_changed_sets_trust_fact():
    r = _reg()
    day = 3
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟"),
        _ev("relationship_changed", day=day, id="艾拉", toward="主角", value="敌对"),
    ]
    w = project(r, evs)
    g: FactGraph = w["systems"]["ontology"]
    assert g.value_at("艾拉", "trust:主角", day) == "敌对"


# ---------------------------------------------------------------------------
# Task 1: validate — "cast" section
# ---------------------------------------------------------------------------

def _world_with_character(char_id="艾拉"):
    g = FactGraph()
    g.add_entity(char_id, "Person")
    return {"systems": {"ontology": g, "character": {}}}


def test_validate_create_missing_sketch():
    cs = CharacterSystem()
    w = _world_with_character()
    errs = cs.validate("cast", [{"op": "create", "id": "新角色", "goal": "某个目标"}], w)
    assert any(e.code == "missing" and "sketch" in e.field for e in errs)


def test_validate_create_missing_goal():
    cs = CharacterSystem()
    w = _world_with_character()
    errs = cs.validate("cast", [{"op": "create", "id": "新角色", "sketch": "某人"}], w)
    assert any(e.code == "missing" and "goal" in e.field for e in errs)


def test_validate_create_missing_id():
    cs = CharacterSystem()
    w = _world_with_character()
    errs = cs.validate("cast", [{"op": "create", "sketch": "某人", "goal": "某目标"}], w)
    assert any(e.code == "missing" and "id" in e.field for e in errs)


def test_validate_evolve_dangling_ref():
    """character_evolved with non-existent id → dangling_ref."""
    cs = CharacterSystem()
    w = _world_with_character()
    errs = cs.validate("cast", [{"op": "evolve", "id": "不存在的人", "predicate": "mood", "value": "哀恸"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_relationship_dangling_ref():
    """relationship_changed with non-existent id → dangling_ref."""
    cs = CharacterSystem()
    w = _world_with_character()
    errs = cs.validate("cast", [{"op": "relationship", "id": "不存在的人", "toward": "主角", "value": "敌对"}], w)
    assert any(e.code == "dangling_ref" for e in errs)


def test_validate_minimal_create_no_errors():
    """Anti-脸谱guarantee: minimal create (id+sketch+goal, NO facets) must produce ZERO errors."""
    cs = CharacterSystem()
    w = _world_with_character()
    errs = cs.validate("cast", [{"op": "create", "id": "纯粹之人", "sketch": "朴素的人", "goal": "活下去"}], w)
    assert errs == [], f"Expected no errors for minimal create, got: {errs}"


# ---------------------------------------------------------------------------
# C1: block cross-system predicate forging in evolve op
# ---------------------------------------------------------------------------

def test_validate_evolve_reserved_predicate_knows():
    """evolve with predicate='knows:x' must produce a 'reserved' error (C1)."""
    cs = CharacterSystem()
    g = FactGraph()
    g.add_entity("艾拉", "Person")
    w = {"systems": {"ontology": g, "character": {}}}
    errs = cs.validate("cast", [{"op": "evolve", "id": "艾拉",
                                  "predicate": "knows:somekey", "value": "true"}], w)
    assert any(e.code == "reserved" for e in errs), f"Expected reserved error, got: {errs}"


def test_validate_evolve_reserved_predicate_rank():
    """evolve with predicate='rank:F' must produce a 'reserved' error (C1)."""
    cs = CharacterSystem()
    g = FactGraph()
    g.add_entity("艾拉", "Person")
    w = {"systems": {"ontology": g, "character": {}}}
    errs = cs.validate("cast", [{"op": "evolve", "id": "艾拉",
                                  "predicate": "rank:F", "value": "士兵"}], w)
    assert any(e.code == "reserved" for e in errs), f"Expected reserved error, got: {errs}"


def test_validate_evolve_normal_predicate_no_reserved_error():
    """evolve with predicate='mood' (no colon / reserved prefix) must pass (C1)."""
    cs = CharacterSystem()
    g = FactGraph()
    g.add_entity("艾拉", "Person")
    w = {"systems": {"ontology": g, "character": {}}}
    errs = cs.validate("cast", [{"op": "evolve", "id": "艾拉",
                                  "predicate": "mood", "value": "平静"}], w)
    reserved_errs = [e for e in errs if e.code == "reserved"]
    assert reserved_errs == [], f"Unexpected reserved errors: {reserved_errs}"


# ---------------------------------------------------------------------------
# Task 1: to_events — "cast" section
# ---------------------------------------------------------------------------

def test_to_events_create_op():
    cs = CharacterSystem()
    decl = [{"op": "create", "id": "艾拉", "sketch": "弓手", "goal": "找弟弟", "tier": "tracked"}]
    evs = cs.to_events("cast", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "character_created"
    assert evs[0]["deltas"]["id"] == "艾拉"


def test_to_events_default_op_is_create():
    """Items without 'op' default to character_created."""
    cs = CharacterSystem()
    decl = [{"id": "艾拉", "sketch": "弓手", "goal": "找弟弟"}]
    evs = cs.to_events("cast", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "character_created"


def test_to_events_evolve_op():
    cs = CharacterSystem()
    decl = [{"op": "evolve", "id": "艾拉", "predicate": "mood", "value": "哀恸"}]
    evs = cs.to_events("cast", decl, turn=1, day=2, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "character_evolved"
    assert evs[0]["deltas"]["predicate"] == "mood"


def test_to_events_relationship_op():
    cs = CharacterSystem()
    decl = [{"op": "relationship", "id": "艾拉", "toward": "主角", "value": "敌对"}]
    evs = cs.to_events("cast", decl, turn=1, day=3, scene="s1")
    assert len(evs) == 1
    assert evs[0]["type"] == "relationship_changed"
    assert evs[0]["deltas"]["toward"] == "主角"


# ---------------------------------------------------------------------------
# Task 2: inject — present-character cards
# ---------------------------------------------------------------------------

def _world_with_characters():
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="一位沉默的弓手", goal="寻找失踪的弟弟", tier="tracked"),
        _ev("character_created", day=1, id="主角", sketch="故事的主人公", goal="成就伟业", tier="tracked"),
        _ev("character_evolved", day=2, id="艾拉", predicate="mood", value="忧郁"),
    ]
    return project(r, evs)


def test_inject_present_character_in_fragment():
    cs = CharacterSystem()
    w = _world_with_characters()
    scene = {"present": ["艾拉"], "day": 2}
    frag = cs.inject(scene, w)
    assert isinstance(frag, Fragment)
    assert frag.system == "character"
    assert frag.layer == "scene"
    assert "艾拉" in frag.text
    assert "一位沉默的弓手" in frag.text


def test_inject_includes_current_goal():
    cs = CharacterSystem()
    w = _world_with_characters()
    scene = {"present": ["艾拉"], "day": 2}
    frag = cs.inject(scene, w)
    assert "寻找失踪的弟弟" in frag.text


def test_inject_includes_current_mood():
    cs = CharacterSystem()
    w = _world_with_characters()
    scene = {"present": ["艾拉"], "day": 2}
    frag = cs.inject(scene, w)
    assert "忧郁" in frag.text


def test_inject_absent_character_omitted():
    """主角 not in present → omitted from fragment."""
    cs = CharacterSystem()
    w = _world_with_characters()
    scene = {"present": ["艾拉"], "day": 2}
    frag = cs.inject(scene, w)
    assert "主角" not in frag.text


def test_inject_empty_present_returns_none():
    cs = CharacterSystem()
    w = _world_with_characters()
    scene = {"present": [], "day": 2}
    result = cs.inject(scene, w)
    assert result is None


def test_inject_absent_present_key_returns_none():
    cs = CharacterSystem()
    w = _world_with_characters()
    scene = {"day": 2}
    result = cs.inject(scene, w)
    assert result is None


def test_inject_non_person_entity_omitted():
    """Entities in 'present' that are not Person type should be skipped."""
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟", tier="tracked"),
    ]
    w = project(r, evs)
    # Manually add a non-Person entity to graph
    g: FactGraph = w["systems"]["ontology"]
    g.add_entity("宝剑", "Item")
    cs = CharacterSystem()
    scene = {"present": ["宝剑", "艾拉"], "day": 1}
    frag = cs.inject(scene, w)
    assert "宝剑" not in frag.text
    assert "艾拉" in frag.text


def test_inject_mood_absent_shows_placeholder():
    """If mood fact not set, show '—' placeholder."""
    cs = CharacterSystem()
    r = _reg()
    evs = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟", tier="tracked")]
    w = project(r, evs)
    scene = {"present": ["艾拉"], "day": 1}
    frag = cs.inject(scene, w)
    assert "—" in frag.text


# ---------------------------------------------------------------------------
# M4: recall hook
# ---------------------------------------------------------------------------

def test_recall_finds_person_by_sketch_substring():
    """CharacterSystem.recall matches persons whose sketch contains the query."""
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="沉默的弓手", goal="找弟弟", tier="tracked"),
        _ev("character_created", day=1, id="巴德", sketch="热情的吟游诗人", goal="传播故事", tier="tracked"),
    ]
    w = project(r, evs)
    cs = CharacterSystem()
    hits = cs.recall("弓手", w)
    assert len(hits) >= 1
    assert any(h.ref.get("id") == "艾拉" for h in hits)
    # 巴德 should not match
    assert not any(h.ref.get("id") == "巴德" for h in hits)


def test_recall_finds_person_by_goal_substring():
    """CharacterSystem.recall also matches persons by goal substring."""
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="弓手", goal="寻找失踪的弟弟", tier="tracked"),
    ]
    w = project(r, evs)
    cs = CharacterSystem()
    hits = cs.recall("失踪", w)
    assert any(h.ref.get("id") == "艾拉" for h in hits)


def test_recall_returns_empty_when_no_match():
    """CharacterSystem.recall returns empty list when query matches nothing."""
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟", tier="tracked"),
    ]
    w = project(r, evs)
    cs = CharacterSystem()
    hits = cs.recall("绝对不存在的词语XYZ", w)
    assert hits == []


def test_recall_hits_have_correct_system():
    """RecallHit objects from CharacterSystem have system='character'."""
    r = _reg()
    evs = [
        _ev("character_created", day=1, id="艾拉", sketch="沉默的弓手", goal="找弟弟", tier="tracked"),
    ]
    w = project(r, evs)
    cs = CharacterSystem()
    hits = cs.recall("弓手", w)
    assert all(h.system == "character" for h in hits)


# ---------------------------------------------------------------------------
# New hardening tests — validate missing required fields per op
# ---------------------------------------------------------------------------

def _world_with_person(pid="艾拉"):
    """World with a pre-existing Person entity (for evolve/relationship tests)."""
    g = FactGraph()
    g.add_entity(pid, "Person")
    return {"systems": {"ontology": g, "character": {}}}


# (a) Missing required fields → ValidationError code="missing" at the right field path

class TestValidateMissingFields:
    """Validate catches each missing required field per op."""

    def test_evolve_missing_id_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "evolve", "predicate": "mood", "value": "平静"}], w)
        assert any(e.code == "missing" and "[0].id" in e.field for e in errs)

    def test_evolve_missing_predicate_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "evolve", "id": "艾拉", "value": "平静"}], w)
        assert any(e.code == "missing" and "[0].predicate" in e.field for e in errs)

    def test_evolve_missing_value_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "evolve", "id": "艾拉", "predicate": "mood"}], w)
        assert any(e.code == "missing" and "[0].value" in e.field for e in errs)

    def test_relationship_missing_id_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "relationship", "toward": "主角", "value": "信任"}], w)
        assert any(e.code == "missing" and "[0].id" in e.field for e in errs)

    def test_relationship_missing_toward_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "relationship", "id": "艾拉", "value": "信任"}], w)
        assert any(e.code == "missing" and "[0].toward" in e.field for e in errs)

    def test_relationship_missing_value_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "relationship", "id": "艾拉", "toward": "主角"}], w)
        assert any(e.code == "missing" and "[0].value" in e.field for e in errs)

    def test_create_missing_id_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "create", "sketch": "某人", "goal": "某目标"}], w)
        assert any(e.code == "missing" and "[0].id" in e.field for e in errs)

    def test_create_missing_sketch_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "create", "id": "新人", "goal": "某目标"}], w)
        assert any(e.code == "missing" and "[0].sketch" in e.field for e in errs)

    def test_create_missing_goal_gives_missing_error(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "create", "id": "新人", "sketch": "某人"}], w)
        assert any(e.code == "missing" and "[0].goal" in e.field for e in errs)

    def test_evolve_value_false_is_valid_not_missing(self):
        """value=False (falsy) must NOT trigger a missing error — 'value' key exists."""
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "evolve", "id": "艾拉", "predicate": "active", "value": False}], w)
        value_missing = [e for e in errs if e.code == "missing" and "value" in e.field]
        assert value_missing == [], f"Unexpected missing-value errors: {value_missing}"

    def test_relationship_value_zero_is_valid_not_missing(self):
        """value=0 (falsy) must NOT trigger a missing error — 'value' key exists."""
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "relationship", "id": "艾拉", "toward": "主角", "value": 0}], w)
        value_missing = [e for e in errs if e.code == "missing" and "value" in e.field]
        assert value_missing == [], f"Unexpected missing-value errors: {value_missing}"

    def test_second_item_field_path_uses_correct_index(self):
        """Field path for the second item in a list should use [1].xxx, not [0].xxx."""
        cs = CharacterSystem()
        w = _world_with_person()
        decl = [
            {"op": "create", "id": "完整角色", "sketch": "某人", "goal": "某目标"},
            {"op": "evolve", "id": "艾拉", "predicate": "mood"},  # missing value
        ]
        errs = cs.validate("cast", decl, w)
        assert any(e.code == "missing" and "[1].value" in e.field for e in errs)
        assert not any(e.code == "missing" and "[0]" in e.field for e in errs)


# (b) Complete valid cast validates clean AND to_events/apply work

class TestValidCompleteDecls:
    """Complete, well-formed declarations produce no errors and round-trip correctly."""

    def test_minimal_create_validates_clean(self):
        """Minimal 纯粹之人 create — past/hidden absent — zero errors."""
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "create", "id": "纯粹之人", "sketch": "朴素的人", "goal": "活下去"}], w)
        assert errs == []

    def test_minimal_create_to_events_and_apply_work(self):
        """to_events + apply on minimal create produce a Person in the graph."""
        cs = CharacterSystem()
        r = _reg()
        decl = [{"op": "create", "id": "纯粹之人", "sketch": "朴素的人", "goal": "活下去"}]
        evs = cs.to_events("cast", decl, turn=1, day=1, scene="s1")
        assert len(evs) == 1
        w = project(r, evs)
        g: FactGraph = w["systems"]["ontology"]
        assert g.get_entity("纯粹之人") is not None
        assert g.value_at("纯粹之人", "sketch", 1) == "朴素的人"
        assert g.value_at("纯粹之人", "goal", 1) == "活下去"
        # past/hidden must be absent
        assert g.value_at("纯粹之人", "past", 1) is None
        assert g.value_at("纯粹之人", "hidden", 1) is None

    def test_full_create_validates_clean(self):
        """Full create with past+hidden also validates clean."""
        cs = CharacterSystem()
        w = _world_with_person()
        decl = [{"op": "create", "id": "艾拉", "sketch": "弓手", "goal": "找弟弟",
                 "past": "曾是侍卫", "hidden": "内心创伤"}]
        assert cs.validate("cast", decl, w) == []

    def test_evolve_complete_validates_clean(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "evolve", "id": "艾拉", "predicate": "mood", "value": "平静"}], w)
        assert errs == []

    def test_evolve_complete_to_events_and_apply_work(self):
        r = _reg()
        evs_setup = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟")]
        w = project(r, evs_setup)
        cs = CharacterSystem()
        decl = [{"op": "evolve", "id": "艾拉", "predicate": "mood", "value": "平静"}]
        evs = cs.to_events("cast", decl, turn=2, day=2, scene="s1")
        assert len(evs) == 1
        assert evs[0]["type"] == "character_evolved"
        # Apply directly on the live world
        cs.apply(w, evs[0])
        g: FactGraph = w["systems"]["ontology"]
        assert g.value_at("艾拉", "mood", 2) == "平静"

    def test_relationship_complete_validates_clean(self):
        cs = CharacterSystem()
        w = _world_with_person()
        errs = cs.validate("cast", [{"op": "relationship", "id": "艾拉", "toward": "主角", "value": "信任"}], w)
        assert errs == []

    def test_relationship_complete_to_events_and_apply_work(self):
        r = _reg()
        evs_setup = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟")]
        w = project(r, evs_setup)
        cs = CharacterSystem()
        decl = [{"op": "relationship", "id": "艾拉", "toward": "主角", "value": "敌对"}]
        evs = cs.to_events("cast", decl, turn=2, day=2, scene="s1")
        assert len(evs) == 1
        assert evs[0]["type"] == "relationship_changed"
        cs.apply(w, evs[0])
        g: FactGraph = w["systems"]["ontology"]
        assert g.value_at("艾拉", "trust:主角", 2) == "敌对"


# (c) to_events / apply on malformed events/decls do NOT raise

class TestDefensivePaths:
    """Malformed events/decls must never raise; they silently skip with a warning."""

    def _base_world(self):
        r = _reg()
        return project(r, [])

    def test_apply_character_created_missing_id_no_raise(self):
        cs = CharacterSystem()
        w = self._base_world()
        bad_event = kernel_event("character_created", day=1, scene="s1",
                                 summary="bad", deltas={"sketch": "某人", "goal": "某事"})
        # Must not raise
        cs.apply(w, bad_event)

    def test_apply_character_created_missing_sketch_no_raise(self):
        cs = CharacterSystem()
        w = self._base_world()
        bad_event = kernel_event("character_created", day=1, scene="s1",
                                 summary="bad", deltas={"id": "孤儿", "goal": "某事"})
        cs.apply(w, bad_event)
        # Entity must not be added (skipped before add_entity)
        g: FactGraph = w["systems"]["ontology"]
        assert g.get_entity("孤儿") is None

    def test_apply_character_created_missing_goal_no_raise(self):
        cs = CharacterSystem()
        w = self._base_world()
        bad_event = kernel_event("character_created", day=1, scene="s1",
                                 summary="bad", deltas={"id": "孤儿", "sketch": "某人"})
        cs.apply(w, bad_event)

    def test_apply_character_evolved_missing_id_no_raise(self):
        cs = CharacterSystem()
        w = self._base_world()
        bad_event = kernel_event("character_evolved", day=1, scene="s1",
                                 summary="bad", deltas={"predicate": "mood", "value": "哀恸"})
        cs.apply(w, bad_event)

    def test_apply_character_evolved_missing_predicate_no_raise(self):
        cs = CharacterSystem()
        r = _reg()
        evs = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟")]
        w = project(r, evs)
        bad_event = kernel_event("character_evolved", day=2, scene="s1",
                                 summary="bad", deltas={"id": "艾拉", "value": "哀恸"})
        cs.apply(w, bad_event)

    def test_apply_character_evolved_missing_value_no_raise(self):
        cs = CharacterSystem()
        r = _reg()
        evs = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟")]
        w = project(r, evs)
        bad_event = kernel_event("character_evolved", day=2, scene="s1",
                                 summary="bad", deltas={"id": "艾拉", "predicate": "mood"})
        cs.apply(w, bad_event)

    def test_apply_relationship_changed_missing_id_no_raise(self):
        cs = CharacterSystem()
        w = self._base_world()
        bad_event = kernel_event("relationship_changed", day=1, scene="s1",
                                 summary="bad", deltas={"toward": "主角", "value": "敌对"})
        cs.apply(w, bad_event)

    def test_apply_relationship_changed_missing_toward_no_raise(self):
        cs = CharacterSystem()
        r = _reg()
        evs = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟")]
        w = project(r, evs)
        bad_event = kernel_event("relationship_changed", day=2, scene="s1",
                                 summary="bad", deltas={"id": "艾拉", "value": "敌对"})
        cs.apply(w, bad_event)

    def test_apply_relationship_changed_missing_value_no_raise(self):
        cs = CharacterSystem()
        r = _reg()
        evs = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟")]
        w = project(r, evs)
        bad_event = kernel_event("relationship_changed", day=2, scene="s1",
                                 summary="bad", deltas={"id": "艾拉", "toward": "主角"})
        cs.apply(w, bad_event)

    def test_apply_empty_deltas_no_raise(self):
        """Completely empty deltas must not raise for any event type."""
        cs = CharacterSystem()
        w = self._base_world()
        for typ in ("character_created", "character_evolved", "relationship_changed"):
            ev = kernel_event(typ, day=1, scene="s1", summary="bad", deltas={})
            cs.apply(w, ev)

    def test_to_events_malformed_item_no_raise(self):
        """to_events with a malformed item (missing fields) must not raise."""
        cs = CharacterSystem()
        # Missing id/sketch/goal for create
        decl = [{"op": "create"}]
        evs = cs.to_events("cast", decl, turn=1, day=1, scene="s1")
        # Should produce an event (gate is validate's job), but must not crash
        assert isinstance(evs, list)

    def test_apply_malformed_event_does_not_corrupt_existing_state(self):
        """After a bad evolve event is skipped, prior state must be intact."""
        cs = CharacterSystem()
        r = _reg()
        good_evs = [_ev("character_created", day=1, id="艾拉", sketch="弓手", goal="找弟弟")]
        w = project(r, good_evs)
        # Apply a bad evolve (missing predicate) — must skip, not corrupt
        bad_event = kernel_event("character_evolved", day=2, scene="s1",
                                 summary="bad", deltas={"id": "艾拉", "value": "哀恸"})
        cs.apply(w, bad_event)
        g: FactGraph = w["systems"]["ontology"]
        # Original entity must still be intact
        assert g.get_entity("艾拉") is not None
        assert g.value_at("艾拉", "sketch", 1) == "弓手"


# ---------------------------------------------------------------------------
# Phase D Task 1: last_update stamping
# ---------------------------------------------------------------------------

def test_character_created_stamps_last_update():
    reg = _reg()
    world = project(reg, [
        kernel_event("character_created", day=3, scene="s1", summary="登场",
                     deltas={"id": "npc", "tier": "tracked",
                             "sketch": "守桥人", "goal": "守住桥"}, turn=1),
    ])
    g = world["systems"]["ontology"]
    assert g.get_entity("npc").attrs.get("last_update") == 3


def test_character_evolved_advances_last_update():
    reg = _reg()
    world = project(reg, [
        kernel_event("character_created", day=1, scene="s1", summary="登场",
                     deltas={"id": "npc", "tier": "tracked",
                             "sketch": "守桥人", "goal": "守桥"}, turn=1),
        kernel_event("character_evolved", day=5, scene="s1", summary="变",
                     deltas={"id": "npc", "predicate": "mood", "value": "疲惫",
                             "op": "evolve"}, turn=2),
    ])
    assert world["systems"]["ontology"].get_entity("npc").attrs.get("last_update") == 5
