"""Small offline production-profile checks; no real keys, network, or ledger."""
import copy
import json
import sqlite3
import urllib.request

import pytest

from engine import settings
from llm.provider import DeepSeekProvider
from scripts.api_budget import BudgetLedger
from scripts import benchmark_production_semantics as bench


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("live network forbidden")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)


def provider(tmp_path):
    ledger = BudgetLedger(tmp_path / "test_budget.json", input_cny_per_million="1",
        output_cny_per_million="2", prior_known_cny="0", uncertainty_reserve_cny="1",
        max_input_tokens=1048576, max_output_tokens=16384)
    return bench.ProductionAuditProvider("offline-key-never-saved", api_budget=ledger)


def response(content=None, *, tool_calls=None):
    message = {"content": json.dumps(content, ensure_ascii=False) if content is not None else None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"model": bench.MODEL, "choices": [{"message": message,
        "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}}


def commit(turn):
    obj = {"narration": "你平静地完成了眼前的行动。", "moves": [], "places": [],
        "cast": [], "facts": [], "items": [], "clock": [{"advance": turn == 10,
        "days": int(turn == 10), "bands": 0, "reason": "等待到清晨" if turn == 10 else "片刻"}]}
    if turn in {4, 6, 8, 11}:
        obj["moves"] = [{"who": "player", "to": bench.frozen.expected_state(turn)["locations"]["player"][0]}]
    if turn in {3, 14, 16}:
        item, source, target = {3: ("brass_bell", "player", "keeper"),
            14: ("brass_bell", "keeper", "player"), 16: ("jade_seal", "player", "keeper")}[turn]
        obj["items"] = [{"op": "transfer", "item": item, "from": source, "to": target}]
    if turn == 15:
        obj["narration"] = "沈掌柜说：你取名雨燕，排除的是归雁。"
    return obj


def fake_transport(p, requests, *, fail_recap=False):
    def transport(url, headers, body=None):
        if body is None:
            return {"data": [{"id": bench.MODEL}]}
        assert p.api_budget.snapshot()["reservation_count"] == len(requests) + 1
        requests.append(copy.deepcopy(body))
        assert body["max_tokens"] == 16384 and "temperature" not in body
        assert body["thinking"] == {"type": "disabled"}
        stage = p._stage(body)
        if stage == "return_intent":
            return response({"status": "none"})
        if stage == "recap":
            if fail_recap:
                raise OSError("synthetic wire failure")
            return response({"summary": "林舟离开客栈，又回来取铜铃。"})
        if stage == "reflection":
            return response({"predicate": "arc", "value": "人物留意身边的事情。"})
        if stage == "catchup":
            return response({"changed": False})
        if stage == "cascade":
            return response({"evolve": False})
        if stage == "narration_repair":
            return response({"facts": []})
        assert stage == "narration", stage
        obj = commit(int(p.case_id.rsplit("_", 1)[-1]))
        if p.case_id == "turn_01":
            obj["facts"] = [{"subject": "missing_entity", "predicate": "name", "value": "错名"}]
        return response(obj)
    return transport


def test_normal_provider_unlimited_posts_smaller_caller_limit_and_native_tools(tmp_path, monkeypatch):
    p = provider(tmp_path)
    assert isinstance(p, DeepSeekProvider) and p.max_tokens == 16384 and p.supports_tools()
    requests = []
    def transport(url, headers, body=None):
        if body is None:
            return {"data": [{"id": bench.MODEL}]}
        requests.append(copy.deepcopy(body))
        if body.get("tools") and not any(m["role"] == "tool" for m in body["messages"]):
            return response(tool_calls=[{"id": "call1", "type": "function", "function": {"name": "look", "arguments": "{}"}}])
        return response({"ok": True})
    monkeypatch.setattr(bench, "_request_json", transport)
    p.preflight()
    for _ in range(65):
        p.complete("summary", "synthetic", max_tokens=64)
    result = p.complete_with_tools([{"role": "user", "content": "look"}],
        [{"type": "function", "function": {"name": "look", "parameters": {"type": "object"}}}],
        lambda name, args: {"visible": True}, max_tool_rounds=3)
    assert json.loads(result) == {"ok": True}
    assert len(requests) == len(p.calls) == 67
    assert requests[0]["max_tokens"] == 64 and requests[-1]["max_tokens"] == 16384
    assert p.calls[-2]["raw_message"]["content"] is None
    assert any(m["role"] == "tool" for m in requests[-1]["messages"])
    assert p.last_usage == {"input": 20, "output": 5, "total": 25}
    assert p.api_budget.snapshot()["pending_count"] == 0
    assert "offline-key" not in bench._json(p.wire_audit)


def test_one_real_play_loop_preserves_scene_history_and_all_audit(tmp_path, monkeypatch):
    p, requests, invocations = provider(tmp_path), [], []
    monkeypatch.setattr(bench, "_request_json", fake_transport(p, requests))
    real_loop = bench.play.play_loop
    def observed(engine, inputs, **kwargs):
        invocations.append(True)
        assert engine.cascade_provider is p and engine.provider is p
        assert settings.get_max_tool_rounds() == kwargs["max_repairs"] == 3
        assert settings.get_verbosity() == "concise" and settings.get_conversation_mode() == "multiturn"
        return real_loop(engine, inputs, **kwargs)
    monkeypatch.setattr(bench.play, "play_loop", observed)
    p.preflight()
    report = bench.run_suite(tmp_path / "run", p)
    assert len(invocations) == 1
    assert report["status"] == "completed_manual_review_required"
    assert report["counts"]["turns_completed"] == 16
    assert report["counts"]["execution_errors"] == 0
    assert report["turns"][0]["observed_guard_codes"] == ["dangling_ref"]
    assert report["turns"][0]["prev_scene"] is None
    assert all(row["prev_scene"] is not None for row in report["turns"][1:])
    assert report["compaction"]["forced_before_turns"] == [] and report["limits"]["http_posts"] is None
    assert all(not row["forced_compaction"] for row in report["turns"])
    assert max(row["thread_after"]["message_count"] for row in report["turns"]) == 17
    assert any(body.get("tools") for body in requests)
    assert {"recap", "return_intent", "narration_repair"} <= {e["stage"] for e in report["wire_audit"]}
    assert any(e["type"] == "variation_sampled" for e in report["all_events"])
    assert report["initial_world"]["meta"]["campaign_seed"] == bench.CAMPAIGN_SEED
    assert bench.fixture_events() == bench.fixture_events()
    assert len((tmp_path / "run" / "wire.jsonl").read_text().splitlines()) == len(report["wire_audit"])
    saved = json.loads((tmp_path / "run" / "report.json").read_text())
    assert saved["counts"] == report["counts"] and "offline-key" not in json.dumps(saved)
    with sqlite3.connect(tmp_path / "run" / "campaign" / "events.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    row = copy.deepcopy(report["turns"][0])
    row["wire_audit"].append({"kind": "request", "body": bench.frozen.CANARY, "player_facing_context": False})
    assert bench._checks(row, row["before"])["no_private_canary_in_player_facing_requests"]
    row["wire_audit"][-1]["player_facing_context"] = True
    assert not bench._checks(row, row["before"])["no_private_canary_in_player_facing_requests"]


def test_fatal_swallowed_backstage_transport_stops_next_input(tmp_path, monkeypatch):
    p, requests = provider(tmp_path), []
    monkeypatch.setattr(bench, "_request_json", fake_transport(p, requests, fail_recap=True))
    p.preflight()
    report = bench.run_suite(tmp_path / "run", p)
    assert report["status"] == "fatal_stopped" and len(report["turns"]) < 16
    assert p.fatal_error == "OSError" and p.api_budget.snapshot()["pending_count"] == 1
    count = len(requests)
    with pytest.raises(bench.FatalProviderStopped):
        p.complete("no", "more")
    assert len(requests) == count


@pytest.mark.parametrize("bad", ["endpoint", "model", "admission"])
def test_no_wire_on_invalid_target_or_admission_and_latched_failure(tmp_path, monkeypatch, bad):
    p = provider(tmp_path)
    p.preflight_ok = True
    def forbidden(*args, **kwargs):
        pytest.fail("must fail before auth transmission")
    monkeypatch.setattr(bench, "_request_json", forbidden)
    body = {"model": "other" if bad == "model" else bench.MODEL,
        "messages": [], "max_tokens": 16385 if bad == "admission" else 16384}
    url = "https://untrusted.invalid/chat/completions" if bad == "endpoint" else bench.BASE_URL + "/chat/completions"
    with pytest.raises(ValueError):
        p._post(url, {}, body)
    assert p.fatal_error and not p.calls
    assert p.api_budget.snapshot()["reservation_count"] == 0
    with pytest.raises(bench.FatalProviderStopped):
        p.preflight()


def test_concurrent_admitted_calls_finish_but_fatal_blocks_new_work(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    p, barrier, sent = provider(tmp_path), Barrier(2), []
    p.preflight_ok = True
    def transport(url, headers, body=None):
        action = body["messages"][-1]["content"]
        sent.append(action)
        barrier.wait(timeout=5)
        if action == "fail":
            raise OSError("synthetic concurrent failure")
        return response({"ok": True})
    monkeypatch.setattr(bench, "_request_json", transport)
    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(p.complete, "test", "fail")
        success = pool.submit(p.complete, "test", "success")
        with pytest.raises(OSError):
            failed.result()
        assert json.loads(success.result()) == {"ok": True}
    assert len(sent) == 2 and p.api_budget.snapshot()["pending_count"] == 1
    with pytest.raises(bench.FatalProviderStopped):
        p.complete("test", "must not send")
    assert len(sent) == 2


def test_transport_diagnostics_do_not_serialize_arbitrary_reason_text():
    import urllib.error
    details = bench._safe_transport_details(urllib.error.URLError(OSError('Tunnel connection failed: 403 Forbidden')))
    assert details['category'] == 'proxy_tunnel_failure'
    assert details['http_status'] == 403
    sentinel = 'PRIVATE_AUTH_SENTINEL_DO_NOT_LOG'
    redacted = bench._safe_transport_details(urllib.error.URLError(sentinel))
    assert sentinel not in json.dumps(redacted)
    assert redacted['category'] == 'unclassified_reason_text_omitted'
