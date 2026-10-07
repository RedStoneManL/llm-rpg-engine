"""Offline tests of the live harness: no credentials or network required."""
import json
import sqlite3
import urllib.error
import urllib.request

import pytest

from scripts import verify_deepseek_resources as harness


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline harness tests must not access the network")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(harness, "_request_json", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def completion(content):
    return {"model": harness.MODEL, "choices": [{"message": {
        "role": "assistant", "content": json.dumps(content, ensure_ascii=False)},
        "finish_reason": "stop"}], "usage": {
            "prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}


def commit(narration="你调整了呼吸。", facts=None):
    return {"narration": narration, "moves": [], "places": [], "cast": [],
            "facts": facts or [], "clock": [{"advance": False, "days": 0,
                "bands": 0, "reason": "片刻"}]}


def scripted_transport(monkeypatch, *, rejected_case=None):
    """Real provider adapter and engine; replace only the HTTP I/O seam."""
    posts, gets = [], []
    case_index = -1
    # Each resource-free final action begins with narration, so identify it from
    # the explicit action rather than deriving the case from an intent call.
    def request(url, headers, body=None):
        nonlocal case_index
        assert headers["Authorization"] == "Bearer offline-test-key"
        if body is None:
            gets.append(url)
            return {"data": [{"id": harness.MODEL}]}
        posts.append(body)
        assert url == harness.BASE_URL + "/chat/completions"
        assert body["model"] == "deepseek-flash"
        assert body["max_tokens"] <= 4096
        assert body["thinking"] == {"type": "disabled"}
        assert "tools" not in body
        messages = body["messages"]
        if "你只解析" in messages[0]["content"]:
            case_index += 1
            assert case_index < 3
            action = json.loads(messages[1]["content"])
            assert action["current_resources"] == {"oxygen": 12}
            return completion({"op": "spend", "resource": "oxygen",
                "amount": 3 if case_index == 0 else 100} if case_index < 2 else {"op": "none"})
        if "我是 observer" in messages[1]["content"]:
            case_index = 3
        wrong = [{"subject": "merchant", "predicate": "coins", "value": 0}]
        if len(messages) > 2:  # Structured repair request.
            return completion({"facts": wrong if case_index == 3 or case_index == rejected_case else []})
        if case_index >= 2 or case_index == rejected_case:
            # Deliberately inconsistent prose is retained for MANUAL review;
            # a repaired ledger alone must never be claimed as prose correctness.
            return completion(commit("商人的钱币全都消失了。", wrong))
        return completion(commit("你消耗了三单位氧气。" if case_index == 0 else "氧气不足，操作未能进行。"))
    monkeypatch.setattr(harness, "_request_json", request)
    return posts, gets


def test_live_opt_in_and_missing_key_create_nothing(tmp_path):
    output = tmp_path / "absent"
    with pytest.raises(SystemExit) as absent_opt_in:
        harness.main(["--output-dir", str(output)])
    assert absent_opt_in.value.code == 2
    with pytest.raises(SystemExit) as absent_key:
        harness.main(["--live", "--output-dir", str(output)])
    assert absent_key.value.code == 2
    assert not output.exists()


def test_model_preflight_requires_exact_id_before_creating_campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    calls = []
    def unavailable(url, headers, body=None):
        calls.append((url, body))
        return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-v4-flash"}]}
    monkeypatch.setattr(harness, "_request_json", unavailable)
    output = tmp_path / "absent"
    assert harness.main(["--live", "--output-dir", str(output)]) == 1
    assert calls == [(harness.BASE_URL + "/models", None)]
    assert not output.exists()


def test_existing_output_is_rejected_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    with pytest.raises(SystemExit):
        harness.main(["--live", "--output-dir", str(tmp_path)])
    assert not (tmp_path / "campaign").exists()


def test_actual_gateway_sqlite_repair_refusal_reopen_and_rewind(tmp_path, monkeypatch):
    posts, gets = scripted_transport(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    # Ambient configuration cannot redirect the run, add model calls, or trace.
    monkeypatch.setenv("DEEPSEEK_MODEL", "another-model")
    monkeypatch.setenv("RPG_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("RPG_CASCADE_MODEL", "another-model")
    monkeypatch.setenv("RPG_EMBEDDER", "fastembed")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "not-real")
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "unexpected-trace"))
    output = tmp_path / "run"
    assert harness.main(["--live", "--output-dir", str(output)]) == 0
    report_text = (output / "report.json").read_text()
    report = json.loads(report_text)
    assert "offline-test-key" not in report_text
    assert report["status"] == "state_checks_passed_manual_prose_review_required"
    assert "required" in report["manual_prose_review"]
    assert len(posts) == report["usage"]["http_post_attempts"] == 9
    assert gets == [harness.BASE_URL + "/models"]
    assert report["usage"]["reported_token_totals"] == {"input": 90, "output": 27, "total": 117}
    assert not (tmp_path / "unexpected-trace").exists()
    allowed, insufficient, repaired, refused = report["cases"]
    assert allowed["after"]["hero"]["oxygen"] == 9
    assert insufficient["resource_resolutions"][0]["outcome"] == "insufficient"
    assert repaired["repair_attempts"] == 1
    assert repaired["audit"][0]["narration"] == repaired["final_narration"] == "商人的钱币全都消失了。"
    assert repaired["resource_guard_exercised"]
    assert refused["result"] == "refused"
    assert refused["refusal"] == "TurnRejected"
    assert refused["resource_guard_exercised"]
    assert refused["usage"]["http_post_attempts"] == 2  # No intent call for observer.
    assert all(all(row["checks"].values()) for row in report["cases"])
    # Prove we exercised actual on-disk SQLite, not an in-memory ledger simulator.
    with sqlite3.connect(output / "campaign" / "events.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] > 5
    assert {event["turn"] for event in map(json.loads,
        (output / "campaign" / "events.jsonl").read_text().splitlines())
        if not event["retracted"]} == {0}


@pytest.mark.parametrize("rejected_case", [0, 1])
def test_required_spend_rejection_never_counts_as_pass(tmp_path, monkeypatch, rejected_case):
    scripted_transport(monkeypatch, rejected_case=rejected_case)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    output = tmp_path / "run"
    assert harness.main(["--live", "--output-dir", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    row = report["cases"][rejected_case]
    assert report["status"] == "state_checks_failed"
    assert row["result"] == "refused"
    assert not row["checks"]["required_outcome"]
    assert row["checks"]["refusal_atomic"]
    assert row["before"] == row["after"]


def test_provider_caps_failed_http_attempts_without_retries(monkeypatch):
    attempts = []
    def failing(url, headers, body=None):
        attempts.append(body)
        raise urllib.error.HTTPError(url, 503, "offline failure", {}, None)
    monkeypatch.setattr(harness, "_request_json", failing)
    provider = harness.BoundedDeepSeekProvider("offline-test-key")
    provider.preflight_ok = True
    for _ in range(harness.MAX_POSTS):
        with pytest.raises(urllib.error.HTTPError):
            provider.complete_messages([{"role": "user", "content": "fixture"}], max_tokens=99999)
    assert len(attempts) == len(provider.calls) == 16
    assert all(body["max_tokens"] == 4096 for body in attempts)
    with pytest.raises(harness.CallBudgetExceeded):
        provider.complete_messages([{"role": "user", "content": "fixture"}])
    assert len(attempts) == 16


def test_provider_cannot_bypass_preflight_or_select_fallback(tmp_path):
    provider = harness.BoundedDeepSeekProvider("offline-test-key")
    with pytest.raises(RuntimeError, match="preflight"):
        provider.complete("test", "test")
    with pytest.raises(ValueError, match="preflight"):
        harness.run_suite(tmp_path / "absent", provider)
    assert not (tmp_path / "absent").exists()
    provider.preflight_ok = True
    with pytest.raises(ValueError, match="exact"):
        provider.complete("test", "test", model="deepseek-chat")
    provider.base_url = "https://invalid.example"
    with pytest.raises(ValueError, match="official"):
        provider.complete("test", "test")
    assert provider.calls == []


def test_optional_spend_budget_reserves_before_transport_and_reconciles(monkeypatch):
    steps = []
    class Budget:
        def reserve(self, body):
            steps.append(('reserve', body['max_tokens']))
            return 'reservation-1'
        def reconcile(self, rid, usage):
            steps.append(('reconcile', rid, usage['completion_tokens']))
    def request(url, headers, body=None):
        assert steps == [('reserve', 4096)]
        steps.append(('transport',))
        return completion(commit())
    monkeypatch.setattr(harness, '_request_json', request)
    provider = harness.BoundedDeepSeekProvider('offline-test-key', api_budget=Budget())
    provider.preflight_ok = True
    provider.complete_messages([{'role': 'user', 'content': 'fixture'}])
    assert steps == [('reserve', 4096), ('transport',), ('reconcile', 'reservation-1', 3)]
    assert provider.calls[0]['budget_reservation'] == 'reservation-1'


def test_exhausted_spend_budget_never_calls_transport():
    class Budget:
        def reserve(self, body):
            raise RuntimeError('session cap')
    provider = harness.BoundedDeepSeekProvider('offline-test-key', api_budget=Budget())
    provider.preflight_ok = True
    with pytest.raises(RuntimeError, match='session cap'):
        provider.complete_messages([{'role': 'user', 'content': 'fixture'}])
    assert not provider.calls


def test_transport_failure_keeps_spend_reservation(monkeypatch):
    steps = []
    class Budget:
        def reserve(self, body):
            steps.append('reserve')
            return 'pending'
        def reconcile(self, *args):
            pytest.fail('failed request must retain full reservation')
    def request(*args):
        raise TimeoutError('offline fixture')
    monkeypatch.setattr(harness, '_request_json', request)
    provider = harness.BoundedDeepSeekProvider('offline-test-key', api_budget=Budget())
    provider.preflight_ok = True
    with pytest.raises(TimeoutError):
        provider.complete_messages([{'role': 'user', 'content': 'fixture'}])
    assert steps == ['reserve']
    assert provider.calls[0]['status'] == 'failed'
