"""Generation prepares conversation; only durable publication commits it."""
from engine import settings
from loop.strategy import AuthorStrategy
from loop.turn import produce_turn
from llm.provider import FakeLLMProvider
from kernel.registry import Registry
from kernel.projection import empty_world
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem


def _make_registry():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    return r


def test_produce_turn_prepares_without_committing_conversation():
    settings.set_conversation_mode("multiturn")
    registry = _make_registry()
    world = empty_world(registry)
    scene = {"protagonist": "hero", "present": [], "day": 1, "location": "town"}
    s = AuthorStrategy()
    prov = FakeLLMProvider(json_responses=[{"narration": "第一段叙事"}])
    produce_turn(registry, world, scene, "动作", strategy=s, provider=prov)
    # Generation may prepare a system cache, but no user/assistant pair is committed.
    assert len(s._thread) == 1
    assert s._pending_user is not None
    settings.set_conversation_mode("multiturn")
