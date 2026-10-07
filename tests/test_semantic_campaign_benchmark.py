"""Small offline semantic-benchmark tests. All HTTP is replaced at the IO seam."""
import copy
import json
import sqlite3
import urllib.request

import pytest

from app.engine import build_engine
from context.access import pov_world
from scripts.api_budget import BudgetLedger
from scripts import benchmark_semantic_campaign as bench
from scripts import verify_deepseek_resources as shared


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("network forbidden in offline benchmark tests")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(shared, "_request_json", forbidden)


def budget(tmp_path, **kwargs):
    return BudgetLedger(tmp_path / "budget.json", input_cny_per_million="1",
        output_cny_per_million="2", prior_known_cny="0", uncertainty_reserve_cny="1", **kwargs)


def response(content):
    return {"model": bench.MODEL, "choices": [{"message": {
        "content": json.dumps(content, ensure_ascii=False)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}}


def turn_commit(turn, *, skip_transfer=False):
    result = {"narration": "你平静地完成了眼前的行动。", "moves": [], "places": [],
        "cast": [], "facts": [], "items": [],
        "clock": [{"advance": turn == 10, "days": int(turn == 10), "bands": 0, "reason": "等待到清晨" if turn == 10 else "片刻"}]}
    if turn in {4, 6, 8, 11}:
        result["moves"] = [{"who": "player", "to": bench.expected_state(turn)["locations"]["player"][0]}]
    if turn in {3, 14, 16} and not skip_transfer:
        item, source, target = {3: ("brass_bell", "player", "keeper"),
            14: ("brass_bell", "keeper", "player"), 16: ("jade_seal", "player", "keeper")}[turn]
        result["items"] = [{"op": "transfer", "item": item, "from": source, "to": target}]
    if turn == 15:
        result["narration"] = "沈掌柜说：你取名雨燕，排除的是归雁。"
    return result


def setup_provider(tmp_path, monkeypatch, *, max_posts=64, skip_transfers=False, fail_summary=False, repair_first=False):
    provider = bench.SemanticDeepSeekProvider("offline-only-key", api_budget=budget(tmp_path), max_posts=max_posts)
    requests = []
    def transport(url, headers, body=None):
        if body is None:
            return {"data": [{"id": bench.MODEL}]}
        requests.append(copy.deepcopy(body))
        assert body["temperature"] == 0 and body["max_tokens"] == 4096
        assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}
        if "剧情摘要员" in body["messages"][0]["content"]:
            if fail_summary:
                raise OSError("synthetic transport failure")
            return response({"summary": "林舟把铜铃交给沈掌柜保管，随后独自离开。"})
        turn = int(provider.case_id.rsplit("_", 1)[-1])
        if repair_first and turn == 1:
            if provider.phase == "narration_repair":
                return response({"facts": []})
            result = turn_commit(turn)
            result["facts"] = [{"subject": "missing_entity", "predicate": "name", "value": "错名"}]
            return response(result)
        return response(turn_commit(turn, skip_transfer=skip_transfers))
    monkeypatch.setattr(shared, "_request_json", transport)
    provider.preflight()
    return provider, requests


def test_frozen_expected_states_and_private_synthetic_fixture(tmp_path):
    assert bench.fixture_events() == bench.fixture_events()
    assert len(bench.ACTIONS) == 16
    assert bench.expected_state(2)["holders"]["brass_bell"] == ["player"]
    assert bench.expected_state(3)["holders"]["brass_bell"] == ["keeper"]
    assert bench.expected_state(13)["holders"]["brass_bell"] == ["keeper"]
    assert bench.expected_state(14)["holders"]["brass_bell"] == ["player"]
    assert bench.expected_state(16)["promise_status"] == "fulfilled"
    with bench.isolated_campaign():
        engine = build_engine(tmp_path / "fixture", provider=object())
        try:
            bench._seed(engine)
            initial = bench.state(engine.world)
            assert initial["clock"] == {"day": 1, "band": 0}
            assert initial["borrowed_from"] == "keeper"
            assert initial["names"] == bench.NAMES
            assert initial["promises"][bench.PROMISE_ID]["evidence"]["player_actions"] == [bench.SYNTHETIC_PRIOR_ACTION]
            for actor, place in (("player", "inn"), ("guard", "bridge")):
                visible = pov_world(engine.world, {"protagonist": actor, "location": place, "day": 1})
                assert bench.CANARY not in bench._json(bench.canonical_world(visible)["systems"]["ontology"])
            keeper = pov_world(engine.world, {"protagonist": "keeper", "location": "inn", "day": 1})
            assert bench.CANARY in bench._json(bench.canonical_world(keeper)["systems"]["ontology"])
        finally:
            engine.store.close()


def test_full_campaign_sqlite_recap_audit_and_forced_compaction(tmp_path, monkeypatch):
    provider, posts = setup_provider(tmp_path, monkeypatch, repair_first=True)
    report = bench.run_suite(tmp_path / "run", provider)
    assert report["status"] == "machine_checks_passed_manual_review_required"
    assert report["counts"]["failed_checks"] == 0 and len(report["turns"]) == 16
    assert report["manual_review_status"] == "required"
    assert report["turns"][0]["observed_guard_codes"] == ["dangling_ref"]
    assert report["turns"][0]["repair_attempts"] == 1
    assert any(a["stage"] == "recap_summary" for a in report["wire_audit"])
    assert any(a["stage"] == "narration_repair" for a in report["wire_audit"])
    for turn in (12, 15):
        row = report["turns"][turn - 1]
        assert row["forced_compaction"] and row["thread_before"]["compaction_due"]
        assert row["thread_after"]["message_count"] == 3
        request = next(a for a in row["wire_audit"] if a["kind"] == "request" and a["stage"] == "narration")
        assert len(request["body"]["messages"]) == 2
    assert provider.api_budget.snapshot()["reservation_count"] == len(posts) == len(provider.calls)
    assert all(bench.CANARY not in bench._json(p) for p in posts)
    assert max(r["thread_after"]["message_count"] for r in report["turns"]) <= 17
    saved = json.loads((tmp_path / "run" / "report.json").read_text())
    assert saved["counts"] == report["counts"]
    assert len((tmp_path / "run" / "wire.jsonl").read_text().splitlines()) == len(report["wire_audit"])
    assert "offline-only-key" not in json.dumps(saved)
    with sqlite3.connect(tmp_path / "run" / "campaign" / "events.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_semantic_failures_are_counted_without_stopping(tmp_path, monkeypatch):
    provider, _ = setup_provider(tmp_path, monkeypatch, skip_transfers=True)
    report = bench.run_suite(tmp_path / "run", provider)
    assert report["status"] == "machine_checks_failed_manual_review_required"
    assert len(report["turns"]) == 16
    assert report["counts"]["failed_turns"] == 13  # T3..T13, T14 and T16 transfer requirements.
    assert report["counts"]["failed_checks"] > report["counts"]["failed_turns"]
    assert report["counts"]["refused_turns"] == 0
    assert all(not r["observed_guard_codes"] for r in report["turns"])


@pytest.mark.parametrize("mode", ["post_cap", "backstage_transport"])
def test_fatal_stop_even_when_backstage_swallows_failure(tmp_path, monkeypatch, mode):
    provider, posts = setup_provider(tmp_path, monkeypatch, max_posts=2 if mode == "post_cap" else 64,
                                    fail_summary=mode == "backstage_transport")
    report = bench.run_suite(tmp_path / "run", provider)
    assert report["status"] == "fatal_stopped" and len(report["turns"]) < 16
    assert provider.fatal_error == ("CallBudgetExceeded" if mode == "post_cap" else "OSError")
    calls = len(posts)
    with pytest.raises(bench.FatalProviderStopped):
        provider.complete_messages([{"role": "user", "content": "must not send"}])
    assert len(posts) == calls
    if mode == "post_cap":
        assert calls == provider.api_budget.snapshot()["reservation_count"] == 2
    else:
        assert provider.api_budget.snapshot()["pending_count"] == 1


def test_requires_explicit_ledger_bounds(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="BudgetLedger"):
        bench.SemanticDeepSeekProvider("offline-only-key", api_budget=None)
    with pytest.raises(ValueError, match="131072"):
        bench.SemanticDeepSeekProvider("offline-only-key", api_budget=budget(tmp_path, max_input_tokens=16384))


def test_permitted_name_and_same_origin_reassertion_are_not_false_failures(tmp_path, monkeypatch):
    provider, _ = setup_provider(tmp_path, monkeypatch)
    report = bench.run_suite(tmp_path / "run", provider)
    initial = report["turns"][0]["before"]
    row = copy.deepcopy(report["turns"][0])
    row["after"]["names"]["brass_bell"] = "雨燕"
    row["after"]["borrowed_from_sources"] = ["new_same_value_assertion"]
    assert bench._checks(row, initial)["fixed_display_names"]
    assert bench._checks(row, initial)["borrower_origin_unchanged"]
    row["after"]["names"]["brass_bell"] = "归雁"
    row["after"]["borrowed_from"] = "guard"
    assert not bench._checks(row, initial)["fixed_display_names"]
    assert not bench._checks(row, initial)["borrower_origin_unchanged"]
    row["after"]["names"]["brass_bell"] = {"invalid": "name shape"}
    assert not bench._checks(row, initial)["fixed_display_names"]
