"""Regression coverage for durable, player-safe public place observations."""
from __future__ import annotations

from app.engine import build_engine
from app.gui import _seed_demo_campaign
from app.play import _build_scene
from context.assembler import assemble_context
from context.native_authoring import native_authoring_context, native_authoring_policy_errors
from context.place_observations import public_place_observations
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.cascade import cascade_trigger
from loop.fleet import backstop_quests


def _engine_with_observations(tmp_path):
    engine = build_engine(tmp_path / "observations", provider=FakeLLMProvider(), embedder=None)
    _seed_demo_campaign(engine)
    engine.store.append_many([
        kernel_event("place_created", day=1, scene="s1", turn=1, summary="布棚外空地", deltas={
            "id": "canvas_edge", "level": 1, "kind": "venue", "seed": "布棚边的公开空地",
            "visibility": "public",
        }),
        kernel_event("place_linked", day=1, scene="s1", turn=1, summary="客栈至布棚外空地", deltas={
            "a": "stonebridge_inn", "b": "canvas_edge", "visibility": "public",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=1, summary="主角抵达布棚外空地", deltas={
            "who": "protagonist", "to": "canvas_edge",
        }),
        kernel_event("world_change", day=1, scene="s1", turn=2, summary="棚布背后是支架与拉绳。", deltas={
            "place": "canvas_edge", "level": 1, "summary": "棚布背后是支架与拉绳。",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "stable_layout", "observation_range": "布棚外空地近处",
        }),
        kernel_event("world_change", day=1, scene="s1", turn=2, summary="这一眼未见第二根木桩。", deltas={
            "place": "canvas_edge", "level": 1, "summary": "这一眼未见第二根木桩。",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "single_observation", "observation_range": "布棚外空地近处",
        }),
        kernel_event("place_created", day=1, scene="s1", turn=2, summary="隐藏库房", deltas={
            "id": "hidden_vault", "level": 1, "kind": "venue", "seed": "PRIVATE_OBSERVATION_SENTINEL",
            "visibility": "hidden",
        }),
        kernel_event("world_change", day=1, scene="s1", turn=2, summary="PRIVATE_OBSERVATION_SENTINEL", deltas={
            "place": "hidden_vault", "level": 1, "summary": "PRIVATE_OBSERVATION_SENTINEL",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "stable_layout", "observation_range": "库房近处",
        }),
        # Untagged legacy/global world changes are deliberately not promoted
        # into a player/native observation merely because their place is known.
        kernel_event("world_change", day=1, scene="s1", turn=2, summary="UNMARKED_WORLD_SENTINEL", deltas={
            "place": "canvas_edge", "level": 1, "summary": "UNMARKED_WORLD_SENTINEL",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    return engine


def test_public_observations_survive_narrative_cropping_and_respect_pov(tmp_path):
    engine = _engine_with_observations(tmp_path)
    try:
        scene = _build_scene(engine)
        observations = public_place_observations(engine.world, scene)
        assert [(item["place"], item["kind"], item["range"]) for item in observations] == [
            ("canvas_edge", "stable_layout", "布棚外空地近处"),
            ("canvas_edge", "single_observation", "布棚外空地近处"),
        ]
        # No narration event was needed: durable event metadata, not a raw
        # transcript, keeps the bounded observation available after trimming.
        assert all(item["source"] == "author_world" and item["day"] == 1 for item in observations)
        native = native_authoring_context(engine.world, scene)
        assert native["public_place_observations"] == observations
        normal = assemble_context(engine.registry, engine.world, scene)
        assert "棚布背后是支架与拉绳。" in normal
        assert "这一眼未见第二根木桩。" in normal
        assert "PRIVATE_OBSERVATION_SENTINEL" not in repr(native)
        assert "PRIVATE_OBSERVATION_SENTINEL" not in normal
        assert "UNMARKED_WORLD_SENTINEL" not in repr(native)
        assert "UNMARKED_WORLD_SENTINEL" not in normal
    finally:
        engine.store.close()


def test_public_observation_remains_place_associated_on_revisit_and_is_not_global(tmp_path):
    engine = _engine_with_observations(tmp_path)
    try:
        engine.store.append_many([
            kernel_event("entity_moved", day=1, scene="s1", turn=3, summary="主角返回客栈", deltas={
                "who": "protagonist", "to": "stonebridge_inn",
            }),
        ], preflight=lambda history: project(engine.registry, history))
        engine.world = project(engine.registry, engine.store.iter_events())
        context = native_authoring_context(engine.world, _build_scene(engine))
        assert {item["place"] for item in context["public_place_observations"]} == {"canvas_edge"}
        assert all(item["place"] != "hidden_vault" for item in context["public_place_observations"])
    finally:
        engine.store.close()


def test_native_world_is_limited_to_explicit_local_public_observations(tmp_path):
    engine = _engine_with_observations(tmp_path)
    try:
        scene = _build_scene(engine)
        generic = TurnCommit.from_dict({"narration": "越权的世界事件", "moves": [], "places": [], "links": [], "cast": [], "facts": [],
            "world": [{"areas": ["canvas_edge"], "level": 1, "summary": "不加范围的全局断言"}],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试"}],
        })
        assert any("explicit public observations" in error
                   for error in native_authoring_policy_errors(engine.world, scene, generic))

        allowed = TurnCommit.from_dict({"narration": "只描述眼前公开布局", "moves": [], "places": [], "links": [], "cast": [], "facts": [],
            "world": [{"areas": ["canvas_edge"], "level": 1, "summary": "棚布支架固定于空地近处。",
                       "public_observation": True, "visibility": "public",
                       "observation_kind": "stable_layout", "observation_range": "布棚外空地近处"}],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试"}],
        })
        assert native_authoring_policy_errors(engine.world, scene, allowed) == []
    finally:
        engine.store.close()


def test_public_observation_marker_neither_cascades_nor_creates_backstop_quest(tmp_path):
    engine = _engine_with_observations(tmp_path)
    try:
        event = kernel_event("world_change", day=1, scene="s1", turn=4, summary="公开近处观察", deltas={
            "place": "canvas_edge", "level": 1, "summary": "公开近处观察",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "single_observation", "observation_range": "脚边",
        })
        assert cascade_trigger([event], engine.world) == []
        assert backstop_quests(engine.world, [event]) is None
    finally:
        engine.store.close()
