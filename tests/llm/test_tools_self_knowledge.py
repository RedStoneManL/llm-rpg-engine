"""Issue #6 root-cause #2 — self-knowledge fog fix (fix6a).

Rule: an agent ALWAYS knows itself (pov_id == entity_id bypasses the knows() gate).

Tests:
  T_SK1  characters_query(pov==self) returns sketch/goal — no knows() grant required.
  T_SK2  characters_query(pov==self) returns additional non-hidden facets at real values.
  T_SK3  characters_query(pov==self) NEVER returns 'hidden' (unknown-even-to-self).
  T_SK4  characters_query on ANOTHER unknown NPC still returns known:false (fog intact).
  T_SK5  recall_query hit about the pov itself is NOT dropped (self-knowledge bypass).
  T_SK6  recall_query hit about an unknown NPC IS still dropped (fog intact for non-self).
  T_SK7  Protagonist NOT in scene["present"] still gets self-knowledge (present exclusion bug).
"""
from __future__ import annotations

import json

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.knowledge import KnowledgeSystem
from systems.character import CharacterSystem
from systems.faction import FactionSystem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(KnowledgeSystem())
    r.register(CharacterSystem())
    r.register(FactionSystem())
    return r


def _world_protagonist_no_self_grant(*, protagonist_has_hidden: bool = False):
    """Build a world where the protagonist ('protagonist') has a real sketch/goal
    but NO knowledge_set grant on protagonist.sketch / protagonist.goal.
    Mirrors the real bootstrap bug: _build_scene excludes the protagonist from
    scene["present"] so co-presence never fires, and no self-grant exists.

    A second NPC ('stranger') is included but protagonist has no knowledge of it.
    """
    r = _reg()
    evs = [
        kernel_event("character_created", day=1, scene="g", summary="protagonist",
                     deltas={
                         "id": "protagonist",
                         "sketch": "一位踏上旅途的冒险者",
                         "goal": "探索这个世界",
                         "tier": "protagonist",
                     }, turn=1),
        kernel_event("character_created", day=1, scene="g", summary="stranger",
                     deltas={
                         "id": "stranger",
                         "sketch": "神秘的路人",
                         "goal": "隐藏真实目的",
                         "tier": "major",
                     }, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="town",
                     deltas={"id": "town", "level": 2, "kind": "settlement",
                             "seed": "镇"}, turn=1),
    ]
    if protagonist_has_hidden:
        evs.append(kernel_event("character_evolved", day=1, scene="g", summary="hidden",
                                deltas={"id": "protagonist", "predicate": "hidden",
                                        "value": "真实身份是转生者"}, turn=1))
    w = project(r, iter(evs))
    # Scene mirrors the bug: protagonist is NOT in present (as _build_scene would do).
    scene = {
        "protagonist": "protagonist",
        "present": [],   # protagonist excluded from present — the original bug
        "day": 1,
        "location": "town",
    }
    return r, w, scene


# ---------------------------------------------------------------------------
# T_SK1: characters_query returns self sketch/goal without a knows() grant
# ---------------------------------------------------------------------------

def test_characters_query_self_returns_sketch_without_knows_grant():
    """T_SK1a: protagonist asking about itself returns sketch even without knowledge grant."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "protagonist"}))
    out_str = json.dumps(out, ensure_ascii=False)

    assert "一位踏上旅途的冒险者" in out_str, (
        "self-knowledge bug: protagonist sketch not returned when querying self"
    )


def test_characters_query_self_returns_goal_without_knows_grant():
    """T_SK1b: protagonist asking about itself returns goal even without knowledge grant."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "protagonist"}))
    out_str = json.dumps(out, ensure_ascii=False)

    assert "探索这个世界" in out_str, (
        "self-knowledge bug: protagonist goal not returned when querying self"
    )


def test_characters_query_self_does_not_return_known_false():
    """T_SK1c: characters_query on self must NOT return known:false regardless of present/knows."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "protagonist"}))

    matches = out.get("matches", [])
    assert matches, "no matches returned for self query"
    self_record = next((m for m in matches if m.get("id") == "protagonist"), None)
    assert self_record is not None, "protagonist not found in characters_query matches"
    assert self_record.get("known") is not False, (
        "self-knowledge bug: protagonist returned as known:false — "
        "an agent must always know itself"
    )


# ---------------------------------------------------------------------------
# T_SK2: characters_query on self returns additional non-hidden facets
# ---------------------------------------------------------------------------

def test_characters_query_self_returns_extra_facets_at_real_values():
    """T_SK2: self-query returns additional non-hidden facets (e.g. 'past') at real values."""
    from llm.tools import build_tool_registry
    from facts.graph import FactGraph

    r, w, scene = _world_protagonist_no_self_grant()
    # Directly assert a 'past' fact on protagonist to the graph (simulates a richer character)
    g: FactGraph = w["systems"]["ontology"]
    g.assert_fact("protagonist", "past", "从小在孤儿院长大",
                  day=1, turn=1, source_event="manual_seed")

    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "protagonist"}))
    out_str = json.dumps(out, ensure_ascii=False)

    assert "从小在孤儿院长大" in out_str, (
        "self-knowledge: 'past' facet not returned for self when no knows() grant exists"
    )


# ---------------------------------------------------------------------------
# T_SK3: 'hidden' is NEVER returned even for self
# ---------------------------------------------------------------------------

def test_characters_query_self_never_returns_hidden():
    """T_SK3: 'hidden' predicate is always gated — not returned even for self."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant(protagonist_has_hidden=True)
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "protagonist"}))
    out_str = json.dumps(out, ensure_ascii=False)

    assert "转生者" not in out_str, (
        "fog-leak: 'hidden' facet leaked to self — hidden must ALWAYS be gated"
    )


# ---------------------------------------------------------------------------
# T_SK4: fog is NOT loosened for non-self (querying another unknown NPC)
# ---------------------------------------------------------------------------

def test_characters_query_other_unknown_npc_still_returns_known_false():
    """T_SK4: querying a never-met NPC (not self) still returns known:false — fog intact."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "stranger"}))

    matches = out.get("matches", [])
    assert matches, "no matches returned for stranger query"
    stranger_record = next((m for m in matches if m.get("id") == "stranger"), None)
    assert stranger_record is not None, "stranger not found in matches"
    assert stranger_record.get("known") is False, (
        "fog regression: unknown NPC 'stranger' should still be known:false — "
        "self-knowledge fix must NOT loosen fog for non-self entities"
    )


def test_characters_query_other_unknown_npc_no_sketch_leak():
    """T_SK4b: the unknown NPC's sketch must not appear in output."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "stranger"}))
    out_str = json.dumps(out, ensure_ascii=False)

    assert "神秘的路人" not in out_str, (
        "fog-leak: stranger's sketch surfaced to protagonist who never met them"
    )
    assert "隐藏真实目的" not in out_str, (
        "fog-leak: stranger's goal surfaced to protagonist who never met them"
    )


# ---------------------------------------------------------------------------
# T_SK5: recall_query hit about the pov itself is NOT dropped
# ---------------------------------------------------------------------------

def test_recall_query_self_hit_not_dropped():
    """T_SK5: a Person recall hit where eid==pov_id must NEVER be fog-dropped."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    # protagonist is NOT in present and has NO knows() grant on self.sketch —
    # without the fix Branch (B) would drop this hit.
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("recall_query", {"q": "冒险者"}))

    assert "hits" in out
    out_str = json.dumps(out, ensure_ascii=False)
    assert "protagonist" in out_str, (
        "self-knowledge bug: protagonist recall hit was fog-dropped — "
        "pov must always see recall hits about itself"
    )


def test_recall_query_self_hit_not_dropped_by_goal_query():
    """T_SK5b: recall on the protagonist's own goal is not dropped."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("recall_query", {"q": "探索"}))

    assert "hits" in out
    out_str = json.dumps(out, ensure_ascii=False)
    assert "protagonist" in out_str, (
        "self-knowledge bug: protagonist recall hit on own goal was fog-dropped"
    )


# ---------------------------------------------------------------------------
# T_SK6: recall_query hit about unknown NPC is still fog-dropped (fog intact)
# ---------------------------------------------------------------------------

def test_recall_query_unknown_npc_hit_still_dropped():
    """T_SK6: fog is intact for non-self — an unknown NPC's hit is still dropped."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("recall_query", {"q": "神秘"}))  # matches stranger sketch

    assert "hits" in out
    out_str = json.dumps(out, ensure_ascii=False)
    assert "神秘的路人" not in out_str, (
        "fog regression: stranger's sketch surfaced in recall even though protagonist "
        "never met stranger and stranger is not co-present"
    )


# ---------------------------------------------------------------------------
# T_SK7: protagonist NOT in present still gets self-knowledge (the original bug scenario)
# ---------------------------------------------------------------------------

def test_characters_query_self_works_when_not_in_present():
    """T_SK7: self-knowledge works even when protagonist is excluded from scene['present'].

    This is the exact scenario that caused the bug: _build_scene excludes the
    protagonist from present, so co_present=False, and without a knows() grant
    the old code returned known:false.  The fix must treat pov==cid before any
    present/knows check.
    """
    from llm.tools import build_tool_registry

    r, w, scene = _world_protagonist_no_self_grant()
    # Double-check scene setup — protagonist must NOT be in present
    assert "protagonist" not in scene["present"], (
        "test setup error: protagonist should not be in present for this test"
    )

    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "protagonist"}))

    matches = out.get("matches", [])
    self_record = next((m for m in matches if m.get("id") == "protagonist"), None)
    assert self_record is not None, "protagonist not found in matches"
    assert "sketch" in self_record, (
        "self-knowledge bug: sketch absent when protagonist not in present — "
        "pov==cid bypass must fire BEFORE the co-present/knows checks"
    )
    assert "goal" in self_record, (
        "self-knowledge bug: goal absent when protagonist not in present"
    )
    assert self_record.get("known") is not False, (
        "self-knowledge bug: known:false returned when protagonist not in present"
    )
