"""Phase A item 1: the knowledge section is exposed in the strategy prompts and
flows end-to-end — a knowledge declaration becomes a knows: fact, which the next
turn's assembled context surfaces as a POV line. This proves the §9 viewpoint
path (build_viewpoint, already wired into assemble_context) is no longer inert."""
from __future__ import annotations

import tempfile
import os

from kernel.registry import Registry
from kernel.projection import empty_world
from kernel.events import open_store
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.knowledge import KnowledgeSystem, knows


def _make_registry():
    registry = Registry()
    registry.register(OntologySystem())
    registry.register(PlaceSystem())
    registry.register(CharacterSystem())
    registry.register(KnowledgeSystem())
    return registry


def _open_temp_store(registry):
    tmp_dir = tempfile.mkdtemp()
    db = os.path.join(tmp_dir, "events.db")
    jsonl = os.path.join(tmp_dir, "events.jsonl")
    return open_store(db, jsonl, allowed_types=registry.event_types())


def test_all_structure_prompts_expose_knowledge_section():
    """甲/丙 structure prompts must mention the knowledge section so the LLM
    knows it can record who-knows-what."""
    from loop import strategy as S
    for prompt in (S._SYSTEM_PROMPT, S._SYSTEM_PROMPT_HYBRID):
        assert "knowledge" in prompt
        assert "told" in prompt  # the primary op must be named


def test_knowledge_declaration_flows_to_pov():
    """told declaration → knows: fact in world → POV line in next turn's context."""
    from loop.strategy import AuthorStrategy
    from loop.turn import run_turn
    from context.assembler import assemble_context

    registry = _make_registry()
    world = empty_world(registry)

    turn1 = {
        "narration": "英雄在集市遇见了老者。",
        "entities": [
            {"id": "hero", "etype": "Person", "tier": "tracked"},
            {"id": "elder", "etype": "Person", "tier": "tracked"},
        ],
    }
    turn2 = {
        "narration": "老者俯身，对英雄低语了一个秘密。",
        "knowledge": [
            {"op": "told", "knower": "hero", "fact_key": "elder.secret",
             "value": "is_spy", "via": "低语"},
        ],
    }
    provider = FakeLLMProvider(json_responses=[turn1, turn2])
    strategy = AuthorStrategy()
    scene = {"protagonist": "hero", "present": ["hero", "elder"], "day": 1,
             "location": "market", "id": "sc01"}

    store = _open_temp_store(registry)
    try:
        result1 = run_turn(registry, store, world, scene, "环顾四周",
                           strategy=strategy, provider=provider)
        # Thread the world forward (as the play loop does) so turn 2's knower ref
        # resolves against the entities turn 1 created.
        result2 = run_turn(registry, store, result1.world, scene, "倾听老者",
                           strategy=strategy, provider=provider)

        # The knows: fact landed in the world.
        g = result2.world["systems"]["ontology"]
        assert knows(g, "hero", "elder.secret", day=1) == "is_spy"

        # And the next turn's assembled context surfaces it as a POV line.
        ctx = assemble_context(registry, result2.world, scene, query="倾听老者")
        assert "pov" in ctx
        assert "elder.secret" in ctx
    finally:
        store.close()
