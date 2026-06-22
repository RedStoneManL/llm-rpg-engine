"""Tests for context.viewpoint — POV / guardrail / NPC bundles.

Design:
  protagonist POV = facts the protagonist KNOWS (via knowledge system)
  guardrail = candidate facts the protagonist does NOT know but have ground-truth in graph
  npc = what each present NPC knows about candidate fact_keys

No LLM/network — pure graph manipulation.
"""
from __future__ import annotations

import pytest
from facts.graph import FactGraph
from context.viewpoint import build_viewpoint


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_graph():
    """Return a small FactGraph with:
      - Place entity '桥' with fact: 桥.status = '破损'
      - Person '主角' (protagonist) knows 桥.status = '破损'
      - Person 'npc甲' knows 密室.location = '地窖' (protagonist does NOT know this)
      - Place '密室' with fact: 密室.location = '地窖'  (ground truth in graph)
      - Protagonist does NOT know 密室.location
    """
    g = FactGraph()
    day = 1

    # entities
    g.add_entity("桥", "Place")
    g.add_entity("密室", "Place")
    g.add_entity("主角", "Person")
    g.add_entity("npc甲", "Person")

    # ground truth facts on subjects
    g.assert_fact("桥", "status", "破损", day=day, turn=0, source_event="e1")
    g.assert_fact("密室", "location", "地窖", day=day, turn=0, source_event="e2")

    # protagonist knows 桥.status
    g.assert_fact("主角", "knows:桥.status", "破损", day=day, turn=0, source_event="e3")

    # npc甲 knows 密室.location (but protagonist does not)
    g.assert_fact("npc甲", "knows:密室.location", "地窖", day=day, turn=0, source_event="e4")

    return g


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_pov_contains_only_protagonist_known_facts():
    """POV should contain only facts the protagonist knows."""
    g = _make_graph()
    candidate_fact_keys = ["桥.status", "密室.location"]
    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角", "npc甲"],
        day=1,
        candidate_fact_keys=candidate_fact_keys,
    )
    pov = result["pov"]
    # protagonist knows 桥.status
    assert "桥.status" in pov
    assert pov["桥.status"] == "破损"
    # protagonist does NOT know 密室.location
    assert "密室.location" not in pov


def test_guardrail_contains_unknown_but_true_facts():
    """Guardrail should contain facts protagonist does NOT know but are true in graph."""
    g = _make_graph()
    candidate_fact_keys = ["桥.status", "密室.location"]
    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角", "npc甲"],
        day=1,
        candidate_fact_keys=candidate_fact_keys,
    )
    guardrail = result["guardrail"]
    # protagonist doesn't know 密室.location, but graph has it
    assert "密室.location" in guardrail
    assert guardrail["密室.location"] == "地窖"
    # protagonist knows 桥.status — NOT in guardrail
    assert "桥.status" not in guardrail


def test_npc_bundle_reflects_npc_knowledge():
    """NPC bundles should contain what each NPC knows about candidate_fact_keys."""
    g = _make_graph()
    candidate_fact_keys = ["桥.status", "密室.location"]
    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角", "npc甲"],
        day=1,
        candidate_fact_keys=candidate_fact_keys,
    )
    npc = result["npc"]
    # npc甲 should be in npc bundle (present but not protagonist)
    assert "npc甲" in npc
    assert npc["npc甲"].get("密室.location") == "地窖"
    # protagonist is not in npc bundle
    assert "主角" not in npc


def test_npc_bundle_excludes_protagonist():
    """The protagonist should not appear in the npc bundle."""
    g = _make_graph()
    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角", "npc甲"],
        day=1,
        candidate_fact_keys=["桥.status", "密室.location"],
    )
    assert "主角" not in result["npc"]


def test_guardrail_empty_if_protagonist_knows_all():
    """If protagonist knows all candidate facts, guardrail is empty."""
    g = FactGraph()
    day = 1
    g.add_entity("桥", "Place")
    g.add_entity("主角", "Person")
    g.assert_fact("桥", "status", "完好", day=day, turn=0, source_event="e1")
    g.assert_fact("主角", "knows:桥.status", "完好", day=day, turn=0, source_event="e2")

    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角"],
        day=1,
        candidate_fact_keys=["桥.status"],
    )
    assert result["guardrail"] == {}


def test_fact_key_without_dot_skipped_in_guardrail():
    """Fact keys without a dot cannot be looked up as subject.predicate — skipped for guardrail."""
    g = FactGraph()
    day = 1
    g.add_entity("主角", "Person")
    # no ground-truth entity for this key
    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角"],
        day=1,
        candidate_fact_keys=["no_dot_key"],
    )
    assert "no_dot_key" not in result["guardrail"]


def test_empty_present_yields_empty_npc():
    """With no present members (or only protagonist), npc bundle is empty."""
    g = _make_graph()
    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角"],
        day=1,
        candidate_fact_keys=["桥.status"],
    )
    assert result["npc"] == {}


def test_result_has_all_three_keys():
    """build_viewpoint must always return all three keys: pov, guardrail, npc."""
    g = _make_graph()
    result = build_viewpoint(
        g,
        protagonist="主角",
        present=["主角", "npc甲"],
        day=1,
        candidate_fact_keys=[],
    )
    assert set(result.keys()) == {"pov", "guardrail", "npc"}
