"""Provider last_usage exposure (Task 6 of multi-turn/compaction)."""
from llm.provider import _norm_usage, OpenAIProvider, FakeLLMProvider
import llm.provider as provider_mod


def test_norm_usage_openai_keys():
    assert _norm_usage({"prompt_tokens": 100, "completion_tokens": 20,
                        "total_tokens": 120}) == {"input": 100, "output": 20, "total": 120}


def test_norm_usage_anthropic_keys():
    assert _norm_usage({"input_tokens": 50, "output_tokens": 10}) == {
        "input": 50, "output": 10, "total": None}


def test_norm_usage_empty_is_none():
    assert _norm_usage({}) is None


def test_fake_provider_last_usage_defaults_none():
    assert FakeLLMProvider().last_usage is None


def test_post_sets_last_usage(monkeypatch):
    # _do_post → _http_post_json(url, headers, data, timeout); stub the HTTP layer.
    def fake_http(url, headers, data, timeout):
        return {"choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 150000, "completion_tokens": 5}}
    monkeypatch.setattr(provider_mod, "_http_post_json", fake_http)
    p = OpenAIProvider(model="m", api_key="k")
    p.complete_messages([{"role": "user", "content": "x"}])
    assert p.last_usage == {"input": 150000, "output": 5, "total": None}
