"""The test provider must fail closed even when application code catches errors."""

import json
from copy import deepcopy

import pytest

from llm.provider import LLMProvider
from tests.scripted_provider import StrictScriptedProvider


def _route(phase):
    return lambda request: request["messages"][0]["content"] == phase


def _provider(responses):
    return StrictScriptedProvider({"author": _route("author")},
                                  {"author": responses})


def test_provider_implements_interface_without_tool_support():
    provider = _provider([])
    assert isinstance(provider, LLMProvider)
    assert provider.supports_tools() is False
    assert provider.calls == []
    assert provider.errors == ()
    provider.assert_consumed()


@pytest.mark.parametrize("response", [
    "  prose\n原文\n", "", "{deliberately malformed JSON}",
    {"changed": False, "name": "原文"}, [1, "two"], None, False, 3,
])
def test_returns_only_explicit_text_or_json_response(response):
    provider = _provider([response])
    result = provider.complete("author", "input")
    if isinstance(response, str):
        assert result == response
    else:
        assert result == json.dumps(response, ensure_ascii=False)
        assert json.loads(result) == response
    provider.assert_consumed()


def test_phase_queues_are_independent_and_preserve_order():
    provider = StrictScriptedProvider(
        {"author": _route("author"), "check": _route("check")},
        {"author": ["first", "second"], "check": [{"pass": True}]})
    assert provider.complete("author", "one") == "first"
    assert provider.consumed == {"author": 1, "check": 0}
    assert provider.remaining == {"author": 1, "check": 1}
    assert json.loads(provider.complete("check", "one")) == {"pass": True}
    assert provider.complete("author", "two") == "second"
    assert [call["phase"] for call in provider.calls] == ["author", "check", "author"]
    assert provider.consumed == {"author": 2, "check": 1}
    assert provider.remaining == {"author": 0, "check": 0}
    provider.assert_consumed()


def test_complete_forwards_system_user_and_exact_supplied_options():
    provider = _provider(["ok"])
    assert provider.complete("author", "user", model="test", max_tokens=17,
                             temperature=0, stop=["END"]) == "ok"
    assert provider.calls == [{
        "messages": [{"role": "system", "content": "author"},
                     {"role": "user", "content": "user"}],
        "options": {"model": "test", "max_tokens": 17,
                    "temperature": 0, "stop": ["END"]},
        "phase": "author",
    }]
    provider.assert_consumed()


def test_callbacks_and_call_record_are_isolated_deep_snapshots():
    messages = [{"role": "system", "content": "author"},
                {"role": "assistant", "content": {"nested": ["history"]}},
                {"role": "user", "content": "repair"}]
    options = {"model": "test", "metadata": {"tags": ["original"]}}
    original = {"messages": deepcopy(messages), "options": deepcopy(options)}
    seen = []

    def mutate_route(request):
        assert request == original
        request["messages"][1]["content"]["nested"].append("route mutation")
        request["options"]["metadata"]["tags"].append("route mutation")
        return False

    def matching_route(request):
        assert request == original
        seen.append(request)
        request["messages"].clear()
        return True

    def response(request):
        assert request == original
        seen.append(request)
        request["messages"][1]["content"]["nested"].append("response mutation")
        request["options"]["metadata"]["tags"].append("response mutation")
        return {"reply": request["messages"][-1]["content"]}

    provider = StrictScriptedProvider(
        {"other": mutate_route, "author": matching_route},
        {"other": [], "author": [response]})
    assert json.loads(provider.complete_messages(messages, **options)) == {"reply": "repair"}
    assert {"messages": messages, "options": options} == original
    assert provider.calls == [{**original, "phase": "author"}]
    messages[1]["content"]["nested"].append("later caller mutation")
    options["metadata"]["tags"].append("later caller mutation")
    seen[1]["options"].clear()
    assert provider.calls == [{**original, "phase": "author"}]
    provider.assert_consumed()


def test_constructor_snapshots_routes_and_script_values():
    routes = {"author": _route("author")}
    value = {"nested": ["original"]}
    scripts = {"author": [value]}
    provider = StrictScriptedProvider(routes, scripts)
    routes.clear()
    value["nested"].append("external mutation")
    scripts["author"].append("unexpected extra response")
    scripts.clear()
    assert json.loads(provider.complete("author", "input")) == {"nested": ["original"]}
    provider.assert_consumed()


def test_count_snapshots_cannot_change_consumption():
    provider = _provider(["ok"])
    provider.consumed["author"] = 100
    provider.remaining["author"] = 0
    assert provider.consumed == {"author": 0}
    assert provider.remaining == {"author": 1}
    provider.complete("author", "input")
    provider.assert_consumed()


@pytest.mark.parametrize("routes,scripts", [
    ({"author": _route("author")}, {}),
    ({}, {"author": []}),
    ({"author": _route("author")}, {"check": []}),
])
def test_constructor_rejects_missing_or_unreachable_phase_scripts(routes, scripts):
    with pytest.raises(AssertionError, match="Route/script phases differ"):
        StrictScriptedProvider(routes, scripts)


def test_unknown_request_does_not_consume_and_cannot_be_hidden_by_later_success():
    provider = _provider(["ok"])
    with pytest.raises(AssertionError, match="no route matched"):
        provider.complete("unplanned semantic check", "input")
    assert provider.calls[0]["phase"] is None
    assert provider.consumed == {"author": 0}
    assert provider.complete("author", "input") == "ok"
    assert provider.remaining == {"author": 0}
    with pytest.raises(AssertionError, match="no route matched"):
        provider.assert_consumed()
    assert len(provider.errors) == 1


def test_ambiguous_request_never_chooses_first_matching_route():
    provider = StrictScriptedProvider(
        {"author": lambda request: True, "check": lambda request: True},
        {"author": ["author"], "check": ["check"]})
    with pytest.raises(AssertionError, match="multiple routes matched.*author.*check"):
        provider.complete("shared prompt", "input")
    assert provider.calls[0]["phase"] is None
    assert provider.consumed == {"author": 0, "check": 0}
    with pytest.raises(AssertionError, match="multiple routes matched"):
        provider.assert_consumed()


def test_exhausted_queue_never_cycles_and_remains_a_failure_when_swallowed():
    provider = _provider(["only response"])
    provider.complete("author", "first")
    with pytest.raises(AssertionError, match="phase 'author' exhausted after 1"):
        provider.complete("author", "unexpected repair")
    assert provider.consumed == {"author": 1}
    assert provider.remaining == {"author": 0}
    assert [call["phase"] for call in provider.calls] == ["author", "author"]
    for _ in range(2):
        with pytest.raises(AssertionError, match="exhausted"):
            provider.assert_consumed()


def test_empty_phase_means_no_calls_allowed():
    provider = _provider([])
    with pytest.raises(AssertionError, match="exhausted after 0"):
        provider.complete("author", "input")
    with pytest.raises(AssertionError, match="exhausted"):
        provider.assert_consumed()


def test_no_routes_means_no_calls_allowed():
    provider = StrictScriptedProvider({}, {})
    provider.assert_consumed()
    with pytest.raises(AssertionError, match="no route matched"):
        provider.complete("any", "input")
    with pytest.raises(AssertionError, match="no route matched"):
        provider.assert_consumed()


def test_unconsumed_error_reports_each_phase_with_remaining_and_consumed_counts():
    provider = StrictScriptedProvider(
        {"author": _route("author"), "check": _route("check")},
        {"author": ["first", "second"], "check": ["one", "two"]})
    provider.complete("author", "input")
    with pytest.raises(AssertionError) as caught:
        provider.assert_consumed()
    assert "Phase 'author': 1 unconsumed response(s), 1 consumed" in str(caught.value)
    assert "Phase 'check': 2 unconsumed response(s), 0 consumed" in str(caught.value)


@pytest.mark.parametrize("exception", [AssertionError("wrong prompt"), ValueError("bad route")])
def test_predicate_failure_is_persistent_without_consuming_response(exception):
    def broken_route(request):
        raise exception

    provider = StrictScriptedProvider({"author": broken_route}, {"author": ["ok"]})
    with pytest.raises(AssertionError, match="route 'author' raised"):
        provider.complete("author", "input")
    assert provider.consumed == {"author": 0}
    with pytest.raises(AssertionError, match=str(exception)):
        provider.assert_consumed()


def test_response_callback_failure_is_persistent_and_does_not_retry():
    calls = []

    def broken_response(request):
        calls.append(request)
        raise AssertionError("unexpected repair context")

    provider = _provider([broken_response])
    with pytest.raises(AssertionError, match="response for phase 'author' raised"):
        provider.complete("author", "input")
    assert len(calls) == 1
    assert provider.consumed == {"author": 1}
    assert provider.remaining == {"author": 0}
    with pytest.raises(AssertionError, match="unexpected repair context"):
        provider.assert_consumed()


def test_unserializable_response_is_a_persistent_protocol_error():
    provider = _provider([{1, 2}])
    with pytest.raises(AssertionError, match="TypeError.*not JSON serializable"):
        provider.complete("author", "input")
    with pytest.raises(AssertionError, match="not JSON serializable"):
        provider.assert_consumed()


def test_complete_json_uses_one_response_and_normal_provider_parser():
    provider = _provider(['```json\n{"accepted": true,}\n```'])
    assert provider.complete_json("author", "input", {}, model="test") == {"accepted": True}
    assert len(provider.calls) == 1
    assert provider.calls[0]["options"] == {"model": "test"}
    provider.assert_consumed()


def test_complete_json_does_not_implicitly_retry_intentionally_bad_json():
    provider = _provider(["malformed", {"accepted": True}])
    with pytest.raises(ValueError, match="not a JSON object"):
        provider.complete_json("author", "input", {})
    assert len(provider.calls) == 1
    assert provider.remaining == {"author": 1}
    assert provider.errors == ()
    assert provider.complete_json("author", "explicit retry", {}) == {"accepted": True}
    provider.assert_consumed()
