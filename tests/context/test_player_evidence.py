"""Offline original-input retrieval and prompt/privacy boundary regressions."""
import copy
import json

import pytest

from context.access import pov_world
from context.assembler import assemble_context
from context.player_evidence import format_player_evidence, read_player_evidence
from engine import settings
from facts.graph import FactGraph
from kernel.contextsystem import ContextSystem, Fragment, RecallHit
from kernel.registry import Registry
from llm.provider import FakeLLMProvider
from llm.tools import build_tool_registry
from loop.strategy import AuthorStrategy, HybridStrategy
from systems.narrative import NarrativeSystem


FIRST = "我把铜铃叫作雨燕，不是归雁。只是在心里试试这个名字。"
CANARY = "A_PRIVATE_SOURCE_CANARY"
QUERY = "还记得我最早给铜铃起的名字吗？"


def _record(turn, text, actor="A", refs=("bell",)):
    stamp = {"day": turn, "band": turn % 4, "scene": f"s{turn}", "location": "inn"}
    return {"source_event_id": f"source-{actor}-{turn}", "turn": turn,
            "actor_id": actor, "input": text, "requested_at": stamp,
            "committed_at": dict(stamp), "outcome": "committed_response_not_proof_of_success",
            "entity_refs": list(refs), "narration_ref": "PRIVATE_NARRATION_REF",
            "effect_refs": ["PRIVATE_EFFECT_REF"]}


@pytest.fixture
def source_world():
    graph = FactGraph()
    for actor in ("A", "B"):
        graph.add_entity(actor, "Person", name=f"角色{actor}")
        graph.add_relation(actor, "located_in", "inn", day=1, turn=0, source_event="setup")
    graph.add_entity("inn", "Place")
    graph.add_entity("bell", "Object", aliases=["铜铃"])
    graph.add_entity("umbrella", "Object", name="雨伞")
    graph.add_entity("hidden", "Object", name="地下暗匣", visibility="hidden")
    for item, alias in (("bell", "铜铃"), ("umbrella", "雨伞")):
        graph.assert_fact(item, "alias", alias, day=1, turn=0,
                          source_event="visible-alias", secrecy="public")
    graph.add_relation("bell", "held_by", "A", day=1, turn=0, source_event="setup")
    for turn, name in ((1, "旧铜铃"), (6, "晚钟"), (13, "晨钟")):
        graph.assert_fact("bell", "name", name, day=turn, turn=turn,
                          source_event=f"name-{turn}", secrecy="public")
    narrative = NarrativeSystem().empty_state()
    narrative["scenes"] = [{"scene": f"s{turn}", "raw": ["你检查了周围。"],
                            "summary": "旅途继续，没有记录命名。"} for turn in (1, 8, 14)]
    narrative["super_summary"] = "一路平安，没有其他值得记下的事情。"
    narrative["player_inputs"] = [_record(1, FIRST), *[
        _record(turn, f"第{turn}次检查铜铃的绳结。") for turn in range(2, 15)]]
    world = {"meta": {"day": 15, "band": 1, "scene": "s15"},
             "systems": {"ontology": graph, "narrative": narrative}}
    scene = {"protagonist": "A", "present": ["A", "B"], "day": 15,
             "location": "inn", "id": "s15"}
    return Registry().register(NarrativeSystem()), world, scene


def test_entity_history_reserves_oldest_and_latest_before_recency(source_world):
    registry, world, scene = source_world
    before = copy.deepcopy(world)
    result = read_player_evidence(world, scene, QUERY)
    assert result["coverage"]["selection"] == "entity_match"
    assert result["coverage"]["own_record_count"] == 14
    assert len(result["records"]) == 4
    assert result["records"][0]["span"]["text"] == FIRST
    assert result["records"][-1]["turn"] == 14
    context = assemble_context(registry, world, scene, query=QUERY)
    assert FIRST in context
    assert "PRIVATE_NARRATION_REF" not in context and "PRIVATE_EFFECT_REF" not in context
    assert "bell.name = \"晨钟\"" in context
    assert world["systems"]["narrative"] == before["systems"]["narrative"]
    assert world["systems"]["ontology"].facts == before["systems"]["ontology"].facts


def test_visible_ids_aliases_and_literal_input_mentions_drive_matching(source_world):
    _, world, scene = source_world
    records = world["systems"]["narrative"]["player_inputs"]
    records[0]["entity_refs"] = []  # a source can have only literal mention evidence
    for query in (QUERY, "What did I name bell?"):
        result = read_player_evidence(world, scene, query)
        assert result["coverage"]["selection"] == "entity_match"
        assert result["records"][0]["span"]["text"] == FIRST
    # Merely sharing actor or location does not match all inputs as an entity.
    result = read_player_evidence(world, scene, "角色A")
    assert result["coverage"]["selection"] == "chronological_sample"
    # Hidden names may occur in a player's claim, but do not become resolved IDs.
    records.append(_record(15, "地下暗匣是否真的存在？", refs=("hidden",)))
    hidden = read_player_evidence(world, scene, "地下暗匣")
    assert hidden["coverage"]["selection"] == "content_match"
    assert all("hidden" not in row["entity_refs"] for row in hidden["records"])


def test_two_related_entities_each_keep_oldest_and_latest(source_world):
    _, world, scene = source_world
    ledger = world["systems"]["narrative"]["player_inputs"]
    ledger[2] = _record(3, "我给雨伞打了一个绳结。", refs=("umbrella",))
    ledger[11] = _record(12, "我解开雨伞上的绳结。", refs=("umbrella",))
    result = read_player_evidence(world, scene, "回忆铜铃和雨伞")
    assert [row["turn"] for row in result["records"]] == [1, 3, 12, 14]
    assert result["coverage"]["partial"]


def test_private_entity_attrs_cannot_create_a_secret_name_retrieval_oracle(source_world):
    _, world, scene = source_world
    graph = world["systems"]["ontology"]
    secret = "SECRET_NAME_42"
    graph.add_entity("guard", "Person", visibility="public", name=secret, aliases=[secret])
    graph.assert_fact("guard", "name", secret, day=1, turn=0,
                      source_event="secret-name", secrecy="secret")
    world["systems"]["narrative"]["player_inputs"] = [
        _record(1, "我问了那位客人一个问题。", refs=("guard",))]
    evidence = read_player_evidence(world, scene, secret)
    assert evidence == read_player_evidence(world, scene, "unrelated meteorology")
    assert evidence["coverage"]["matched_record_count"] == 0


class _SerializeWorld(ContextSystem):
    """A future generic system must not accidentally turn private logs public."""
    name = "serialize_world"

    def inject(self, scene, world):
        return Fragment(self.name, "scene", json.dumps(world, default=str, ensure_ascii=False))

    def recall(self, query, world):
        return [RecallHit(self.name, 1, json.dumps(world, default=str, ensure_ascii=False))]


@pytest.mark.parametrize("actor", ["B", None, "missing", "bell"])
def test_other_or_invalid_actor_has_no_source_canary_or_count_leak(source_world, actor):
    registry, world, scene = source_world
    ledger = world["systems"]["narrative"]["player_inputs"]
    ledger.append(_record(16, CANARY))
    ledger.append(_record(17, "乙在整理自己的行李。", actor="B", refs=()))
    other_scene = {**scene, "protagonist": actor}
    without_a = copy.deepcopy(world)
    without_a["systems"]["narrative"]["player_inputs"] = [row for row in ledger if row["actor_id"] != "A"]
    evidence = read_player_evidence(world, other_scene, "quiet")
    assert evidence == read_player_evidence(without_a, other_scene, "quiet")
    assert CANARY not in json.dumps(evidence, ensure_ascii=False)
    registry.register(_SerializeWorld())
    context = assemble_context(registry, world, other_scene, query="quiet")
    assert CANARY not in context and FIRST not in context
    view = pov_world(world, other_scene)
    assert "player_inputs" not in view["systems"]["narrative"]
    tools = build_tool_registry(registry, world, other_scene)
    assert CANARY not in tools.execute("recall_query", {"q": "quiet"})
    assert CANARY not in tools.execute("recall_query", {"q": "quiet", "pov": "A"})
    assert NarrativeSystem().recall("quiet", world) == []


def test_missing_graph_redacts_private_slice_and_does_not_mutate(source_world):
    _, world, scene = source_world
    world["systems"]["ontology"] = None
    before = copy.deepcopy(world)
    view = pov_world(world, scene)
    assert "player_inputs" not in view["systems"]["narrative"]
    assert read_player_evidence(world, scene, QUERY)["coverage"]["status"] == "unknown_no_original_evidence"
    view["systems"]["narrative"]["scenes"].clear()
    assert world == before


def test_unknown_legacy_and_nonmatching_partial_sample_never_synthesize(source_world):
    registry, world, scene = source_world
    sampled = read_player_evidence(world, scene, "unrelated meteorology")
    assert sampled["coverage"]["selection"] == "chronological_sample"
    assert sampled["coverage"]["partial"] and sampled["coverage"]["matched_record_count"] == 0
    assert [row["turn"] for row in sampled["records"]] == [1, 14]
    del world["systems"]["narrative"]["player_inputs"]
    world["systems"]["narrative"]["super_summary"] = "据说你给铜铃起名归雁。"
    evidence = read_player_evidence(world, scene, QUERY)
    assert evidence["records"] == []
    assert evidence["coverage"]["status"] == "unknown_no_original_evidence"
    assert "归雁" not in format_player_evidence(evidence)
    assert "unknown_no_original_evidence" in assemble_context(registry, world, scene, query=QUERY)


def test_wrong_premise_and_negated_name_remain_input_not_graph_fact(source_world):
    registry, world, scene = source_world
    wrong = "我早已把铜铃卖给B，他听见了我说暗号是雪鸦，不是寒鸦。"
    world["systems"]["narrative"]["player_inputs"] = [_record(1, wrong)]
    graph = world["systems"]["ontology"]
    before = copy.deepcopy(graph.__dict__)
    text = assemble_context(registry, world, scene, query="铜铃")
    assert wrong in text and "不是 canonical fact" in text
    assert "成功执行的证明" in text and "NPC 听见/知晓的证据" in text
    assert graph.__dict__ == before
    assert graph.neighbors("bell", "held_by", 15) == ["A"]
    assert not any(fact.predicate.startswith("knows:") for fact in graph.facts)


def test_long_exact_spans_keep_local_negation_and_original_unicode_offsets(source_world):
    _, world, scene = source_world
    for prefix in ("背景。" * 200, "ß" * 400 + "。", "İ" * 400 + "。"):
        original = prefix + FIRST + "接下来我查看天气。" * 60
        world["systems"]["narrative"]["player_inputs"] = [_record(1, original)]
        result = read_player_evidence(world, scene, "铜铃", {"max_chars_per_record": 100})
        span = result["records"][0]["span"]
        assert span["text"] == original[span["start"]:span["end"]]
        assert span["truncated"] and len(span["text"]) <= 100
        assert "叫作雨燕，不是归雁" in span["text"]
        assert "…" not in span["text"]
        assert result["coverage"]["truncated_record_count"] == 1
    graph = world["systems"]["ontology"]
    graph.assert_fact("bell", "alias", "Straße", day=15, turn=15,
                      source_event="folded-alias", secrecy="public")
    original = "前文。" * 150 + "STRASSE is corrected, not River." + "结束。" * 100
    world["systems"]["narrative"]["player_inputs"] = [_record(1, original)]
    result = read_player_evidence(world, scene, "Straße", {"max_chars_per_record": 100})
    span = result["records"][0]["span"]
    assert span["text"] == original[span["start"]:span["end"]]
    assert "STRASSE is corrected, not River." in span["text"]


class _CaptureProvider(FakeLLMProvider):
    def __init__(self):
        super().__init__(responses=["你观察片刻。"], json_responses=[{
            "narration": "你观察片刻。", "moves": [], "places": [], "cast": [],
            "facts": [], "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "原地"}],
        }])
        self.requests = []

    def complete_messages(self, messages, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        return super().complete_messages(messages, **kwargs)


def test_trimmed_author_fresh_compaction_and_hybrid_receive_same_source(source_world):
    registry, world, scene = source_world
    settings.set_conversation_mode("multiturn")
    strategy, provider = AuthorStrategy(), _CaptureProvider()
    # Simulate real cache commits. The first naming request is gone after 14 turns.
    strategy._thread = [{"role": "system", "content": "system"}]
    for row in world["systems"]["narrative"]["player_inputs"]:
        strategy._pending_action = row["input"]
        strategy._pending_user = row["input"]
        strategy.commit_to_thread("你检查了周围。")
    assert len(strategy._thread) == 17
    assert FIRST not in json.dumps(strategy._thread, ensure_ascii=False)
    expected = format_player_evidence(read_player_evidence(world, scene, QUERY))
    strategy.produce(registry, world, scene, QUERY, provider=provider)
    assert expected in provider.requests[-1][-1]["content"]
    strategy._compaction_due = True
    strategy.produce(registry, world, scene, QUERY, provider=provider)
    assert len(provider.requests[-1]) == 2
    assert expected in provider.requests[-1][-1]["content"]
    AuthorStrategy().produce(registry, world, scene, QUERY, provider=provider)
    assert expected in provider.requests[-1][-1]["content"]
    HybridStrategy().produce(registry, world, scene, QUERY, provider=provider)
    assert expected in provider.requests[-1][-1]["content"]
    assert expected in provider.calls[-2][1]  # Hybrid's free-prose call too.
