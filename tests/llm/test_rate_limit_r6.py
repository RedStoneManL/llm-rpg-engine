"""#R6 — honor Retry-After on 429 + a runtime-settable max-tool-rounds knob."""
import urllib.error
import pytest

import llm.provider as P
from engine import settings


def test_retry_after_seconds_parses_int():
    e = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "7"}, None)
    assert P._retry_after_seconds(e) == 7


def test_retry_after_seconds_absent_or_date_is_none():
    assert P._retry_after_seconds(
        urllib.error.HTTPError("u", 429, "x", {}, None)) is None
    # HTTP-date form is not supported -> None (fall back to exponential backoff)
    assert P._retry_after_seconds(
        urllib.error.HTTPError("u", 429, "x", {"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}, None)
    ) is None


def test_do_post_honors_retry_after(monkeypatch):
    waits = []
    monkeypatch.setattr(P.time, "sleep", lambda s: waits.append(s))
    calls = {"n": 0}

    def fake_post(url, headers, data, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "5"}, None)
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post_json", fake_post)
    out = P._do_post("u", {}, {"model": "m", "messages": []})
    assert out == {"ok": True}
    assert waits == [5]          # honored Retry-After (5), not the 2**0=1 backoff


def test_do_post_backoff_when_no_retry_after(monkeypatch):
    waits = []
    monkeypatch.setattr(P.time, "sleep", lambda s: waits.append(s))
    calls = {"n": 0}

    def fake_post(url, headers, data, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise urllib.error.HTTPError("u", 503, "busy", {}, None)
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post_json", fake_post)
    out = P._do_post("u", {}, {"model": "m", "messages": []})
    assert out == {"ok": True}
    assert waits == [1, 2]       # exponential backoff (2**0, 2**1) when no header


def test_set_max_tool_rounds():
    assert settings.set_max_tool_rounds(4) is True
    assert settings.get_max_tool_rounds() == 4
    assert settings.set_max_tool_rounds("nope") is False
    assert settings.set_max_tool_rounds(-1) is False
