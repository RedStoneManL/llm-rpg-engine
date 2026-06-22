import pytest
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from facts.graph import FactGraph


def _reg():
    return Registry().register(OntologySystem())


# ---------------------------------------------------------------------------
# empty_state is a FactGraph
# ---------------------------------------------------------------------------

def test_empty_state_is_fact_graph():
    s = OntologySystem()
    assert isinstance(s.empty_state(), FactGraph)


# ---------------------------------------------------------------------------
# apply mutations
# ---------------------------------------------------------------------------

def test_apply_entity_created():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    ev = kernel_event("entity_created", day=1, scene="s1", summary="艾拉登场",
                      deltas={"id": "艾拉", "etype": "Person", "tier": "tracked"})
    s.apply(world, ev)
    e = g.get_entity("艾拉")
    assert e is not None and e.etype == "Person" and e.tier == "tracked"


def test_apply_fact_asserted():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    # need entity first
    g.add_entity("艾拉", "Person", tier="tracked")
    ev = kernel_event("fact_asserted", day=2, scene="s1", summary="trust=中",
                      deltas={"subject": "艾拉", "predicate": "trust", "value": "中"})
    s.apply(world, ev)
    assert g.value_at("艾拉", "trust", 2) == "中"


def test_apply_relation_added():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    g.add_entity("艾拉", "Person")
    g.add_entity("王都", "Place")
    ev = kernel_event("relation_added", day=3, scene="s1", summary="艾拉 located_in 王都",
                      deltas={"src": "艾拉", "rel": "located_in", "dst": "王都"})
    s.apply(world, ev)
    assert g.neighbors("艾拉", "located_in", day=3) == ["王都"]


def test_apply_tier_changed():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    g.add_entity("龙", "Creature", tier="mentioned")
    ev = kernel_event("tier_changed", day=4, scene="s1", summary="龙升级",
                      deltas={"id": "龙", "tier": "tracked"})
    s.apply(world, ev)
    assert g.get_entity("龙").tier == "tracked"


# ---------------------------------------------------------------------------
# validate: dangling_ref (original tests preserved)
# ---------------------------------------------------------------------------

def test_validate_facts_dangling_subject_returns_error():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    # subject "鬼" does not exist in graph
    decl = [{"subject": "鬼", "predicate": "trust", "value": "低"}]
    errs = s.validate("facts", decl, world)
    assert len(errs) == 1
    assert errs[0].code == "dangling_ref"
    assert errs[0].section == "facts"
    assert errs[0].field == "[0].subject"


def test_validate_relations_dangling_src_returns_error():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    g.add_entity("王都", "Place")
    # src "幽灵" does not exist
    decl = [{"src": "幽灵", "rel": "located_in", "dst": "王都"}]
    errs = s.validate("relations", decl, world)
    assert len(errs) == 1
    assert errs[0].code == "dangling_ref"
    assert errs[0].field == "[0].src"


def test_validate_relations_dangling_dst_returns_error():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    g.add_entity("艾拉", "Person")
    # dst "虚空之地" does not exist
    decl = [{"src": "艾拉", "rel": "located_in", "dst": "虚空之地"}]
    errs = s.validate("relations", decl, world)
    assert len(errs) == 1
    assert errs[0].code == "dangling_ref"
    assert errs[0].field == "[0].dst"


def test_validate_no_errors_when_entities_exist():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    g.add_entity("艾拉", "Person")
    decl = [{"subject": "艾拉", "predicate": "trust", "value": "高"}]
    errs = s.validate("facts", decl, world)
    assert errs == []


# ---------------------------------------------------------------------------
# to_events shape
# ---------------------------------------------------------------------------

def test_to_events_entities_section():
    s = OntologySystem()
    decl = [{"id": "艾拉", "etype": "Person", "tier": "tracked"}]
    evs = s.to_events("entities", decl, turn=1, day=1, scene="s1")
    assert len(evs) == 1 and evs[0]["type"] == "entity_created"
    assert evs[0]["deltas"]["id"] == "艾拉"


def test_to_events_facts_section():
    s = OntologySystem()
    decl = [{"subject": "艾拉", "predicate": "trust", "value": "中"}]
    evs = s.to_events("facts", decl, turn=1, day=2, scene="s1")
    assert len(evs) == 1 and evs[0]["type"] == "fact_asserted"
    assert evs[0]["deltas"]["subject"] == "艾拉"


def test_to_events_relations_section():
    s = OntologySystem()
    decl = [{"src": "艾拉", "rel": "located_in", "dst": "王都"}]
    evs = s.to_events("relations", decl, turn=1, day=3, scene="s1")
    assert len(evs) == 1 and evs[0]["type"] == "relation_added"
    assert evs[0]["deltas"]["src"] == "艾拉"


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def test_registration_in_registry():
    s = OntologySystem()
    r = Registry().register(s)
    assert r.owner_of_event("entity_created") is s
    assert r.owner_of_event("fact_asserted") is s
    assert r.owner_of_event("relation_added") is s
    assert r.owner_of_event("tier_changed") is s
    assert r.owner_of_section("entities") is s
    assert r.owner_of_section("facts") is s
    assert r.owner_of_section("relations") is s


# ---------------------------------------------------------------------------
# end-to-end project
# ---------------------------------------------------------------------------

def test_project_builds_graph_from_ontology_events():
    r = Registry().register(OntologySystem())
    evs = [
        kernel_event("entity_created", day=1, scene="s1", summary="艾拉登场",
                     deltas={"id": "艾拉", "etype": "Person", "tier": "tracked"}),
        kernel_event("fact_asserted", day=1, scene="s1", summary="信任=中",
                     deltas={"subject": "艾拉", "predicate": "trust", "value": "中"}),
    ]
    w = project(r, evs)
    g = w["systems"]["ontology"]
    assert isinstance(g, FactGraph)
    assert g.get_entity("艾拉").tier == "tracked"
    assert g.value_at("艾拉", "trust", 1) == "中"


# ---------------------------------------------------------------------------
# inject
# ---------------------------------------------------------------------------

def test_inject_returns_fragment_with_tracked_entities():
    from kernel.contextsystem import Fragment
    s = OntologySystem()
    g = s.empty_state()
    g.add_entity("艾拉", "Person", tier="tracked")
    g.add_entity("路人甲", "Person", tier="mentioned")
    world = {"systems": {s.name: g}}
    frag = s.inject("s1", world)
    assert isinstance(frag, Fragment)
    assert "艾拉" in frag.text
    assert "路人甲" not in frag.text


def test_inject_returns_none_when_no_tracked():
    s = OntologySystem()
    g = s.empty_state()
    world = {"systems": {s.name: g}}
    assert s.inject("s1", world) is None


def test_inject_accepts_dict_scene():
    """inject() must accept a dict scene (base contract), not only a str (M3)."""
    import typing
    s = OntologySystem()
    g = s.empty_state()
    g.add_entity("艾拉", "Person", tier="tracked")
    world = {"systems": {s.name: g}}
    # call with dict — must not raise
    frag = s.inject({"day": 1}, world)
    assert frag is not None
    assert "艾拉" in frag.text
    # annotation must be dict (use get_type_hints to resolve forward refs from __future__)
    hints = typing.get_type_hints(s.inject)
    assert hints.get("scene") is dict, (
        f"Expected 'scene' annotation to be dict, got {hints.get('scene')!r}"
    )


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------

def test_recall_finds_entity_by_substring():
    from kernel.contextsystem import RecallHit
    s = OntologySystem()
    g = s.empty_state()
    g.add_entity("艾拉", "Person", tier="tracked")
    g.add_entity("王都", "Place", tier="tracked")
    world = {"systems": {s.name: g}}
    hits = s.recall("艾拉", world)
    assert len(hits) == 1
    assert isinstance(hits[0], RecallHit)
    assert "艾拉" in hits[0].text


def test_recall_empty_when_no_match():
    s = OntologySystem()
    g = s.empty_state()
    g.add_entity("艾拉", "Person")
    world = {"systems": {s.name: g}}
    assert s.recall("鬼怪", world) == []


# ---------------------------------------------------------------------------
# NEW: validate — missing required fields (code="missing")
# ---------------------------------------------------------------------------

class TestValidateMissingFields:
    """Task 4a: missing required field → ValidationError code='missing'."""

    # --- entities section ---------------------------------------------------

    def test_entities_missing_id_gives_missing_error(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("entities", [{"etype": "Person"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].id") in codes_fields

    def test_entities_missing_etype_gives_missing_error(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("entities", [{"id": "艾拉"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].etype") in codes_fields

    def test_entities_missing_both_id_and_etype(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("entities", [{}], world)
        fields = [e.field for e in errs if e.code == "missing"]
        assert "[0].id" in fields
        assert "[0].etype" in fields

    def test_entities_section_is_facts(self):
        """Correct section name appears in each ValidationError."""
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("entities", [{"etype": "Person"}], world)
        assert all(e.section == "entities" for e in errs)

    # --- facts section ------------------------------------------------------

    def test_facts_missing_subject_gives_missing_error(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        errs = s.validate("facts", [{"predicate": "trust", "value": "高"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].subject") in codes_fields

    def test_facts_missing_predicate_gives_missing_error(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        world = {"systems": {s.name: g}}
        errs = s.validate("facts", [{"subject": "艾拉", "value": "高"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].predicate") in codes_fields

    def test_facts_missing_value_gives_missing_error(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        world = {"systems": {s.name: g}}
        errs = s.validate("facts", [{"subject": "艾拉", "predicate": "trust"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].value") in codes_fields

    def test_facts_missing_all_three_required_fields(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("facts", [{}], world)
        fields = [e.field for e in errs if e.code == "missing"]
        assert "[0].subject" in fields
        assert "[0].predicate" in fields
        assert "[0].value" in fields

    def test_facts_value_zero_is_valid(self):
        """value=0 is a legitimate value; must NOT raise a missing error."""
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        world = {"systems": {s.name: g}}
        errs = s.validate("facts", [{"subject": "艾拉", "predicate": "hp", "value": 0}], world)
        missing = [e for e in errs if e.code == "missing" and e.field == "[0].value"]
        assert missing == []

    def test_facts_section_name_correct(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("facts", [{"predicate": "trust"}], world)
        assert all(e.section == "facts" for e in errs)

    # --- relations section --------------------------------------------------

    def test_relations_missing_src_gives_missing_error(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("王都", "Place")
        world = {"systems": {s.name: g}}
        errs = s.validate("relations", [{"rel": "located_in", "dst": "王都"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].src") in codes_fields

    def test_relations_missing_rel_gives_missing_error(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        g.add_entity("王都", "Place")
        world = {"systems": {s.name: g}}
        errs = s.validate("relations", [{"src": "艾拉", "dst": "王都"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].rel") in codes_fields

    def test_relations_missing_dst_gives_missing_error(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        world = {"systems": {s.name: g}}
        errs = s.validate("relations", [{"src": "艾拉", "rel": "located_in"}], world)
        codes_fields = [(e.code, e.field) for e in errs]
        assert ("missing", "[0].dst") in codes_fields

    def test_relations_missing_all_three_required_fields(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("relations", [{}], world)
        fields = [e.field for e in errs if e.code == "missing"]
        assert "[0].src" in fields
        assert "[0].rel" in fields
        assert "[0].dst" in fields

    def test_relations_section_name_correct(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        errs = s.validate("relations", [{"rel": "knows"}], world)
        assert all(e.section == "relations" for e in errs)

    # --- multi-item decl: only bad items produce errors ---------------------

    def test_second_item_missing_field_gives_error_at_correct_index(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        world = {"systems": {s.name: g}}
        decl = [
            {"subject": "艾拉", "predicate": "trust", "value": "高"},   # valid
            {"predicate": "hp", "value": 10},                             # missing subject
        ]
        errs = s.validate("facts", decl, world)
        assert any(e.field == "[1].subject" and e.code == "missing" for e in errs)
        assert not any(e.field.startswith("[0]") and e.code == "missing" for e in errs)


# ---------------------------------------------------------------------------
# NEW: validate — complete valid decl produces no errors (Task 4b)
# ---------------------------------------------------------------------------

class TestValidateCleanPass:
    """Task 4b: complete valid decl validates clean."""

    def test_entities_complete_decl_no_errors(self):
        s = OntologySystem()
        world = {"systems": {s.name: s.empty_state()}}
        decl = [{"id": "艾拉", "etype": "Person", "tier": "tracked"}]
        assert s.validate("entities", decl, world) == []

    def test_facts_complete_decl_no_errors(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        world = {"systems": {s.name: g}}
        decl = [{"subject": "艾拉", "predicate": "trust", "value": "高"}]
        assert s.validate("facts", decl, world) == []

    def test_relations_complete_decl_no_errors(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        g.add_entity("王都", "Place")
        world = {"systems": {s.name: g}}
        decl = [{"src": "艾拉", "rel": "located_in", "dst": "王都"}]
        assert s.validate("relations", decl, world) == []

    def test_complete_entities_decl_to_events_and_apply_work(self):
        """Task 4b: end-to-end pipeline works on valid input."""
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        decl = [{"id": "艾拉", "etype": "Person", "tier": "tracked"}]
        assert s.validate("entities", decl, world) == []
        evs = s.to_events("entities", decl, turn=1, day=1, scene="s1")
        assert len(evs) == 1
        for ev in evs:
            s.apply(world, ev)
        assert g.get_entity("艾拉") is not None

    def test_complete_facts_decl_to_events_and_apply_work(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        world = {"systems": {s.name: g}}
        decl = [{"subject": "艾拉", "predicate": "trust", "value": "中"}]
        assert s.validate("facts", decl, world) == []
        evs = s.to_events("facts", decl, turn=1, day=2, scene="s1")
        assert len(evs) == 1
        for ev in evs:
            s.apply(world, ev)
        assert g.value_at("艾拉", "trust", 2) == "中"

    def test_complete_relations_decl_to_events_and_apply_work(self):
        s = OntologySystem()
        g = s.empty_state()
        g.add_entity("艾拉", "Person")
        g.add_entity("王都", "Place")
        world = {"systems": {s.name: g}}
        decl = [{"src": "艾拉", "rel": "located_in", "dst": "王都"}]
        assert s.validate("relations", decl, world) == []
        evs = s.to_events("relations", decl, turn=1, day=3, scene="s1")
        assert len(evs) == 1
        for ev in evs:
            s.apply(world, ev)
        assert g.neighbors("艾拉", "located_in", day=3) == ["王都"]


# ---------------------------------------------------------------------------
# NEW: to_events / apply on malformed input must NOT raise (Task 4c)
# ---------------------------------------------------------------------------

class TestDefensivePipeline:
    """Task 4c: to_events()/apply() on malformed data must not raise."""

    # --- to_events defensive -------------------------------------------------

    def test_to_events_entities_missing_id_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("entities", [{"etype": "Person"}], turn=1, day=1, scene="s1")
        assert evs == []  # item skipped, no crash

    def test_to_events_entities_missing_etype_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("entities", [{"id": "艾拉"}], turn=1, day=1, scene="s1")
        assert evs == []

    def test_to_events_facts_missing_subject_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("facts", [{"predicate": "trust", "value": "高"}],
                          turn=1, day=1, scene="s1")
        assert evs == []

    def test_to_events_facts_missing_predicate_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("facts", [{"subject": "艾拉", "value": "高"}],
                          turn=1, day=1, scene="s1")
        assert evs == []

    def test_to_events_facts_missing_value_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("facts", [{"subject": "艾拉", "predicate": "trust"}],
                          turn=1, day=1, scene="s1")
        assert evs == []

    def test_to_events_relations_missing_src_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("relations", [{"rel": "located_in", "dst": "王都"}],
                          turn=1, day=1, scene="s1")
        assert evs == []

    def test_to_events_relations_missing_rel_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("relations", [{"src": "艾拉", "dst": "王都"}],
                          turn=1, day=1, scene="s1")
        assert evs == []

    def test_to_events_relations_missing_dst_skips_item_no_raise(self):
        s = OntologySystem()
        evs = s.to_events("relations", [{"src": "艾拉", "rel": "located_in"}],
                          turn=1, day=1, scene="s1")
        assert evs == []

    def test_to_events_mixed_good_and_bad_items_only_good_emitted(self):
        """Malformed item is skipped; good item still produces an event."""
        s = OntologySystem()
        decl = [
            {"subject": "艾拉", "predicate": "trust", "value": "高"},  # good
            {"predicate": "hp", "value": 10},                           # missing subject
        ]
        evs = s.to_events("facts", decl, turn=1, day=1, scene="s1")
        assert len(evs) == 1
        assert evs[0]["deltas"]["subject"] == "艾拉"

    # --- apply() defensive on stored events ----------------------------------

    def test_apply_entity_created_missing_id_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("entity_created", day=1, scene="s1", summary="bad",
                          deltas={"etype": "Person"})  # missing id
        s.apply(world, ev)  # must not raise
        assert len(g.entities) == 0  # nothing added

    def test_apply_entity_created_missing_etype_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("entity_created", day=1, scene="s1", summary="bad",
                          deltas={"id": "艾拉"})  # missing etype
        s.apply(world, ev)  # must not raise
        assert len(g.entities) == 0

    def test_apply_fact_asserted_missing_subject_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("fact_asserted", day=1, scene="s1", summary="bad",
                          deltas={"predicate": "trust", "value": "高"})
        s.apply(world, ev)  # must not raise

    def test_apply_fact_asserted_missing_predicate_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("fact_asserted", day=1, scene="s1", summary="bad",
                          deltas={"subject": "艾拉", "value": "高"})
        s.apply(world, ev)  # must not raise

    def test_apply_fact_asserted_missing_value_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("fact_asserted", day=1, scene="s1", summary="bad",
                          deltas={"subject": "艾拉", "predicate": "trust"})
        s.apply(world, ev)  # must not raise

    def test_apply_relation_added_missing_src_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("relation_added", day=1, scene="s1", summary="bad",
                          deltas={"rel": "located_in", "dst": "王都"})
        s.apply(world, ev)  # must not raise

    def test_apply_relation_added_missing_rel_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("relation_added", day=1, scene="s1", summary="bad",
                          deltas={"src": "艾拉", "dst": "王都"})
        s.apply(world, ev)  # must not raise

    def test_apply_relation_added_missing_dst_does_not_raise(self):
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        ev = kernel_event("relation_added", day=1, scene="s1", summary="bad",
                          deltas={"src": "艾拉", "rel": "located_in"})
        s.apply(world, ev)  # must not raise

    def test_apply_empty_deltas_does_not_raise(self):
        """A stored event with no deltas at all must never crash projection."""
        s = OntologySystem()
        g = s.empty_state()
        world = {"systems": {s.name: g}}
        for ev_type in ("entity_created", "fact_asserted", "relation_added"):
            ev = kernel_event(ev_type, day=1, scene="s1", summary="empty",
                              deltas={})
            s.apply(world, ev)  # must not raise

    def test_project_with_malformed_stored_events_does_not_raise(self):
        """Projection over a mix of good and bad stored events must not crash."""
        r = Registry().register(OntologySystem())
        evs = [
            # good event
            kernel_event("entity_created", day=1, scene="s1", summary="艾拉登场",
                         deltas={"id": "艾拉", "etype": "Person", "tier": "tracked"}),
            # malformed — missing subject (the live crash case)
            kernel_event("fact_asserted", day=1, scene="s1", summary="bad",
                         deltas={"predicate": "trust", "value": "高"}),
            # malformed — missing rel
            kernel_event("relation_added", day=1, scene="s1", summary="bad",
                         deltas={"src": "艾拉", "dst": "王都"}),
        ]
        w = project(r, evs)  # must not raise
        g = w["systems"]["ontology"]
        # The good entity was still created
        assert g.get_entity("艾拉") is not None
