"""Opt-in one-arm production-profile semantic benchmark; no credentials/CLI defaults.

Call ProductionAuditProvider with the EXISTING session BudgetLedger, preflight(),
then run_suite(new_output_directory, provider, arm=...). No credential-file reads,
ledger creation, automatic reruns, or publication. Fixture is synthetic; this is
resumed gameplay, not bootstrap/character-creation coverage. The old forced-
compaction benchmark remains a separate stress profile.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import hashlib
import json
import os
import errno
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import build_engine
from app import play
from engine import settings
from engine.oracle import load_pack_manifest
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import DeepSeekProvider, _norm_usage
from loop import return_intent
from loop.strategy import AuthorStrategy, COMPACTION_RATIO, CONTEXT_WINDOW
from loop.turn import TurnRejected
from scripts.api_budget import BudgetLedger
from scripts import benchmark_semantic_campaign as frozen
from scripts.verify_deepseek_resources import _NoRedirect, _usage

MODEL, BASE_URL = frozen.MODEL, frozen.BASE_URL
MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS = 1048576, 16384
MAX_REPAIRS, MAX_TOOL_ROUNDS = 3, 3  # run-deepseek.sh, not generic app defaults
CAMPAIGN_SEED, FLAVOR = 72916001, "isekai"
ACTIONS = frozen.ACTIONS
_utc, _json, _hash = frozen._utc, frozen._json, frozen._hash


class FatalProviderStopped(RuntimeError):
    """A prior transport/admission failure forbids all subsequent wire calls."""


def _request_json(url, headers, body=None):
    """One official request; default JSON serialization matches BudgetLedger."""
    request = urllib.request.Request(url, headers=headers,
        method="GET" if body is None else "POST",
        data=None if body is None else json.dumps(body, allow_nan=False).encode("utf-8"))
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=600) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_transport_details(error):
    """Diagnose standard network failures without serializing arbitrary exception text."""
    reason = getattr(error, "reason", error)
    details = {"exception_class": type(error).__name__, "reason_class": type(reason).__name__}
    number = getattr(reason, "errno", None)
    if type(number) is int:
        details["reason_errno"] = number
        details["errno_name"] = errno.errorcode.get(number, "platform_specific")
    code = getattr(error, "code", None)
    if type(code) is int:
        details["http_status"] = code
    # Never store raw exception strings: URLs/proxy tokens can appear in them.
    message = str(reason)
    tunnel = re.fullmatch(r"Tunnel connection failed: ([0-9]{3}) [A-Za-z ]{1,80}", message)
    if tunnel:
        details.update(category="proxy_tunnel_failure", http_status=int(tunnel.group(1)))
    elif isinstance(reason, TimeoutError) or "timed out" in message.lower():
        details["category"] = "timeout"
    elif "name or service not known" in message.lower() or "name resolution" in message.lower():
        details["category"] = "name_resolution_failure"
    elif "certificate verify failed" in message.lower():
        details["category"] = "certificate_verification_failure"
    elif isinstance(reason, ConnectionError):
        details["category"] = "connection_failure"
    else:
        details["category"] = "unclassified_reason_text_omitted"
    return details


class ProductionAuditProvider(DeepSeekProvider):
    """Normal 16k native-tools provider with a shared, durable session CNY guard.

    No total request cap or output clamp. Caller-requested smaller max_tokens are
    preserved. Concurrent cascade calls retain their normal concurrency. Admission is locked;
    after a fatal failure, already-admitted calls finish/account but no new call
    is admitted. The shared ledger reserves their combined worst-case cost.
    """
    def __init__(self, api_key, *, api_budget):
        if not isinstance(api_budget, BudgetLedger):
            raise ValueError("an explicit existing session BudgetLedger is required")
        config = api_budget.snapshot()["configuration"]
        if (config["max_input_tokens"], config["max_output_tokens"]) != (MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS):
            raise ValueError("production ledger bounds must be 1048576 input / 16384 output")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("an explicit nonempty API key is required")
        super().__init__(MODEL, api_key, BASE_URL, thinking="disabled")
        self.api_budget, self.calls, self.wire_audit = api_budget, [], []
        self.audit_sink = self.fatal_error = self.case_id = None
        self.phase, self.preflight_ok = "startup", False
        self._wire_lock = threading.RLock()

    def _emit(self, event):
        with self._wire_lock:
            self.wire_audit.append(event)
            if self.audit_sink:
                self.audit_sink(event)

    def _validate(self, url, body=None):
        if self.fatal_error:
            raise FatalProviderStopped(self.fatal_error)
        if self.model != MODEL or self.base_url != BASE_URL or self.thinking != "disabled":
            raise ValueError("only the official deepseek-flash disabled-thinking profile is allowed")
        endpoint = BASE_URL + ("/models" if body is None else "/chat/completions")
        if url != endpoint or (body is not None and body.get("model") != MODEL):
            raise ValueError("only the official endpoint and exact deepseek-flash model are allowed")

    def _stage(self, body):
        system = "\n".join(str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system")
        for marker, stage in (("归还承诺意图分类器", "return_intent"),
                ("只解析玩家本次行动", "resource_intent"), ("剧情摘要员", "recap"),
                ("叙事分析师", "reflection"), ("hidden quest skeletons", "density"),
                ("父地点刚发生", "cascade"), ("角色/地点状态推演模块", "catchup"),
                ("剧情设计师", "lore_resequence")):
            if marker in system:
                return stage
        return self.phase

    def _wire(self, url, headers, body=None, number=None):
        stage = "model_preflight" if body is None else self._stage(body)
        common = {"case": self.case_id, "post_number": number, "stage": stage,
                  "player_facing_context": self.phase in {"narration", "narration_repair"}}
        start = time.monotonic()
        self._emit({**common, "kind": "request", "at": _utc(), "url": url,
                    "method": "GET" if body is None else "POST", "body": copy.deepcopy(body)})
        try:
            response = _request_json(url, headers, body)
        except Exception as error:
            with self._wire_lock:
                self._stop(error)
                self._emit({**common, "kind": "transport_failure", "at": _utc(),
                            "elapsed_seconds": time.monotonic() - start, "error_type": type(error).__name__,
                            "safe_transport_details": _safe_transport_details(error)})
            raise
        self._emit({**common, "kind": "response", "at": _utc(), "body": copy.deepcopy(response),
                    "elapsed_seconds": time.monotonic() - start})
        return response

    def _stop(self, error):
        with self._wire_lock:
            if not self.fatal_error:
                self.fatal_error = type(error).__name__
                self._emit({"kind": "fatal_stop", "case": self.case_id, "stage": self.phase,
                            "at": _utc(), "error_type": self.fatal_error})

    def preflight(self):
        with self._wire_lock:
            try:
                url = BASE_URL + "/models"
                self._validate(url)  # Validate BEFORE constructing/sending auth.
                response = self._wire(url, {"Authorization": "Bearer " + self.api_key})
                if not any(item.get("id") == MODEL for item in response.get("data", [])):
                    raise ValueError("exact deepseek-flash model unavailable; no fallback")
                self.preflight_ok = True
                return response
            except Exception as error:
                self._stop(error)
                raise

    def _post(self, url, headers, body, **kwargs):
        entry = None
        try:
            with self._wire_lock:
                try:
                    self._validate(url, body)
                    if not self.preflight_ok:
                        raise RuntimeError("exact-model /models preflight is required")
                    body = self._prepare_body(copy.deepcopy(body))
                    reservation = self.api_budget.reserve(body)
                    entry = {"number": len(self.calls) + 1, "case": self.case_id,
                             "phase": self.phase, "stage": self._stage(body), "model": MODEL,
                             "max_output_tokens": body["max_tokens"], "status": "attempted",
                             "budget_reservation": reservation}
                    self.calls.append(entry)
                except Exception as error:
                    self._stop(error)
                    raise
            response = self._wire(url, headers, body, entry["number"])
            entry.update(status="received", usage=response.get("usage"), response_model=response.get("model"))
            self.api_budget.reconcile(reservation, response.get("usage"))
            self.last_usage = _norm_usage(response.get("usage") or {})
            choice = response["choices"][0]
            entry.update(raw_message=copy.deepcopy(choice["message"]),
                         finish_reason=choice.get("finish_reason"))
            return response  # Native tool calls may legitimately have content=None.
        except Exception as error:
            if entry is not None:
                entry.update(status="failed", error_type=type(error).__name__)
            self._stop(error)
            raise


@contextmanager
def _phase(provider, name):
    old = provider.phase
    provider.phase = name
    try:
        yield
    finally:
        provider.phase = old


@contextmanager
def production_profile():
    """Only match launcher settings, disable external IO, and retain all hooks."""
    with ExitStack() as stack:
        for name, value in (("_conversation_mode", "multiturn"), ("_verbosity", "concise"),
                ("_max_tool_rounds", MAX_TOOL_ROUNDS), ("_style", ""),
                ("_pack_voice", load_pack_manifest(FLAVOR).get("voice", ""))):
            stack.enter_context(patch.object(settings, name, value))
        stack.enter_context(patch.dict(os.environ, {"RPG_EMBEDDER": "none",
            "RPG_CASCADE_MODEL": MODEL, "GLM_CASCADE_MODEL": "", "RPG_RESOURCE_RULES": "1",
            "LANGFUSE_PUBLIC_KEY": "", "RPG_DEBUG_TRACE": ""}))
        yield


def fixture_events():
    return frozen.fixture_events() + [kernel_event("campaign_seeded", id="fixture_production_seed",
        day=1, scene="inn", turn=0, summary="fixed synthetic production-profile campaign seed",
        deltas={"campaign_seed": CAMPAIGN_SEED, "flavor": FLAVOR})]


def source_snapshot():
    root = Path(__file__).resolve().parents[1]
    paths = [root / "run-deepseek.sh"]
    for directory in ("app", "context", "engine", "facts", "kernel", "llm", "loop", "memory", "systems", "scripts", "data"):
        paths += [p for p in (root / directory).rglob("*") if p.is_file() and p.suffix in {".py", ".json"}]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True).stdout.strip()
    return {"git_head": head, "files_sha256": hashes, "source_sha256": _hash(hashes)}


def _checks(row, initial):
    checks = frozen._checks(row, initial)
    checks.pop("no_private_canary_in_requests")
    checks["no_private_canary_in_player_facing_requests"] = not any(
        frozen.CANARY in _json(e.get("body")) for e in row["wire_audit"]
        if e["kind"] == "request" and e.get("player_facing_context"))
    return checks


def run_suite(output_dir, provider, *, arm="production"):
    """Exactly one continuous play_loop; finish all 16 inputs unless fatal IO."""
    if not isinstance(provider, ProductionAuditProvider) or not provider.preflight_ok:
        raise ValueError("preflighted ProductionAuditProvider required")
    if provider.calls or provider.fatal_error:
        raise ValueError("each arm requires a fresh provider sharing the session ledger")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "arm": arm, "started_at": _utc(), "source": source_snapshot(),
        "model": MODEL, "endpoint": BASE_URL, "actions": list(ACTIONS),
        "fixture_sha256": _hash(frozen.fixture_events()), "effective_fixture_sha256": _hash(fixture_events()),
        "actions_sha256": _hash(ACTIONS), "fixture_events": fixture_events(), "display_names": frozen.NAMES,
        "fixture_kind": "synthetic prior history; typed promise seeded; no bootstrap or natural-language registration coverage",
        "profile": {"source": "run-deepseek.sh", "conversation_mode": "multiturn", "verbosity": "concise",
            "style": "flavor default", "flavor": FLAVOR, "campaign_seed": CAMPAIGN_SEED,
            "play_loop_invocations": 1, "cascade_provider": "same audited main-provider instance",
            "all_normal_backstage_hooks_enabled": True, "native_tools": True,
            "no_guarantee_all_hooks_trigger": "fixture has no L2 town/genesis quests; enablement is not exercise coverage"},
        "limits": {"http_posts": None, "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_input_tokens": MAX_INPUT_TOKENS, "max_repairs": MAX_REPAIRS,
            "max_tool_rounds": MAX_TOOL_ROUNDS, "temperature_override": None,
            "http_retries": 0, "http_timeout_seconds": 600, "redirects": 0},
        "safety_exceptions": ["shared strict session CNY ledger; no per-arm reset", "no transport retries",
            "fatal latch blocks new admissions; already-admitted concurrent calls finish and stay charged"],
        "compaction": {"forced_before_turns": [], "natural_threshold_tokens": CONTEXT_WINDOW * COMPACTION_RATIO},
        "disabled": ["embeddings", "external tracing"], "manual_review_status": "required",
        "manual_review": frozen.MANUAL_REVIEW + [
            "Checks are strict fixture-scenario expectations, not an automatic semantic verdict. Legitimate authored backstage changes can diverge from the fixture.",
            "Compare original/repair/final prose and canonical events; distinguish unauthorized contradiction from justified world evolution.",
            "The secret canary in legitimate NPC-scoped backstage context is not player leakage. Review all raw bodies; player-facing author/tool requests are checked separately."],
        "turns": [], "calls": provider.calls, "wire_audit": provider.wire_audit,
        "budget_before": provider.api_budget.snapshot()}

    def persist(event):
        with (output / "wire.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    for event in provider.wire_audit:
        persist(event)
    provider.audit_sink = persist
    strategy, row, engine = AuthorStrategy(), None, None
    original_produce, original_repair = AuthorStrategy.produce, AuthorStrategy.repair_sections
    original_turn, original_extract = play.run_turn, return_intent.extract_return_intent

    def produce(self, *args, provider, **kwargs):
        with _phase(provider, "narration"):
            result = original_produce(self, *args, provider=provider, **kwargs)
        row["strategy_audit"].append({"kind": "original_commit", "at": _utc(),
            "narration": result.narration, "sections": copy.deepcopy(result.sections)})
        return result

    def repair(self, failing_sections, errors, *, provider):
        row["strategy_audit"].append({"kind": "repair_request", "at": _utc(),
            "sections": sorted(failing_sections), "errors": [asdict(e) for e in errors]})
        with _phase(provider, "narration_repair"):
            result = original_repair(self, failing_sections, errors, provider=provider)
        row["strategy_audit"].append({"kind": "repair_response", "at": _utc(),
            "sections": copy.deepcopy(result), "narration": result.get("narration")})
        return result

    def observed_extract(*args, **kwargs):
        try:
            with _phase(provider, "return_intent"):
                intent = original_extract(*args, **kwargs)
            row["intent"] = copy.deepcopy(intent)
            if intent["status"] == "clarify":
                row["result"] = "clarified"
            return intent
        except Exception as error:
            row.update(result="error", error_type=type(error).__name__, error_stage="return_intent")
            raise

    def observed_turn(*args, **kwargs):
        row.update(gateway_player_input=args[4], gateway_return_commitment=copy.deepcopy(kwargs.get("return_commitment")),
                   prev_scene=copy.deepcopy(kwargs.get("prev_scene")))
        try:
            with _phase(provider, "gateway_or_backstage"):
                result = original_turn(*args, **kwargs)
            row.update(result="committed", final_narration=result.narration,
                final_sections=copy.deepcopy(result.commit.sections), repair_attempts=result.repair_attempts,
                receipt=result.receipt, dropped_sections=result.dropped_sections)
            return result
        except Exception as error:
            row.update(result="refused" if isinstance(error, TurnRejected) else "error",
                       error_type=type(error).__name__, error_stage="gateway")
            raise

    def inputs():
        nonlocal row
        initial = frozen.state(engine.world)
        for turn, action in enumerate(ACTIONS, 1):
            if provider.fatal_error:
                break
            provider.case_id, provider.phase = f"turn_{turn:02d}", "input"
            start, call_start, wire_start = time.monotonic(), len(provider.calls), len(provider.wire_audit)
            prior_ids = {e["id"] for e in engine.store.iter_events()}
            row = {"turn": turn, "action": action, "started_at": _utc(), "expected": frozen.expected_state(turn),
                "before": frozen.state(engine.world), "canonical_before": frozen.canonical_world(engine.world),
                "thread_before": frozen.thread_state(strategy), "pending_before": copy.deepcopy(engine.pending_return_intent),
                "forced_compaction": False, "strategy_audit": [], "ui_output": [], "intent": None,
                "result": "unresolved", "final_narration": None, "repair_attempts": 0}
            report["in_progress_turn"] = row
            frozen._write_report(output, report)
            yield action
            row.update(after=frozen.state(engine.world), canonical_after=frozen.canonical_world(engine.world),
                new_events=[e for e in engine.store.iter_events() if e["id"] not in prior_ids],
                thread_after=frozen.thread_state(strategy), pending_after=copy.deepcopy(engine.pending_return_intent),
                wire_audit=copy.deepcopy(provider.wire_audit[wire_start:]), usage=_usage(provider.calls[call_start:]),
                summaries=copy.deepcopy(engine.world["systems"]["narrative"]),
                elapsed_seconds=time.monotonic() - start, ended_at=_utc())
            row["checks"] = _checks(row, initial)
            row["checks_kind"] = "strict fixture-scenario checks; manually review backstage divergence"
            row["failed_checks"] = [name for name, ok in row["checks"].items() if not ok]
            row["observed_guard_codes"] = sorted({e["code"] for a in row["strategy_audit"] if a["kind"] == "repair_request" for e in a["errors"]})
            report["turns"].append(row)
            report.pop("in_progress_turn", None)
            report.update(usage=_usage(provider.calls), budget_after=provider.api_budget.snapshot())
            frozen._write_report(output, report)

    try:
        with production_profile(), patch.object(AuthorStrategy, "produce", produce), \
                patch.object(AuthorStrategy, "repair_sections", repair), \
                patch.object(play, "run_turn", observed_turn), \
                patch.object(return_intent, "extract_return_intent", observed_extract):
            # Avoid constructing an unguarded second provider. The normal cascade
            # model is restored immediately, then every route shares this identity.
            with patch.dict(os.environ, {"RPG_CASCADE_MODEL": ""}):
                engine = build_engine(output / "campaign", provider=provider)
            engine.cascade_provider = provider
            engine.store.append_many(fixture_events())
            engine.world = project(engine.registry, engine.store.iter_events())
            engine.world["_revision"] = engine.store.revision
            engine.campaign_seed = CAMPAIGN_SEED
            report["initial_world"] = frozen.canonical_world(engine.world)
            report["profile"]["effective_style"] = settings.get_style()
            play.play_loop(engine, inputs(), strategy=strategy, out=lambda line: row["ui_output"].append(line),
                max_repairs=MAX_REPAIRS, transcript_path=output / "transcript.jsonl")
    except Exception as error:
        report["execution_error"] = type(error).__name__
    finally:
        if engine is not None:
            report["final_world"] = frozen.canonical_world(engine.world)
            report["all_events"] = list(engine.store.iter_events())
            engine.store.close()
        provider.audit_sink = None
        report.update(usage=_usage(provider.calls), budget_after=provider.api_budget.snapshot(), ended_at=_utc())
        report["fatal_error"] = provider.fatal_error
        report["status"] = "fatal_stopped" if provider.fatal_error else (
            "execution_failed" if "execution_error" in report else "completed_manual_review_required")
        frozen._write_report(output, report)
    return report
