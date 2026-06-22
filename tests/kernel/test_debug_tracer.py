import json, os
import pytest
from kernel.observability import DebugTracer

def _records(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]

def test_span_nesting_produces_path(tmp_path):
    p = str(tmp_path / "t.jsonl")
    t = DebugTracer(p, run="r1")
    with t.span("turn", turn=3):
        with t.span("cascade"):
            t.event("note", msg="hi")
    recs = _records(p)
    ev = next(r for r in recs if r["type"] == "event")
    assert ev["path"] == "turn:3▸cascade▸note"      # nested path with attr-enriched label
    assert ev["attrs"] == {"msg": "hi"}
    # span_end carries dur_ms + ref_seq back to its span_start
    ends = [r for r in recs if r["type"] == "span_end"]
    starts = [r for r in recs if r["type"] == "span_start"]
    assert len(ends) == 2 and len(starts) == 2
    assert all("dur_ms" in r for r in ends)
    # span_end links back to its span_start (ref_seq) and carries parent_seq (schema)
    start_seqs = {r["seq"] for r in starts}
    assert all(r.get("ref_seq") in start_seqs for r in ends)
    assert all("parent_seq" in r for r in ends)

def test_generation_captures_input_output_usage(tmp_path):
    p = str(tmp_path / "t.jsonl")
    t = DebugTracer(p)
    with t.span("turn", turn=1):
        with t.generation("llm", model="glm", input=[{"role":"user","content":"hi"}]) as g:
            g.finish(output="hello", usage={"input": 3, "output": 1})
    gen = next(r for r in _records(p) if r["type"] == "gen")
    assert gen["path"] == "turn:1▸llm"
    assert gen["input"] == [{"role":"user","content":"hi"}]
    assert gen["output"] == "hello"
    assert gen["usage"] == {"input": 3, "output": 1}
    assert "dur_ms" in gen and gen["attrs"]["model"] == "glm"

def test_generation_records_even_without_finish(tmp_path):
    p = str(tmp_path / "t.jsonl")
    t = DebugTracer(p)
    with t.generation("llm", model="x"):
        pass
    gen = next(r for r in _records(p) if r["type"] == "gen")
    assert gen["output"] is None    # graceful: record written on ctx exit

def test_write_failure_never_raises(tmp_path):
    t = DebugTracer("/nonexistent_dir/cannot/write.jsonl")
    with t.span("turn"):           # must not raise despite unwritable path
        t.event("x")

def test_get_tracer_singleton_under_env(tmp_path, monkeypatch):
    import kernel.observability as obs
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "t.jsonl"))
    obs._DEBUG_TRACER = None       # reset cache
    a = obs.get_tracer(); b = obs.get_tracer()
    assert isinstance(a, obs.DebugTracer) and a is b   # same singleton

def test_get_tracer_noop_when_off(monkeypatch):
    import kernel.observability as obs
    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    obs._DEBUG_TRACER = None
    assert isinstance(obs.get_tracer(), obs.NoopTracer)   # zero-overhead preserved
