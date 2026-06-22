"""Task 2: provider generation captures prompt input + completion output."""

import json
import kernel.observability as obs
from llm import provider as prov


def test_do_post_records_input_and_output(tmp_path, monkeypatch):
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "t.jsonl"))
    obs._DEBUG_TRACER = None
    # Stub the HTTP layer: make _do_post's transport return a canned OpenAI-shape response.
    fake = {"choices": [{"message": {"content": "你好旅人"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
    monkeypatch.setattr(prov, "_http_post_json", lambda *a, **k: fake, raising=False)
    # Call the lowest LLM chokepoint the providers funnel through:
    body = {"model": "glm", "max_tokens": 100,
            "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]}
    with obs.get_tracer().span("turn", turn=1):
        prov._do_post("http://x", {}, body)     # uses the stubbed transport
    gen = next(r for r in (json.loads(l) for l in open(tmp_path / "t.jsonl")) if r["type"] == "gen")
    assert gen["input"] == body["messages"]      # prompt captured
    assert gen["output"] == "你好旅人"             # completion captured
    assert gen["usage"]["output"] == 4
