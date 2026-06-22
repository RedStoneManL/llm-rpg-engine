import logging

from kernel.observability import get_tracer, NoopTracer, dump


def test_default_tracer_is_noop_without_creds(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    t = get_tracer()
    assert isinstance(t, NoopTracer)


def test_noop_span_is_a_usable_contextmanager():
    t = NoopTracer()
    with t.span("turn", turn=1) as sp:
        assert sp is None  # no-op yields nothing, never raises


def test_noop_generation_yields_finishable_handle():
    """The generation() context manager yields a handle whose finish() accepts
    output/usage and never raises — so _do_post can record token usage offline."""
    t = NoopTracer()
    with t.generation("llm", model="glm-4.7", max_tokens=32768) as gen:
        assert gen is not None
        gen.finish(output="hi", usage={"input": 10, "output": 20, "total": 30})
        gen.finish()  # no-arg finish is also safe


def test_record_usage_normalizes_both_dialects():
    """provider._record_usage maps OpenAI- and Anthropic-style usage onto the
    handle without raising, regardless of which token keys are present."""
    from llm.provider import _record_usage

    captured = {}

    class _Gen:
        def finish(self, *, output=None, usage=None):
            captured["usage"] = usage

    _record_usage(_Gen(), {"usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}})
    assert captured["usage"] == {"input": 5, "output": 7, "total": 12}

    _record_usage(_Gen(), {"usage": {"input_tokens": 3, "output_tokens": 4}})
    assert captured["usage"] == {"input": 3, "output": 4}

    _record_usage(_Gen(), {})  # no usage key → finish(usage=None), no crash
    assert captured["usage"] is None


def test_dump_logs_only_when_debug(monkeypatch, caplog):
    monkeypatch.setenv("RPG_DEBUG", "1")
    with caplog.at_level(logging.DEBUG, logger="rpg.kernel.observability"):
        dump("turn-commit", {"narration": "hi"})
    assert any("turn-commit" in r.message for r in caplog.records)
