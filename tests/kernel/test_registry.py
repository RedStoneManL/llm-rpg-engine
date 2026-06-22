import pytest

from kernel.contextsystem import ContextSystem
from kernel.registry import Registry


class _A(ContextSystem):
    name = "a"
    def event_types(self): return {"a_made"}
    def commit_sections(self): return {"a"}


class _B(ContextSystem):
    name = "b"
    def event_types(self): return {"b_made"}
    def commit_sections(self): return {"b"}


class _CollideEvent(ContextSystem):
    name = "c"
    def event_types(self): return {"a_made"}  # collides with _A


class _NarrationSection(ContextSystem):
    name = "d"
    def event_types(self): return {"d_made"}
    def commit_sections(self): return {"narration"}  # reserved name


def test_register_and_lookup():
    r = Registry().register(_A()).register(_B())
    assert {s.name for s in r.systems} == {"a", "b"}
    assert r.event_types() == {"a_made", "b_made"}
    assert r.owner_of_event("a_made").name == "a"
    assert r.owner_of_section("b").name == "b"
    assert r.owner_of_event("nope") is None
    assert r.owner_of_section("nope") is None


def test_event_type_collision_rejected():
    r = Registry().register(_A())
    with pytest.raises(ValueError, match="a_made"):
        r.register(_CollideEvent())


def test_narration_section_reserved():
    r = Registry()
    with pytest.raises(ValueError, match="narration"):
        r.register(_NarrationSection())


# ---------------------------------------------------------------------------
# I3: system dependency enforcement via requires()
# ---------------------------------------------------------------------------

class _NeedsOntology(ContextSystem):
    name = "needs_ontology"
    def event_types(self): return {"no_made"}
    def commit_sections(self): return {"no"}
    def requires(self): return {"ontology"}


class _StandaloneNoReqs(ContextSystem):
    name = "standalone"
    def event_types(self): return {"st_made"}
    def commit_sections(self): return {"st"}
    # relies on default requires() → empty set


def test_register_dependency_not_met_raises():
    """Registering a system whose requires() dep isn't registered yet → ValueError (I3)."""
    r = Registry()
    with pytest.raises(ValueError, match="requires"):
        r.register(_NeedsOntology())


def test_register_dependency_met_succeeds():
    """Registering ontology first, then a system requiring it, succeeds (I3)."""
    from systems.ontology import OntologySystem
    r = Registry()
    r.register(OntologySystem())
    r.register(_NeedsOntology())
    assert any(s.name == "needs_ontology" for s in r.systems)


def test_register_no_requires_always_succeeds():
    """A system with empty requires() can register without any prereqs (I3)."""
    r = Registry()
    r.register(_StandaloneNoReqs())
    assert any(s.name == "standalone" for s in r.systems)
