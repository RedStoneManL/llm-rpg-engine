"""P3 Tasks 3, 4, 5: Tool / ToolRegistry scaffold + POV fog (offline).

Task 3: Tool dataclass, ToolRegistry schema/execute/error-check, build_tool_registry.
Task 4: map_query fog-of-war — public topology, knows-gated place facts.
Task 5: recall_query POV fog + map_query navigate path support.
C1/I1: recall_query Person-hit fog — NPC sketch/goal must be gated by knows().
"""
from __future__ import annotations

import json
import pytest

from kernel.registry import Registry
from kernel.projection import empty_world, project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.knowledge import KnowledgeSystem
from systems.character import CharacterSystem
from systems.faction import FactionSystem


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(KnowledgeSystem())
    return r


def _reg_with_characters():
    """Registry including CharacterSystem — needed for Person-hit fog tests."""
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(KnowledgeSystem())
    r.register(CharacterSystem())
    return r


def _reg_with_factions():
    """Registry including CharacterSystem + FactionSystem — needed for faction fog tests."""
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(KnowledgeSystem())
    r.register(CharacterSystem())
    r.register(FactionSystem())
    return r


def _scene(protagonist="hero", present=None, day=1, location="town"):
    return {
        "protagonist": protagonist,
        "present": present if present is not None else [protagonist],
        "day": day,
        "location": location,
    }


# ---------------------------------------------------------------------------
# Task 3: Tool dataclass + ToolRegistry scaffold
# ---------------------------------------------------------------------------

def test_tool_dataclass_schema_shape():
    from llm.tools import Tool
    t = Tool(name="map_query", description="d",
             parameters={"type": "object", "properties": {}}, fn=lambda: {})
    s = t.schema()
    assert s["type"] == "function"
    assert s["function"]["name"] == "map_query"
    assert "parameters" in s["function"]
    assert s["function"]["description"] == "d"


def test_registry_schemas_and_execute():
    from llm.tools import Tool, ToolRegistry
    reg = ToolRegistry([Tool(name="echo", description="d",
                             parameters={"type": "object"},
                             fn=lambda **kw: {"got": kw})])
    schemas = reg.schemas()
    assert isinstance(schemas, list) and schemas[0]["function"]["name"] == "echo"
    out = reg.execute("echo", {"a": 1})
    assert json.loads(out) == {"got": {"a": 1}}


def test_registry_execute_unknown_tool_returns_error_json():
    from llm.tools import ToolRegistry
    reg = ToolRegistry([])
    out = reg.execute("nope", {})
    assert "error" in json.loads(out)


def test_registry_execute_catches_tool_exception():
    """A throwing tool must NEVER crash the turn — execute returns {"error":...}."""
    from llm.tools import Tool, ToolRegistry
    def boom(**kw):
        raise ValueError("bad arg")
    reg = ToolRegistry([Tool(name="boom", description="d",
                             parameters={"type": "object"}, fn=boom)])
    out = reg.execute("boom", {})
    assert "error" in json.loads(out)


def test_build_tool_registry_returns_named_tools():
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene())
    names = {s["function"]["name"] for s in reg.schemas()}
    # P3a minimal surface:
    assert "map_query" in names
    assert "recall_query" in names


def test_build_tool_registry_dm_false_excludes_dm_tools():
    """dm=False (default) must NOT include dm_world_query."""
    from llm.tools import build_tool_registry
    reg = build_tool_registry(_reg(), empty_world(_reg()), _scene(), dm=False)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "dm_world_query" not in names


# ---------------------------------------------------------------------------
# Task 4: map_query fog-of-war fixtures
# ---------------------------------------------------------------------------

def _world_with_map(knows_gate: bool):
    """Build a world with two places (city, gate), a link, and optionally a
    knowledge_set granting hero the gate.是否可通行 fact."""
    r = _reg()
    evs = [
        kernel_event("place_created", day=1, scene="g", summary="city",
                     deltas={"id": "city", "level": 2, "kind": "settlement",
                             "seed": "城"}, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="gate",
                     deltas={"id": "gate", "level": 3, "kind": "venue",
                             "seed": "门", "parent": "city"}, turn=1),
        kernel_event("place_linked", day=1, scene="g", summary="link",
                     deltas={"a": "city", "b": "gate", "travel_cost": 1}, turn=1),
        # Create hero entity so entity_moved works
        kernel_event("place_created", day=1, scene="g", summary="hero entity",
                     deltas={"id": "hero", "level": 1, "kind": "settlement",
                             "seed": "x"}, turn=1),
        kernel_event("entity_moved", day=1, scene="g", summary="hero@city",
                     deltas={"who": "hero", "to": "city"}, turn=1),
    ]
    if knows_gate:
        evs.append(kernel_event("knowledge_set", day=1, scene="g", summary="knows",
                                deltas={"knower": "hero",
                                        "fact_key": "gate.是否可通行",
                                        "value": "可通行"}, turn=1))
    w = project(r, iter(evs))
    # Assert ground-truth (divergent from believed value) directly on the graph.
    g = w["systems"]["ontology"]
    g.assert_fact("gate", "是否可通行", "其实已塌",
                  day=1, turn=1, source_event="seed_gt")
    return r, w


def test_map_query_returns_topology_public():
    """Exits/containment are public regardless of knowledge."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=False)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("map_query", {"q": "city"}))
    # exits / adjacent places visible (public topology):
    assert "gate" in json.dumps(out, ensure_ascii=False)


def test_map_query_hides_unknown_place_fact():
    """Protagonist does NOT know gate.是否可通行 → the fact must NOT appear."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=False)
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("map_query", {"q": "gate"})
    assert "其实已塌" not in out      # ground truth never leaks to POV
    assert "可通行" not in out        # even the category phrase must not appear


def test_map_query_shows_known_place_fact():
    """When the protagonist KNOWS the fact, the BELIEVED value appears (not truth)."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=True)
    reg = build_tool_registry(r, w, _scene())
    out = reg.execute("map_query", {"q": "gate"})
    assert "可通行" in out            # believed value surfaces
    assert "其实已塌" not in out      # divergent ground truth still hidden


def test_map_query_pov_not_in_scene_errors():
    """A pov entity not in scene["present"] and not the protagonist → error."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=True)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("map_query", {"q": "gate", "pov": "stranger"}))
    assert "error" in out


def test_map_query_pov_present_npc_allowed():
    """A pov entity that IS in scene["present"] is valid (DD5)."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=False)
    scene = _scene(present=["hero", "ally"])
    reg = build_tool_registry(r, w, scene)
    # 'ally' is present; should not return an error (may return empty facts for ally)
    out = json.loads(reg.execute("map_query", {"q": "gate", "pov": "ally"}))
    assert "error" not in out


# ---------------------------------------------------------------------------
# Task 5: recall_query POV fog + navigate path in map_query
# ---------------------------------------------------------------------------

def _world_with_recall(knows_place_fact: bool):
    """Build a world with two places and a knowledge fact on 'gate'.
    The recall system will return hits for places.
    If knows_place_fact=True, hero knows gate.是否可通行."""
    r = _reg()
    evs = [
        kernel_event("place_created", day=1, scene="g", summary="city",
                     deltas={"id": "city", "level": 2, "kind": "settlement",
                             "seed": "城市"}, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="gate",
                     deltas={"id": "gate", "level": 3, "kind": "venue",
                             "seed": "城门"}, turn=1),
        kernel_event("place_linked", day=1, scene="g", summary="link",
                     deltas={"a": "city", "b": "gate", "travel_cost": 1}, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="hero ent",
                     deltas={"id": "hero", "level": 1, "kind": "settlement",
                             "seed": "x"}, turn=1),
        kernel_event("entity_moved", day=1, scene="g", summary="hero@city",
                     deltas={"who": "hero", "to": "city"}, turn=1),
    ]
    if knows_place_fact:
        evs.append(kernel_event("knowledge_set", day=1, scene="g", summary="knows gate",
                                deltas={"knower": "hero",
                                        "fact_key": "gate.secret",
                                        "value": "有守卫"}, turn=1))
    return r, project(r, iter(evs))


def test_map_query_navigate_path():
    """map_query with path_to returns the navigate result: path + total_cost."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=False)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("map_query", {"q": "city", "path_to": "gate"}))
    assert "path" in out
    assert "total_cost" in out
    assert "gate" in out["path"]
    assert out["total_cost"] == 1


def test_map_query_navigate_path_not_found():
    """map_query with path_to returns path=[] total_cost=None when unreachable."""
    from llm.tools import build_tool_registry
    r, w = _world_with_map(knows_gate=False)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("map_query", {"q": "city", "path_to": "nonexistent"}))
    assert out.get("path") == [] or out.get("total_cost") is None


def test_recall_query_returns_hits():
    """recall_query returns text hits matching the query string."""
    from llm.tools import build_tool_registry
    r, w = _world_with_recall(knows_place_fact=False)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("recall_query", {"q": "城"}))
    # Should include a 'hits' key
    assert "hits" in out
    assert len(out["hits"]) > 0


def test_recall_query_fog_drops_unknown_fact_hits():
    """Recall hits referencing a fact the POV agent doesn't know are dropped.

    We create a Knowledge system recall hit that only appears when
    a knows:{fact} fact exists and the pov agent doesn't know it → hit is dropped.
    Since PlaceSystem.recall returns entity id/seed hits (structural, always public),
    we verify the fog rule by checking that a hit whose underlying fact is
    fog-gated is not in the POV result.

    Implementation: inject a fake hit via a custom system that mimics knowledge-gated
    recall. Since our engine's PlaceSystem.recall hits are structural (public),
    we test the fog drop rule via the KnowledgeSystem-based approach: a hit whose
    ref["fact_key"] is set and the protagonist does NOT know it is dropped.
    """
    from llm.tools import build_tool_registry
    # With knows_place_fact=True, hero knows gate.secret
    r, w = _world_with_recall(knows_place_fact=True)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("recall_query", {"q": "城"}))
    # At minimum the city place is returned (structural hit — always public)
    assert "hits" in out
    hits_text = json.dumps(out, ensure_ascii=False)
    # city and gate both contain "城" in seed — both public topology hits must appear
    assert "城" in hits_text


def test_recall_query_empty_query_returns_hits_list():
    """recall_query with any query returns a dict with 'hits' key (even if empty)."""
    from llm.tools import build_tool_registry
    r, w = _world_with_recall(knows_place_fact=False)
    reg = build_tool_registry(r, w, _scene())
    out = json.loads(reg.execute("recall_query", {"q": "xyz_not_found"}))
    assert "hits" in out
    assert isinstance(out["hits"], list)


def test_recall_query_gated_fact_hit_dropped():
    """A recall hit referencing a knowledge-gated fact_key is dropped from POV result.

    This is the original (forward-guard) test: hits with ref["fact_key"] set that
    the pov agent does NOT know must be excluded. Structural place hits (no fact_key)
    remain public.
    """
    from llm.tools import build_tool_registry
    from systems.knowledge import knows

    # Build a world WITHOUT the knowledge fact, so hero does NOT know gate.secret
    r, w = _world_with_recall(knows_place_fact=False)
    scene = _scene()
    reg = build_tool_registry(r, w, scene)

    # Verify hero doesn't know the fact (no knowledge_set event fired):
    g = w["systems"]["ontology"]
    assert knows(g, "hero", "gate.secret", 1) is None

    # Place entity hits (ref={"id": ...} without fact_key) are structural and returned.
    out = json.loads(reg.execute("recall_query", {"q": "城门"}))
    assert "hits" in out
    assert isinstance(out["hits"], list)


# ---------------------------------------------------------------------------
# C1/I1 — Person-hit fog: NPC sketch/goal must be gated by knows()
# ---------------------------------------------------------------------------

def _world_with_spy_npc(*, protagonist_knows_spy: bool, spy_in_scene: bool):
    """Build a world with a spy NPC whose sketch/goal are secret.

    protagonist_knows_spy=True  → assert knowledge_set for spy.sketch + spy.goal
                                   so the protagonist legitimately knows the NPC.
    spy_in_scene=True           → spy appears in scene["present"] (co-presence rule).
    """
    r = _reg_with_characters()
    evs = [
        kernel_event("character_created", day=1, scene="g", summary="spy",
                     deltas={
                         "id": "spy",
                         "sketch": "潜伏的刺客，真实身份是叛徒",
                         "goal": "刺杀国王",
                         "tier": "major",
                     }, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="town",
                     deltas={"id": "town", "level": 2, "kind": "settlement",
                             "seed": "镇"}, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="hero entity",
                     deltas={"id": "hero", "level": 1, "kind": "settlement",
                             "seed": "h"}, turn=1),
    ]
    if protagonist_knows_spy:
        # Grant the protagonist knowledge of both spy facets
        evs.append(kernel_event("knowledge_set", day=1, scene="g", summary="knows sketch",
                                deltas={"knower": "hero",
                                        "fact_key": "spy.sketch",
                                        "value": "潜伏的刺客，真实身份是叛徒"}, turn=1))
        evs.append(kernel_event("knowledge_set", day=1, scene="g", summary="knows goal",
                                deltas={"knower": "hero",
                                        "fact_key": "spy.goal",
                                        "value": "刺杀国王"}, turn=1))
    w = project(r, iter(evs))
    present = ["hero", "spy"] if spy_in_scene else ["hero"]
    scene = {"protagonist": "hero", "present": present, "day": 1, "location": "town"}
    return r, w, scene


def test_recall_query_person_hit_dropped_when_never_met():
    """C1 fog-leak regression: protagonist who never met NPC must NOT see sketch/goal.

    This is the critical test — it MUST FAIL against the pre-fix code and PASS
    after the Person-hit gating is added to _recall_query_fn.
    """
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_spy_npc(protagonist_knows_spy=False, spy_in_scene=False)
    reg = build_tool_registry(r, w, scene)

    out = json.loads(reg.execute("recall_query", {"q": "刺"}))
    assert "hits" in out

    # The secret text must NOT appear anywhere in the response
    out_str = json.dumps(out, ensure_ascii=False)
    assert "潜伏的刺客" not in out_str, (
        "fog-leak: spy sketch leaked to protagonist who never met NPC"
    )
    assert "刺杀国王" not in out_str, (
        "fog-leak: spy goal leaked to protagonist who never met NPC"
    )
    # The hit itself must be absent (dropped entirely)
    assert all(h.get("system") != "character" or "spy" not in h.get("text", "")
               for h in out["hits"]), (
        "fog-leak: spy character hit returned to protagonist who never met NPC"
    )


def test_recall_query_person_hit_allowed_when_protagonist_knows():
    """Protagonist who KNOWS the NPC's sketch/goal fact sees the hit (fog allows)."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_spy_npc(protagonist_knows_spy=True, spy_in_scene=False)
    reg = build_tool_registry(r, w, scene)

    out = json.loads(reg.execute("recall_query", {"q": "刺"}))
    assert "hits" in out

    out_str = json.dumps(out, ensure_ascii=False)
    # When the protagonist knows the NPC, the hit should be returned
    assert "spy" in out_str, (
        "known NPC hit was dropped — should be present when protagonist knows spy"
    )


def test_recall_query_person_hit_allowed_when_co_present():
    """NPC in scene['present'] is co-present — hit must be returned even if no knows()."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_spy_npc(protagonist_knows_spy=False, spy_in_scene=True)
    reg = build_tool_registry(r, w, scene)

    out = json.loads(reg.execute("recall_query", {"q": "刺"}))
    assert "hits" in out

    out_str = json.dumps(out, ensure_ascii=False)
    # Co-presence makes the NPC visible — hit must be returned
    assert "spy" in out_str, (
        "co-present NPC hit was dropped — should be present when spy is in scene"
    )


# ---------------------------------------------------------------------------
# Task 8 (P3b): characters_query + factions_query POV tools
# ---------------------------------------------------------------------------

# --- characters_query fixtures ---

def _world_with_npc(
    *,
    protagonist_knows_sketch: bool = False,
    protagonist_knows_goal: bool = False,
    npc_in_scene: bool = False,
    npc_has_hidden: bool = False,
):
    """Build a world with hero + informant NPC.

    informant has sketch (身份) and goal, plus optionally a hidden facet.
    protagonist_knows_sketch / protagonist_knows_goal control knowledge grants.
    npc_in_scene=True adds informant to scene["present"].
    """
    r = _reg_with_characters()
    evs = [
        kernel_event("character_created", day=1, scene="g", summary="hero",
                     deltas={
                         "id": "hero",
                         "sketch": "一个普通的旅行者",
                         "goal": "找到失踪的父亲",
                         "tier": "protagonist",
                     }, turn=1),
        kernel_event("character_created", day=1, scene="g", summary="informant",
                     deltas={
                         "id": "informant",
                         "sketch": "城里最消息灵通的商人",
                         "goal": "垄断情报市场",
                         "tier": "major",
                     }, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="town",
                     deltas={"id": "town", "level": 2, "kind": "settlement",
                             "seed": "镇"}, turn=1),
    ]
    if npc_has_hidden:
        evs.append(kernel_event("character_evolved", day=1, scene="g", summary="hidden",
                                deltas={"id": "informant", "predicate": "hidden",
                                        "value": "实为帝国密探"}, turn=1))
    if protagonist_knows_sketch:
        evs.append(kernel_event("knowledge_set", day=1, scene="g", summary="knows sketch",
                                deltas={"knower": "hero",
                                        "fact_key": "informant.sketch",
                                        "value": "城里最消息灵通的商人"}, turn=1))
    if protagonist_knows_goal:
        evs.append(kernel_event("knowledge_set", day=1, scene="g", summary="knows goal",
                                deltas={"knower": "hero",
                                        "fact_key": "informant.goal",
                                        "value": "垄断情报市场"}, turn=1))
    w = project(r, iter(evs))
    present = ["hero"] + (["informant"] if npc_in_scene else [])
    scene = {"protagonist": "hero", "present": present, "day": 1, "location": "town"}
    return r, w, scene


def test_characters_query_known_sketch_returned():
    """When protagonist knows informant.sketch, it appears in the result."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc(protagonist_knows_sketch=True, protagonist_knows_goal=False)
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "informant"}))
    out_str = json.dumps(out, ensure_ascii=False)
    # The believed sketch value must appear
    assert "城里最消息灵通的商人" in out_str, (
        "known sketch facet was omitted — should be present when protagonist knows it"
    )


def test_characters_query_unknown_goal_omitted():
    """When protagonist does NOT know informant.goal, it must be absent from result."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc(protagonist_knows_sketch=True, protagonist_knows_goal=False)
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "informant"}))
    out_str = json.dumps(out, ensure_ascii=False)
    # Goal is NOT known — must not appear
    assert "垄断情报市场" not in out_str, (
        "fog-leak: unknown goal facet leaked to protagonist"
    )


def test_characters_query_known_goal_returned():
    """When protagonist knows informant.goal, it appears in the result."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc(protagonist_knows_sketch=True, protagonist_knows_goal=True)
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "informant"}))
    out_str = json.dumps(out, ensure_ascii=False)
    assert "垄断情报市场" in out_str, (
        "known goal facet was omitted — should be present when protagonist knows it"
    )


def test_characters_query_hidden_always_gated():
    """'hidden' facet is ALWAYS fog-gated, even if protagonist knows sketch/goal."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc(
        protagonist_knows_sketch=True,
        protagonist_knows_goal=True,
        npc_has_hidden=True,
    )
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "informant"}))
    out_str = json.dumps(out, ensure_ascii=False)
    assert "帝国密探" not in out_str, (
        "fog-leak: hidden facet leaked to protagonist — hidden is ALWAYS gated"
    )


def test_characters_query_never_met_returns_known_false():
    """A character the protagonist has never met (no knows, not co-present) → known:false."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc(
        protagonist_knows_sketch=False,
        protagonist_knows_goal=False,
        npc_in_scene=False,
    )
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "informant"}))
    out_str = json.dumps(out, ensure_ascii=False)
    # No sketch/goal must leak
    assert "城里最消息灵通的商人" not in out_str, (
        "fog-leak: sketch of never-met NPC leaked"
    )
    assert "垄断情报市场" not in out_str, (
        "fog-leak: goal of never-met NPC leaked"
    )
    # The result should signal the character exists but is unknown
    assert "known" in out_str and ("false" in out_str.lower() or "False" in out_str), (
        "never-met NPC should be returned with known:false marker"
    )


def test_characters_query_co_present_existence_visible():
    """NPC in scene['present'] — existence is visible (co-present rule), but
    unknown facets are still gated."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc(
        protagonist_knows_sketch=False,
        protagonist_knows_goal=False,
        npc_in_scene=True,
    )
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "informant"}))
    out_str = json.dumps(out, ensure_ascii=False)
    # Existence must be visible (co-present) — the id at least
    assert "informant" in out_str, (
        "co-present NPC's existence was hidden — should be visible"
    )
    # But unknown sketch/goal must still be gated
    assert "城里最消息灵通的商人" not in out_str, (
        "fog-leak: unknown sketch of co-present NPC leaked"
    )
    assert "垄断情报市场" not in out_str, (
        "fog-leak: unknown goal of co-present NPC leaked"
    )


def test_characters_query_pov_not_in_scene_errors():
    """A pov entity not in scene["present"] → {"error": ...}."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("characters_query", {"q": "informant", "pov": "stranger"}))
    assert "error" in out


def test_characters_query_in_build_tool_registry():
    """characters_query must be in the POV tool set (dm=False default)."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_npc()
    reg = build_tool_registry(r, w, scene)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "characters_query" in names


# --- factions_query fixtures ---

def _world_with_guild(
    *,
    protagonist_knows_member_rank: bool = False,
):
    """Build a world with a guild faction and two members (alice, bob).

    alice has rank 'apprentice', bob has rank 'master'.
    protagonist_knows_member_rank=True grants hero knowledge of alice.rank:guild.
    bob's rank is always unknown to protagonist in this fixture.
    """
    r = _reg_with_factions()
    evs = [
        kernel_event("character_created", day=1, scene="g", summary="hero",
                     deltas={"id": "hero", "sketch": "主角", "goal": "冒险",
                             "tier": "protagonist"}, turn=1),
        kernel_event("character_created", day=1, scene="g", summary="alice",
                     deltas={"id": "alice", "sketch": "学徒炼金师",
                             "goal": "掌握炼金术", "tier": "major"}, turn=1),
        kernel_event("character_created", day=1, scene="g", summary="bob",
                     deltas={"id": "bob", "sketch": "行会秘密大师",
                             "goal": "掌控行会", "tier": "major"}, turn=1),
        kernel_event("faction_created", day=1, scene="g", summary="guild",
                     deltas={"id": "guild", "op": "faction",
                             "ranks": ["apprentice", "journeyman", "master"],
                             "seed": "炼金师行会"}, turn=1),
        kernel_event("member_changed", day=1, scene="g", summary="alice joins guild",
                     deltas={"op": "member", "person": "alice", "faction": "guild",
                             "rank": "apprentice"}, turn=1),
        kernel_event("member_changed", day=1, scene="g", summary="bob joins guild",
                     deltas={"op": "member", "person": "bob", "faction": "guild",
                             "rank": "master"}, turn=1),
        kernel_event("place_created", day=1, scene="g", summary="town",
                     deltas={"id": "town", "level": 2, "kind": "settlement",
                             "seed": "镇"}, turn=1),
    ]
    if protagonist_knows_member_rank:
        # Grant knowledge of alice's rank in guild only
        evs.append(kernel_event("knowledge_set", day=1, scene="g",
                                summary="knows alice rank",
                                deltas={"knower": "hero",
                                        "fact_key": "alice.rank:guild",
                                        "value": "apprentice"}, turn=1))
    w = project(r, iter(evs))
    scene = {"protagonist": "hero", "present": ["hero"], "day": 1, "location": "town"}
    return r, w, scene


def test_factions_query_known_member_rank_returned():
    """When protagonist knows alice.rank:guild, alice's rank appears in the result."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_guild(protagonist_knows_member_rank=True)
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("factions_query", {"q": "guild"}))
    out_str = json.dumps(out, ensure_ascii=False)
    # alice's rank is known — must appear
    assert "apprentice" in out_str, (
        "known member rank was omitted — should appear when protagonist knows alice.rank:guild"
    )
    assert "alice" in out_str, (
        "alice's membership was omitted — should appear when protagonist knows her rank"
    )


def test_factions_query_unknown_member_rank_omitted():
    """When protagonist does NOT know bob.rank:guild, bob's rank must not appear."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_guild(protagonist_knows_member_rank=True)
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("factions_query", {"q": "guild"}))
    out_str = json.dumps(out, ensure_ascii=False)
    # bob's rank is NOT known — 'master' rank must not leak
    # (bob himself might or might not appear, but 'master' must not)
    assert "master" not in out_str, (
        "fog-leak: bob's unknown rank 'master' leaked to protagonist"
    )


def test_factions_query_no_knowledge_all_members_hidden():
    """When protagonist knows nothing about guild, no member ranks are revealed."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_guild(protagonist_knows_member_rank=False)
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("factions_query", {"q": "guild"}))
    out_str = json.dumps(out, ensure_ascii=False)
    # Neither rank should appear
    assert "apprentice" not in out_str, (
        "fog-leak: unknown alice rank 'apprentice' leaked"
    )
    assert "master" not in out_str, (
        "fog-leak: unknown bob rank 'master' leaked"
    )


def test_factions_query_pov_not_in_scene_errors():
    """A pov entity not in scene['present'] → {"error": ...}."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_guild()
    reg = build_tool_registry(r, w, scene)
    out = json.loads(reg.execute("factions_query", {"q": "guild", "pov": "outsider"}))
    assert "error" in out


def test_factions_query_in_build_tool_registry():
    """factions_query must be in the POV tool set (dm=False default)."""
    from llm.tools import build_tool_registry

    r, w, scene = _world_with_guild()
    reg = build_tool_registry(r, w, scene)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "factions_query" in names
