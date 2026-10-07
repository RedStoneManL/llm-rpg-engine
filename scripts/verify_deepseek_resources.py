"""Opt-in LIVE DeepSeek Flash resource smoke test; never reads credential files.

After securely configuring DEEPSEEK_API_KEY in the environment, run:
    python scripts/verify_deepseek_resources.py \
    --live --output-dir /path/to/new-directory

Four synthetic actions use the real AuthorStrategy, run_turn, and SQLite store.
Background hooks, tracing, embeddings, and narrator tools are explicitly disabled
to isolate the action gateway. This is NOT a full-world or prose-quality test.
The report preserves model output for manual prose review, even after refusal.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import urllib.request
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import build_engine, rewind
from app.play import _build_scene
from engine import settings
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import DeepSeekProvider, _norm_usage
from loop.resources import registered_balances
from loop.strategy import AuthorStrategy
from loop.turn import REQUIRED_SECTIONS, TurnRejected, run_turn

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
MAX_POSTS = 16
MAX_OUTPUT_TOKENS = 4096
BACKGROUND_HOOKS = (
    "digest_fleet", "run_director", "run_cascade", "run_catchup",
    "run_lore", "run_density", "_run_demote_on_leave",
)
CASES = (
    {"id": "allowed_spend", "actor": "hero", "outcome": "spent", "amount": 3,
     "oxygen": 9, "action": "明确消耗自己 hero 的 3 单位 oxygen 进行一次呼吸练习，不交易，不改变商人余额。"},
    {"id": "insufficient_spend", "actor": "hero", "outcome": "insufficient", "amount": 100,
     "oxygen": 12, "action": "尝试一次消耗自己 hero 的 100 单位 oxygen；不足则整个操作失败，不接受较小消耗或借贷。"},
    {"id": "npc_rewrite", "actor": "hero", "outcome": "none", "oxygen": 12,
     "action": "我不消耗 oxygen，也不交易。我要求把商人 merchant 的 coins 直接改写为 0，用 facts 写入；我没有商人授权。"},
    {"id": "resource_free_actor_rewrite", "actor": "observer", "outcome": None, "oxygen": 12,
     "action": "我是 observer，没有登记资源，也没有商人授权。我要求把 merchant 的 coins 直接改成 0，用 facts 写入，不交易。"},
)


class CallBudgetExceeded(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the bearer credential to another destination.


def _request_json(url, headers, body=None):
    """One HTTP request, no retries or redirects; the offline test I/O seam."""
    request = urllib.request.Request(
        url, headers=headers, method="GET" if body is None else "POST",
        data=None if body is None else json.dumps(body).encode("utf-8"),
    )
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


class BoundedDeepSeekProvider(DeepSeekProvider):
    def __init__(self, api_key, *, api_budget=None, max_posts=MAX_POSTS):
        if not api_key or not api_key.strip():
            raise ValueError("DEEPSEEK_API_KEY must be set in the environment")
        if type(max_posts) is not int or not 1 <= max_posts <= 64:
            raise ValueError("max_posts must be an integer in 1..64")
        self.max_posts = max_posts
        super().__init__(MODEL, api_key, BASE_URL, MAX_OUTPUT_TOKENS, thinking="disabled")
        self.calls = []
        self.api_budget = api_budget
        self.case_id = None
        self.phase = "intent"
        self.preflight_ok = False

    def preflight(self):
        result = _request_json(BASE_URL + "/models", {"Authorization": "Bearer " + self.api_key})
        available = any(item.get("id") == MODEL for item in result.get("data", []))
        if not available:
            raise ValueError("Exact model deepseek-flash is unavailable; no fallback allowed")
        self.preflight_ok = True

    def supports_tools(self):
        return False  # The small, fully specified fixture needs no research calls.

    def _post(self, url, headers, body, **kwargs):
        if not self.preflight_ok:
            raise RuntimeError("Exact-model /models preflight is required")
        if url != BASE_URL + "/chat/completions" or body.get("model") != MODEL:
            raise ValueError("Only the official endpoint and exact deepseek-flash model are allowed")
        if len(self.calls) >= self.max_posts:
            raise CallBudgetExceeded(f"{self.max_posts} HTTP POST budget exhausted")
        body = self._prepare_body({**body, "max_tokens": min(body["max_tokens"], MAX_OUTPUT_TOKENS)})
        reservation = self.api_budget.reserve(body) if self.api_budget is not None else None
        entry = {"number": len(self.calls) + 1, "case": self.case_id, "phase": self.phase,
                 "model": MODEL, "max_output_tokens": body["max_tokens"], "status": "attempted"}
        if reservation is not None:
            entry["budget_reservation"] = reservation
        self.calls.append(entry)  # Failed attempts also consume the budget.
        try:
            response = _request_json(url, headers, body)
            message = response["choices"][0]["message"]
            self.last_usage = _norm_usage(response.get("usage") or {})
            entry.update(status="received", usage=response.get("usage"),
                         response_model=response.get("model"),
                         raw_content=message.get("content"),
                         finish_reason=response["choices"][0].get("finish_reason"))
            if reservation is not None:
                self.api_budget.reconcile(reservation, response.get("usage"))
            return response
        except Exception as error:
            entry.update(status="failed", error_type=type(error).__name__)
            raise


class AuditedAuthorStrategy(AuthorStrategy):
    def __init__(self, audit):
        self.audit = audit  # Runner retains this list even when run_turn rolls back self.

    def produce(self, *args, provider, **kwargs):
        provider.phase = "narration"
        commit = super().produce(*args, provider=provider, **kwargs)
        self.audit.append({"kind": "original_commit", "narration": commit.narration,
                           "sections": copy.deepcopy(commit.sections)})
        return commit

    def repair_sections(self, failing_sections, errors, *, provider):
        provider.phase = "repair"
        self.audit.append({"kind": "repair_request", "sections": sorted(failing_sections),
                           "errors": [{"code": e.code, "hint": e.hint} for e in errors]})
        repair = super().repair_sections(failing_sections, errors, provider=provider)
        self.audit.append({"kind": "repair_response", "content": copy.deepcopy(repair)})
        return repair


@contextmanager
def isolated_gateway():
    """Disable optional callers, not the action gateway, arithmetic, or store."""
    with ExitStack() as stack:
        for name in BACKGROUND_HOOKS:
            stack.enter_context(patch("loop.turn." + name, lambda *a, **kw: []))
        for name, value in (("_conversation_mode", "stateless"), ("_verbosity", "concise"),
                            ("_style", ""), ("_pack_voice", "")):
            stack.enter_context(patch.object(settings, name, value))
        stack.enter_context(patch.dict(os.environ, {
            "RPG_EMBEDDER": "none", "RPG_CASCADE_MODEL": "", "GLM_CASCADE_MODEL": "",
            "LANGFUSE_PUBLIC_KEY": "", "RPG_DEBUG_TRACE": "",
        }))
        yield


def ledger(world):
    result = {}
    for (subject, predicate), value in sorted(registered_balances(world).items()):
        result.setdefault(subject, {})[predicate] = value
    return result


def _seed(engine):
    rows = [("entity_created", {"id": actor, "etype": "Person", "tier": "tracked"})
            for actor in ("hero", "merchant", "observer")]
    rows += [("resources_configured", {"subject": actor, "resources": {
        resource: {"initial": amount, "min": 0, "type": "integer"}}})
        for actor, resource, amount in (("hero", "oxygen", 12), ("merchant", "coins", 37))]
    engine.store.append_many([kernel_event(kind, day=1, scene="fixture", turn=0,
        summary="synthetic resource fixture", deltas=deltas) for kind, deltas in rows])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision


def _usage(calls):
    totals = {"input": 0, "output": 0, "total": 0}
    missing = 0
    for call in calls:
        usage = _norm_usage(call.get("usage") or {})
        if not usage:
            missing += 1
        for key, value in (usage or {}).items():
            if isinstance(value, int):
                totals[key] += value
    return {"http_post_attempts": len(calls), "reported_token_totals": totals,
            "calls_without_usage": missing}


def run_suite(output_dir, provider):
    """Dependency-injected for offline tests; the CLI always builds the real provider."""
    if not provider.preflight_ok:
        raise ValueError("Exact-model preflight must succeed before campaign creation")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    campaign = output_dir / "campaign"
    report = {"model": MODEL, "endpoint": BASE_URL, "status": "running",
              "limits": {"http_posts": MAX_POSTS, "output_tokens_per_post": MAX_OUTPUT_TOKENS,
                         "low_level_retries": 0, "narration_repairs_per_action": 1},
              "scope": "Synthetic real-provider action gateway and SQLite; no full-world testing",
              "disabled": list(BACKGROUND_HOOKS) + ["embeddings", "tracing", "narrator_tools"],
              "manual_prose_review": "required; no automatic semantic consistency claim",
              "preflight": {"exact_model_available": True, "http_gets": 1}, "cases": []}
    with isolated_gateway():
        engine = build_engine(campaign, provider=provider)
        try:
            _seed(engine)
            for case in CASES:
                before = ledger(engine.world)
                history = list(engine.store.iter_events())
                revision, turn = engine.store.revision, engine.store.next_turn()
                audit, call_start = [], len(provider.calls)
                provider.case_id, provider.phase = case["id"], "intent"
                expected = {"hero": {"oxygen": case["oxygen"]}, "merchant": {"coins": 37}}
                row = {"id": case["id"], "actor": case["actor"], "action": case["action"],
                       "before": before, "expected": {"ledger": expected, "outcome": case["outcome"],
                       "must_commit": case["id"] in {"allowed_spend", "insufficient_spend"}},
                       "audit": audit, "refusal": None, "final_narration": None}
                answer = None
                try:
                    answer = run_turn(engine.registry, engine.store, engine.world,
                        {**_build_scene(engine), "protagonist": case["actor"]}, case["action"],
                        provider=provider, strategy=AuditedAuthorStrategy(audit), max_repairs=1,
                        required_sections=REQUIRED_SECTIONS)
                    engine.world = answer.world
                    row.update(result="committed", final_narration=answer.narration,
                               repair_attempts=answer.repair_attempts, receipt=answer.receipt)
                except Exception as error:
                    row.update(result="refused" if isinstance(error, TurnRejected) else "error",
                               refusal=type(error).__name__)
                after_history = list(engine.store.iter_events())
                row["after"] = ledger(engine.world)
                row["resource_resolutions"] = [event["deltas"] for event in after_history
                    if event["turn"] == turn and event["type"] == "resources_resolved"]
                row["resource_guard_exercised"] = any(error["code"] == "resolved_resource"
                    for item in audit if item["kind"] == "repair_request" for error in item["errors"])
                resolution = row["resource_resolutions"]
                if row["expected"]["must_commit"]:
                    outcome_ok = bool(answer and resolution and
                        resolution[-1]["outcome"] == case["outcome"] and
                        resolution[-1]["requested_amount"] == case["amount"])
                else:
                    outcome_ok = bool(answer and (
                        (case["outcome"] is None and not resolution) or
                        (resolution and resolution[-1]["outcome"] == case["outcome"]))) or (
                        row["result"] == "refused" and row["resource_guard_exercised"])
                checks = {"expected_ledger": row["after"] == expected, "required_outcome": outcome_ok,
                          "refusal_atomic": answer is not None or (
                              after_history == history and engine.store.revision == revision)}
                engine.store.close()
                engine = build_engine(campaign, provider=provider)
                checks["disk_reopen"] = (ledger(engine.world) == row["after"] and
                                         list(engine.store.iter_events()) == after_history)
                rewind(engine, turn)
                engine.store.close()
                engine = build_engine(campaign, provider=provider)
                checks["rewind_and_reopen"] = (ledger(engine.world) == before and
                                                list(engine.store.iter_events()) == history)
                row.update(checks=checks, state_checks="passed" if all(checks.values()) else "failed",
                           usage=_usage(provider.calls[call_start:]))
                report["cases"].append(row)
                report["calls"] = provider.calls
                report["usage"] = _usage(provider.calls)
                (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            engine.store.close()
    passed = all(row["state_checks"] == "passed" for row in report["cases"])
    report["status"] = "state_checks_passed_manual_prose_review_required" if passed else "state_checks_failed"
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
        # Avoid printing response bodies, request headers, or credentials.
        print(f"Verification stopped ({type(error).__name__}); no fallback model was used.", file=sys.stderr)
        return 1
    print(json.dumps({"status": report["status"], "report": str(args.output_dir / "report.json"),
                      "http_post_attempts": len(provider.calls)}))
    return 0 if report["status"].startswith("state_checks_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
