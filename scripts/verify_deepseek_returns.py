"""Opt-in bounded DeepSeek Flash smoke for the full return-clarification REPL.

    python scripts/verify_deepseek_returns.py --live --output-dir /new/directory

Five literal player inputs exercise the real play_loop, extractor, AuthorStrategy,
turn gateway and SQLite. No credentials are read from files. Transport, exact
model preflight, 16-POST cap, 4096-output cap, no retries/redirects, and optional
caller isolation are shared with verify_deepseek_resources. State checks cannot
prove prose quality or the semantic correctness of the model's intent judgment.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import build_engine, rewind
from app.play import _build_scene, play_loop
from kernel.events import kernel_event
from kernel.projection import project
from loop.return_intent import extract_return_intent
from loop.strategy import AuthorStrategy
from loop.turn import REQUIRED_SECTIONS, TurnRejected, run_turn
from scripts.verify_deepseek_resources import (
    BACKGROUND_HOOKS, BASE_URL, BoundedDeepSeekProvider, MAX_OUTPUT_TOKENS,
    MAX_POSTS, MODEL, _usage, isolated_gateway,
)
from systems.return_commitments import visible_records

CASES = (
    {"id": "missing_details", "action": "我答应归还蓝伞。"},
    {"id": "supply_details", "action": "还给B，第2天中午。"},
    {"id": "remind_only", "action": "提醒我之前蓝伞的归还约定：答应给谁、哪天哪个时段？只是查询，不作新承诺，也不交接物品。"},
    {"id": "false_completion", "action": "我现在不把蓝伞还给B，伞仍在A手里；只要求把原归还约定标成已完成，不要虚构交接，也不是新承诺。"},
    {"id": "physical_return", "action": "我现在把自己A手里的蓝伞实际交还给B，B接过，完成之前的约定。只做这一次交接，不作新承诺。"},
)
_CACHE_FIELDS = ("_messages", "_thread", "_pending_user", "_pending_action",
                 "_compaction_due", "_bound_actor", "_committed_revision")


class AuditedAuthorStrategy(AuthorStrategy):
    """Observe real generation; keep rejected originals outside rollback state."""
    def __init__(self, audit):
        self.audit = audit

    def produce(self, *args, provider, **kwargs):
        provider.phase = provider.case_id + "/narration"
        commit = super().produce(*args, provider=provider, **kwargs)
        self.audit.append({"kind": "original_commit", "narration": commit.narration,
                           "sections": copy.deepcopy(commit.sections)})
        return commit

    def repair_sections(self, failing_sections, errors, *, provider):
        provider.phase = provider.case_id + "/narration_repair"
        self.audit.append({"kind": "repair_request", "sections": sorted(failing_sections),
                           "errors": [{"section": e.section, "code": e.code,
                                       "hint": e.hint} for e in errors]})
        repair = super().repair_sections(failing_sections, errors, provider=provider)
        self.audit.append({"kind": "repair_response", "content": copy.deepcopy(repair)})
        return repair


def _seed(engine):
    rows = [("entity_created", {"id": who, "etype": "Person", "tier": "tracked"})
            for who in ("A", "B")]
    rows += [("entity_created", {"id": "room", "etype": "Place", "tier": "tracked"}),
             ("object_created", {"id": "umbrella", "tier": "tracked", "visibility": "public"}),
             ("item_transferred", {"item": "umbrella", "from": None, "to": "A"})]
    rows += [("relation_added", {"src": who, "rel": "located_in", "dst": "room"}) for who in ("A", "B")]
    rows += [("fact_asserted", {"subject": who, "predicate": "name", "value": name,
                                "secrecy": "public"})
             for who, name in (("A", "A"), ("B", "B"), ("room", "旅店会客厅"), ("umbrella", "蓝伞"))]
    engine.store.append_many([kernel_event(kind, day=1, scene="room", turn=0,
        summary="synthetic return-commitment fixture", deltas=data) for kind, data in rows])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    if _build_scene(engine)["protagonist"] != "A":
        raise ValueError("fixture did not bind actual actor A")


def ledger(world):
    graph = world["systems"]["ontology"]
    day = world.get("meta", {}).get("day") or 1
    return {"holders": {"umbrella": sorted(graph.neighbors("umbrella", "held_by", day))},
            "records": copy.deepcopy(world["systems"]["return_commitments"]["records"])}


def _world_snapshot(world):
    """All canonical graph data and system slices; no object-identity comparison."""
    result = copy.deepcopy(world)
    graph = world["systems"]["ontology"]
    result["systems"]["ontology"] = {
        "entities": {key: asdict(value) for key, value in graph.entities.items()},
        "facts": [asdict(value) for value in graph.facts],
        "relations": [asdict(value) for value in graph.relations],
    }
    result.pop("_revision", None)  # Rewind intentionally advances the store revision.
    return result


def _cache(strategy):
    return {name: copy.deepcopy(getattr(strategy, name, None)) for name in _CACHE_FIELDS}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _one_expected_record(state, *, status="open"):
    records = list(state["records"].values())
    if len(records) != 1:
        return False
    record = records[0]
    evidence = record.get("evidence") or {}
    actions = [case["action"] for case in CASES[:2]]
    quotes = evidence.get("quotes")
    return (record.get("debtor") == "A" and record.get("recipient") == "B"
            and record.get("item") == "umbrella" and record.get("due") == {"day": 2, "band": 1}
            and record.get("status") == status and evidence.get("player_actions") == actions
            and isinstance(quotes, list) and bool(quotes)
            and all(isinstance(quote, str) and quote and any(quote in action for action in actions)
                    for quote in quotes))


def _classify(row):
    """Describe observed model choices without crediting an unexercised guard."""
    status = (row.get("intent") or {}).get("status")
    if row["result"] == "error":
        return "execution_error"
    if row["id"] == "missing_details":
        return "clarification_observed" if status == "clarify" else "missing_required_clarification"
    if row["id"] == "supply_details":
        return "ready_intent_committed" if status == "ready" and row["result"] == "committed" else "clarification_not_resolved"
    if status != "none":
        return "ordinary_or_negative_input_misclassified_as_commitment"
    if row["id"] == "false_completion":
        if row["before"] != row["after"]:
            return "unexpected_canonical_change"
        attempted = any(entry["kind"] == "original_commit" and entry["sections"].get("promises")
                        for entry in row["audit"])
        if attempted and row["result"] == "refused":
            return "fulfill_attempt_refused_without_physical_return"
        if attempted and row["guard_errors"] and row["result"] == "committed":
            return "fulfill_attempt_guarded_then_repaired"
        return "model_chose_no_completion_without_guard_evidence"
    return "ordinary_inquiry_without_new_commitment" if row["id"] == "remind_only" else "physical_return_without_new_commitment"


def _case_checks(row, *, unchanged, cache_unchanged):
    events = row["new_events"]
    promises = [e for e in events if e["type"] == "item_return_promised"]
    completions = [e for e in events if e["type"] == "item_return_fulfilled"]
    transfers = [e for e in events if e["type"] == "item_transferred"]
    intent = row.get("intent") or {}
    checks = {"no_execution_error": row["result"] != "error",
              "noncommit_atomic": row["result"] == "committed" or (unchanged and cache_unchanged)}
    if row["id"] == "missing_details":
        checks.update(required_clarification=intent.get("status") == "clarify"
                      and bool(intent.get("question", "").strip())
                      and any("[需要确认归还约定]" in line for line in row["ui_output"]),
                      no_events_time_world_or_cache_change=unchanged and cache_unchanged,
                      no_narration=not row["audit"] and row["final_narration"] is None,
                      no_commitment=not row["after"]["records"],
                      pending_preserved=(row["pending_after"] or {}).get("player_actions") == [CASES[0]["action"]])
    elif row["id"] == "supply_details":
        checks.update(ready_and_committed=intent.get("status") == "ready" and row["result"] == "committed",
                      one_exact_commitment=_one_expected_record(row["after"]) and len(promises) == 1,
                      no_physical_return=row["after"]["holders"] == {"umbrella": ["A"]} and not transfers and not completions,
                      pending_cleared=row["pending_after"] is None)
    else:
        checks.update(noncommitment_classification=intent.get("status") == "none",
                      no_new_commitment=not promises and len(row["after"]["records"]) == 1)
        if row["id"] == "physical_return":
            before_record = next(iter(row["before"]["records"].values()), {})
            after_record = next(iter(row["after"]["records"].values()), {})
            immutable = {key: value for key, value in after_record.items() if key not in {"status", "fulfilled_at"}}
            before_immutable = {key: value for key, value in before_record.items() if key not in {"status", "fulfilled_at"}}
            checks.update(physical_return_committed=row["result"] == "committed"
                          and row["after"]["holders"] == {"umbrella": ["B"]}
                          and len(transfers) == 1
                          and all(transfers[0]["deltas"].get(k) == v for k, v in
                                  {"item": "umbrella", "from": "A", "to": "B"}.items()),
                          fulfilled_same_action=_one_expected_record(row["after"], status="fulfilled")
                          and len(completions) == 1 and len(transfers) == 1
                          and completions[0]["turn"] == transfers[0]["turn"] == row["expected_turn"],
                          immutable_contract_preserved=immutable == before_immutable)
        else:
            checks.update(canonical_commitment_and_holder_unchanged=row["after"] == row["before"]
                          and _one_expected_record(row["after"]) and row["after"]["holders"] == {"umbrella": ["A"]},
                          no_transfer_or_completion=not transfers and not completions,
                          outcome_allowed=row["result"] == "committed" or (
                              row["id"] == "false_completion" and row["result"] == "refused"))
    return checks


def _observe_action(engine, strategy, provider, case, index):
    before_events = list(engine.store.iter_events())
    before_world = _world_snapshot(engine.world)
    before_cache = _cache(strategy)
    revision = engine.store.revision
    call_start = len(provider.calls)
    audit = []
    strategy.audit = audit  # Same strategy across inputs; replace only external audit sink.
    row = {"id": case["id"], "action_index": index, "actor": _build_scene(engine)["protagonist"],
           "action": case["action"], "before": ledger(engine.world), "audit": audit,
           "ui_output": [], "intent": None, "error": None, "result": "unresolved",
           "expected_turn": engine.store.next_turn(), "final_narration": None,
           "pending_before": copy.deepcopy(engine.pending_return_intent)}
    provider.case_id, provider.phase = case["id"], case["id"] + "/extraction"

    def observed_extract(*args, **kwargs):
        try:
            intent = extract_return_intent(*args, **kwargs)
            row["intent"] = copy.deepcopy(intent)
            if intent["status"] == "clarify":
                row["result"] = "clarified"
            return intent
        except Exception as error:
            row.update(result="error", error={"type": type(error).__name__, "message": str(error), "stage": "extraction"})
            raise

    def observed_turn(*args, **kwargs):
        row["gateway_player_input"] = args[4]
        row["gateway_return_commitment"] = copy.deepcopy(kwargs.get("return_commitment"))
        try:
            result = run_turn(*args, **kwargs)
            row.update(result="committed", final_narration=result.narration,
                       final_sections=copy.deepcopy(result.commit.sections),
                       repair_attempts=result.repair_attempts, receipt=result.receipt)
            return result
        except Exception as error:
            row.update(result="refused" if isinstance(error, TurnRejected) else "error",
                       error={"type": type(error).__name__, "message": str(error), "stage": "gateway"})
            raise

    with patch("loop.return_intent.extract_return_intent", observed_extract), patch("app.play.run_turn", observed_turn):
        # A one-element iterable ends naturally, unlike /quit which cancels pending.
        play_loop(engine, [case["action"]], strategy=strategy, out=row["ui_output"].append,
                  max_repairs=1, required_sections=REQUIRED_SECTIONS)
    after_events = list(engine.store.iter_events())
    before_ids = {event["id"] for event in before_events}
    row.update(after=ledger(engine.world), new_events=[e for e in after_events if e["id"] not in before_ids],
               pending_after=copy.deepcopy(engine.pending_return_intent),
               revision_before=revision, revision_after=engine.store.revision,
               time_before={key: before_world["meta"].get(key, 0) for key in ("day", "band")},
               time_after={key: engine.world["meta"].get(key, 0) for key in ("day", "band")},
               cache_digest_before=_digest(before_cache), cache_digest_after=_digest(_cache(strategy)),
               usage=_usage(provider.calls[call_start:]), call_numbers=[c["number"] for c in provider.calls[call_start:]])
    row["guard_errors"] = [error for entry in audit if entry["kind"] == "repair_request" for error in entry["errors"]]
    unchanged = after_events == before_events and revision == engine.store.revision and _world_snapshot(engine.world) == before_world
    row["checks"] = _case_checks(row, unchanged=unchanged, cache_unchanged=_cache(strategy) == before_cache)
    row["classification"] = _classify(row)
    row["state_checks"] = "passed" if all(row["checks"].values()) else "failed"
    return row, before_events, before_world


def _overdue_projection(engine):
    """A separately labeled deterministic check; never write the synthetic clock."""
    history, revision = list(engine.store.iter_events()), engine.store.revision
    original = _world_snapshot(engine.world)
    clock = kernel_event("clock_advanced", day=3, scene="room", turn=engine.store.next_turn(),
        summary="deterministic overdue projection only; never persisted", deltas={"advance": True, "days": 2, "bands": 2})
    projected = project(engine.registry, history + [clock])
    scene = {**_build_scene(engine), "day": 3}
    records = visible_records(projected, scene)
    return {"kind": "deterministic_projection_only_no_model_call", "synthetic_event": clock,
            "visible_records": records,
            "checks": {"open_record_is_overdue": len(records) == 1 and records[0]["status"] == "open" and records[0]["overdue"] is True,
                       "overdue_not_persisted_in_record": all("overdue" not in r for r in ledger(projected)["records"].values()),
                       "original_save_unchanged": list(engine.store.iter_events()) == history
                       and engine.store.revision == revision and _world_snapshot(engine.world) == original}}


def _write_report(output_dir, report, provider):
    report.update(calls=copy.deepcopy(provider.calls), usage=_usage(provider.calls),
                  response_models=sorted({c["response_model"] for c in provider.calls if c.get("response_model")}))
    if getattr(provider, "api_budget", None) is not None:
        report["session_budget"] = provider.api_budget.snapshot()
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_suite(output_dir, provider):
    if not provider.preflight_ok:
        raise ValueError("Exact-model preflight must succeed before campaign creation")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    campaign = output_dir / "campaign"
    report = {"model": MODEL, "endpoint": BASE_URL, "status": "running", "cases": [],
              "limits": {"player_actions": 5, "http_posts": MAX_POSTS, "output_tokens_per_post": MAX_OUTPUT_TOKENS,
                         "low_level_retries": 0, "redirects": 0, "extraction_repairs_per_action": 1, "narration_repairs_per_action": 1},
              "preflight": {"exact_model_available": True, "http_gets": 1},
              "scope": "Five sequential full play_loop inputs in one synthetic SQLite campaign; no full-world guarantee",
              "call_phases": "extraction includes its bounded schema repair; narration and narration_repair are separate phases",
              "intent_semantics": "The model classifies ordinary inquiry, negative request, clarification, or commitment. Literal evidence proves provenance, not semantic entailment.",
              "manual_prose_review_required": True,
              "manual_prose_review": "Compare raw classifier responses, original narration, repairs, UI question, final narration and ledger. State success is not a semantic/prose guarantee.",
              "disabled": list(BACKGROUND_HOOKS) + ["embeddings", "tracing", "narrator_tools"]}
    engine = None
    try:
        with isolated_gateway():
            engine = build_engine(campaign, provider=provider)
            _seed(engine)
            report["fixture_events"] = list(engine.store.iter_events())
            strategy = AuditedAuthorStrategy([])
            for index, case in enumerate(CASES, 1):
                row, before_events, before_world = _observe_action(engine, strategy, provider, case, index)
                report["cases"].append(row)
                if index == 2:
                    history, current = list(engine.store.iter_events()), _world_snapshot(engine.world)
                    engine.store.close()
                    engine = build_engine(campaign, provider=provider)
                    strategy = AuditedAuthorStrategy([])
                    report["reopen_after_ready"] = {
                        "new_engine_and_strategy": True,
                        "checks": {"events_preserved": list(engine.store.iter_events()) == history,
                                   "world_preserved": _world_snapshot(engine.world) == current,
                                   "one_open_exact_commitment": _one_expected_record(ledger(engine.world)),
                                   "no_pending_intent": engine.pending_return_intent is None}}
                    report["overdue_projection"] = _overdue_projection(engine)
                if index == 5:
                    history, current = list(engine.store.iter_events()), _world_snapshot(engine.world)
                    engine.store.close()
                    engine = build_engine(campaign, provider=provider)
                    report["reopen_after_return"] = {"checks": {
                        "events_preserved": list(engine.store.iter_events()) == history,
                        "world_preserved": _world_snapshot(engine.world) == current,
                        "fulfilled_and_held_by_B": _one_expected_record(ledger(engine.world), status="fulfilled")
                        and ledger(engine.world)["holders"] == {"umbrella": ["B"]}}}
                    result = rewind(engine, row["expected_turn"])
                    engine.store.close()
                    engine = build_engine(campaign, provider=provider)
                    report["rewind_final_return"] = {"result": result, "after": ledger(engine.world), "checks": {
                        "prior_events_restored": list(engine.store.iter_events()) == before_events,
                        "prior_world_restored": _world_snapshot(engine.world) == before_world,
                        "open_and_held_by_A": _one_expected_record(ledger(engine.world))
                        and ledger(engine.world)["holders"] == {"umbrella": ["A"]}}}
                    report["events_including_retracted"] = list(engine.store.iter_events(include_retracted=True))
                _write_report(output_dir, report, provider)
    except Exception as error:
        report.update(status="state_checks_failed", fatal_error={"type": type(error).__name__, "message": str(error)})
    finally:
        if engine is not None:
            engine.store.close()
        groups = ("reopen_after_ready", "overdue_projection", "reopen_after_return", "rewind_final_return")
        passed = ("fatal_error" not in report and len(report["cases"]) == len(CASES)
                  and all(row["state_checks"] == "passed" for row in report["cases"])
                  and all(name in report and all(report[name]["checks"].values()) for name in groups))
        report["status"] = "state_checks_passed_manual_prose_review_required" if passed else "state_checks_failed"
        _write_report(output_dir, report, provider)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow bounded paid DeepSeek requests")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory only")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required; no requests sent")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        parser.error("output directory already exists; use a fresh directory")
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        parser.error("DEEPSEEK_API_KEY must be set in the environment; no campaign created")
    try:
        provider = BoundedDeepSeekProvider(key)
        provider.preflight()
        report = run_suite(args.output_dir, provider)
    except Exception as error:
        print(f"Verification stopped ({type(error).__name__}); no fallback model was used.", file=sys.stderr)
        return 1
    print(json.dumps({"status": report["status"], "report": str(args.output_dir / "report.json"),
                      "http_post_attempts": len(provider.calls)}))
    return 0 if report["status"].startswith("state_checks_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
