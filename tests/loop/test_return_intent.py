"""Offline extraction tests: no paid API, store writes or narrative evidence."""
import copy
import json

import pytest

from facts.graph import FactGraph
from loop.return_intent import ReturnIntentError, extract_return_intent, parse_deadline


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete_messages(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


@pytest.fixture
def state():
    graph = FactGraph()
    graph.add_entity("hero", "Person", "tracked")
    graph.add_entity("lin", "Person", "tracked", visibility="public")
    graph.add_entity("shop", "Place", "tracked")
    graph.add_entity("umbrella", "Object", "tracked")
    for eid, name in (("hero", "我"), ("lin", "阿林"), ("shop", "店铺"), ("umbrella", "伞")):
        graph.assert_fact(eid, "name", name, day=1, turn=1, source_event="seed", secrecy="public")
    world = {"meta": {"day": 4, "band": 2}, "systems": {"ontology": graph}, "_revision": 7}
    scene = {"protagonist": "hero", "present": ["lin"], "location": "shop", "day": 4}
    return world, scene


def ready(action="我答应明天中午把伞还给阿林", expression="明天中午", **changes):
    return {"status": "ready", "item": "umbrella", "recipient": "lin",
            "due_expression": expression, "evidence_quotes": [action], **changes}


def snapshot(world):
    graph = world["systems"]["ontology"]
    return copy.deepcopy((world["meta"], world["_revision"], graph.__dict__))


def test_ready_preserves_actual_turn_and_host_clock(state):
    world, scene = state
    action = "  我答应明天中午把伞还给阿林。\n"
    before = snapshot(world)
    provider = Provider(ready(action))
    result = extract_return_intent(world, scene, action, provider)
    assert result == {
        "status": "ready", "player_input": action,
        "pending": {"player_actions": [action], "_revision": 7, "actor": "hero", "day": 4, "band": 2},
        "promise": {"item": "umbrella", "recipient": "lin", "due": {"day": 5, "band": 1},
                    "evidence": {"player_actions": [action], "quotes": [action]}},
    }
    assert "debtor" not in result["promise"]  # host scene actor supplies it
    assert snapshot(world) == before
    assert len(provider.calls) == 1


@pytest.mark.parametrize("expression,day,band,expected", [
    ("今天下午", 4, 2, {"day": 4, "band": 2}),
    ("今天晚上", 4, 2, {"day": 4, "band": 3}),
    ("明天早上", 4, 2, {"day": 5, "band": 0}),
    ("明天中午前", 4, 2, {"day": 5, "band": 1}),
    ("后天下午", 4, 2, {"day": 6, "band": 2}),
    ("第7天夜晚", 4, 2, {"day": 7, "band": 3}),
    ("第 7 天中午之前", 4, 2, {"day": 7, "band": 1}),
    ("by tomorrow at noon", 4, 2, {"day": 5, "band": 1}),
    ("the day after tomorrow in the morning", 4, 2, {"day": 6, "band": 0}),
    ("day 7 at night", 4, 2, {"day": 7, "band": 3}),
])
def test_parse_deadline_clear_forms(expression, day, band, expected):
    assert parse_deadline(expression, day, band) == expected


@pytest.mark.parametrize("expression", [
    "明天", "过几天", "过几天中午", "下星期中午", "第7天", "第0天中午",
    "明天中午或后天下午", "明天大概中午", "中午", "今天早晨", "第3天下午",
    "三天后", "later", "tomorrow", "next week at noon", "第5天中午，然后第6天夜晚",
])
def test_parse_deadline_rejects_ambiguous_missing_or_past(expression):
    assert parse_deadline(expression, 4, 2) is None


@pytest.mark.parametrize("day,band", [(True, 0), (0, 0), (1, True), (1, 4), (1, -1)])
def test_parse_deadline_rejects_invalid_clock(day, band):
    assert parse_deadline("明天中午", day, band) is None


@pytest.mark.parametrize("action", [
    "我不答应明天中午把伞还给阿林",
    "如果雨停，我就明天中午把伞还给阿林",
    "阿林说：‘我明天中午把伞还给你’",
    "假设我答应明天中午还伞，会怎样？",
    "借我一把伞", "我看看伞",
])
def test_none_classification_and_exclusion_examples_are_in_prompt(state, action):
    world, scene = state
    provider = Provider({"status": "none"})
    assert extract_return_intent(world, scene, action, provider) == {
        "status": "none", "pending": None, "player_input": action,
    }
    prompt = provider.calls[0][0]["content"]
    assert action in prompt
    for example in ("条件", "否定", "quoted-other-person", "hypothetical", "none", "clarify", "ready"):
        assert example in prompt


def test_model_clarification_preserves_sources_without_mutation(state):
    world, scene = state
    before = snapshot(world)
    action = "我答应过几天把伞还给阿林"
    result = extract_return_intent(world, scene, action,
                                   Provider({"status": "clarify", "question": "具体哪天哪个时段？"}))
    assert result["status"] == "clarify"
    assert result["question"] == "具体哪天哪个时段？"
    assert result["pending"]["player_actions"] == [action]
    assert "promise" not in result
    assert snapshot(world) == before


def test_vague_ready_date_clarifies_then_reply_resolves(state):
    world, scene = state
    original = "我答应过几天把伞还给阿林"
    first = extract_return_intent(world, scene, original, Provider(ready(original, "过几天")))
    assert first["status"] == "clarify"
    assert "日期和时段" in first["question"]
    before_pending = copy.deepcopy(first["pending"])
    reply = "明天中午"
    provider = Provider(ready(original, reply, evidence_quotes=[original, reply]))
    second = extract_return_intent(world, scene, reply, provider, pending=first["pending"])
    assert second["status"] == "ready"
    assert second["promise"]["due"] == {"day": 5, "band": 1}
    assert second["promise"]["evidence"] == {"player_actions": [original, reply], "quotes": [original, reply]}
    assert second["player_input"] == original + "\n" + reply
    assert second["pending"]["player_actions"] == [original, reply]
    assert first["pending"] == before_pending
    prompt = json.loads(provider.calls[0][1]["content"])
    assert prompt["player_actions"] == [original, reply]
    assert prompt["has_pending"] is True
    assert first["question"] not in prompt["player_actions"]


def test_pending_none_cancels_only_unrecorded_proposal(state):
    world, scene = state
    first = extract_return_intent(world, scene, "我明天还伞", Provider({"status": "clarify", "question": "给谁？"}))
    world["systems"]["existing_obligations"] = {"registered": {"status": "open"}}
    result = extract_return_intent(world, scene, "算了，我看看天气", Provider({"status": "none"}), first["pending"])
    assert result == {"status": "none", "pending": None, "player_input": "算了，我看看天气"}
    assert world["systems"]["existing_obligations"] == {"registered": {"status": "open"}}


@pytest.mark.parametrize("change", ["revision", "actor", "day", "band", "removed_actor", "invalid_pending"])
def test_stale_pending_clears_and_demands_full_restatement_without_provider(state, change):
    world, scene = state
    first = extract_return_intent(world, scene, "我明天还伞", Provider({"status": "clarify", "question": "给谁？"}))
    if change == "revision":
        world["_revision"] += 1
    elif change == "actor":
        scene["protagonist"] = "lin"
    elif change == "removed_actor":
        del world["systems"]["ontology"].entities["hero"]
    elif change in {"day", "band"}:
        world["meta"][change] += 1
    else:
        first["pending"] = {"player_actions": ["伪造"]}
    provider = Provider(ready())
    result = extract_return_intent(world, scene, "明天中午", provider, pending=first["pending"])
    assert result["status"] == "clarify" and result["pending"] is None
    assert "重新完整说明" in result["question"]
    assert "promise" not in result
    assert provider.calls == []


@pytest.mark.parametrize("changes", [
    {"item": "伞"}, {"item": "new_umbrella"}, {"item": "lin"},
    {"recipient": "阿林"}, {"recipient": "hero"}, {"recipient": "umbrella"}, {"recipient": "unknown"},
])
def test_ready_refs_must_be_existing_visible_correct_types(state, changes):
    world, scene = state
    result = extract_return_intent(world, scene, "我答应明天中午把伞还给阿林", Provider(ready(**changes)))
    assert result["status"] == "clarify"
    assert "promise" not in result


def test_place_is_valid_recipient(state):
    world, scene = state
    action = "我答应明天中午把伞还到店铺"
    result = extract_return_intent(world, scene, action, Provider(ready(action, recipient="shop")))
    assert result["promise"]["recipient"] == "shop"


@pytest.mark.parametrize("change", ["missing_graph", "missing_actor", "non_person_actor"])
def test_ineligible_world_does_not_call_provider(state, change):
    world, scene = state
    if change == "missing_graph":
        world["systems"] = {}
    elif change == "missing_actor":
        scene.pop("protagonist")
    elif change == "non_person_actor":
        scene["protagonist"] = "shop"
    provider = Provider(ready())
    result = extract_return_intent(world, scene, "我答应明天中午把伞还给阿林", provider)
    assert result["status"] == "none"
    assert provider.calls == []


@pytest.mark.parametrize("item_state", ["absent", "hidden"])
@pytest.mark.parametrize("classification", ["none", "clarify", "ready"])
def test_empty_visible_items_still_classifies_and_clarifies_unresolved_promise(state, item_state, classification):
    world, scene = state
    graph = world["systems"]["ontology"]
    del graph.entities["umbrella"]
    if item_state == "hidden":
        graph.add_entity("SECRET_ITEM_CANARY", "Object", visibility="hidden")
    action = "我看看天气" if classification == "none" else "我答应明天中午把伞还给阿林"
    if classification == "ready":
        response = ready(action, item="SECRET_ITEM_CANARY" if item_state == "hidden" else "umbrella")
    elif classification == "clarify":
        response = {"status": "clarify", "question": "请明确要归还哪件当前可见的物品。"}
    else:
        response = {"status": "none"}
    provider = Provider(response)
    before = snapshot(world)
    result = extract_return_intent(world, scene, action, provider)
    assert len(provider.calls) == 1
    assert result["status"] == ("none" if classification == "none" else "clarify")
    if classification != "none":
        assert result["question"]
        assert result["pending"]["player_actions"] == [action]
        assert "promise" not in result
    else:
        assert result["player_input"] == action
    payload = json.loads(provider.calls[0][1]["content"])
    assert not any(candidate["etype"] == "Object" for candidate in payload["candidates"])
    assert "SECRET_ITEM_CANARY" not in json.dumps(provider.calls, ensure_ascii=False)
    assert "SECRET_ITEM_CANARY" not in json.dumps(result, ensure_ascii=False)
    assert snapshot(world) == before


def test_prompt_contains_only_pov_candidates_and_actual_player_text(state):
    world, scene = state
    graph = world["systems"]["ontology"]
    graph.add_entity("SECRET_OBJECT_CANARY", "Object", visibility="hidden")
    graph.add_entity("SECRET_PERSON_CANARY", "Person", visibility="hidden")
    graph.entities["lin"].attrs["private_note"] = "SECRET_ATTR_CANARY"
    graph.assert_fact("lin", "name", "SECRET_REAL_NAME_CANARY", day=4, turn=2,
                      source_event="private", secrecy="secret")
    graph.assert_fact("lin", "knows:lin.name", "SECRET_NPC_KNOWLEDGE_CANARY", day=4, turn=2,
                      source_event="private", secrecy="secret")
    world["meta"]["timeline"] = [{"summary": "SECRET_NARRATOR_CANARY"}]
    world["systems"]["plot"] = {"secret": "SECRET_SYSTEM_CANARY"}
    scene["summary"] = "SECRET_SCENE_CANARY"
    before = snapshot(world)
    provider = Provider({"status": "none"})
    extract_return_intent(world, scene, "我看看伞", provider)
    serialized = json.dumps(provider.calls, ensure_ascii=False)
    assert "SECRET_" not in serialized
    payload = json.loads(provider.calls[0][1]["content"])
    assert {candidate["id"] for candidate in payload["candidates"]} == {"lin", "shop", "umbrella"}
    assert snapshot(world) == before


def test_hidden_reference_cannot_become_ready_even_when_guessed(state):
    world, scene = state
    world["systems"]["ontology"].add_entity("secret_lender", "Person", visibility="hidden")
    result = extract_return_intent(world, scene, "我答应明天中午把伞还给 secret_lender",
                                   Provider(ready("我答应明天中午把伞还给 secret_lender", recipient="secret_lender")))
    assert result["status"] == "clarify"
    assert "secret_lender" not in result["question"]


@pytest.mark.parametrize("response", [
    "not json", {}, {"status": "promise"}, {"status": "clarify"},
    {"status": "none", "item": "umbrella"},
    ready(evidence_quotes=["伪造的承诺原话"]), ready(evidence_quotes=[]),
    ready(evidence_quotes=[" "]), ready(evidence_quotes=[4]),
    ready(due_expression="第5天中午"), ready(due={"day": 99, "band": 1}),
    {"status": "ready", "item": "umbrella", "recipient": "lin", "due": {"day": 5, "band": 1}},
])
def test_malformed_or_forged_classifications_fail_closed_with_one_repair(state, response):
    world, scene = state
    before = snapshot(world)
    provider = Provider(response)
    with pytest.raises(ValueError, match="could not be resolved"):
        extract_return_intent(world, scene, "我答应明天中午把伞还给阿林", provider)
    assert len(provider.calls) == 2
    assert snapshot(world) == before


def test_repair_output_never_becomes_a_new_source_of_evidence(state):
    world, scene = state
    invented = "我答应明天中午把伞还给阿林"
    provider = Provider({"status": "clarify", "question": invented, "invalid": True}, ready(invented))
    with pytest.raises(ValueError):
        extract_return_intent(world, scene, "我看看伞", provider)
    assert len(provider.calls) == 2


def test_provider_failure_never_falls_back_to_none(state):
    world, scene = state
    provider = Provider(RuntimeError("offline"))
    with pytest.raises(ValueError, match="could not be resolved"):
        extract_return_intent(world, scene, "我答应明天中午把伞还给阿林", provider)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("response", [
    RuntimeError("offline"), "not json",
    ready(evidence_quotes=["MODEL_OUTPUT_CANARY"]),
])
def test_classifier_failure_retains_current_real_reply_in_copied_pending(state, response):
    world, scene = state
    original, reply = "我答应明天还伞给阿林", "中午"
    first = extract_return_intent(world, scene, original,
        Provider({"status": "clarify", "question": "明天哪个时段？"}))
    before_world, before_pending = snapshot(world), copy.deepcopy(first["pending"])
    with pytest.raises(ReturnIntentError) as raised:
        extract_return_intent(world, scene, reply, Provider(response), first["pending"])
    assert isinstance(raised.value, ValueError)
    assert raised.value.pending == {
        "player_actions": [original, reply], "_revision": 7, "actor": "hero", "day": 4, "band": 2,
    }
    assert "MODEL_OUTPUT_CANARY" not in json.dumps(raised.value.pending, ensure_ascii=False)
    assert first["pending"] == before_pending
    assert snapshot(world) == before_world
    raised.value.pending["player_actions"].append("local retry-context edit")
    assert first["pending"] == before_pending


def test_initial_classification_failure_also_retains_only_actual_input(state):
    world, scene = state
    action = "我答应明天中午把伞还给阿林"
    with pytest.raises(ReturnIntentError) as raised:
        extract_return_intent(world, scene, action, Provider(RuntimeError("offline")))
    assert raised.value.pending["player_actions"] == [action]
    assert set(raised.value.pending) == {"player_actions", "_revision", "actor", "day", "band"}


def test_one_repair_can_recover_schema_without_changing_player_sources(state):
    world, scene = state
    provider = Provider({"status": "invalid"}, ready())
    result = extract_return_intent(world, scene, "我答应明天中午把伞还给阿林", provider)
    assert result["status"] == "ready"
    assert len(provider.calls) == 2
    assert result["promise"]["evidence"]["player_actions"] == ["我答应明天中午把伞还给阿林"]


def test_world_clock_wins_over_stale_scene_day(state):
    world, scene = state
    scene["day"] = 1
    result = extract_return_intent(world, scene, "我答应明天中午把伞还给阿林", Provider(ready()))
    assert result["promise"]["due"] == {"day": 5, "band": 1}


def test_band_omission_never_defaults_due_to_noon(state):
    world, scene = state
    action = "我答应明天把伞还给阿林"
    result = extract_return_intent(world, scene, action, Provider(ready(action, "明天")))
    assert result["status"] == "clarify"
    assert "promise" not in result


def ready_parts(actions, date="明天", date_index=0, band="中午", band_index=1):
    return {"status": "ready", "item": "umbrella", "recipient": "lin",
            "due_parts": {"date": {"text": date, "action_index": date_index},
                          "band": {"text": band, "action_index": band_index}},
            "evidence_quotes": actions}


def test_date_original_band_reply_resolves_without_fabricating_combined_quote(state):
    world, scene = state
    original, reply = "我答应明天还伞给阿林", "中午"
    before = snapshot(world)
    first = extract_return_intent(world, scene, original,
                                   Provider({"status": "clarify", "question": "明天哪个时段？"}))
    provider = Provider(ready_parts([original, reply]))
    result = extract_return_intent(world, scene, reply, provider, first["pending"])
    assert result["status"] == "ready"
    assert result["promise"]["due"] == {"day": 5, "band": 1}
    assert result["promise"]["evidence"] == {"player_actions": [original, reply], "quotes": [original, reply]}
    assert result["player_input"] == original + "\n" + reply
    assert result["pending"]["player_actions"] == [original, reply]
    assert "明天中午" not in json.dumps(result["promise"]["evidence"], ensure_ascii=False)
    assert snapshot(world) == before
    prompt = provider.calls[0][0]["content"]
    assert "due_parts" in prompt and "action_index" in prompt
    assert "优先使用最近明确选定的原话" in prompt


def test_band_original_date_reply_uses_original_clock(state):
    world, scene = state
    original, reply = "我答应中午还伞给阿林", "明天"
    first = extract_return_intent(world, scene, original,
                                   Provider({"status": "clarify", "question": "哪天中午？"}))
    result = extract_return_intent(world, scene, reply,
        Provider(ready_parts([original, reply], date_index=1, band_index=0)), first["pending"])
    assert result["promise"]["due"] == {"day": 5, "band": 1}
    assert result["promise"]["evidence"]["player_actions"] == [original, reply]


def test_recipient_only_reply_keeps_full_expression_compatibility(state):
    world, scene = state
    original, reply = "我答应明天中午还伞", "给阿林"
    first = extract_return_intent(world, scene, original,
                                   Provider({"status": "clarify", "question": "还给谁？"}))
    result = extract_return_intent(world, scene, reply,
        Provider(ready(original, evidence_quotes=[original, reply])), first["pending"])
    assert result["promise"]["recipient"] == "lin"
    assert result["promise"]["due"] == {"day": 5, "band": 1}
    assert result["promise"]["evidence"]["quotes"] == [original, reply]


def test_recipient_reply_after_band_reply_preserves_all_three_sources(state):
    world, scene = state
    actions = ["我答应明天还伞", "中午", "给阿林"]
    first = extract_return_intent(world, scene, actions[0],
        Provider({"status": "clarify", "question": "明天哪个时段，还给谁？"}))
    second = extract_return_intent(world, scene, actions[1],
        Provider({"status": "clarify", "question": "还给谁？"}), first["pending"])
    result = extract_return_intent(world, scene, actions[2], Provider(ready_parts(actions)), second["pending"])
    assert result["promise"]["due"] == {"day": 5, "band": 1}
    assert result["promise"]["recipient"] == "lin"
    assert result["promise"]["evidence"] == {"player_actions": actions, "quotes": actions}
    assert result["player_input"] == "\n".join(actions)


def test_latest_explicit_date_correction_is_resolved_from_its_real_source(state):
    world, scene = state
    original, reply = "我答应明天还伞给阿林", "改成后天中午"
    first = extract_return_intent(world, scene, original,
                                   Provider({"status": "clarify", "question": "哪个时段？"}))
    result = extract_return_intent(world, scene, reply,
        Provider(ready_parts([original, reply], date="后天", date_index=1)), first["pending"])
    assert result["promise"]["due"] == {"day": 6, "band": 1}
    assert result["promise"]["evidence"]["player_actions"] == [original, reply]


@pytest.mark.parametrize("part,field,value", [
    ("date", "text", "后天"), ("band", "text", "下午"),
    ("date", "action_index", 1), ("band", "action_index", 0),
    ("date", "action_index", -1), ("band", "action_index", 2),
    ("date", "action_index", True), ("band", "action_index", "1"),
    ("date", "text", 5), ("band", "text", ""),
])
def test_forged_or_wrong_index_parts_fail_closed_with_one_repair(state, part, field, value):
    world, scene = state
    original, reply = "我答应明天还伞给阿林", "中午"
    first = extract_return_intent(world, scene, original,
                                   Provider({"status": "clarify", "question": "哪个时段？"}))
    malformed = ready_parts([original, reply])
    malformed["due_parts"][part][field] = value
    provider = Provider(malformed)
    before = snapshot(world)
    with pytest.raises(ValueError, match="could not be resolved"):
        extract_return_intent(world, scene, reply, provider, first["pending"])
    assert len(provider.calls) == 2
    assert snapshot(world) == before


@pytest.mark.parametrize("date,band", [
    ("过几天", "中午"), ("明天或后天", "中午"), ("明天", "中午或下午"),
    ("明天", "晚一点"), ("第5天", "不确定时段"), ("今天", "中午"),
])
def test_vague_ambiguous_conflicting_or_past_parts_clarify(state, date, band):
    world, scene = state
    original = f"我答应{date}还伞给阿林"
    first = extract_return_intent(world, scene, original,
                                   Provider({"status": "clarify", "question": "具体日期和时段？"}))
    result = extract_return_intent(world, scene, band,
        Provider(ready_parts([original, band], date=date, band=band)), first["pending"])
    assert result["status"] == "clarify"
    assert "promise" not in result
    assert result["pending"]["player_actions"] == [original, band]


@pytest.mark.parametrize("mutation", ["both_sources", "absolute_due", "extra_numeric_date", "missing_band", "list_parts"])
def test_parts_schema_rejects_conflicting_or_model_numeric_authority(state, mutation):
    world, scene = state
    action = "我答应明天中午还伞给阿林"
    response = ready_parts([action], band_index=0)
    if mutation == "both_sources":
        response["due_expression"] = "明天中午"
    elif mutation == "absolute_due":
        response["due"] = {"day": 8, "band": 3}
    elif mutation == "extra_numeric_date":
        response["due_parts"]["date"]["day"] = 8
    elif mutation == "missing_band":
        del response["due_parts"]["band"]
    else:
        response["due_parts"] = ["明天", "中午"]
    provider = Provider(response)
    with pytest.raises(ValueError, match="could not be resolved"):
        extract_return_intent(world, scene, action, provider)
    assert len(provider.calls) == 2
