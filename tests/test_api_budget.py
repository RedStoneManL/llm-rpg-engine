"""Offline spend-guard tests: no credentials, provider, or network access."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from decimal import Decimal, localcontext
import json
import os

import pytest

from scripts.api_budget import BudgetExceeded, BudgetLedger, BudgetValidationError


RATES = dict(input_cny_per_million=Decimal("2"), output_cny_per_million=Decimal("8"),
             prior_known_cny=Decimal("0.091914"), uncertainty_reserve_cny=Decimal("5"),
             pricing_checked_at="2026-10-07")
BODY = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "合成测试"}],
        "max_tokens": 4096, "thinking": {"type": "disabled"}}
USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}


def ledger(tmp_path, **overrides):
    return BudgetLedger(tmp_path / "budget.json", **(RATES | overrides))


def cost(body=BODY):
    return (Decimal(len(json.dumps(body).encode("utf-8")) + 4096) * 2
            + Decimal(body["max_tokens"]) * 8) / 1000000


def test_reservation_is_persisted_before_return_and_contains_no_body(tmp_path):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    data = json.loads(budget.path.read_text())
    row = data["reservations"][reservation]
    assert row["status"] == "pending"
    assert row["body_bytes"] == len(json.dumps(BODY).encode("utf-8"))
    assert row["input_tokens"] == row["body_bytes"] + 4096
    assert Decimal(row["reserved_cny"]) == cost()
    assert "messages" not in budget.path.read_text() and "合成测试" not in budget.path.read_text()
    assert "thinking" not in budget.path.read_text() and "content" not in budget.path.read_text()
    assert os.stat(budget.path).st_mode & 0o777 == 0o600
    snapshot = budget.snapshot()
    assert Decimal(snapshot["conservative_total_cny"]) == Decimal("5.091914") + cost()
    assert snapshot["pending_count"] == 1
    assert ledger(tmp_path).snapshot() == snapshot


def test_verified_usage_reconciles_once_ignoring_reasoning_breakdown(tmp_path):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    raw = USAGE | {"completion_tokens_details": {"reasoning_tokens": 12}, "prompt_cache_hit_tokens": 90}
    assert budget.reconcile(reservation, raw)
    snapshot = budget.snapshot()
    assert Decimal(snapshot["reconciled_cny"]) == Decimal("0.00036")
    assert Decimal(snapshot["conservative_total_cny"]) == Decimal("5.092274")
    assert snapshot["pending_count"] == 0 and snapshot["reconciled_count"] == 1
    assert budget.reconcile(reservation, raw)
    assert budget.snapshot() == snapshot
    with pytest.raises(BudgetValidationError, match="conflicting"):
        budget.reconcile(reservation, {"prompt_tokens": 1, "completion_tokens": 1})
    assert budget.snapshot() == snapshot


@pytest.mark.parametrize("usage", [None, {}, [], {"prompt_tokens": 1},
    {"prompt_tokens": 1.0, "completion_tokens": 2},
    {"prompt_tokens": True, "completion_tokens": 2},
    {"prompt_tokens": "1", "completion_tokens": 2},
    {"prompt_tokens": -1, "completion_tokens": 2},
    {"prompt_tokens": 1, "completion_tokens": False},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4},
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3.0}])
def test_missing_or_invalid_usage_retains_full_failed_attempt_charge(tmp_path, usage):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    before = budget.snapshot()
    assert budget.reconcile(reservation, usage) is False
    assert ledger(tmp_path).snapshot() == before


def test_transport_failure_crash_has_no_refund_and_survives_restart(tmp_path):
    first = ledger(tmp_path)
    first.reserve(BODY)
    del first  # Simulate caller failure without final usage or cleanup.
    second = ledger(tmp_path)
    second.reserve(BODY)
    assert second.snapshot()["pending_count"] == 2
    assert Decimal(second.snapshot()["pending_cny"]) == 2 * cost()


@pytest.mark.parametrize("usage", [{"prompt_tokens": 131073, "completion_tokens": 1},
                                   {"prompt_tokens": 1, "completion_tokens": 4097}])
def test_reported_overrun_blocks_restarts_and_preserves_original_reservation(tmp_path, usage):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    with pytest.raises(BudgetExceeded, match="persistently blocked"):
        budget.reconcile(reservation, usage)
    snapshot = budget.snapshot()
    assert snapshot["blocked"] and snapshot["overrun_count"] == 1
    assert Decimal(snapshot["overrun_cny"]) == cost()
    with pytest.raises(BudgetExceeded, match="blocked"):
        ledger(tmp_path).reserve(BODY)
    with pytest.raises(BudgetExceeded, match="remains blocked"):
        budget.reconcile(reservation, usage)


def test_exact_ceiling_rejected_and_no_reservation_written(tmp_path):
    budget = ledger(tmp_path, prior_known_cny=Decimal("45") - Decimal("5") - cost())
    with pytest.raises(BudgetExceeded, match="reach or exceed"):
        budget.reserve(BODY)
    assert budget.snapshot()["reservation_count"] == 0


def test_exhaustion_counts_prior_uncertainty_and_failed_pending(tmp_path):
    budget = ledger(tmp_path, ceiling_cny=Decimal("5.091914") + cost() * 2)
    budget.reserve(BODY)
    with pytest.raises(BudgetExceeded):
        budget.reserve(BODY)
    assert budget.snapshot()["pending_count"] == 1


@pytest.mark.parametrize("change", [{"input_cny_per_million": 2.0}, {"input_cny_per_million": "NaN"},
    {"output_cny_per_million": Decimal("Infinity")}, {"input_cny_per_million": 0},
    {"prior_known_cny": -1}, {"prior_known_cny": Decimal("-0")},
    {"uncertainty_reserve_cny": True}, {"ceiling_cny": "45.01"},
    {"ceiling_cny": float("nan")}, {"model": "deepseek-chat"}, {"currency": "USD"},
    {"overhead_tokens": 4095}, {"max_output_tokens": 16385},
    {"max_input_tokens": 4096}, {"pricing_checked_at": "20261007"}])
def test_invalid_configuration_rejected_without_ledger(tmp_path, change):
    with pytest.raises(BudgetValidationError):
        ledger(tmp_path, **change)
    assert not (tmp_path / "budget.json").exists()


def test_seeds_at_ceiling_rejected(tmp_path):
    with pytest.raises(BudgetExceeded):
        ledger(tmp_path, prior_known_cny="40", uncertainty_reserve_cny="5")


@pytest.mark.parametrize("change", [{"input_cny_per_million": "3"}, {"output_cny_per_million": "9"},
    {"prior_known_cny": "0"}, {"uncertainty_reserve_cny": "0"}, {"max_output_tokens": 2048},
    {"max_input_tokens": 65536}, {"pricing_checked_at": "2026-10-06"}])
def test_restart_rejects_different_rates_seeds_or_bounds(tmp_path, change):
    first = ledger(tmp_path)
    first.reserve(BODY)
    before = first.path.read_bytes()
    with pytest.raises(BudgetValidationError, match="mismatch"):
        ledger(tmp_path, **change)
    assert first.path.read_bytes() == before


@pytest.mark.parametrize("contents", ["", "{", "[]", "null", '{"version":1,"version":1}',
    '{"version":NaN}', '{"version":1.0}'])
def test_malformed_persisted_ledger_never_resets(tmp_path, contents):
    path = tmp_path / "budget.json"
    path.write_text(contents)
    with pytest.raises(BudgetValidationError):
        ledger(tmp_path)
    assert path.read_text() == contents


def test_missing_initialized_ledger_never_resets(tmp_path):
    budget = ledger(tmp_path)
    budget.reserve(BODY)
    budget.path.unlink()
    with pytest.raises(BudgetValidationError, match="missing"):
        ledger(tmp_path)
    with pytest.raises(FileNotFoundError):
        budget.reserve(BODY)
    assert not budget.path.exists()


def test_persisted_counter_and_currency_tampering_rejected(tmp_path):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    state = json.loads(budget.path.read_text())
    state["reservations"][reservation]["charged_cny"] = "0"
    budget.path.write_text(json.dumps(state))
    with pytest.raises(BudgetValidationError, match="charge mismatch"):
        budget.snapshot()
    state["reservations"][reservation]["charged_cny"] = state["reservations"][reservation]["reserved_cny"]
    state["config"]["currency"] = "USD"
    budget.path.write_text(json.dumps(state))
    with pytest.raises(BudgetValidationError, match="configuration mismatch"):
        budget.reserve(BODY)


@pytest.mark.parametrize("change", [{"model": "deepseek-chat"}, {"max_tokens": None},
    {"max_tokens": 4097}, {"max_tokens": 0}, {"max_tokens": True}, {"max_tokens": 2.0},
    {"stream": True}, {"n": 2}, {"max_completion_tokens": 100}, {"temperature": float("nan")}])
def test_invalid_body_never_reserves(tmp_path, change):
    budget = ledger(tmp_path)
    with pytest.raises(BudgetValidationError):
        budget.reserve(BODY | change)
    assert budget.snapshot()["reservation_count"] == 0


def test_input_byte_bound_is_enforced_exactly_without_clamping(tmp_path):
    size = len(json.dumps(BODY).encode("utf-8"))
    budget = ledger(tmp_path, max_input_tokens=size + 4096)
    budget.reserve(BODY)
    with pytest.raises(BudgetValidationError, match="input token bound"):
        budget.reserve(BODY | {"extra": "x"})
    assert budget.snapshot()["reservation_count"] == 1


def test_low_external_decimal_precision_cannot_undercount(tmp_path):
    with localcontext() as ctx:
        ctx.prec = 2
        budget = ledger(tmp_path)
        reservation = budget.reserve(BODY)
        budget.reconcile(reservation, USAGE)
        assert budget.snapshot()["conservative_total_cny"] == "5.092274"


def _process_reserve(args):
    directory, cap = args
    try:
        budget = BudgetLedger(os.path.join(directory, "budget.json"), **(RATES | {"ceiling_cny": cap}))
        return budget.reserve(BODY)
    except BudgetExceeded:
        return None


def test_cross_process_concurrency_has_no_lost_reservations_or_overspend(tmp_path):
    cap = Decimal("5.091914") + cost() * Decimal("7.5")
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_process_reserve, [(str(tmp_path), cap)] * 20))
    successful = [value for value in results if value]
    assert len(successful) == len(set(successful)) == 7
    snapshot = ledger(tmp_path, ceiling_cny=cap).snapshot()
    assert snapshot["pending_count"] == 7
    assert Decimal(snapshot["conservative_total_cny"]) < cap


def test_threaded_idempotent_reconciliation_cannot_release_twice(tmp_path):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(lambda _: budget.reconcile(reservation, USAGE), range(12)))
    assert budget.snapshot()["reconciled_count"] == 1
    assert budget.snapshot()["conservative_total_cny"] == "5.092274"


def test_failed_atomic_replace_never_returns_reservation(tmp_path, monkeypatch):
    budget = ledger(tmp_path)
    before = budget.path.read_bytes()
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        budget.reserve(BODY)
    assert budget.path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


UPGRADED_LIMITS = {"max_input_tokens": 1048576, "max_output_tokens": 16384}


def test_explicit_limit_upgrade_preserves_all_charges_and_restarts(tmp_path):
    budget = ledger(tmp_path)
    pending = budget.reserve(BODY)
    settled = budget.reserve(BODY)
    budget.reconcile(settled, USAGE)
    before = json.loads(budget.path.read_text())
    before_snapshot = budget.snapshot()
    with pytest.raises(BudgetValidationError, match="configuration mismatch"):
        ledger(tmp_path, **UPGRADED_LIMITS)  # Never migrate implicitly.
    budget.upgrade_token_limits(**UPGRADED_LIMITS)
    after = json.loads(budget.path.read_text())
    assert after == before | {"config": before["config"] | UPGRADED_LIMITS}
    assert after["reservations"][pending]["output_tokens"] == 4096
    assert after["reservations"][pending]["status"] == "pending"
    snapshot = budget.snapshot()
    assert snapshot == before_snapshot | {"configuration": before_snapshot["configuration"] | UPGRADED_LIMITS}
    assert ledger(tmp_path, **UPGRADED_LIMITS).snapshot() == snapshot
    with pytest.raises(BudgetValidationError, match="configuration mismatch"):
        ledger(tmp_path)
    larger_body = BODY | {"max_tokens": 16384, "messages": [{"role": "user", "content": "x" * 140000}]}
    new = budget.reserve(larger_body)
    rows = json.loads(budget.path.read_text())["reservations"]
    assert rows[pending] == before["reservations"][pending]
    assert rows[settled] == before["reservations"][settled]
    assert rows[new]["input_tokens"] == len(json.dumps(larger_body).encode()) + 4096
    assert rows[new]["output_tokens"] == 16384
    assert Decimal(rows[new]["reserved_cny"]) == cost(larger_body)
    assert budget.reconcile(pending, USAGE)  # Original pending usage remains reconcilable.


def test_upgrade_cannot_relax_an_existing_reservations_own_bounds(tmp_path):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    budget.upgrade_token_limits()
    with pytest.raises(BudgetExceeded, match="persistently blocked"):
        budget.reconcile(reservation, {"prompt_tokens": 1, "completion_tokens": 4097})
    assert budget.snapshot()["blocked"]
    assert Decimal(budget.snapshot()["overrun_cny"]) == cost()


def test_upgrade_preserves_preexisting_overrun_block(tmp_path):
    budget = ledger(tmp_path)
    reservation = budget.reserve(BODY)
    with pytest.raises(BudgetExceeded):
        budget.reconcile(reservation, {"prompt_tokens": 1, "completion_tokens": 4097})
    before = json.loads(budget.path.read_text())
    budget.upgrade_token_limits()
    assert json.loads(budget.path.read_text()) == before | {"config": before["config"] | UPGRADED_LIMITS}
    with pytest.raises(BudgetExceeded, match="blocked"):
        ledger(tmp_path, **UPGRADED_LIMITS).reserve(BODY)


def test_old_config_instances_fail_closed_after_concurrent_upgrade(tmp_path):
    budget = ledger(tmp_path)
    stale = ledger(tmp_path)
    pending = stale.reserve(BODY)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(budget.upgrade_token_limits).result()
    before = budget.path.read_bytes()
    for operation in (lambda: stale.reserve(BODY), lambda: stale.reconcile(pending, USAGE),
                      stale.snapshot, stale.upgrade_token_limits):
        with pytest.raises(BudgetValidationError, match="configuration mismatch"):
            operation()
    assert budget.path.read_bytes() == before


@pytest.mark.parametrize("limits", [{"max_output_tokens": 16385}, {"max_input_tokens": 1048577},
    {"max_output_tokens": 4095}, {"max_input_tokens": 131071},
    {"max_output_tokens": 16384.0}, {"max_input_tokens": True}])
def test_invalid_limit_upgrade_changes_nothing(tmp_path, limits):
    budget = ledger(tmp_path)
    budget.reserve(BODY)
    before = budget.path.read_bytes()
    with pytest.raises(BudgetValidationError):
        budget.upgrade_token_limits(**limits)
    assert budget.path.read_bytes() == before
    assert ledger(tmp_path).snapshot() == budget.snapshot()


def test_failed_limit_upgrade_write_keeps_old_configuration_and_pending(tmp_path, monkeypatch):
    budget = ledger(tmp_path)
    budget.reserve(BODY)
    before = budget.path.read_bytes()
    before_snapshot = budget.snapshot()
    def fail(*args):
        raise OSError("simulated migration write failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="migration write failure"):
        budget.upgrade_token_limits()
    assert budget.path.read_bytes() == before
    assert budget.snapshot() == before_snapshot
    assert ledger(tmp_path).snapshot() == before_snapshot
    assert list(tmp_path.glob("*.tmp")) == []


def test_upgrade_does_not_relax_strict_money_ceiling(tmp_path):
    larger_body = BODY | {"max_tokens": 16384}
    budget = ledger(tmp_path, prior_known_cny=Decimal("40") - cost(larger_body))
    budget.upgrade_token_limits()
    with pytest.raises(BudgetExceeded, match="reach or exceed"):
        budget.reserve(larger_body)
    assert budget.snapshot()["reservation_count"] == 0


def test_uncertain_upgrade_after_replace_fails_closed_until_reopened(tmp_path, monkeypatch):
    budget = ledger(tmp_path)
    budget.reserve(BODY)
    before = json.loads(budget.path.read_text())
    real_write = budget._write
    def uncertain_write(state):
        real_write(state)
        raise OSError("simulated durability uncertainty after replacement")
    monkeypatch.setattr(budget, "_write", uncertain_write)
    with pytest.raises(OSError, match="uncertainty"):
        budget.upgrade_token_limits()
    assert json.loads(budget.path.read_text()) == before | {"config": before["config"] | UPGRADED_LIMITS}
    with pytest.raises(BudgetValidationError, match="configuration mismatch"):
        budget.reserve(BODY)
    verified = ledger(tmp_path, **UPGRADED_LIMITS)
    assert verified.snapshot()["pending_count"] == 1
    assert Decimal(verified.snapshot()["pending_cny"]) == cost()
