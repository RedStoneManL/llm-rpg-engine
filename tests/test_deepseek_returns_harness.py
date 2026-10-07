"""Offline full-app/real-provider-adapter checks; only HTTP I/O is scripted."""
import copy
import json
import re
import sqlite3
import urllib.error
import urllib.request

import pytest

from scripts import verify_deepseek_resources as transport
from scripts import verify_deepseek_returns as harness


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline return harness must never access the network")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(transport, "_request_json", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def completion(content):
    return {"model": harness.MODEL, "choices": [{"message": {"role": "assistant",
        "content": json.dumps(content, ensure_ascii=False)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}


def commit(narration, **sections):
    return {"narration": narration, "moves": [], "places": [], "cast": [], "facts": [],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "片刻"}], **sections}


def scripted_transport(monkeypatch, *, initial="clarify", wrong_party=False,
                       wrong_date=False, false_completion="repair", duplicate=False,
                       no_return=False, extraction_repair=False, unavailable=False):
    posts, gets, classifications = [], [], []
    current_case = None

    def request(url, headers, body=None):
        nonlocal current_case
        assert headers["Authorization"] == "Bearer offline-test-key"
        if body is None:
            gets.append(url)
            return {"data": [{"id": "deepseek-chat" if unavailable else harness.MODEL}]}
        posts.append(copy.deepcopy(body))
        assert url == harness.BASE_URL + "/chat/completions"
        assert body["model"] == "deepseek-flash"
        assert body["max_tokens"] <= 4096
        assert body["thinking"] == {"type": "disabled"}
        assert "tools" not in body
        messages = body["messages"]
        if "归还承诺意图分类器" in messages[0]["content"]:
            data = json.loads(messages[1]["content"])
            current_case = next(case["id"] for case in harness.CASES if case["action"] == data["player_actions"][-1])
            classifications.append(copy.deepcopy(data))
            assert data["actor"] == "A"
            names = {item["id"]: item.get("labels", {}).get("name") for item in data["candidates"]}
            assert names["umbrella"] == "蓝伞" and names["room"] == "旅店会客厅"
            if current_case == "missing_details":
                if extraction_repair and len(messages) == 2:
                    return completion({"status": "invalid"})
                return completion({"status": "clarify", "question": "要还给谁？请说清哪一天、哪个时段。"}
                                  if initial == "clarify" else {"status": "none"})
            if current_case == "supply_details":
                return completion({"status": "ready", "item": "umbrella",
                    "recipient": "room" if wrong_party else "B",
                    "due_expression": "第3天中午" if wrong_date else "第2天中午",
                    "evidence_quotes": data["player_actions"]})
            if current_case == "remind_only" and duplicate:
                # A quote from the old conversation is not evidence in this new input.
                return completion({"status": "ready", "item": "umbrella", "recipient": "B",
                                   "due_expression": "第2天中午", "evidence_quotes": [harness.CASES[1]["action"]]})
            return completion({"status": "none"})
        assert current_case is not None
        ids = re.findall(r"return_[a-f0-9]{32}", messages[1]["content"])
        contract_id = ids[0] if ids else "missing-contract"
        if len(messages) > 2:
            # Actual AuthorStrategy section-repair grammar and app execution.
            if current_case == "false_completion":
                return completion({"promises": [] if false_completion == "repair" else
                                   [{"op": "fulfill", "id": contract_id}]})
            return completion({"facts": [], "items": [], "promises": []})
        if current_case == "missing_details":
            return completion(commit("你说想归还蓝伞。"))
        if current_case == "supply_details":
            return completion(commit("你答应第2天中午把蓝伞还给B。"))
        if current_case == "remind_only":
            return completion(commit("你约定第2天中午把蓝伞还给B，现在仍未归还。"))
        if current_case == "false_completion":
            if false_completion == "none":
                return completion(commit("蓝伞仍在你手里，约定还没完成。"))
            # Deliberately false original prose remains visible in report after repair.
            return completion(commit("蓝伞已经归还，约定完成了。",
                                     promises=[{"op": "fulfill", "id": contract_id}]))
        assert current_case == "physical_return"
        return completion(commit("你把蓝伞递给B，B接过。", items=[] if no_return else
                                 [{"op": "transfer", "item": "umbrella", "from": "A", "to": "B"}]))

    monkeypatch.setattr(transport, "_request_json", request)
    return posts, gets, classifications


def run_report(tmp_path, monkeypatch, **options):
    calls = scripted_transport(monkeypatch, **options)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    output = tmp_path / "run"
    code = harness.main(["--live", "--output-dir", str(output)])
    return code, json.loads((output / "report.json").read_text()), calls, output


def test_opt_in_and_missing_key_create_nothing(tmp_path):
    output = tmp_path / "absent"
    for args in (["--output-dir", str(output)], ["--live", "--output-dir", str(output)]):
        with pytest.raises(SystemExit) as error:
            harness.main(args)
        assert error.value.code == 2
        assert not output.exists()


def test_existing_output_precedes_credentials_or_network(tmp_path):
    with pytest.raises(SystemExit):
        harness.main(["--live", "--output-dir", str(tmp_path)])
    assert not (tmp_path / "campaign").exists()


def test_exact_model_unavailable_creates_no_directory(tmp_path, monkeypatch):
    posts, gets, _ = scripted_transport(monkeypatch, unavailable=True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    output = tmp_path / "absent"
    assert harness.main(["--live", "--output-dir", str(output)]) == 1
    assert gets == [harness.BASE_URL + "/models"] and posts == []
    assert not output.exists()


def test_requires_preflight_before_campaign_creation(tmp_path):
    provider = harness.BoundedDeepSeekProvider("offline-test-key")
    with pytest.raises(ValueError, match="preflight"):
        harness.run_suite(tmp_path / "absent", provider)
    assert not (tmp_path / "absent").exists()


def test_full_app_clarification_sqlite_repair_reopen_overdue_rewind(tmp_path, monkeypatch):
    monkeypatch.setenv("RPG_EMBEDDER", "fastembed")
    monkeypatch.setenv("RPG_CASCADE_MODEL", "unwanted-model")
    monkeypatch.setenv("RPG_DEBUG_TRACE", str(tmp_path / "unwanted-trace"))
    monkeypatch.setenv("RPG_BASE_URL", "https://invalid.example")
    code, report, (posts, gets, classifications), output = run_report(tmp_path, monkeypatch)
    assert code == 0, json.dumps(report, ensure_ascii=False, indent=2)
    assert report["status"] == "state_checks_passed_manual_prose_review_required"
    assert report["manual_prose_review_required"] is True
    assert report["response_models"] == ["deepseek-flash"]
    assert gets == [harness.BASE_URL + "/models"]
    assert len(posts) == report["usage"]["http_post_attempts"] == 10
    assert report["usage"]["reported_token_totals"] == {"input": 100, "output": 30, "total": 130}
    assert len(classifications) == 5
    assert classifications[0]["has_pending"] is False
    assert classifications[1]["has_pending"] is True
    assert classifications[1]["player_actions"] == [case["action"] for case in harness.CASES[:2]]
    assert all(data["has_pending"] is False for data in classifications[2:])
    first, ready, query, false, returned = report["cases"]
    assert first["new_events"] == [] and first["usage"]["http_post_attempts"] == 1
    assert first["cache_digest_before"] == first["cache_digest_after"]
    assert first["time_before"] == first["time_after"]
    assert "需要确认归还约定" in "".join(first["ui_output"])
    assert ready["gateway_player_input"] == "\n".join(case["action"] for case in harness.CASES[:2])
    assert ready["gateway_return_commitment"]["due"] == {"day": 2, "band": 1}
    assert query["classification"] == "ordinary_inquiry_without_new_commitment"
    assert false["classification"] == "fulfill_attempt_guarded_then_repaired"
    assert false["repair_attempts"] == 1 and false["guard_errors"]
    assert false["audit"][0]["narration"] == false["final_narration"] == "蓝伞已经归还，约定完成了。"
    assert returned["after"]["holders"] == {"umbrella": ["B"]}
    assert len(returned["after"]["records"]) == 1
    assert all(all(row["checks"].values()) for row in report["cases"])
    assert all(report["overdue_projection"]["checks"].values())
    assert report["overdue_projection"]["kind"] == "deterministic_projection_only_no_model_call"
    assert report["rewind_final_return"]["after"]["holders"] == {"umbrella": ["A"]}
    assert all(report["rewind_final_return"]["checks"].values())
    assert "offline-test-key" not in (output / "report.json").read_text()
    assert not (tmp_path / "unwanted-trace").exists()
    with sqlite3.connect(output / "campaign" / "events.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    events = report["events_including_retracted"]
    assert sum(e["type"] == "item_return_promised" and not e["retracted"] for e in events) == 1
    assert sum(e["type"] == "item_return_fulfilled" and e["retracted"] for e in events) == 1
    assert not any("deterministic overdue" in e["summary"] for e in events)
    assert all("raw_content" in call and call["phase"].startswith(call["case"] + "/") for call in report["calls"])


@pytest.mark.parametrize("choice, classification", [
    ("none", "model_chose_no_completion_without_guard_evidence"),
    ("refuse", "fulfill_attempt_refused_without_physical_return"),
])
def test_distinguishes_model_choice_and_actual_guard(tmp_path, monkeypatch, choice, classification):
    code, report, _, _ = run_report(tmp_path, monkeypatch, false_completion=choice)
    assert code == 0
    row = report["cases"][3]
    assert row["classification"] == classification
    assert row["before"] == row["after"]
    if choice == "refuse":
        assert row["result"] == "refused"
        assert row["error"]["type"] == "TurnRejected"
        assert row["checks"]["noncommit_atomic"]
        assert row["new_events"] == []
    else:
        assert row["guard_errors"] == []


@pytest.mark.parametrize("options, failed_index", [
    ({"initial": "none"}, 0), ({"wrong_party": True}, 1),
    ({"wrong_date": True}, 1), ({"duplicate": True}, 2), ({"no_return": True}, 4),
])
def test_model_failures_are_not_counted_as_passes(tmp_path, monkeypatch, options, failed_index):
    code, report, _, _ = run_report(tmp_path, monkeypatch, **options)
    assert code == 1
    assert report["status"] == "state_checks_failed"
    assert report["cases"][failed_index]["state_checks"] == "failed"
    assert report["manual_prose_review_required"] is True
    assert len(report["calls"]) <= 16


def test_one_bounded_extraction_repair_is_logged(tmp_path, monkeypatch):
    code, report, (posts, _, _), _ = run_report(tmp_path, monkeypatch, extraction_repair=True)
    assert code == 0
    assert report["cases"][0]["usage"]["http_post_attempts"] == 2
    assert len(posts) == 11
    assert "invalid" in report["calls"][0]["raw_content"]
    assert "clarify" in report["calls"][1]["raw_content"]


def test_duplicate_wrong_date_and_party_fail_exact_record_checker():
    record = {"id": "return_fixture", "item": "umbrella", "debtor": "A", "recipient": "B",
              "due": {"day": 2, "band": 1}, "status": "open", "evidence": {
                  "player_actions": [case["action"] for case in harness.CASES[:2]],
                  "quotes": [case["action"] for case in harness.CASES[:2]]}}
    state = {"records": {"return_fixture": record}}
    assert harness._one_expected_record(state)
    for field, value in (("debtor", "B"), ("recipient", "room"), ("due", {"day": 3, "band": 1})):
        bad = copy.deepcopy(state)
        bad["records"]["return_fixture"][field] = value
        assert not harness._one_expected_record(bad)
    state["records"]["duplicate"] = copy.deepcopy(record)
    assert not harness._one_expected_record(state)


def test_reuses_bounded_provider_with_no_http_retries(monkeypatch):
    assert harness.BoundedDeepSeekProvider is transport.BoundedDeepSeekProvider
    assert harness.isolated_gateway is transport.isolated_gateway
    attempts = []
    def failing(url, headers, body=None):
        attempts.append(body)
        raise urllib.error.HTTPError(url, 503, "offline failure", {}, None)
    monkeypatch.setattr(transport, "_request_json", failing)
    provider = harness.BoundedDeepSeekProvider("offline-test-key")
    provider.preflight_ok = True
    for _ in range(16):
        with pytest.raises(urllib.error.HTTPError):
            provider.complete_messages([{"role": "user", "content": "fixture"}], max_tokens=99999)
    with pytest.raises(transport.CallBudgetExceeded):
        provider.complete_messages([{"role": "user", "content": "fixture"}])
    assert len(attempts) == len(provider.calls) == 16
    assert all(body["max_tokens"] == 4096 for body in attempts)
