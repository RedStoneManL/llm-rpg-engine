"""Opt-in LIVE DeepSeek Flash item-provenance smoke test (four fresh saves).

After securely setting DEEPSEEK_API_KEY in the environment, run:
    python scripts/verify_deepseek_items.py --live --output-dir /path/to/new-dir

Uses the real AuthorStrategy, action gateway and SQLite, with the existing
resource harness's bounded transport and disabled background callers. Raw
original/repair output and ownership history are retained for manual narration
review. This is not an authorization/consent check or a full-gameplay guarantee.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import build_engine, rewind
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from loop.turn import REQUIRED_SECTIONS, TurnRejected, run_turn
from scripts.verify_deepseek_resources import (
    AuditedAuthorStrategy, BACKGROUND_HOOKS, BASE_URL, BoundedDeepSeekProvider,
    MAX_OUTPUT_TOKENS, MAX_POSTS, MODEL, _usage, isolated_gateway,
)

CASES = (
    {"id": "valid_gift", "action": "A 把自己当前持有的 umbrella 实际赠送给 B，B 接过。只发生这一次交接。"},
    {"id": "wrong_source", "action": "C 声称自己持有 umbrella，试图交给 B，并要求记录 from=C、to=B。请依据当前物品记录处理，不能把错误来源当作真实来源。"},
    {"id": "sequential_transfer", "action": "在同一回合中，A 先把自己持有的 umbrella 交给 B，B 接过后马上交给 C，C 接过。按发生顺序记录两次实际交接 A→B→C。"},
    {"id": "generic_relation_bypass", "action": "没有任何实际交接、赠送、偷窃或移动，umbrella 仍在 A 手上。我只要求绕开 items，直接用 relations 写 umbrella held_by B 来改账本；不要虚构交接。"},
)
AB = {"item": "umbrella", "from": "A", "to": "B"}
BC = {"item": "umbrella", "from": "B", "to": "C"}
ITEM_GUARD_CODES = frozenset({
    "stale_holder", "missing_source", "ambiguous_holder", "item_route",
    "item_type", "holder_type", "item_type_change", "item_preview", "item_op",
})


def ledger(world):
    graph = world["systems"]["ontology"]
    day = world.get("meta", {}).get("day") or 1
    return {item: sorted(graph.neighbors(item, "held_by", day))
            for item, entity in sorted(graph.entities.items()) if entity.etype == "Object"}


def ownership_history(world):
    """Keep same-day intermediate owners, which current-day queries collapse."""
    return [asdict(relation) for relation in world["systems"]["ontology"].relations
            if relation.rel == "held_by"]


def _seed(engine):
    rows = [("entity_created", {"id": who, "etype": "Person", "tier": "tracked"})
            for who in ("A", "B", "C")]
    rows += [("entity_created", {"id": "room", "etype": "Place", "tier": "tracked"}),
             ("object_created", {"id": "umbrella", "tier": "tracked"}),
             ("item_transferred", {"item": "umbrella", "from": None, "to": "A"})]
    rows += [("relation_added", {"src": who, "rel": "located_in", "dst": "room"})
             for who in ("A", "B", "C")]
    engine.store.append_many([kernel_event(kind, day=1, scene="room", turn=0,
        summary="synthetic item fixture", deltas=data) for kind, data in rows])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision


def _transfers(events):
    return [{key: event["deltas"].get(key) for key in ("item", "from", "to")}
            for event in events if event["type"] == "item_transferred"]


def _original_transfers(audit):
    original = next((entry["sections"] for entry in audit
                     if entry["kind"] == "original_commit"), {})
    items = original.get("items") or []
    return [{key: row.get(key) for key in ("item", "from", "to")}
            for row in items if isinstance(row, dict) and row.get("op") == "transfer"] \
        if isinstance(items, list) else []


def _classify(case_id, row, transfers):
    """Observed outcomes, never infer a deterministic guard from a prompt alone."""
    if row["result"] == "error":
        return "execution_error"
    if row["result"] == "refused":
        return "guard_observed_then_refused" if row["item_guard_exercised"] else "refused_without_item_guard_evidence"
    if case_id == "wrong_source":
        original = _original_transfers(row["audit"])
        had_wrong_source = {"item": "umbrella", "from": "C", "to": "B"} in original
        if transfers == [AB]:
            if had_wrong_source and "stale_holder" in row["observed_guard_codes"]:
                return "wrong_source_blocked_then_repaired_to_actual_source"
            if original == [AB]:
                return "model_corrected_source_before_validation"
            return "actual_source_transfer_after_other_repair"
        if not transfers and row["after"] == row["before"]:
            return "guard_observed_then_no_change" if row["item_guard_exercised"] else "model_chose_no_change_without_guard_evidence"
        return "unexpected_transfer"
    if case_id == "generic_relation_bypass":
        if not transfers and row["after"] == row["before"]:
            return "generic_route_blocked_then_no_change" if "item_route" in row["observed_guard_codes"] else "no_change_without_generic_guard_evidence"
        return "ownership_changed_despite_no_handover_request"
    return "expected_transfer_committed" if transfers == ([AB] if case_id == "valid_gift" else [AB, BC]) else "missing_or_unexpected_transfer"


def _expected_outcome(case_id, row, transfers):
    if row["result"] == "error":
        return False
    if case_id == "valid_gift":
        return row["result"] == "committed" and transfers == [AB] and row["after"] == {"umbrella": ["B"]}
    if case_id == "sequential_transfer":
        return row["result"] == "committed" and transfers == [AB, BC] and row["after"] == {"umbrella": ["C"]}
    if case_id == "wrong_source" and row["result"] == "committed" and transfers == [AB]:
        return row["after"] == {"umbrella": ["B"]}
    return not transfers and row["after"] == row["before"]


def run_suite(output_dir, provider):
    """Inject only transport in offline tests; each case exercises real SQLite."""
    if not provider.preflight_ok:
        raise ValueError("Exact-model preflight must succeed before campaign creation")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "model": MODEL, "endpoint": BASE_URL, "status": "running",
        "limits": {"http_posts": MAX_POSTS, "output_tokens_per_post": MAX_OUTPUT_TOKENS,
                   "low_level_retries": 0, "narration_repairs_per_action": 1},
        "scope": "Four independent synthetic fresh-state AuthorStrategy/action-gateway/SQLite cases; no full gameplay guarantee",
        "provenance_semantics": "from is the prior holder, not consent, permission, or legality; truthful gifts and theft are allowed",
        "wrong_source_interpretation": "A corrected A-to-B transfer can pass provenance checks; this never means the requested C-to-B source was accepted",
        "disabled": list(BACKGROUND_HOOKS) + ["embeddings", "tracing", "narrator_tools"],
        "manual_prose_review": "required: compare original and final narration with actual transfers and the requested action; state checks do not prove semantic consistency",
        "preflight": {"exact_model_available": True, "http_gets": 1}, "cases": [],
    }
    with isolated_gateway():
        for case in CASES:
            campaign = output_dir / case["id"] / "campaign"
            engine = build_engine(campaign, provider=provider)
            try:
                _seed(engine)
                before, before_ownership = ledger(engine.world), ownership_history(engine.world)
                history = list(engine.store.iter_events())
                revision, turn = engine.store.revision, engine.store.next_turn()
                audit, call_start = [], len(provider.calls)
                provider.case_id, provider.phase = case["id"], "narration"
                row = {"id": case["id"], "campaign": str(campaign.relative_to(output_dir)),
                       "actor": "A", "action": case["action"], "before": before,
                       "ownership_history_before": before_ownership, "audit": audit,
                       "refusal": None, "final_narration": None}
                answer = None
                try:
                    answer = run_turn(engine.registry, engine.store, engine.world,
                        {**_build_scene(engine), "protagonist": "A", "present": ["A", "B", "C"],
                         "location": "room", "day": 1}, case["action"], provider=provider,
                        strategy=AuditedAuthorStrategy(audit), max_repairs=1,
                        required_sections=REQUIRED_SECTIONS)
                    engine.world = answer.world
                    row.update(result="committed", final_narration=answer.narration,
                               final_sections=answer.commit.sections,
                               repair_attempts=answer.repair_attempts, receipt=answer.receipt)
                except Exception as error:
                    row.update(result="refused" if isinstance(error, TurnRejected) else "error",
                               refusal=type(error).__name__)
                after_history = list(engine.store.iter_events())
                new_events = [event for event in after_history if event["turn"] == turn]
                transfers = _transfers(new_events)
                row.update(after=ledger(engine.world), ownership_history_after=ownership_history(engine.world),
                           committed_transfers=transfers,
                           item_events=[event for event in new_events if event["type"] in
                                        {"item_transferred", "object_created"} or
                                        event["type"] == "relation_added" and event["deltas"].get("rel") == "held_by"])
                codes = {error["code"] for entry in audit if entry["kind"] == "repair_request"
                         for error in entry["errors"]} & ITEM_GUARD_CODES
                row.update(observed_guard_codes=sorted(codes), item_guard_exercised=bool(codes))
                row["classification"] = _classify(case["id"], row, transfers)
                graph = engine.world["systems"]["ontology"]
                checks = {
                    "expected_outcome": _expected_outcome(case["id"], row, transfers),
                    "no_generic_held_by_committed": not any(event["type"] == "relation_added"
                        and event["deltas"].get("rel") == "held_by" for event in new_events),
                    "fixture_types_preserved": all(graph.get_entity(who).etype == "Person" for who in ("A", "B", "C"))
                        and graph.get_entity("umbrella").etype == "Object",
                    "refusal_atomic": answer is not None or (
                        after_history == history and engine.store.revision == revision),
                }
                engine.store.close()
                engine = build_engine(campaign, provider=provider)
                checks["disk_reopen"] = (ledger(engine.world) == row["after"]
                    and ownership_history(engine.world) == row["ownership_history_after"]
                    and list(engine.store.iter_events()) == after_history)
                row["rewind"] = rewind(engine, turn)
                engine.store.close()
                engine = build_engine(campaign, provider=provider)
                checks["rewind_and_reopen"] = (ledger(engine.world) == before
                    and ownership_history(engine.world) == before_ownership
                    and list(engine.store.iter_events()) == history)
                row.update(checks=checks, state_checks="passed" if all(checks.values()) else "failed",
                           usage=_usage(provider.calls[call_start:]))
                report["cases"].append(row)
                report.update(calls=provider.calls, usage=_usage(provider.calls))
                _write_report(output_dir, report)
            finally:
                engine.store.close()
    passed = len(report["cases"]) == len(CASES) and all(row["state_checks"] == "passed" for row in report["cases"])
    report["status"] = "state_checks_passed_manual_prose_review_required" if passed else "state_checks_failed"
    _write_report(output_dir, report)
    return report


def _write_report(output_dir, report):
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


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
