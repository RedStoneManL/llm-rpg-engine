"""P3 Task 1: ScriptedToolProvider — offline-deterministic tool-loop fake (NO network).
   P3 Task 2: _run_tool_loop orchestrator — pure, injectable post/parse seams."""
from __future__ import annotations

import json
import pytest


class _RecordingExecutor:
    """A trivial tool_executor: maps name->canned-json, records every call."""
    def __init__(self, table: dict[str, dict]):
        self._table = table
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return json.dumps(self._table.get(name, {"error": f"no tool {name}"}),
                          ensure_ascii=False)


def test_supports_tools_default_false():
    from llm.provider import FakeLLMProvider
    assert FakeLLMProvider().supports_tools() is False


def test_scripted_provider_supports_tools_true():
    from llm.provider import ScriptedToolProvider
    assert ScriptedToolProvider(script=[{"content": "x"}]).supports_tools() is True


def test_scripted_provider_replays_tool_then_final():
    """A script of [tool_calls round, final content] must: execute the tool via the
    REAL executor, feed its result back, then return the final content — and record
    the exact (name, arguments) sequence the model emitted."""
    from llm.provider import ScriptedToolProvider

    script = [
        {"tool_calls": [{"name": "map_query", "arguments": {"q": "city"}}]},
        {"content": '{"narration": "done", "moves": []}'},
    ]
    executor = _RecordingExecutor({"map_query": {"places": ["city", "gate"]}})
    prov = ScriptedToolProvider(script=script)

    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "look around"}]
    final = prov.complete_with_tools(
        messages, tools=[{"type": "function",
                          "function": {"name": "map_query", "parameters": {}}}],
        tool_executor=executor, max_tool_rounds=3)

    assert final == '{"narration": "done", "moves": []}'
    # The executor really ran with the scripted args:
    assert executor.calls == [("map_query", {"q": "city"})]
    # And the provider recorded the model->tool invocations deterministically:
    assert prov.tool_invocations == [("map_query", {"q": "city"})]


def test_scripted_provider_multi_round_sequence():
    """Two tool rounds then final — asserts ordered multi-call research."""
    from llm.provider import ScriptedToolProvider
    script = [
        {"tool_calls": [{"name": "map_query", "arguments": {"q": "city"}}]},
        {"tool_calls": [{"name": "recall_query", "arguments": {"q": "桥"}}]},
        {"content": '{"narration": "ok"}'},
    ]
    executor = _RecordingExecutor({"map_query": {"ok": 1}, "recall_query": {"ok": 2}})
    prov = ScriptedToolProvider(script=script)
    out = prov.complete_with_tools([{"role": "user", "content": "go"}],
                                   tools=[], tool_executor=executor, max_tool_rounds=3)
    assert out == '{"narration": "ok"}'
    assert [c[0] for c in executor.calls] == ["map_query", "recall_query"]


def test_scripted_provider_respects_max_tool_rounds():
    """If the script keeps asking for tools past the cap, the provider stops and
    returns the LAST content it can (forced-final), and logs — never loops forever."""
    from llm.provider import ScriptedToolProvider
    # 5 tool rounds scripted, cap = 2 → must not execute more than the cap allows,
    # and must terminate with the forced-final content.
    script = [{"tool_calls": [{"name": "map_query", "arguments": {}}]}] * 5
    script.append({"content": "FINAL"})
    executor = _RecordingExecutor({"map_query": {"ok": 1}})
    prov = ScriptedToolProvider(script=script)
    out = prov.complete_with_tools([{"role": "user", "content": "go"}],
                                   tools=[], tool_executor=executor, max_tool_rounds=2)
    assert out == "FINAL"
    assert len(executor.calls) == 2  # capped


# ---------------------------------------------------------------------------
# Task 2: _run_tool_loop orchestrator (fake post, NO network)
# ---------------------------------------------------------------------------

def test_run_tool_loop_executes_and_feeds_back():
    """post returns a tool_calls response once, then a final-text response.
    _run_tool_loop must execute the tool via tool_executor, append the result
    to messages, re-post, and return the final text."""
    from llm.provider import _run_tool_loop

    posts: list[tuple] = []
    responses = iter([
        # round 0: model asks for a tool
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "map_query",
                                         "arguments": '{"q": "city"}'}}]}}]},
        # round 1: model answers
        {"choices": [{"finish_reason": "stop",
                      "message": {"content": "FINAL"}}]},
    ])

    def post(messages, tools):
        posts.append((len(messages), tools is not None))
        return next(responses)

    def parse(resp):
        msg = resp["choices"][0]["message"]
        if resp["choices"][0].get("finish_reason") == "tool_calls":
            calls = [{"id": c["id"], "name": c["function"]["name"],
                      "arguments": __import__("json").loads(c["function"]["arguments"])}
                     for c in msg["tool_calls"]]
            return None, calls
        return msg.get("content"), []

    seen = []
    def executor(name, arguments):
        seen.append((name, arguments))
        return '{"places": ["city"]}'

    def append_result(messages, call, result_str):
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": call["id"], "type": "function",
                                         "function": {"name": call["name"], "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_str})

    messages = [{"role": "user", "content": "look"}]
    out = _run_tool_loop(messages, tools=[{"x": 1}], tool_executor=executor,
                         post=post, parse=parse, append_result=append_result,
                         max_tool_rounds=3)
    assert out == "FINAL"
    assert seen == [("map_query", {"q": "city"})]
    assert len(posts) == 2                 # one research post + one final post
    assert posts[1][1] is True             # tools still offered on the 2nd (under cap)


def test_run_tool_loop_cap_forces_final_without_tools():
    """When the model keeps requesting tools past max_tool_rounds, the loop does
    ONE final post with tools=None (forcing a textual answer) and returns it."""
    from llm.provider import _run_tool_loop

    tool_resp = {"choices": [{"finish_reason": "tool_calls", "message": {
        "tool_calls": [{"id": "c", "type": "function",
                        "function": {"name": "map_query", "arguments": "{}"}}]}}]}
    final_resp = {"choices": [{"finish_reason": "stop",
                               "message": {"content": "FORCED"}}]}
    calls_tools_flag: list[bool] = []
    state = {"n": 0}

    def post(messages, tools):
        calls_tools_flag.append(tools is not None)
        # Always ask for a tool until tools is None (the forced-final call).
        if tools is None:
            return final_resp
        state["n"] += 1
        return tool_resp

    def parse(resp):
        msg = resp["choices"][0]["message"]
        if resp["choices"][0].get("finish_reason") == "tool_calls":
            return None, [{"id": "c", "name": "map_query", "arguments": {}}]
        return msg.get("content"), []

    def executor(name, arguments):
        return "{}"

    def append_result(messages, call, result_str):
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_str})

    out = _run_tool_loop([{"role": "user", "content": "go"}], tools=[{"x": 1}],
                         tool_executor=executor, post=post, parse=parse,
                         append_result=append_result, max_tool_rounds=2)
    assert out == "FORCED"
    assert calls_tools_flag[-1] is False   # final call omits tools
    assert state["n"] == 2                  # exactly max_tool_rounds tool rounds
