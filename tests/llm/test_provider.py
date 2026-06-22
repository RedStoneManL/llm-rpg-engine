"""Tests for LLMProvider ABC and FakeLLMProvider (Task 1)
and provider adapters + switchboard (Task 2)."""

import json
import pytest


# ---------------------------------------------------------------------------
# Task 1: LLMProvider ABC + FakeLLMProvider
# ---------------------------------------------------------------------------

class TestFakeLLMProvider:
    def test_complete_returns_canned_response(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["hello world"])
        result = fake.complete("sys", "usr")
        assert result == "hello world"

    def test_complete_records_call(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["ok"])
        fake.complete("system_prompt", "user_prompt")
        assert len(fake.calls) == 1
        assert fake.calls[0] == ("system_prompt", "user_prompt")

    def test_complete_cycles_responses(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["a", "b", "a"])
        assert fake.complete("s", "u") == "a"
        assert fake.complete("s", "u") == "b"
        assert fake.complete("s", "u") == "a"

    def test_complete_echo_when_no_responses(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider()
        result = fake.complete("sys", "hello echo")
        assert "hello echo" in result

    def test_complete_json_returns_canned_dict(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(json_responses=[{"key": "value"}])
        result = fake.complete_json("sys", "usr", schema={})
        assert result == {"key": "value"}

    def test_complete_json_records_calls(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(json_responses=[{"x": 1}])
        fake.complete_json("s", "u", schema={})
        assert len(fake.calls) == 1

    def test_complete_json_retries_bad_json_then_succeeds(self):
        """FakeLLMProvider wrapping a text provider: first response bad JSON,
        second response good JSON. complete_json must retry once."""
        from llm.provider import FakeLLMProvider
        # Inject text responses (not json_responses) to test retry via complete()
        bad_json = "not-json-at-all"
        good_json = '{"result": "ok"}'
        fake = FakeLLMProvider(responses=[bad_json, good_json])
        result = fake.complete_json("sys", "usr", schema={})
        assert result == {"result": "ok"}
        # Two complete() calls were made (initial + retry)
        assert len(fake.calls) == 2

    def test_complete_json_raises_after_two_failures(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["bad", "also-bad"])
        with pytest.raises(ValueError):
            fake.complete_json("sys", "usr", schema={})

    def test_provider_is_abc(self):
        """LLMProvider is an ABC with complete and complete_json as abstract methods."""
        from llm.provider import LLMProvider
        import abc
        assert issubclass(LLMProvider, abc.ABC)

    def test_fake_provider_is_llm_provider(self):
        from llm.provider import LLMProvider, FakeLLMProvider
        assert issubclass(FakeLLMProvider, LLMProvider)

    def test_complete_accepts_model_and_max_tokens(self):
        from llm.provider import FakeLLMProvider
        fake = FakeLLMProvider(responses=["hi"])
        result = fake.complete("sys", "usr", model="gpt-4", max_tokens=512)
        assert result == "hi"


# ---------------------------------------------------------------------------
# Task 2: Provider adapters + make_provider switchboard
# ---------------------------------------------------------------------------

class TestMakeProvider:
    def test_make_provider_fake(self):
        from llm.provider import make_provider, FakeLLMProvider
        p = make_provider("fake", model=None)
        assert isinstance(p, FakeLLMProvider)

    def test_make_provider_openai_returns_openai_provider(self):
        from llm.provider import make_provider, OpenAIProvider
        p = make_provider("openai", model="gpt-4o", api_key="sk-test")
        assert isinstance(p, OpenAIProvider)
        assert p.model == "gpt-4o"

    def test_make_provider_zhipu_returns_zhipu_provider(self):
        from llm.provider import make_provider, ZhipuProvider
        p = make_provider("zhipu", model="glm-4", api_key="zhipu-test")
        assert isinstance(p, ZhipuProvider)
        assert p.model == "glm-4"

    def test_make_provider_anthropic_returns_anthropic_provider(self):
        from llm.provider import make_provider, AnthropicProvider
        p = make_provider("anthropic", model="claude-3-haiku-20240307", api_key="ant-test")
        assert isinstance(p, AnthropicProvider)
        assert p.model == "claude-3-haiku-20240307"

    def test_make_provider_unknown_raises(self):
        from llm.provider import make_provider
        with pytest.raises((ValueError, KeyError)):
            make_provider("unknown_provider", model="x")


class TestOpenAIProviderRequestBuilding:
    def test_build_request_url(self):
        from llm.provider import OpenAIProvider
        p = OpenAIProvider(model="gpt-4o", api_key="sk-test",
                           base_url="https://api.openai.com/v1")
        url, headers, body = p._build_request("sys", "usr", max_tokens=256)
        assert url == "https://api.openai.com/v1/chat/completions"

    def test_build_request_headers_auth(self):
        from llm.provider import OpenAIProvider
        p = OpenAIProvider(model="gpt-4o", api_key="sk-test",
                           base_url="https://api.openai.com/v1")
        url, headers, body = p._build_request("sys", "usr", max_tokens=256)
        assert headers.get("Authorization") == "Bearer sk-test"
        assert "application/json" in headers.get("Content-Type", "")

    def test_build_request_body_messages(self):
        from llm.provider import OpenAIProvider
        p = OpenAIProvider(model="gpt-4o", api_key="sk-test",
                           base_url="https://api.openai.com/v1")
        url, headers, body = p._build_request("system_msg", "user_msg", max_tokens=512)
        assert body["model"] == "gpt-4o"
        assert body["max_tokens"] == 512
        messages = body["messages"]
        assert any(m["role"] == "system" and m["content"] == "system_msg" for m in messages)
        assert any(m["role"] == "user" and m["content"] == "user_msg" for m in messages)


class TestZhipuProviderRequestBuilding:
    def test_build_request_url_default(self):
        from llm.provider import ZhipuProvider
        p = ZhipuProvider(model="glm-4", api_key="zhipu-key")
        url, headers, body = p._build_request("sys", "usr", max_tokens=256)
        assert "zhipuai" in url or "bigmodel" in url or "glm" in url.lower() or "zhipu" in url.lower()

    def test_build_request_openai_compatible_body(self):
        from llm.provider import ZhipuProvider
        p = ZhipuProvider(model="glm-4", api_key="zhipu-key")
        url, headers, body = p._build_request("sys", "usr", max_tokens=128)
        # OpenAI-compatible: must have messages field
        assert "messages" in body
        assert body["model"] == "glm-4"

    def test_build_request_headers_auth(self):
        from llm.provider import ZhipuProvider
        p = ZhipuProvider(model="glm-4", api_key="zhipu-key")
        url, headers, body = p._build_request("sys", "usr", max_tokens=128)
        auth = headers.get("Authorization", "")
        assert "zhipu-key" in auth


class TestAnthropicProviderRequestBuilding:
    def test_build_request_url(self):
        from llm.provider import AnthropicProvider
        p = AnthropicProvider(model="claude-3-haiku-20240307", api_key="ant-key",
                              base_url="https://api.anthropic.com")
        url, headers, body = p._build_request("sys", "usr", max_tokens=256)
        assert url.endswith("/v1/messages")

    def test_build_request_headers(self):
        from llm.provider import AnthropicProvider
        p = AnthropicProvider(model="claude-3-haiku-20240307", api_key="ant-key",
                              base_url="https://api.anthropic.com")
        url, headers, body = p._build_request("sys", "usr", max_tokens=256)
        assert headers.get("x-api-key") == "ant-key"
        assert "application/json" in headers.get("Content-Type", "")
        assert "anthropic-version" in headers

    def test_build_request_body_anthropic_format(self):
        from llm.provider import AnthropicProvider
        p = AnthropicProvider(model="claude-3-haiku-20240307", api_key="ant-key",
                              base_url="https://api.anthropic.com")
        url, headers, body = p._build_request("my_system", "user_says", max_tokens=512)
        assert body["model"] == "claude-3-haiku-20240307"
        assert body["max_tokens"] == 512
        # Anthropic format: system at top level, messages array with user
        assert body.get("system") == "my_system"
        messages = body["messages"]
        assert any(m["role"] == "user" for m in messages)


def test_do_post_retries_on_read_timeout(monkeypatch):
    """A transient socket read-timeout must be retried, not fatal — long glm-5.1
    sessions occasionally hit a slow call; one flaky TimeoutError must not kill
    the turn. (Live-caught: a 300s read timeout crashed turn 5 of a long run.)"""
    import llm.provider as P

    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"ok": True, "usage": {}}).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("The read operation timed out")
        return _Resp()

    monkeypatch.setattr(P.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(P.time, "sleep", lambda *_a, **_k: None)  # skip real backoff

    result = P._do_post("https://x/v1/chat/completions", {}, {"model": "glm-5.1"})
    assert result == {"ok": True, "usage": {}}
    assert calls["n"] == 2  # first attempt timed out, retry succeeded



def test_openai_chat_body_carries_tools():
    """_openai_chat_body with tools kwarg puts 'tools' in the body dict."""
    from llm.provider import _openai_chat_body
    msgs = [{"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "map_query", "parameters": {}}}]
    body = _openai_chat_body("gpt-4o", msgs, 1024, tools=tools)
    assert "tools" in body
    assert body["tools"] == tools


def test_openai_chat_body_no_tools_key_when_none():
    """_openai_chat_body with tools=None must NOT add 'tools' to the body."""
    from llm.provider import _openai_chat_body
    msgs = [{"role": "user", "content": "hi"}]
    body = _openai_chat_body("gpt-4o", msgs, 1024, tools=None)
    assert "tools" not in body


# ---------------------------------------------------------------------------
# Task 6: supports_tools on real adapters + complete_with_tools offline tests
# ---------------------------------------------------------------------------

def test_supports_tools_true_on_zhipu():
    from llm.provider import ZhipuProvider
    assert ZhipuProvider(model="glm-4.7", api_key="k").supports_tools() is True


def test_supports_tools_true_on_openai():
    from llm.provider import OpenAIProvider
    assert OpenAIProvider(model="gpt-4o", api_key="k").supports_tools() is True


def test_zhipu_complete_with_tools_offline(monkeypatch):
    """ZhipuProvider.complete_with_tools must: send tools in body, call the executor
    with parsed args, return final text — with NO real HTTP (monkeypatched _do_post)."""
    import json
    from llm import provider as P
    responses = iter([
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "map_query",
                                         "arguments": '{"q": "city"}'}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "DONE"}}]},
    ])
    calls = {"n": 0, "bodies": []}

    def fake_post(url, headers, body, timeout=300, *, max_retries=4):
        calls["n"] += 1
        calls["bodies"].append(body)
        return next(responses)

    monkeypatch.setattr(P, "_do_post", fake_post)

    seen = []
    def executor(name, arguments):
        seen.append((name, arguments)); return '{"ok": 1}'

    prov = P.ZhipuProvider(model="glm-4.7", api_key="k")
    out = prov.complete_with_tools(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "look"}],
        tools=[{"type": "function", "function": {"name": "map_query", "parameters": {}}}],
        tool_executor=executor, max_tool_rounds=3)

    assert out == "DONE"
    assert seen == [("map_query", {"q": "city"})]
    assert calls["n"] == 2
    # First request carried tools in the body:
    assert "tools" in calls["bodies"][0]


def test_openai_complete_with_tools_offline(monkeypatch):
    """OpenAIProvider.complete_with_tools: same two-round test."""
    import json
    from llm import provider as P
    responses = iter([
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "c2", "type": "function",
                            "function": {"name": "recall_query",
                                         "arguments": '{"q": "bridge"}'}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "OK"}}]},
    ])
    calls = {"n": 0}

    def fake_post(url, headers, body, timeout=300, *, max_retries=4):
        calls["n"] += 1
        return next(responses)

    monkeypatch.setattr(P, "_do_post", fake_post)

    seen = []
    def executor(name, arguments):
        seen.append((name, arguments)); return '{}'

    prov = P.OpenAIProvider(model="gpt-4o", api_key="k")
    out = prov.complete_with_tools(
        [{"role": "user", "content": "go"}],
        tools=[{"type": "function", "function": {"name": "recall_query", "parameters": {}}}],
        tool_executor=executor, max_tool_rounds=3)

    assert out == "OK"
    assert seen == [("recall_query", {"q": "bridge"})]
    assert calls["n"] == 2


def test_zhipu_tool_result_uses_arguments_raw(monkeypatch):
    """The assistant echo message must carry arguments_raw (the original JSON string),
    not a re-serialised '{}' — GLM arguments_raw 坑 preservation test."""
    from llm import provider as P

    captured_messages: list[list] = []
    raw_args = '{"q": "city", "extra": 42}'
    responses = iter([
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "c3", "type": "function",
                            "function": {"name": "map_query",
                                         "arguments": raw_args}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]},
    ])

    def fake_post(url, headers, body, timeout=300, *, max_retries=4):
        captured_messages.append([m.copy() for m in body["messages"]])
        return next(responses)

    monkeypatch.setattr(P, "_do_post", fake_post)

    prov = P.ZhipuProvider(model="glm-4.7", api_key="k")
    prov.complete_with_tools(
        [{"role": "user", "content": "look"}],
        tools=[{"type": "function", "function": {"name": "map_query", "parameters": {}}}],
        tool_executor=lambda n, a: '{"result": "x"}',
        max_tool_rounds=3)

    # Second post's messages should include the assistant echo with raw arguments:
    second_msgs = captured_messages[1]
    assistant_msg = next(m for m in second_msgs if m.get("role") == "assistant")
    echoed_args = assistant_msg["tool_calls"][0]["function"]["arguments"]
    assert echoed_args == raw_args  # raw string preserved, not re-serialised

