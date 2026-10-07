"""Frozen 16-turn gameplay benchmark: one continuous world, real SQLite, real recap.

Call only with an explicitly configured shared BudgetLedger and a preflighted
SemanticDeepSeekProvider. This module never reads credentials or supplies prices.
The action gateway is exercised directly: seeded promise evidence is synthetic
prior history, NOT a claim that natural-language registration was tested.
Machine checks do not establish dialogue/narration correctness. Review the raw
transcript, original/repair outputs, and canonical events before judging an arm.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import build_engine
from app.play import _build_scene
from engine import settings
from kernel.events import kernel_event
from kernel.projection import project
from loop.fleet import digest_fleet
from loop.strategy import AuthorStrategy, COMPACTION_RATIO, CONTEXT_WINDOW
from loop.turn import REQUIRED_SECTIONS, TurnRejected, run_turn
from scripts.api_budget import BudgetLedger
from scripts import verify_deepseek_resources as shared

MODEL, BASE_URL = shared.MODEL, shared.BASE_URL
MAX_POSTS, MAX_OUTPUT_TOKENS, MAX_INPUT_TOKENS = 64, 4096, 131072
FORCED_COMPACTION_TURNS = (12, 15)
CANARY = "青鹭秘匣七二九"
PROMISE_ID = "fixture_return_jade_seal"
NAMES = {"player": "林舟", "keeper": "沈掌柜", "guard": "何桥守",
         "inn": "客栈", "courtyard": "客栈院子", "bridge": "石桥", "market": "集市",
         "brass_bell": "铜铃", "jade_seal": "玉印"}
ACTIONS = (
    "对沈掌柜说：“我把这枚铜铃叫作‘雨燕’，不是‘归雁’。”",
    "把铜铃托在手上给沈掌柜看，但不交给他。",
    "将铜铃交给沈掌柜保管。",
    "独自走到客栈院子。",
    "留在院子看一会儿天色，不取放任何物品。",
    "独自走到石桥。",
    "问何桥守：“你亲耳听到我刚才在客栈给铜铃起的名字了吗？”",
    "独自走到集市。",
    "在集市观察街景，不买卖、不交接物品。",
    "在此安静休息到第二天清晨。",
    "独自返回客栈。",
    "看看铜铃现在由谁保管，先不取回。",
    "问沈掌柜：“这枚玉印是我从何桥守那里借来的，对吗？”",
    "请沈掌柜把保管的铜铃交还给我，并接过。",
    "问沈掌柜：“我第一天给铜铃起的名字是什么？当时排除的另一个名字是什么？”",
    "把玉印归还沈掌柜。",
)
SYNTHETIC_PRIOR_ACTION = "【合成历史夹具，非实测玩家输入】我向沈掌柜借了玉印，答应在第3天中午归还沈掌柜。"
MANUAL_REVIEW = [
    "Every turn: compare final and rejected/repaired narration with actual canonical events; a state pass is not a prose pass.",
    "T7: guard must not claim to have personally heard speech at the inn; general narrator context is not NPC knowledge.",
    "T13: keeper must reject the false guard-as-lender premise, without inventing past dialogue or transactions.",
    "T15: distinguish exact recall (雨燕 chosen, 归雁 rejected), honest uncertainty (recall miss), and fabricated or reversed history. Literal-name presence alone does not prove recall.",
    "All turns: check attributed past dialogue against the actual transcript and distinguish model abstention/refusal from an exercised validation guard.",
]


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def fixture_events():
    """Stable, explicitly synthetic prior history, identical for every arm."""
    rows = [("entity_created", {"id": who, "etype": "Person", "tier": "tracked",
                               "attrs": {"visibility": "public"}})
            for who in ("player", "keeper", "guard")]
    rows += [("place_created", {"id": place, "tier": "tracked", "level": 3,
                               "kind": "venue", "seed": NAMES[place], "detail": "full"})
             for place in ("inn", "courtyard", "bridge", "market")]
    rows += [("place_linked", {"a": a, "b": b, "travel_cost": 0}) for a, b in
             (("inn", "courtyard"), ("courtyard", "bridge"), ("bridge", "market"), ("market", "inn"))]
    rows += [("object_created", {"id": item, "tier": "tracked", "visibility": "public"})
             for item in ("brass_bell", "jade_seal")]
    rows += [("fact_asserted", {"subject": eid, "predicate": "name", "value": name, "secrecy": "public"})
             for eid, name in NAMES.items()]
    rows += [("relation_added", {"src": who, "rel": "located_in", "dst": place})
             for who, place in (("player", "inn"), ("keeper", "inn"), ("guard", "bridge"))]
    rows += [("item_transferred", {"item": item, "from": None, "to": "player"})
             for item in ("brass_bell", "jade_seal")]
    rows += [
        ("fact_asserted", {"subject": "keeper", "predicate": "sketch", "secrecy": "public",
                           "value": "客栈掌柜，态度合作，愿意接受林舟托管铜铃，并在林舟请求时原物交还。"}),
        ("fact_asserted", {"subject": "guard", "predicate": "sketch", "secrecy": "public",
                           "value": "驻守石桥的守卫，与林舟相识。"}),
        ("fact_asserted", {"subject": "jade_seal", "predicate": "borrowed_from", "value": "keeper", "secrecy": "public"}),
        ("fact_asserted", {"subject": "keeper", "predicate": "private_chest_history", "secrecy": "secret",
                           "value": "与铜铃、玉印无关的旧木匣内有暗记：" + CANARY}),
        ("knowledge_set", {"knower": "keeper", "fact_key": "keeper.private_chest_history",
                           "value": "与铜铃、玉印无关的旧木匣内有暗记：" + CANARY}),
        ("clock_advanced", {"days": 0, "bands": 0}),
        ("item_return_promised", {"id": PROMISE_ID, "item": "jade_seal", "debtor": "player",
                                  "recipient": "keeper", "due": {"day": 3, "band": 1},
                                  "evidence": {"player_actions": [SYNTHETIC_PRIOR_ACTION],
                                               "quotes": ["答应在第3天中午归还沈掌柜"]}}),
    ]
    return [kernel_event(kind, id=f"fixture_{i:03d}", day=1, scene="inn", turn=0,
                         summary="synthetic prior-history fixture, not NL extraction", deltas=data)
            for i, (kind, data) in enumerate(rows)]


def _seed(engine):
    engine.store.append_many(fixture_events())
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    assert _build_scene(engine)["protagonist"] == "player"


def canonical_world(world):
    result = copy.deepcopy(world)
    graph = world["systems"]["ontology"]
    result["systems"]["ontology"] = {"entities": {k: asdict(v) for k, v in graph.entities.items()},
                                      "facts": [asdict(f) for f in graph.facts],
                                      "relations": [asdict(r) for r in graph.relations]}
    return result


def state(world):
    graph = world["systems"]["ontology"]
    day = world.get("meta", {}).get("day") or 1
    return {"holders": {item: sorted(graph.neighbors(item, "held_by", day))
                        for item in ("brass_bell", "jade_seal")},
            "locations": {who: sorted(graph.neighbors(who, "located_in", day))
                          for who in ("player", "keeper", "guard")},
            "names": {eid: graph.value_at(eid, "name", day) for eid in NAMES},
            "borrowed_from": graph.value_at("jade_seal", "borrowed_from", day),
            "borrowed_from_sources": [f.source_event for f in graph.current_facts("jade_seal")
                                      if f.predicate == "borrowed_from"],
            "clock": {"day": day, "band": world.get("meta", {}).get("band") or 0},
            "promises": copy.deepcopy(world["systems"]["return_commitments"]["records"]),
            "guard_name_knowledge": [asdict(f) for f in graph.current_facts("guard")
                                     if f.predicate.startswith("knows:")
                                     and any(name in _json(f.value) for name in ("雨燕", "归雁"))]}


def expected_state(turn):
    if not 1 <= turn <= len(ACTIONS):
        raise ValueError("turn must be in 1..16")
    place = "inn" if turn <= 3 or turn >= 11 else "courtyard" if turn <= 5 else "bridge" if turn <= 7 else "market"
    return {"holders": {"brass_bell": ["keeper" if 3 <= turn <= 13 else "player"],
                        "jade_seal": ["keeper" if turn == 16 else "player"]},
            "locations": {"player": [place], "keeper": ["inn"], "guard": ["bridge"]},
            "promise_status": "fulfilled" if turn == 16 else "open",
            "due": {"day": 3, "band": 1}}


def thread_state(strategy):
    thread = getattr(strategy, "_thread", None)
    return {"message_count": len(thread or []), "utf8_bytes": len(_json(thread).encode()),
            "compaction_due": bool(getattr(strategy, "_compaction_due", False)),
            "thread": copy.deepcopy(thread)}


class FatalProviderStopped(RuntimeError):
    """Once any wire/admission failure occurs, there can be no more HTTP calls."""


class SemanticDeepSeekProvider(shared.BoundedDeepSeekProvider):
    """Reuse the existing exact-model transport and ledger, with full body audit.

    Headers and credentials are never recorded. Audit writes happen before each
    HTTP attempt and immediately after its response, including summary repairs.
    A failure latch prevents swallowed backstage failures from allowing more IO.
    """
    def __init__(self, api_key, *, api_budget, max_posts=MAX_POSTS):
        if not isinstance(api_budget, BudgetLedger):
            raise ValueError("an explicit shared BudgetLedger is required")
        config = api_budget.snapshot()["configuration"]
        if config["max_input_tokens"] != MAX_INPUT_TOKENS or config["max_output_tokens"] != MAX_OUTPUT_TOKENS:
            raise ValueError("benchmark ledger bounds must be 131072 input / 4096 output")
        super().__init__(api_key, api_budget=api_budget, max_posts=max_posts)
        self.wire_audit = []
        self.audit_sink = None
        self.fatal_error = None

    def _emit(self, event):
        self.wire_audit.append(event)
        if self.audit_sink:
            self.audit_sink(event)

    def _stage(self, body):
        messages = body.get("messages", [])
        system = str(messages[0].get("content", "")) if messages else ""
        repair = any("Your previous JSON did NOT conform" in str(m.get("content", "")) for m in messages)
        if "剧情摘要员" in system:
            name = "recap_recompression" if "多场景" in system else "recap_summary"
            return name + ("_repair" if repair else "")
        return self.phase

    @contextmanager
    def _observed_transport(self):
        original = shared._request_json
        def observed(url, headers, body=None):
            stage = "model_preflight" if body is None else self._stage(body)
            number = len(self.calls) if body is not None else None
            common = {"case": self.case_id, "post_number": number, "stage": stage}
            started = time.monotonic()
            self._emit({**common, "kind": "request", "at": _utc(), "url": url,
                        "method": "GET" if body is None else "POST", "body": copy.deepcopy(body)})
            try:
                response = original(url, headers, body)
            except Exception as error:
                self._emit({**common, "kind": "transport_failure", "at": _utc(),
                            "elapsed_seconds": time.monotonic() - started, "error_type": type(error).__name__})
                raise
            self._emit({**common, "kind": "response", "at": _utc(),
                        "elapsed_seconds": time.monotonic() - started, "body": copy.deepcopy(response)})
            return response
        with patch.object(shared, "_request_json", observed):
            yield

    def preflight(self):
        if self.fatal_error:
            raise FatalProviderStopped(self.fatal_error)
        try:
            with self._observed_transport():
                return super().preflight()
        except Exception as error:
            self.fatal_error = type(error).__name__
            raise

    def _post(self, url, headers, body, **kwargs):
        if self.fatal_error:
            raise FatalProviderStopped(self.fatal_error)
        try:
            with self._observed_transport():
                return super()._post(url, headers, {**body, "temperature": 0}, **kwargs)
        except Exception as error:
            self.fatal_error = type(error).__name__
            self._emit({"kind": "fatal_stop", "stage": self._stage(body), "case": self.case_id,
                        "at": _utc(), "error_type": self.fatal_error})
            raise


@contextmanager
def isolated_campaign():
    """Actual recap remains on; irrelevant optional plotting remains off."""
    def recap_only(*args, **kwargs):
        return digest_fleet(*args, **{**kwargs, "threshold": 10**12, "importance_provider": None})
    with ExitStack() as stack:
        for name in shared.BACKGROUND_HOOKS:
            stack.enter_context(patch("loop.turn." + name, recap_only if name == "digest_fleet" else lambda *a, **k: []))
        stack.enter_context(patch("loop.fleet.backstop_quests", lambda *a, **k: None))
        stack.enter_context(patch("loop.variation.prepare_variation", lambda *a, **k: None))
        for name, value in (("_conversation_mode", "multiturn"), ("_verbosity", "concise"),
                            ("_style", ""), ("_pack_voice", "")):
            stack.enter_context(patch.object(settings, name, value))
        stack.enter_context(patch.dict(os.environ, {"RPG_EMBEDDER": "none", "RPG_CASCADE_MODEL": "",
            "GLM_CASCADE_MODEL": "", "LANGFUSE_PUBLIC_KEY": "", "RPG_DEBUG_TRACE": ""}))
        yield


def _checks(row, initial):
    after, before, expected = row["after"], row["before"], row["expected"]
    record = after["promises"].get(PROMISE_ID, {})
    immutable = lambda r: {k: v for k, v in r.items() if k not in {"status", "fulfilled_at"}}
    clock = lambda s: (s["clock"]["day"], s["clock"]["band"])
    events = row["new_events"]
    transfers = [e["deltas"] for e in events if e["type"] == "item_transferred"]
    required_transfers = {3: [{"item": "brass_bell", "from": "player", "to": "keeper"}],
                          14: [{"item": "brass_bell", "from": "keeper", "to": "player"}],
                          16: [{"item": "jade_seal", "from": "player", "to": "keeper"}]}.get(row["turn"], [])
    actual_transfers = [{k: d.get(k) for k in ("item", "from", "to")} for d in transfers]
    fulfillment = [e for e in events if e["type"] == "item_return_fulfilled"]
    checks = {
        "turn_committed": row["result"] == "committed",
        "item_holders": after["holders"] == expected["holders"],
        "actor_and_npc_locations": after["locations"] == expected["locations"],
        "fixed_display_names": all(after["names"][eid] == name for eid, name in NAMES.items() if eid != "brass_bell")
            and after["names"]["brass_bell"] in ("铜铃", "雨燕"),
        "borrower_origin_unchanged": after["borrowed_from"] == "keeper",
        "one_immutable_promise": set(after["promises"]) == {PROMISE_ID}
            and immutable(record) == immutable(initial["promises"][PROMISE_ID]),
        "promise_status": record.get("status") == expected["promise_status"],
        "only_requested_item_transfers": actual_transfers == required_transfers,
        "fulfillment_only_at_physical_return": len(fulfillment) == (1 if row["turn"] == 16 else 0),
        "no_clock_regression": clock(after) >= clock(before),
        "no_guard_alias_knowledge": not after["guard_name_knowledge"],
        "no_private_canary_in_requests": not any(CANARY in _json(e.get("body")) for e in row["wire_audit"] if e["kind"] == "request"),
        "no_private_canary_in_prose": CANARY not in (row.get("final_narration") or "")
            and all(CANARY not in str(e.get("narration", "")) for e in row["strategy_audit"]),
    }
    if row["turn"] == 10:
        checks["explicit_wait_reaches_day2_morning"] = after["clock"] == {"day": 2, "band": 0}
    return checks


def _write_report(output, report):
    rows = report["turns"]
    report["counts"] = {"turns_completed": len(rows),
        "failed_turns": sum(any(not ok for ok in r.get("checks", {}).values()) for r in rows),
        "failed_checks": sum(not ok for r in rows for ok in r.get("checks", {}).values()),
        "refused_turns": sum(r["result"] == "refused" for r in rows),
        "execution_errors": sum(r["result"] == "error" for r in rows)}
    temp = output / "report.json.tmp"
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(output / "report.json")


def run_suite(output_dir, provider, *, strategy_factory=AuthorStrategy, arm="author"):
    """Run one arm only. No semantic fail-fast, rewinds, automatic reruns or A/B."""
    if not isinstance(provider, SemanticDeepSeekProvider) or not provider.preflight_ok:
        raise ValueError("preflighted SemanticDeepSeekProvider required")
    if provider.calls or provider.fatal_error:
        raise ValueError("each arm requires a fresh provider and unspent POST allowance")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    strategy = strategy_factory()
    report = {"status": "running", "arm": arm, "strategy": type(strategy).__name__,
        "started_at": _utc(), "model": MODEL, "endpoint": BASE_URL,
        "fixture_sha256": _hash(fixture_events()), "actions_sha256": _hash(ACTIONS),
        "fixture_kind": "synthetic prior history; typed promise seeded, NL registration excluded",
        "actions": list(ACTIONS), "display_names": NAMES, "fixture_events": fixture_events(),
        "limits": {"http_posts": provider.max_posts, "max_output_tokens": MAX_OUTPUT_TOKENS,
                   "max_input_tokens": MAX_INPUT_TOKENS, "temperature": 0, "http_retries": 0,
                   "narration_repairs_per_action": 1},
        "compaction": {"forced_before_turns": list(FORCED_COMPACTION_TURNS),
            "label": "test-stress cache compaction, not natural token-threshold evidence",
            "natural_threshold_tokens": CONTEXT_WINDOW * COMPACTION_RATIO},
        "preserved": ["multiturn AuthorStrategy", "digest_fleet scene recap summarization", "SQLite"],
        "disabled": [n for n in shared.BACKGROUND_HOOKS if n != "digest_fleet"]
            + ["arc reflection (threshold 1e12)", "importance LLM", "quest backstop", "variation", "embeddings", "tracing", "narrator tools"],
        "manual_review_status": "required", "manual_review": MANUAL_REVIEW,
        "turns": [], "calls": provider.calls, "wire_audit": provider.wire_audit,
        "budget_before": provider.api_budget.snapshot()}
    def persist_wire(event):
        with (output / "wire.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    for event in provider.wire_audit:
        persist_wire(event)
    provider.audit_sink = persist_wire
    audit = []
    original_produce, original_repair = type(strategy).produce, type(strategy).repair_sections
    def produce(self, *args, provider, **kwargs):
        provider.phase = "narration"
        commit = original_produce(self, *args, provider=provider, **kwargs)
        audit.append({"kind": "original_commit", "at": _utc(), "narration": commit.narration,
                      "sections": copy.deepcopy(commit.sections)})
        return commit
    def repair(self, failing_sections, errors, *, provider):
        provider.phase = "narration_repair"
        audit.append({"kind": "repair_request", "at": _utc(), "sections": sorted(failing_sections),
                      "errors": [asdict(e) for e in errors]})
        result = original_repair(self, failing_sections, errors, provider=provider)
        audit.append({"kind": "repair_response", "at": _utc(), "sections": copy.deepcopy(result),
                      "narration": result.get("narration")})
        return result
    _write_report(output, report)
    with isolated_campaign(), patch.object(type(strategy), "produce", produce), patch.object(type(strategy), "repair_sections", repair):
        engine = build_engine(output / "campaign", provider=provider)
        try:
            _seed(engine)
            initial = state(engine.world)
            report["initial_world"] = canonical_world(engine.world)
            for turn, action in enumerate(ACTIONS, 1):
                audit = []
                forced = turn in FORCED_COMPACTION_TURNS
                cache_before_force = thread_state(strategy)
                if forced:
                    strategy._compaction_due = True
                start = time.monotonic()
                prior_ids = {e["id"] for e in engine.store.iter_events()}
                wire_start, call_start = len(provider.wire_audit), len(provider.calls)
                provider.case_id, provider.phase = f"turn_{turn:02d}", "narration"
                row = {"turn": turn, "action": action, "started_at": _utc(), "expected": expected_state(turn),
                    "before": state(engine.world), "canonical_before": canonical_world(engine.world),
                    "thread_before_force": cache_before_force, "thread_before": thread_state(strategy),
                    "forced_compaction": forced, "strategy_audit": audit, "result": "running",
                    "final_narration": None, "repair_attempts": 0}
                report["in_progress_turn"] = row
                _write_report(output, report)
                try:
                    answer = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), action,
                        provider=provider, strategy=strategy, max_repairs=1, required_sections=REQUIRED_SECTIONS)
                    engine.world = answer.world
                    row.update(result="committed", final_narration=answer.narration,
                               repair_attempts=answer.repair_attempts, receipt=answer.receipt)
                except Exception as error:
                    row.update(result="refused" if isinstance(error, TurnRejected) else "error", error_type=type(error).__name__)
                row.update(after=state(engine.world), canonical_after=canonical_world(engine.world),
                    new_events=[e for e in engine.store.iter_events() if e["id"] not in prior_ids],
                    thread_after=thread_state(strategy), wire_audit=copy.deepcopy(provider.wire_audit[wire_start:]),
                    usage=shared._usage(provider.calls[call_start:]),
                    summaries=copy.deepcopy(engine.world["systems"]["narrative"]),
                    elapsed_seconds=time.monotonic() - start, ended_at=_utc())
                row["provenance_observations"] = {"borrowed_from_source_changed":
                    row["after"]["borrowed_from_sources"] != initial["borrowed_from_sources"],
                    "note": "Reasserting the same keeper origin is not itself a semantic failure."}
                row["checks"] = _checks(row, initial)
                row["failed_checks"] = [k for k, ok in row["checks"].items() if not ok]
                row["observed_guard_codes"] = sorted({e["code"] for a in audit if a["kind"] == "repair_request" for e in a["errors"]})
                if turn == 15:
                    row["recall_observation"] = {"contains_chosen_literal": "雨燕" in (row["final_narration"] or ""),
                        "contains_rejected_literal": "归雁" in (row["final_narration"] or ""),
                        "classification": "manual_review_required; uncertainty is a recall miss, not fabrication"}
                report["turns"].append(row)
                report.pop("in_progress_turn", None)
                report["usage"] = shared._usage(provider.calls)
                report["budget_after"] = provider.api_budget.snapshot()
                _write_report(output, report)
                if provider.fatal_error:
                    report["fatal_error"] = provider.fatal_error
                    break
        finally:
            engine.store.close()
            provider.audit_sink = None
    report["status"] = ("fatal_stopped" if provider.fatal_error else
        "machine_checks_failed_manual_review_required" if report["counts"]["failed_checks"] else
        "machine_checks_passed_manual_review_required")
    report["ended_at"] = _utc()
    _write_report(output, report)
    return report
