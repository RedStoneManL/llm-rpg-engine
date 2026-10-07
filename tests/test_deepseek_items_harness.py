"""Offline item-harness tests: mocked HTTP, actual gateway and SQLite files."""
import copy
import json
import sqlite3
import urllib.error
import urllib.request

import pytest

from scripts import verify_deepseek_items as harness
from scripts import verify_deepseek_resources as shared


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline item harness tests must not access the network")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(shared, "_request_json", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def completion(content):
    return {"model": harness.MODEL, "choices": [{"message": {
        "role": "assistant", "content": json.dumps(content, ensure_ascii=False)},
        "finish_reason": "stop"}], "usage": {
            "prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}


def transfer(source, target):
    return {"op": "transfer", "item": "umbrella", "from": source, "to": target}


def commit(*, items=None, relations=None, narration="雨伞交接完毕。"):
    return {"narration": narration, "moves": [], "places": [], "cast": [], "facts": [],
            "items": items or [], "relations": relations or [],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "片刻"}]}


def scripted_transport(monkeypatch, *, wrong_mode="repair", bypass_mode="refuse", gift_mode="valid",
                       sequential_mode="valid"):
    """Replace the shared I/O seam, never the provider, strategy or game engine."""
    posts, gets = [], []
    wrong_transfer = transfer("C", "B")
    bypass = {"src": "umbrella", "rel": "held_by", "dst": "B"}

    def request(url, headers, body=None):
        assert headers["Authorization"] == "Bearer offline-test-key"
        if body is None:
            gets.append(url)
            return {"data": [{"id": harness.MODEL}]}
        posts.append(copy.deepcopy(body))
        assert url == harness.BASE_URL + "/chat/completions"
        assert body["model"] == "deepseek-flash"
        assert body["max_tokens"] <= 4096
        assert body["thinking"] == {"type": "disabled"}
        assert "tools" not in body
        messages = body["messages"]
        case_id = next(case["id"] for case in harness.CASES
                       if case["action"] in messages[1]["content"])
        repairing = len(messages) > 2
        if case_id == "valid_gift":
            if gift_mode == "refuse":
                return completion({"items": [wrong_transfer]} if repairing else commit(items=[wrong_transfer]))
            return completion(commit(items=[transfer("A", "B")]))
        if case_id == "sequential_transfer":
            target = [transfer("A", "B"), transfer("B" if sequential_mode == "valid" else "A", "C")]
            return completion({"items": target} if repairing else commit(items=target))
        if case_id == "wrong_source":
            if wrong_mode == "correct":
                return completion(commit(items=[transfer("A", "B")]))
            if wrong_mode == "no_change":
                return completion(commit(narration="雨伞仍在 A 手里，没有交接。"))
            if repairing:
                return completion({"items": [wrong_transfer] if wrong_mode == "refuse" else
                                  [] if wrong_mode == "repair_no_change" else [transfer("A", "B")]})
            # Intentionally wrong prose must remain visible for manual review,
            # even when the structured repair changes the source to A.
            return completion(commit(items=[wrong_transfer], narration="C 把自己的雨伞交给了 B。"))
        if bypass_mode == "routed":
            return completion(commit(items=[transfer("A", "B")]))
        if bypass_mode == "no_change":
            return completion(commit(narration="没有交接，雨伞仍在 A 手中。"))
        if repairing:
            return completion({"relations": [bypass] if bypass_mode == "refuse" else []})
        return completion(commit(relations=[bypass], narration="账本已把 B 记为持有者。"))

    monkeypatch.setattr(shared, "_request_json", request)
    return posts, gets


def run_mocked(tmp_path, monkeypatch, **modes):
    posts, gets = scripted_transport(monkeypatch, **modes)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    output = tmp_path / "run"
    code = harness.main(["--live", "--output-dir", str(output)])
    return code, json.loads((output / "report.json").read_text()), output, posts, gets


def test_live_opt_in_missing_key_and_existing_directory_create_nothing(tmp_path):
    output = tmp_path / "absent"
    with pytest.raises(SystemExit) as no_opt_in:
        harness.main(["--output-dir", str(output)])
    assert no_opt_in.value.code == 2
    with pytest.raises(SystemExit) as no_key:
        harness.main(["--live", "--output-dir", str(output)])
    assert no_key.value.code == 2
    with pytest.raises(SystemExit):
        harness.main(["--live", "--output-dir", str(tmp_path)])
    assert not output.exists()
    assert not (tmp_path / "campaign").exists()


def test_exact_model_preflight_before_campaign_and_no_transport_duplication(tmp_path, monkeypatch):
    assert harness.BoundedDeepSeekProvider is shared.BoundedDeepSeekProvider
    assert harness.AuditedAuthorStrategy is shared.AuditedAuthorStrategy
    assert harness.isolated_gateway is shared.isolated_gateway
    assert harness._usage is shared._usage
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    calls = []
    def unavailable(url, headers, body=None):
        calls.append((url, body))
        return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-v4-flash"}]}
    monkeypatch.setattr(shared, "_request_json", unavailable)
    output = tmp_path / "absent"
    assert harness.main(["--live", "--output-dir", str(output)]) == 1
    assert calls == [(harness.BASE_URL + "/models", None)]
    assert not output.exists()
    provider = harness.BoundedDeepSeekProvider("offline-test-key")
    with pytest.raises(ValueError, match="preflight"):
        harness.run_suite(output, provider)
    assert not output.exists()


def test_actual_sqlite_gift_repair_sequence_refusal_reopen_and_rewind(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "another-model")
    monkeypatch.setenv("RPG_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("RPG_CASCADE_MODEL", "another-model")
    monkeypatch.setenv("RPG_EMBEDDER", "fastembed")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "not-real")
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "unexpected-trace"))
    code, report, output, posts, gets = run_mocked(tmp_path, monkeypatch)
    assert code == 0
    assert report["status"] == "state_checks_passed_manual_prose_review_required"
    assert "offline-test-key" not in (output / "report.json").read_text()
    assert "required" in report["manual_prose_review"]
    assert "not consent" in report["provenance_semantics"]
    assert len(posts) == report["usage"]["http_post_attempts"] == 6
    assert gets == [harness.BASE_URL + "/models"]
    assert report["usage"]["reported_token_totals"] == {"input": 60, "output": 18, "total": 78}
    assert not (tmp_path / "unexpected-trace").exists()
    gift, wrong, sequence, bypass = report["cases"]
    assert gift["after"] == {"umbrella": ["B"]}
    assert not gift["item_guard_exercised"]
    assert wrong["classification"] == "wrong_source_blocked_then_repaired_to_actual_source"
    assert wrong["committed_transfers"] == [harness.AB]
    assert wrong["observed_guard_codes"] == ["stale_holder"]
    assert wrong["audit"][0]["sections"]["items"] == [transfer("C", "B")]
    assert wrong["audit"][2]["content"]["items"] == [transfer("A", "B")]
    assert wrong["final_narration"] == wrong["audit"][0]["narration"] == "C 把自己的雨伞交给了 B。"
    assert wrong["repair_attempts"] == 1
    assert sequence["after"] == {"umbrella": ["C"]}
    assert sequence["committed_transfers"] == [harness.AB, harness.BC]
    assert [r["dst"] for r in sequence["ownership_history_after"]] == ["A", "B", "C"]
    assert bypass["result"] == "refused"
    assert bypass["classification"] == "guard_observed_then_refused"
    assert bypass["observed_guard_codes"] == ["item_route"]
    assert bypass["refusal"] == "TurnRejected"
    assert bypass["after"] == bypass["before"] == {"umbrella": ["A"]}
    assert all(all(row["checks"].values()) for row in report["cases"])
    assert all(row["before"] == {"umbrella": ["A"]} for row in report["cases"])
    assert len({row["ownership_history_before"][0]["source_event"] for row in report["cases"]}) == 4
    assert [call["phase"] for call in report["calls"]] == ["narration", "narration", "repair", "narration", "narration", "repair"]
    assert all(call["raw_content"] for call in report["calls"])
    for row in report["cases"]:
        campaign = output / row["campaign"]
        with sqlite3.connect(campaign / "events.db") as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert db.execute("SELECT count(*) FROM events").fetchone()[0] >= 9
        assert {event["turn"] for event in map(json.loads,
            (campaign / "events.jsonl").read_text().splitlines()) if not event["retracted"]} == {0}


@pytest.mark.parametrize("wrong_mode,classification,guard", [
    ("correct", "model_corrected_source_before_validation", False),
    ("no_change", "model_chose_no_change_without_guard_evidence", False),
    ("repair_no_change", "guard_observed_then_no_change", True),
    ("refuse", "guard_observed_then_refused", True),
])
def test_wrong_source_classification_does_not_overclaim_guard(tmp_path, monkeypatch, wrong_mode, classification, guard):
    code, report, _, _, _ = run_mocked(tmp_path, monkeypatch, wrong_mode=wrong_mode)
    row = report["cases"][1]
    assert code == 0
    assert row["classification"] == classification
    assert row["item_guard_exercised"] is guard
    assert {"item": "umbrella", "from": "C", "to": "B"} not in row["committed_transfers"]


@pytest.mark.parametrize("bypass_mode,classification,guard,code", [
    ("repair", "generic_route_blocked_then_no_change", True, 0),
    ("no_change", "no_change_without_generic_guard_evidence", False, 0),
    ("routed", "ownership_changed_despite_no_handover_request", False, 1),
])
def test_bypass_is_not_permission_for_a_routed_transfer(tmp_path, monkeypatch, bypass_mode, classification, guard, code):
    actual, report, _, _, _ = run_mocked(tmp_path, monkeypatch, bypass_mode=bypass_mode)
    row = report["cases"][3]
    assert actual == code
    assert row["classification"] == classification
    assert row["item_guard_exercised"] is guard
    assert row["checks"]["expected_outcome"] is (code == 0)


@pytest.mark.parametrize("modes,index", [({"gift_mode": "refuse"}, 0), ({"sequential_mode": "stale"}, 2)])
def test_required_transfer_refusal_never_counts_as_pass(tmp_path, monkeypatch, modes, index):
    code, report, _, _, _ = run_mocked(tmp_path, monkeypatch, **modes)
    row = report["cases"][index]
    assert code == 1
    assert report["status"] == "state_checks_failed"
    assert row["result"] == "refused"
    assert not row["checks"]["expected_outcome"]
    assert row["checks"]["refusal_atomic"]
    assert row["after"] == row["before"]
    assert row["committed_transfers"] == []


def test_transport_failure_is_not_a_successful_rejection(tmp_path, monkeypatch):
    attempts = []
    def fail(url, headers, body=None):
        if body is None:
            return {"data": [{"id": harness.MODEL}]}
        attempts.append(body)
        raise urllib.error.HTTPError(url, 503, "offline failure", {}, None)
    monkeypatch.setattr(shared, "_request_json", fail)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    output = tmp_path / "run"
    assert harness.main(["--live", "--output-dir", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    assert len(attempts) == 4  # One failed POST per case, no lower-level retry.
    assert report["usage"]["calls_without_usage"] == 4
    assert all(row["classification"] == "execution_error" for row in report["cases"])
    assert all(row["checks"]["refusal_atomic"] for row in report["cases"])
    assert not any(row["checks"]["expected_outcome"] for row in report["cases"])


def test_shared_post_budget_and_output_cap_are_enforced(monkeypatch):
    attempts = []
    def request(url, headers, body=None):
        attempts.append(body)
        return completion({})
    monkeypatch.setattr(shared, "_request_json", request)
    provider = harness.BoundedDeepSeekProvider("offline-test-key")
    provider.preflight_ok = True
    for _ in range(harness.MAX_POSTS):
        provider.complete_messages([{"role": "user", "content": "fixture"}], max_tokens=99999)
    assert len(attempts) == len(provider.calls) == 16
    assert all(body["max_tokens"] == 4096 for body in attempts)
    with pytest.raises(shared.CallBudgetExceeded):
        provider.complete_messages([{"role": "user", "content": "fixture"}])
    assert len(attempts) == 16
