"""Offline prompt-boundary tests for canonical actor and item ownership.

Exercise the real projection/context/turn pipeline. Provider inputs are copied in
full: last-user-only call logs cannot prove a repair retained its binding.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from app.engine import build_engine
from app.play import _build_scene
from context.assembler import assemble_context
from engine import settings
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.lore import create_lore_line, run_lore
from loop.strategy import AuthorStrategy, HybridStrategy
from loop.turn import run_turn


class CapturingProvider(FakeLLMProvider):
    """Keep immutable copies of every complete outbound conversation."""

    def __init__(self, proposals, *, prose="你把雨伞放在桌边，观察来往的客人。"):
        super().__init__(responses=[prose], json_responses=copy.deepcopy(proposals))
        self.requests = []

    def complete(self, system, user, **kwargs):
        self.requests.append({"kind": "prose", "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]})
        return super().complete(system, user, **kwargs)

    def complete_messages(self, messages, **kwargs):
        self.requests.append({"kind": "json", "messages": copy.deepcopy(messages)})
        return super().complete_messages(messages, **kwargs)


def _event(event_type, *, day=1, **deltas):
    return kernel_event(event_type, day=day, scene="inn", turn=0,
                        summary="actor inventory fixture", deltas=deltas)


def _refresh(game):
    game.world = project(game.registry, game.store.iter_events())
    game.world["_revision"] = game.store.revision


def _proposal(*, items=None, days=0, narration="你停下脚步，听着窗外的雨声。"):
    proposal = {
        "narration": narration, "moves": [], "places": [], "cast": [], "facts": [],
        "clock": [{"advance": bool(days), "days": days, "bands": 0, "reason": "交接时间"}],
    }
    if items is not None:
        proposal["items"] = items
    return proposal


def _transfer(source="A", destination="B"):
    return {"op": "transfer", "item": "umbrella", "from": source, "to": destination}


@pytest.fixture
def grounding_game(tmp_path, monkeypatch):
    monkeypatch.setattr("app.engine.get_embedder", lambda: None)
    for name in ("digest_fleet", "run_director", "run_cascade", "run_catchup",
                 "run_lore", "run_density", "_run_demote_on_leave"):
        monkeypatch.setattr("loop.turn." + name, lambda *args, **kwargs: [])

    def no_network(*args, **kwargs):
        raise AssertionError("canonical grounding tests must remain offline")

    monkeypatch.setattr("llm.provider._do_post", no_network)
    settings.set_conversation_mode("multiturn")
    game = build_engine(tmp_path / "grounding", provider=FakeLLMProvider())
    game.store.append_many([
        _event("entity_created", id="A", etype="Person", tier="tracked"),
        _event("entity_created", id="B", etype="Person", tier="tracked"),
        _event("entity_created", id="C", etype="Person", tier="tracked",
               attrs={"visibility": "public"}),
        _event("entity_created", id="D", etype="Person", tier="mentioned"),
        _event("entity_created", id="inn", etype="Place", tier="tracked",
               attrs={"level": 3, "kind": "venue", "seed": "旅店"}),
        _event("entity_created", id="away", etype="Place", tier="mentioned",
               attrs={"level": 3, "kind": "venue", "seed": "远处的驿站"}),
        *[_event("relation_added", src=person, rel="located_in", dst="inn")
          for person in ("A", "B", "D")],
        _event("relation_added", src="C", rel="located_in", dst="away"),
        *[_event("object_created", id=item) for item in
          ("umbrella", "staff", "floor_coin", "apron", "OFFSCENE_INVENTORY_CANARY")],
        # Historical events without from remain valid save/replay fixtures.
        *[_event("item_transferred", item=item, to=holder) for item, holder in
          (("umbrella", "A"), ("staff", "B"), ("floor_coin", "inn"),
           ("apron", "D"), ("OFFSCENE_INVENTORY_CANARY", "C"))],
        _event("fact_asserted", subject="umbrella", predicate="holder",
               value="protagonist", secrecy="public"),
        _event("fact_asserted", subject="umbrella", predicate="local_owner_alias",
               value="最初的旅人", secrecy="public"),
        _event("fact_asserted", subject="umbrella", predicate="color",
               value="深蓝色", secrecy="public"),
        _event("fact_asserted", subject="umbrella", predicate="material",
               value="竹骨油纸", secrecy="public"),
    ])
    _refresh(game)
    assert _build_scene(game)["protagonist"] == "A"
    yield game
    game.store.close()


def _json_objects(text):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _bindings(text):
    return [value for value in _json_objects(text)
            if "actor_id" in value and "inventory" in value]


def _binding(text):
    bindings = _bindings(text)
    assert len(bindings) == 1, "each fresh context needs one canonical machine binding"
    return bindings[0]


def _request_text(request):
    return "\n".join(message.get("content") or "" for message in request["messages"])


def _assert_request_binding(request, *, actor="A", holder="A", since_day=1):
    bindings = _bindings(_request_text(request))
    assert bindings, "generation and repair must both receive canonical state"
    for binding in bindings:
        assert binding["actor_id"] == actor
        rows = [row for row in binding["inventory"] if row["item_id"] == "umbrella"]
        assert rows == [{"item_id": "umbrella", "holder_id": holder, "since_day": since_day}]
    assert "OFFSCENE_INVENTORY_CANARY" not in _request_text(request)


def _assert_author_examples(request, actor):
    system = next(message["content"] for message in request["messages"]
                  if message["role"] == "system")
    objects = list(_json_objects(system))
    assert any(value.get("knower") == actor for value in objects)
    assert any(value.get("who") == actor for value in objects)
    assert not re.search(r'"(?:who|knower)"\s*:\s*"protagonist"', system)


def _run(game, strategy, provider, *, action="查看眼前的物品", scene=None):
    result = run_turn(game.registry, game.store, game.world,
                      scene if scene is not None else _build_scene(game), action,
                      strategy=strategy, provider=provider)
    game.world = result.world
    return result


def test_context_prefers_canonical_holder_without_erasing_descriptive_or_legacy_facts(grounding_game):
    game = grounding_game
    game.store.append(_event("item_transferred", day=2, item="umbrella", to="B"))
    _refresh(game)
    graph = game.world["systems"]["ontology"]
    before = copy.deepcopy(graph.__dict__)
    text = assemble_context(game.registry, game.world, _build_scene(game))
    binding = _binding(text)
    assert binding["actor_id"] == "A"
    assert {"item_id": "umbrella", "holder_id": "B", "since_day": 2} in binding["inventory"]
    assert not any(row["holder_id"] == "protagonist" for row in binding["inventory"])
    assert 'umbrella.holder = "protagonist"' in text
    assert 'umbrella.local_owner_alias = "最初的旅人"' in text
    assert 'umbrella.color = "深蓝色"' in text
    assert 'umbrella.material = "竹骨油纸"' in text
    assert re.search(r"held_by[^\n]*优先[^\n]*facts", text)
    assert "禁止在正文复述" in text
    assert "物品实际转移只写 items" in text
    assert "发生付款/获得/消耗时，按原有字段记入 facts" not in text
    assert graph.__dict__ == before


def test_inventory_is_scene_scoped_and_never_turns_redaction_into_unheld(grounding_game):
    game = grounding_game
    game.store.append_many([
        _event("entity_created", id="HIDDEN_HOLDER_CANARY", etype="Person",
               attrs={"visibility": "hidden"}),
        _event("object_created", id="HIDDEN_OBJECT_CANARY", visibility="hidden"),
        _event("object_created", id="sealed_case"),
        _event("object_created", id="private_satchel"),
        _event("item_transferred", item="HIDDEN_OBJECT_CANARY", to="A"),
        _event("item_transferred", item="sealed_case", to="HIDDEN_HOLDER_CANARY"),
        _event("item_transferred", item="private_satchel", to="B", visibility="hidden"),
    ])
    _refresh(game)
    graph = game.world["systems"]["ontology"]
    before = copy.deepcopy(graph.__dict__)
    scene = _build_scene(game)
    assert "B" in scene["present"] and "D" not in scene["present"]
    text = assemble_context(game.registry, game.world, scene)
    binding = _binding(text)
    assert {row["item_id"]: row["holder_id"] for row in binding["inventory"]} == {
        "umbrella": "A", "staff": "B", "floor_coin": "inn", "apron": "D",
    }
    assert binding["inventory_truncated"] is False
    assert not any(row["holder_id"] is None for row in binding["inventory"])
    for canary in ("HIDDEN_HOLDER_CANARY", "HIDDEN_OBJECT_CANARY", "OFFSCENE_INVENTORY_CANARY"):
        assert canary not in text
    assert "未列出不表示无人持有" in text
    assert graph.__dict__ == before


def test_binding_uses_relations_valid_at_scene_day(grounding_game):
    game = grounding_game
    game.store.append(_event("item_transferred", day=3, item="umbrella", to="B"))
    _refresh(game)
    for day, holder, since in ((1, "A", 1), (3, "B", 3)):
        scene = {**_build_scene(game), "day": day}
        text = assemble_context(game.registry, game.world, scene)
        rows = [row for row in _binding(text)["inventory"] if row["item_id"] == "umbrella"]
        assert rows == [{"item_id": "umbrella", "holder_id": holder, "since_day": since}]


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
@pytest.mark.parametrize("mode", ["stateless", "multiturn"])
def test_full_generation_and_modular_repair_calls_keep_actual_binding(grounding_game, strategy_type, mode):
    game = grounding_game
    settings.set_conversation_mode(mode)
    game.store.append(_event("item_transferred", day=2, item="umbrella", to="B"))
    _refresh(game)
    provider = CapturingProvider([
        _proposal(items=[_transfer("protagonist", "A")]),
        {"items": [_transfer("B", "A")]},
    ])
    result = _run(game, strategy_type(), provider)
    assert result.repair_attempts == 1
    assert result.dropped_sections == []
    structured = [request for request in provider.requests if request["kind"] == "json"]
    assert len(structured) == 2
    assert len(structured[0]["messages"]) == 2
    assert len(structured[1]["messages"]) == 4
    for request in provider.requests:
        _assert_request_binding(request, holder="B", since_day=2)
        if strategy_type is AuthorStrategy:
            _assert_author_examples(request, "A")
    graph = result.world["systems"]["ontology"]
    assert graph.neighbors("umbrella", "held_by", 2) == ["A"]
    assert graph.get_entity("protagonist") is None


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
def test_whole_commit_repair_keeps_bound_context(grounding_game, strategy_type):
    game = grounding_game
    strategy = strategy_type()
    provider = CapturingProvider([_proposal(), _proposal()])
    scene = _build_scene(game)
    strategy.produce(game.registry, game.world, scene, "看一看", provider=provider)
    strategy.produce(game.registry, game.world, scene, "看一看", provider=provider,
                     repair="请重新输出完整回合，继续使用已给出的实际实体。")
    for request in provider.requests:
        _assert_request_binding(request)
    assert len([request for request in provider.requests if request["kind"] == "json"]) == 2
    if strategy_type is HybridStrategy:
        assert len([request for request in provider.requests if request["kind"] == "prose"]) == 1


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
def test_next_turn_refreshes_owner_after_committed_transfer(grounding_game, strategy_type):
    game = grounding_game
    strategy = strategy_type()
    provider = CapturingProvider([
        _proposal(items=[_transfer()], days=1, narration="你将雨伞交给同伴。"),
        _proposal(narration="同伴握着雨伞，朝门外望去。"),
    ])
    _run(game, strategy, provider, action="将雨伞交给同伴")
    first_count = len(provider.requests)
    _run(game, strategy, provider, action="看看交接后的情况")
    for request in provider.requests[:first_count]:
        _assert_request_binding(request)
    for request in provider.requests[first_count:]:
        _assert_request_binding(request, holder="B", since_day=2)
    if strategy_type is AuthorStrategy:
        assert len(provider.requests[first_count]["messages"]) == 4
        _assert_author_examples(provider.requests[first_count], "A")


def test_compaction_rebuilds_current_binding_instead_of_cached_owner(grounding_game):
    game = grounding_game
    strategy = AuthorStrategy()
    provider = CapturingProvider([_proposal(items=[_transfer()], days=1), _proposal()])
    provider.last_usage = {"input": 150_000, "output": 10}
    _run(game, strategy, provider)
    assert strategy._compaction_due
    provider.last_usage = {"input": 2_000, "output": 10}
    _run(game, strategy, provider, action="交接后重新观察")
    assert not strategy._compaction_due
    assert len(provider.requests[-1]["messages"]) == 2
    _assert_request_binding(provider.requests[-1], holder="B", since_day=2)
    _assert_author_examples(provider.requests[-1], "A")


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
def test_reopened_save_renders_current_owner_and_actual_actor(grounding_game, strategy_type):
    game = grounding_game
    _run(game, AuthorStrategy(), CapturingProvider([_proposal(items=[_transfer()], days=1)]))
    reopened = build_engine(Path(game.store.db_path).parent, provider=FakeLLMProvider())
    try:
        provider = CapturingProvider([_proposal()])
        _run(reopened, strategy_type(), provider)
        for request in provider.requests:
            _assert_request_binding(request, holder="B", since_day=2)
        assert reopened.world["systems"]["ontology"].value_at("umbrella", "holder", 2) == "protagonist"
    finally:
        reopened.store.close()


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
def test_actor_switch_discards_previous_bound_conversational_cache(grounding_game, strategy_type):
    game = grounding_game
    strategy = strategy_type()
    provider = CapturingProvider([_proposal(), _proposal()], prose="此前仅为甲准备的散文。")
    scene_a = _build_scene(game)
    commit = strategy.produce(game.registry, game.world, scene_a,
                              "OLD_ACTOR_ACTION_CANARY", provider=provider)
    strategy.commit_to_thread(commit.narration)
    first_count = len(provider.requests)
    scene_b = {**scene_a, "protagonist": "B", "present": ["A"]}
    # A repair request cannot reuse another actor's frozen prose or author thread.
    strategy.produce(game.registry, game.world, scene_b, "乙观察手边的物品", provider=provider,
                     repair="继续当前角色的回合")
    for request in provider.requests[first_count:]:
        _assert_request_binding(request, actor="B")
        assert "OLD_ACTOR_ACTION_CANARY" not in _request_text(request)
        if strategy_type is AuthorStrategy:
            _assert_author_examples(request, "B")
    assert all(len(request["messages"]) == 2 for request in provider.requests[first_count:])
    if strategy_type is HybridStrategy:
        assert provider.requests[first_count]["kind"] == "prose"


def test_actor_id_is_json_quoted_in_author_examples(grounding_game):
    game = grounding_game
    actor = '旅人"甲\\乙'
    game.store.append_many([
        _event("entity_created", id=actor, etype="Person", tier="tracked"),
        _event("relation_added", src=actor, rel="located_in", dst="inn"),
    ])
    _refresh(game)
    scene = {**_build_scene(game), "protagonist": actor, "present": ["A", "B"]}
    provider = CapturingProvider([_proposal()])
    AuthorStrategy().produce(game.registry, game.world, scene, "观察", provider=provider)
    _assert_request_binding(provider.requests[0], actor=actor)
    _assert_author_examples(provider.requests[0], actor)


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
def test_ordinary_prose_can_mention_relations_without_keyword_rejection(grounding_game, strategy_type):
    narration = "你翻开一本名叫 Relations 的旧书，书页间夹着一片干叶。"
    provider = CapturingProvider([_proposal(narration=narration)], prose=narration)
    result = _run(grounding_game, strategy_type(), provider)
    assert result.narration == narration
    assert result.repair_attempts == 0
    assert any(event["type"] == "narration_recorded" and event["deltas"]["text"] == narration
               for event in result.events)


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({}, {"line_a"}),
    ({"protagonist": "B"}, {"line_b"}),
    ({"protagonist": "missing_actor"}, set()),
])
def test_lore_actor_override_controls_dormancy_and_legacy_default_is_preserved(grounding_game, kwargs, expected):
    game = grounding_game
    game.store.append_many([
        _event("place_created", id="town_a", level=2, kind="settlement", seed="甲镇"),
        _event("place_created", id="town_b", level=2, kind="settlement", seed="乙镇"),
        _event("relation_added", src="inn", rel="contained_by", dst="town_a"),
        _event("entity_moved", who="B", to="town_b"),
    ])
    for suffix in ("a", "b"):
        create_lore_line(game.store, {
            "id": "line_" + suffix, "complexity": "simple", "about": "街坊的传闻",
            "anchor": "town_" + suffix, "l3_anchor": "inn",
            "description": "一条待推进的线索", "trigger": "有人留意", "threshold": 100,
            "stages": [{"hint": "街角出现了新的痕迹"}, {"hint": "有人前来询问"}],
        }, day=1, scene="inn", turn=0)
    _refresh(game)
    events = run_lore(game.registry, game.store, game.world, **kwargs)
    assert {event["deltas"]["id"] for event in events if event["type"] == "lore_advanced"} == expected


def test_run_turn_forwards_actual_scene_actor_to_lore(grounding_game, monkeypatch):
    received = []

    def capture_lore(registry, store, world, **kwargs):
        received.append(kwargs)
        return []

    monkeypatch.setattr("loop.turn.run_lore", capture_lore)
    scene = {**_build_scene(grounding_game), "protagonist": "B", "present": ["A"]}
    _run(grounding_game, AuthorStrategy(), CapturingProvider([_proposal()]), scene=scene)
    assert received == [{"protagonist": "B"}]


def _visibility_event(event_type, metadata):
    references = ({"item": "RELATION_ITEM_CANARY", "to": "B"}
                  if event_type == "item_transferred" else
                  {"src": "RELATION_ITEM_CANARY", "rel": "held_by", "dst": "B"})
    return _event(event_type, **references, **metadata)


@pytest.mark.parametrize("event_type", ["item_transferred", "relation_added"])
@pytest.mark.parametrize(("metadata", "normalized", "visible"), [
    ({}, {}, True),
    ({"visibility": "public"}, {"visibility": "public"}, True),
    ({"attrs": {"visibility": "public"}}, {"visibility": "public"}, True),
    ({"visibility": "hidden"}, {"visibility": "hidden"}, False),
    ({"attrs": {"visibility": "secret"}}, {"visibility": "secret"}, False),
    ({"visibility": "PUBLIC"}, {"visibility": "hidden"}, False),
    ({"visibility": None}, {"visibility": "hidden"}, False),
    ({"visibility": ["public"]}, {"visibility": "hidden"}, False),
    ({"attrs": "public"}, {"visibility": "hidden"}, False),
    ({"visibility": "public", "attrs": {"visibility": "secret"}},
     {"visibility": "secret"}, False),
    ({"visibility": "secret", "attrs": {"visibility": "public"}},
     {"visibility": "secret"}, False),
])
def test_relation_visibility_survives_both_event_paths_and_reopen(
    grounding_game, event_type, metadata, normalized, visible,
):
    game = grounding_game
    visibility_event = _visibility_event(event_type, metadata)
    game.store.append_many([
        _event("object_created", id="RELATION_ITEM_CANARY"), visibility_event,
    ])
    _refresh(game)
    reopened = build_engine(Path(game.store.db_path).parent, provider=FakeLLMProvider())
    try:
        for candidate in (game, reopened):
            graph = candidate.world["systems"]["ontology"]
            assert graph.neighbors("RELATION_ITEM_CANARY", "held_by", 1) == ["B"]
            relation = next(row for row in graph.relations if row.src == "RELATION_ITEM_CANARY")
            assert relation.attrs == normalized
            text = assemble_context(candidate.registry, candidate.world, _build_scene(candidate))
            rows = [row for row in _binding(text)["inventory"]
                    if row["item_id"] == "RELATION_ITEM_CANARY"]
            if visible:
                assert rows == [{"item_id": "RELATION_ITEM_CANARY", "holder_id": "B", "since_day": 1}]
            else:
                assert rows == []
                assert "RELATION_ITEM_CANARY" not in text
            # Normalizing projection metadata must never rewrite the old event.
            stored = next(event for event in candidate.store.iter_events()
                          if event["id"] == visibility_event["id"])
            assert stored["deltas"] == visibility_event["deltas"]
    finally:
        reopened.store.close()


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
@pytest.mark.parametrize("event_type", ["item_transferred", "relation_added"])
@pytest.mark.parametrize("metadata", [{"visibility": "hidden"}, {"attrs": {"visibility": "secret"}}])
def test_reopened_hidden_relation_stays_out_of_full_generation_and_repair_calls(
    grounding_game, strategy_type, event_type, metadata,
):
    game = grounding_game
    game.store.append_many([
        _event("object_created", id="RELATION_ITEM_CANARY"),
        _visibility_event(event_type, metadata),
    ])
    reopened = build_engine(Path(game.store.db_path).parent, provider=FakeLLMProvider())
    try:
        provider = CapturingProvider([
            _proposal(items=[_transfer("protagonist", "B")]),
            {"items": [_transfer("A", "B")]},
        ])
        result = _run(reopened, strategy_type(), provider)
        assert result.repair_attempts == 1
        assert result.dropped_sections == []
        assert len([request for request in provider.requests if request["kind"] == "json"]) == 2
        for request in provider.requests:
            _assert_request_binding(request)
            assert "RELATION_ITEM_CANARY" not in _request_text(request)
        graph = result.world["systems"]["ontology"]
        assert graph.neighbors("RELATION_ITEM_CANARY", "held_by", 1) == ["B"]
    finally:
        reopened.store.close()


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
@pytest.mark.parametrize("actor", [None, "", "   ", "missing_actor", "inn", "umbrella"])
def test_invalid_actor_fails_before_any_provider_call_or_persistent_change(
    grounding_game, strategy_type, actor,
):
    game = grounding_game
    strategy = strategy_type()
    provider = CapturingProvider([_proposal()])
    scene = {**_build_scene(game), "protagonist": actor}
    before = {
        "events": copy.deepcopy(list(game.store.iter_events(include_retracted=True))),
        "revision": game.store.revision,
        "mirror": Path(game.store.jsonl_path).read_bytes(),
        "graph": copy.deepcopy(game.world["systems"]["ontology"].__dict__),
        "strategy": copy.deepcopy(strategy.__dict__),
    }
    with pytest.raises(ValueError, match="protagonist"):
        _run(game, strategy, provider, scene=scene)
    assert provider.requests == []
    assert provider.calls == []
    assert list(game.store.iter_events(include_retracted=True)) == before["events"]
    assert game.store.revision == before["revision"]
    assert Path(game.store.jsonl_path).read_bytes() == before["mirror"]
    assert game.world["systems"]["ontology"].__dict__ == before["graph"]
    assert strategy.__dict__ == before["strategy"]

    # A cached repair must enforce the same entry boundary, not bypass it by
    # continuing messages assembled before an actor was successfully bound.
    strategy._messages = [{"role": "user", "content": "UNBOUND_REPAIR_CANARY"}]
    if strategy_type is HybridStrategy:
        strategy._frozen_prose = "UNBOUND_REPAIR_CANARY"
    cached = copy.deepcopy(strategy.__dict__)
    with pytest.raises(ValueError, match="protagonist"):
        strategy.produce(game.registry, game.world, scene, "继续", provider=provider,
                         repair="修复当前回合")
    assert provider.requests == []
    assert provider.calls == []
    assert strategy.__dict__ == cached
    assert game.store.revision == before["revision"]


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
def test_binding_actual_actor_clears_preexisting_unbound_repair_cache(grounding_game, strategy_type):
    game = grounding_game
    strategy = strategy_type()
    secret = "UNBOUND_SECRET_CACHE_CANARY"
    cached = [
        {"role": "system", "content": secret},
        {"role": "user", "content": secret},
        {"role": "assistant", "content": secret},
    ]
    strategy._bound_actor = None
    strategy._messages = copy.deepcopy(cached)
    if strategy_type is AuthorStrategy:
        strategy._thread = copy.deepcopy(cached)
        strategy._pending_user = secret
    else:
        strategy._frozen_prose = secret
    before_events = copy.deepcopy(list(game.store.iter_events(include_retracted=True)))
    before_revision = game.store.revision
    provider = CapturingProvider([_proposal()])

    strategy.produce(game.registry, game.world, _build_scene(game), "检查雨伞",
                     provider=provider, repair="重新生成这一回合")

    assert provider.requests
    assert strategy._bound_actor == "A"
    for request in provider.requests:
        assert len(request["messages"]) == 2
        assert secret not in _request_text(request)
        _assert_request_binding(request)
    if strategy_type is AuthorStrategy:
        _assert_author_examples(provider.requests[0], "A")
        assert secret not in json.dumps(strategy._thread, ensure_ascii=False)
    else:
        assert [request["kind"] for request in provider.requests] == ["prose", "json"]
        assert strategy._frozen_prose != secret
    assert list(game.store.iter_events(include_retracted=True)) == before_events
    assert game.store.revision == before_revision


@pytest.mark.parametrize("strategy_type", [AuthorStrategy, HybridStrategy])
def test_nonperson_resource_owner_is_rejected_before_resource_intent_call(
    grounding_game, strategy_type, monkeypatch,
):
    game = grounding_game
    secret_balance = 730_491_827
    game.store.append_many([
        _event("object_created", id="vault"),
        _event("resources_configured", subject="vault", resources={
            "coins": {"initial": 0, "type": "integer", "min": 0},
        }),
        _event("fact_asserted", subject="vault", predicate="coins",
               value=secret_balance, secrecy="secret"),
    ])
    _refresh(game)
    graph = game.world["systems"]["ontology"]
    assert graph.get_entity("vault").etype == "Object"
    assert graph.get_entity("vault").attrs["fact_rules"]["coins"]["resource"] is True
    assert graph.value_at("vault", "coins", 1) == secret_balance
    assert any(fact.subject == "vault" and fact.predicate == "coins" and fact.is_current()
               and fact.secrecy == "secret" for fact in graph.facts)

    strategy = strategy_type()
    strategy._bound_actor = "A"
    strategy._messages = [{"role": "user", "content": "existing bound conversation"}]
    if strategy_type is AuthorStrategy:
        strategy._thread = copy.deepcopy(strategy._messages)
    else:
        strategy._frozen_prose = "existing bound prose"
    provider = CapturingProvider([{"op": "none"}, _proposal()])
    attempted_methods = []
    for name in ("complete", "complete_json", "complete_messages", "complete_with_tools", "supports_tools"):
        def forbidden(*args, _method=name, **kwargs):
            attempted_methods.append(_method)
            raise AssertionError("invalid actor must fail before any provider method")

        monkeypatch.setattr(provider, name, forbidden)

    before_events = copy.deepcopy(list(game.store.iter_events(include_retracted=True)))
    before_revision = game.store.revision
    before_mirror = Path(game.store.jsonl_path).read_bytes()
    before_cache = copy.deepcopy(strategy.__dict__)
    before_graph = copy.deepcopy(graph.__dict__)
    scene = {**_build_scene(game), "protagonist": "vault"}
    with pytest.raises(ValueError, match="protagonist"):
        _run(game, strategy, provider, action="花费一枚硬币", scene=scene)
    assert attempted_methods == []
    assert provider.requests == []
    assert provider.calls == []
    assert list(game.store.iter_events(include_retracted=True)) == before_events
    assert game.store.revision == before_revision
    assert Path(game.store.jsonl_path).read_bytes() == before_mirror
    assert strategy.__dict__ == before_cache
    assert graph.__dict__ == before_graph
