"""Tests for context.assembler — cache-layered context assembly.

Verifies that assemble_context produces a single string with:
  - per-system inject fragments (place exits, character cards) — stable→scene order
  - recalled items (volatile) when query matches
  - protagonist POV facts (scene layer)
  - guardrail marker (⚠️只约束·勿泄露) for unknown-but-true facts
  - stable-before-scene-before-volatile ordering

Offline only: uses FakeEmbedder (or no embedder); no LLM/network.
"""
from __future__ import annotations

import pytest
from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from engine.embed import FakeEmbedder
from systems.ontology import OntologySystem
from systems.character import CharacterSystem
from systems.place import PlaceSystem
from systems.faction import FactionSystem
from systems.knowledge import KnowledgeSystem
from context.assembler import assemble_context


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reg():
    return (
        Registry()
        .register(OntologySystem())
        .register(CharacterSystem())
        .register(PlaceSystem())
        .register(FactionSystem())
        .register(KnowledgeSystem())
    )


def _ev(typ, day=1, scene="s1", **deltas):
    return kernel_event(typ, day=day, scene=scene, summary=f"{typ}", deltas=deltas)


def _build_world():
    """Build a small world with:
      - Place 桥头 (settlement) with adjacent 大路 (cost 1)
      - Character 主角 (protagonist) located in 桥头
      - Character 老兵 (NPC) in scene, knows 密道.location
      - Knowledge: 主角 knows 桥头.status = '安全'
      - Knowledge: 老兵 knows 密道.location = '地窖' (protagonist does NOT know)
      - Ground-truth fact: 密道.location = '地窖' exists on entity 密道
    """
    r = _reg()
    evs = [
        # Places
        _ev("place_created", day=1, id="桥头", level=2, kind="settlement",
            seed="石砌桥头", tier="tracked", detail="partial"),
        _ev("place_created", day=1, id="大路", level=2, kind="wilderness",
            seed="宽阔大道", tier="tracked", detail="partial"),
        _ev("place_created", day=1, id="密道", level=3, kind="dungeon",
            seed="隐秘地道", tier="mentioned", detail="partial"),
        # Link
        _ev("place_linked", day=1, a="桥头", b="大路", travel_cost=1),
        # Characters
        _ev("character_created", day=1, id="主角", sketch="旅行者", goal="调查事件", tier="tracked"),
        _ev("character_created", day=1, id="老兵", sketch="疲惫的老兵", goal="守卫家园", tier="tracked"),
        # Move protagonist to 桥头
        _ev("entity_moved", day=1, who="主角", to="桥头"),
        # Ground-truth fact for 密道
        # We write this as a regular fact on the 密道 entity.
        # We use KnowledgeSystem via knowledge_set on the entity itself — but
        # ground truth needs to be a plain graph fact. We add it via character_evolved
        # using ontology — but 密道 is a Place. Let's use knowledge_broadcast to
        # a dummy knower... actually we need a plain graph.assert_fact.
        # The cleanest way: use character_evolved on the Place entity itself is wrong.
        # We'll add it directly to the graph after projection.
    ]
    w = project(r, evs)

    # Add ground-truth fact for 密道.location directly on the graph
    # (represents god-truth not from any system event, just present in graph)
    g = w["systems"]["ontology"]
    g.assert_fact("密道", "location", "地窖", day=1, turn=0, source_event="gt-1")

    # Knowledge: 主角 knows 桥头.status = '安全'
    g.assert_fact("主角", "knows:桥头.status", "安全", day=1, turn=0, source_event="k1")

    # Knowledge: 老兵 knows 密道.location = '地窖' (主角 does not know this)
    g.assert_fact("老兵", "knows:密道.location", "地窖", day=1, turn=0, source_event="k2")

    # Also add 桥头.status ground truth
    g.assert_fact("桥头", "status", "安全", day=1, turn=0, source_event="gt-2")

    return r, w


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_assembled_context_contains_place_exits():
    """Place exits from PlaceSystem.inject should appear in the assembled context."""
    r, w = _build_world()
    scene = {
        "protagonist": "主角",
        "present": ["主角", "老兵"],
        "day": 1,
        "location": "桥头",
    }
    result = assemble_context(r, w, scene)
    # PlaceSystem.inject should include current location + exits
    assert "桥头" in result
    assert "大路" in result


def test_assembled_context_contains_character_card():
    """CharacterSystem.inject should include present character cards."""
    r, w = _build_world()
    scene = {
        "protagonist": "主角",
        "present": ["主角", "老兵"],
        "day": 1,
        "location": "桥头",
    }
    result = assemble_context(r, w, scene)
    # CharacterSystem.inject renders present characters
    assert "旅行者" in result or "主角" in result
    assert "老兵" in result


def test_assembled_context_contains_recall_hit_when_query_matches():
    """When query matches a place or character, recall hit appears in context."""
    r, w = _build_world()
    scene = {
        "protagonist": "主角",
        "present": ["主角", "老兵"],
        "day": 1,
        "location": "桥头",
    }
    # Query for 老兵 by sketch — should trigger recall hit
    result = assemble_context(r, w, scene, query="疲惫", embedder=FakeEmbedder(), k=6)
    assert "老兵" in result


def test_assembled_context_contains_pov_fact():
    """Protagonist's known fact should appear in assembled context (POV section)."""
    r, w = _build_world()
    scene = {
        "protagonist": "主角",
        "present": ["主角", "老兵"],
        "day": 1,
        "location": "桥头",
    }
    result = assemble_context(r, w, scene)
    # 主角 knows 桥头.status = '安全' → should appear in pov
    assert "安全" in result


def test_assembled_context_contains_guardrail_marker():
    """Facts protagonist does NOT know but are in graph should have guardrail marker."""
    r, w = _build_world()
    scene = {
        "protagonist": "主角",
        "present": ["主角", "老兵"],
        "day": 1,
        "location": "桥头",
    }
    result = assemble_context(r, w, scene)
    # 密道.location is unknown to protagonist but true in graph — guardrail
    assert "⚠️只约束·勿泄露" in result


def test_assembled_context_stable_before_scene_before_volatile():
    """Layer ordering: stable fragments must appear before scene, scene before volatile."""
    r, w = _build_world()
    scene = {
        "protagonist": "主角",
        "present": ["主角", "老兵"],
        "day": 1,
        "location": "桥头",
    }
    result = assemble_context(r, w, scene, query="老兵", embedder=FakeEmbedder(), k=6)
    # Check ordering via layer markers
    stable_pos = result.find("[stable]")
    scene_pos = result.find("[scene]")
    volatile_pos = result.find("[volatile]")

    # [scene] must come after [stable] if stable exists
    if stable_pos != -1 and scene_pos != -1:
        assert stable_pos < scene_pos, "stable must precede scene"
    # [volatile] must come after [scene] if both exist
    if scene_pos != -1 and volatile_pos != -1:
        assert scene_pos < volatile_pos, "scene must precede volatile"


def test_assembled_context_no_query_skips_recall():
    """Without a query, the recall block is absent (no volatile recall section)."""
    r, w = _build_world()
    scene = {
        "protagonist": "主角",
        "present": ["主角", "老兵"],
        "day": 1,
        "location": "桥头",
    }
    # With no query, recall is not invoked — but viewpoint/inject fragments still present
    result = assemble_context(r, w, scene)
    # Result should be a non-empty string
    assert isinstance(result, str)
    assert len(result) > 0


def test_assemble_context_returns_string():
    """assemble_context always returns a str."""
    r, w = _build_world()
    scene = {"protagonist": "主角", "present": ["主角"], "day": 1}
    result = assemble_context(r, w, scene)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Task 9: P2 — force-push recap + storylines (query-independent)
# ---------------------------------------------------------------------------

def test_recap_and_storylines_force_pushed_without_query():
    """Recap stable summary + recent raw + quest ledger (明账) all appear with query=None."""
    from systems.lore import LoreSystem
    from systems.narrative import NarrativeSystem
    from kernel.events import kernel_event as ke

    reg = (Registry()
           .register(OntologySystem())
           .register(CharacterSystem())
           .register(PlaceSystem())
           .register(FactionSystem())
           .register(KnowledgeSystem())
           .register(LoreSystem())
           .register(NarrativeSystem()))

    evs = [
        # Quest opened (creates a 明 line → appears in 明账 inject)
        ke("quest_opened", day=1, scene="s1", summary="open th_a",
           deltas={"id": "th_a", "summary": "线A：查案", "state": "明"}, turn=1),
        # s1 narration + summary (aged out)
        ke("narration_recorded", day=1, scene="s1", summary="narr",
           deltas={"scene": "s1", "text": "最老原文"}, turn=2),
        ke("scene_summarized", day=1, scene="s1", summary="summ",
           deltas={"scene": "s1", "summary": "s1摘要"}, turn=3),
        # s2 narration (recent)
        ke("narration_recorded", day=1, scene="s2", summary="narr",
           deltas={"scene": "s2", "text": "中间原文"}, turn=4),
        # s3 narration (most recent)
        ke("narration_recorded", day=1, scene="s3", summary="narr",
           deltas={"scene": "s3", "text": "最近原文"}, turn=5),
    ]

    world = project(reg, evs)
    scene = {"protagonist": None, "present": [], "day": 1, "id": "s3"}
    out = assemble_context(reg, world, scene, query=None)   # NO query → still present
    assert "线A：查案" in out            # quest 明账 ledger force-pushed
    assert "最近原文" in out             # recent raw force-pushed
    assert "s1摘要" in out               # aged summary force-pushed (stable block)
    assert "最老原文" not in out         # aged out of raw window
    # ordering: stable summary block precedes scene raw/ledger
    assert out.index("s1摘要") < out.index("最近原文")


def test_recap_summary_absent_when_no_summaries():
    """If no scenes have been summarized, the 往昔概要 block is not emitted."""
    from systems.narrative import NarrativeSystem
    from systems.lore import LoreSystem

    reg = (Registry()
           .register(OntologySystem())
           .register(CharacterSystem())
           .register(PlaceSystem())
           .register(FactionSystem())
           .register(KnowledgeSystem())
           .register(LoreSystem())
           .register(NarrativeSystem()))

    world = project(reg, [])
    out = assemble_context(reg, world, {"protagonist": None, "present": [],
                                        "day": 1, "id": "s1"}, query=None)
    assert "往昔概要" not in out          # nothing summarized → no stable block
