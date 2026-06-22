"""Tests for llm.structured.complete_structured — the harness validate→repair loop."""
from __future__ import annotations

import json

from llm.provider import FakeLLMProvider
from llm.structured import complete_structured, build_structured_repair


def _need_foo(obj):
    """Validator: require a non-empty string field 'foo'."""
    if not isinstance(obj, dict) or not isinstance(obj.get("foo"), str) or not obj["foo"].strip():
        return ['missing or empty string field "foo"']
    return []


def test_conforms_first_round_single_call():
    fake = FakeLLMProvider(json_responses=[{"foo": "ok"}])
    obj, errors = complete_structured(fake, system="s", user="u", validate=_need_foo)
    assert errors == []
    assert obj == {"foo": "ok"}
    assert len(fake.calls) == 1  # no repair


def test_bad_then_good_repairs():
    fake = FakeLLMProvider(json_responses=[{"bar": "x"}, {"foo": "fixed"}])
    obj, errors = complete_structured(fake, system="s", user="u", validate=_need_foo)
    assert errors == []
    assert obj == {"foo": "fixed"}
    assert len(fake.calls) == 2  # one repair round


def test_repair_message_names_the_field():
    fake = FakeLLMProvider(json_responses=[{"bar": "x"}, {"foo": "fixed"}])
    complete_structured(fake, system="s", user="u", validate=_need_foo)
    repair = fake.calls[1][1]  # 2nd call's user turn
    assert '"foo"' in repair


def test_never_conforms_returns_errors_after_repairs():
    fake = FakeLLMProvider(json_responses=[{"bar": "x"}])  # cycles the same bad response
    obj, errors = complete_structured(fake, system="s", user="u",
                                      validate=_need_foo, max_repairs=2)
    assert errors  # non-empty → did not conform
    assert len(fake.calls) == 3  # initial + 2 repairs


def test_provider_none():
    obj, errors = complete_structured(None, system="s", user="u", validate=_need_foo)
    assert obj is None
    assert errors == ["no provider"]


def test_provider_raises_does_not_propagate():
    class _Raiser:
        def complete_messages(self, messages, **kw):
            raise RuntimeError("boom")
    obj, errors = complete_structured(_Raiser(), system="s", user="u", validate=_need_foo)
    assert errors  # returned, not raised


def test_non_json_response_is_an_error():
    fake = FakeLLMProvider(responses=["this is prose, not json"])  # complete_messages → text
    obj, errors = complete_structured(fake, system="s", user="u",
                                      validate=_need_foo, max_repairs=0)
    assert errors  # malformed
    assert len(fake.calls) == 1


def test_validate_crash_treated_as_malformed():
    def _boom(obj):
        raise ValueError("validator bug")
    fake = FakeLLMProvider(json_responses=[{"foo": "ok"}])
    obj, errors = complete_structured(fake, system="s", user="u",
                                      validate=_boom, max_repairs=0)
    assert errors  # crash → treated as not-conformed, never propagates


def test_max_repairs_zero_is_single_call():
    fake = FakeLLMProvider(json_responses=[{"bar": "x"}])
    obj, errors = complete_structured(fake, system="s", user="u",
                                      validate=_need_foo, max_repairs=0)
    assert errors
    assert len(fake.calls) == 1


def test_build_structured_repair_lists_errors_and_reminder():
    msg = build_structured_repair(['missing "a"', 'missing "b"'], schema_reminder="keys: a, b")
    assert 'missing "a"' in msg and 'missing "b"' in msg
    assert "keys: a, b" in msg
