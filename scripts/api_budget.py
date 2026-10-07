"""Fail-closed, local CNY ledger for explicitly authorized DeepSeek smoke calls.

No network, credentials, prompts, or responses are stored here. Call ``reserve``
BEFORE every HTTP attempt, then ``reconcile`` only with that attempt's raw usage.
Never automatically retry an attempt using its old reservation. Missing usage,
exceptions, and process crashes leave the full reservation charged indefinitely.
Use the SAME ledger path and seed values across every run in an authorized session.
The ledger cannot discover unrelated account spend: seed known prior costs and a
separate uncertainty reserve, and prevent other callers from sharing the budget.

Prices must be explicit Decimal/string/integer CNY per million tokens. Float,
nonfinite, negative, zero-price, and mismatched configurations fail closed. Input
is reserved as the actual default ``json.dumps`` UTF-8 wire size plus configurable
provider overhead (at least 4096). This is a conservative byte/token bound, NOT an
exact tokenizer or a provider billing guarantee. The caller must send that same
body with that serialization; input estimates and output maxima are capped. All
prompt tokens are priced at the supplied uncached-input rate; reasoning tokens
are already included in completion_tokens and must not be added again.

Reservations are locked across processes, atomically replaced, and fsynced before
``reserve`` returns. Every admission requires total < ceiling <= CNY45, leaving
additional margin below the separately authorized CNY50 total. Unrecognized or
corrupt state is never reset. Reported over-bound usage blocks the ledger until a
human investigates; pending reservations cannot be released by a failed call.
This is a cooperative POSIX local-filesystem guard, not an account-level limit.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal, localcontext
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


MODEL = "deepseek-flash"
CURRENCY = "CNY"
HARD_CEILING_CNY = Decimal("45")
HARD_MAX_OUTPUT_TOKENS = 16384
PRICING_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
_MAX_COUNTER = 2**63 - 1
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_RECORD_KEYS = {"body_bytes", "input_tokens", "output_tokens", "reserved_cny",
                "status", "reported_input_tokens", "reported_output_tokens", "charged_cny"}


class BudgetValidationError(ValueError):
    """Invalid request, rates, configuration, usage, or persisted ledger."""


class BudgetExceeded(RuntimeError):
    """The next request cannot safely fit, or the ledger is blocked."""


def _amount(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise BudgetValidationError(f"{name} must be a Decimal, decimal string, or integer")
    if isinstance(value, str) and not _DECIMAL.fullmatch(value):
        raise BudgetValidationError(f"{name} must be a nonnegative finite decimal")
    number = Decimal(value)
    if (not number.is_finite() or number.is_signed()
            or len(number.as_tuple().digits) > 48
            or abs(number.as_tuple().exponent) > 28
            or number > Decimal("1000000000000")
            or (positive and number == 0)):
        raise BudgetValidationError(f"{name} is outside the supported nonnegative finite range")
    return number


def _decimal_string(value):
    # Avoid Decimal.normalize(), which rounds using the caller's decimal context.
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _integer(value, name, *, minimum=0, maximum=_MAX_COUNTER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise BudgetValidationError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BudgetValidationError("duplicate key in budget ledger")
        result[key] = value
    return result


def _bad_json_number(_value):
    raise BudgetValidationError("floating-point or nonfinite number in budget ledger")


class BudgetLedger:
    """Persistent session ledger; construction validates or creates the local file.

    Amounts and seed values are required explicitly. ``pricing_checked_at`` is an
    optional ISO date recording the caller's verification, not a claim by this
    module that prices remain current. Changing any configuration on an existing
    ledger is rejected, including seeds, model, rates, and token bounds. Call
    ``upgrade_token_limits`` explicitly on the old configuration to raise only
    its input/output limits without changing previous reservations or charges.
    """

    def __init__(self, path, *, input_cny_per_million, output_cny_per_million,
                 prior_known_cny, uncertainty_reserve_cny,
                 ceiling_cny=HARD_CEILING_CNY, max_input_tokens=131072,
                 max_output_tokens=4096, overhead_tokens=4096,
                 model=MODEL, currency=CURRENCY, pricing_source=PRICING_SOURCE,
                 pricing_checked_at=None):
        if model != MODEL or currency != CURRENCY:
            raise BudgetValidationError("only model deepseek-flash and currency CNY are supported")
        if pricing_source != PRICING_SOURCE:
            raise BudgetValidationError("pricing_source must be the official DeepSeek pricing URL")
        if pricing_checked_at is not None:
            if not isinstance(pricing_checked_at, str):
                raise BudgetValidationError("pricing_checked_at must be an ISO date")
            try:
                if date.fromisoformat(pricing_checked_at).isoformat() != pricing_checked_at:
                    raise ValueError
            except ValueError as error:
                raise BudgetValidationError("pricing_checked_at must be an ISO date") from error
        self._input_rate = _amount(input_cny_per_million, "input price", positive=True)
        self._output_rate = _amount(output_cny_per_million, "output price", positive=True)
        ceiling = _amount(ceiling_cny, "ceiling", positive=True)
        prior = _amount(prior_known_cny, "known prior cost")
        uncertainty = _amount(uncertainty_reserve_cny, "uncertainty reserve")
        if ceiling > HARD_CEILING_CNY:
            raise BudgetValidationError("the internal ceiling cannot exceed CNY45")
        with localcontext() as ctx:
            ctx.prec = 128
            if prior + uncertainty >= ceiling:
                raise BudgetExceeded("prior cost and uncertainty reserve exhaust the strict ceiling")
        _integer(overhead_tokens, "overhead_tokens", minimum=4096, maximum=1048576)
        _integer(max_input_tokens, "max_input_tokens", minimum=overhead_tokens + 1, maximum=1048576)
        _integer(max_output_tokens, "max_output_tokens", minimum=1, maximum=HARD_MAX_OUTPUT_TOKENS)
        self.path = Path(path).absolute()
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._config = {
            "currency": currency, "model": model,
            "input_cny_per_million": _decimal_string(self._input_rate),
            "output_cny_per_million": _decimal_string(self._output_rate),
            "prior_known_cny": _decimal_string(prior),
            "uncertainty_reserve_cny": _decimal_string(uncertainty),
            "ceiling_cny": _decimal_string(ceiling),
            "max_input_tokens": max_input_tokens, "max_output_tokens": max_output_tokens,
            "overhead_tokens": overhead_tokens, "pricing_source": pricing_source,
            "pricing_checked_at": pricing_checked_at,
        }
        # Do not silently create another directory and thereby another budget.
        if not self.path.parent.is_dir():
            raise BudgetValidationError("budget ledger directory must already exist")
        with self._lock() as lock_fd:
            try:
                self._read()
            except FileNotFoundError:
                if os.fstat(lock_fd).st_size:
                    raise BudgetValidationError("initialized budget ledger is missing; refusing to reset")
                self._write({"version": 1, "config": self._config,
                             "blocked_reason": None, "reservations": {}})
            if not os.fstat(lock_fd).st_size:
                os.write(lock_fd, b"initialized\n")
                os.fsync(lock_fd)

    def upgrade_token_limits(self, *, max_input_tokens=1048576, max_output_tokens=16384):
        """Explicitly raise future-request bounds; preserve all existing charges.

        Open the ledger with its CURRENT configuration before calling. Future
        constructors must use the upgraded bounds; old-config instances fail
        closed. Existing reservations retain their original per-call bounds,
        including pending and overrun records. No other configuration changes.
        A write failure raises; after uncertain persistence, reopen to verify
        which configuration reached disk rather than retrying blindly.
        """
        with self._lock():
            state = self._read()  # Reject a concurrent migration or corrupt state.
            _integer(max_input_tokens, "max_input_tokens",
                     minimum=self._config["max_input_tokens"], maximum=1048576)
            _integer(max_output_tokens, "max_output_tokens",
                     minimum=self._config["max_output_tokens"], maximum=HARD_MAX_OUTPUT_TOKENS)
            updated = self._config | {"max_input_tokens": max_input_tokens,
                                      "max_output_tokens": max_output_tokens}
            if updated == self._config:
                return
            state["config"] = updated
            self._write(state)
            self._config = updated  # Only adopt the new bounds after durable write.

    @contextmanager
    def _lock(self):
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BudgetValidationError("budget lock must be a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield fd
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _cost(self, input_tokens, output_tokens):
        with localcontext() as ctx:
            ctx.prec = 128
            return (input_tokens * self._input_rate + output_tokens * self._output_rate) / Decimal(1000000)

    def _read(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise BudgetValidationError("budget ledger must be a regular file")
            try:
                state = json.load(handle, object_pairs_hook=_unique_object,
                                  parse_float=_bad_json_number, parse_constant=_bad_json_number)
            except (ValueError, UnicodeError) as error:
                raise BudgetValidationError("malformed budget ledger; refusing to reset") from error
        self._validate(state)
        return state

    def _validate(self, state):
        if (type(state) is not dict or set(state) != {"version", "config", "blocked_reason", "reservations"}
                or type(state["version"]) is not int or state["version"] != 1
                or state["config"] != self._config
                or state["blocked_reason"] not in (None, "reported_usage_exceeds_reservation")
                or type(state["reservations"]) is not dict):
            raise BudgetValidationError("budget ledger schema or configuration mismatch")
        overrun = False
        for reservation_id, row in state["reservations"].items():
            if (not re.fullmatch(r"[0-9a-f]{32}", reservation_id)
                    or type(row) is not dict or set(row) != _RECORD_KEYS):
                raise BudgetValidationError("invalid budget reservation record")
            size = _integer(row["body_bytes"], "body bytes", minimum=1)
            inp = _integer(row["input_tokens"], "reserved input", minimum=1,
                           maximum=self._config["max_input_tokens"])
            out = _integer(row["output_tokens"], "reserved output", minimum=1,
                           maximum=self._config["max_output_tokens"])
            if inp != size + self._config["overhead_tokens"]:
                raise BudgetValidationError("reservation input bound mismatch")
            reserved = self._cost(inp, out)
            if row["reserved_cny"] != _decimal_string(reserved):
                raise BudgetValidationError("reservation price mismatch")
            if row["status"] == "pending":
                if row["reported_input_tokens"] is not None or row["reported_output_tokens"] is not None:
                    raise BudgetValidationError("pending reservation has final usage")
                charge = reserved
            elif row["status"] in ("reconciled", "overrun"):
                actual_input = _integer(row["reported_input_tokens"], "reported input")
                actual_output = _integer(row["reported_output_tokens"], "reported output")
                actual_cost = self._cost(actual_input, actual_output)
                exceeds = actual_input > inp or actual_output > out or actual_cost > reserved
                if (row["status"] == "overrun") != exceeds:
                    raise BudgetValidationError("inconsistent reservation usage bounds")
                overrun = overrun or exceeds
                charge = reserved if exceeds else actual_cost
            else:
                raise BudgetValidationError("unknown reservation status")
            if row["charged_cny"] != _decimal_string(charge):
                raise BudgetValidationError("reservation charge mismatch")
        if bool(state["blocked_reason"]) != overrun:
            raise BudgetValidationError("inconsistent blocked ledger state")
        if not overrun and self._total(state) >= Decimal(self._config["ceiling_cny"]):
            raise BudgetValidationError("persisted ledger reaches or exceeds the strict ceiling")

    def _total(self, state):
        with localcontext() as ctx:
            ctx.prec = 128
            return (Decimal(self._config["prior_known_cny"])
                    + Decimal(self._config["uncertainty_reserve_cny"])
                    + sum((Decimal(row["charged_cny"]) for row in state["reservations"].values()), Decimal(0)))

    def _write(self, state):
        # Separate stable lock inode is essential: replacing the ledger's own
        # locked inode would permit another process to lock a different inode.
        fd, filename = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=True, sort_keys=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def reserve(self, body):
        """Durably charge an upper-bound reservation before an HTTP attempt.

        Return an opaque ID. Budget exhaustion, malformed/oversized requests,
        blocked state, and persistence failures raise before the caller may send.
        The caller must not mutate ``body`` or alter its JSON serialization after
        reserving; the existing smoke transport uses default json.dumps exactly.
        """
        if type(body) is not dict or body.get("model") != MODEL:
            raise BudgetValidationError("request must explicitly use deepseek-flash")
        output = _integer(body.get("max_tokens"), "max_tokens", minimum=1,
                          maximum=self._config["max_output_tokens"])
        if body.get("stream", False) is not False or body.get("n", 1) != 1:
            raise BudgetValidationError("only a single non-streamed completion is supported")
        if "max_completion_tokens" in body:
            raise BudgetValidationError("ambiguous alternative output limit")
        try:
            body_bytes = len(json.dumps(body, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as error:
            raise BudgetValidationError("request body must be strict JSON") from error
        input_tokens = body_bytes + self._config["overhead_tokens"]
        if input_tokens > self._config["max_input_tokens"]:
            raise BudgetValidationError("serialized request exceeds conservative input token bound")
        amount = self._cost(input_tokens, output)
        with self._lock():
            state = self._read()
            if state["blocked_reason"]:
                raise BudgetExceeded("budget ledger blocked by reported usage exceeding its reservation")
            with localcontext() as ctx:
                ctx.prec = 128
                if self._total(state) + amount >= Decimal(self._config["ceiling_cny"]):
                    raise BudgetExceeded("request would reach or exceed the strict CNY budget ceiling")
            reservation_id = uuid.uuid4().hex
            if reservation_id in state["reservations"]:
                raise BudgetValidationError("reservation identifier collision")
            state["reservations"][reservation_id] = {
                "body_bytes": body_bytes, "input_tokens": input_tokens,
                "output_tokens": output, "reserved_cny": _decimal_string(amount),
                "status": "pending", "reported_input_tokens": None,
                "reported_output_tokens": None, "charged_cny": _decimal_string(amount),
            }
            self._write(state)
        return reservation_id

    def reconcile(self, reservation_id, usage):
        """Release verified savings once; missing/invalid usage retains the charge.

        Repeating identical final usage is idempotent. Conflicting final usage is
        rejected. Usage above a reserved bound persistently blocks future calls,
        retains its charge, and raises BudgetExceeded. No cancellation/refund API
        exists because a transport failure does not prove a request was unbilled.
        """
        with self._lock():
            state = self._read()
            if not isinstance(reservation_id, str) or reservation_id not in state["reservations"]:
                raise BudgetValidationError("unknown budget reservation")
            row = state["reservations"][reservation_id]
            if type(usage) is not dict:
                return False
            try:
                inp = _integer(usage.get("prompt_tokens"), "prompt_tokens")
                out = _integer(usage.get("completion_tokens"), "completion_tokens")
                if "total_tokens" in usage:
                    total = _integer(usage["total_tokens"], "total_tokens")
                    if total != inp + out:
                        return False
            except BudgetValidationError:
                return False
            if row["status"] != "pending":
                if row["reported_input_tokens"] != inp or row["reported_output_tokens"] != out:
                    raise BudgetValidationError("conflicting final usage for settled reservation")
                if row["status"] == "overrun":
                    raise BudgetExceeded("reported usage exceeds the reservation; ledger remains blocked")
                return True
            actual = self._cost(inp, out)
            row["reported_input_tokens"], row["reported_output_tokens"] = inp, out
            if inp > row["input_tokens"] or out > row["output_tokens"] or actual > Decimal(row["reserved_cny"]):
                row["status"] = "overrun"
                state["blocked_reason"] = "reported_usage_exceeds_reservation"
                self._write(state)
                raise BudgetExceeded("reported usage exceeds the reservation; ledger persistently blocked")
            row["status"] = "reconciled"
            row["charged_cny"] = _decimal_string(actual)
            self._write(state)
            return True

    def snapshot(self):
        """Return JSON-safe configuration, monetary strings, and aggregate counters."""
        with self._lock():
            state = self._read()
            rows = list(state["reservations"].values())
            with localcontext() as ctx:
                ctx.prec = 128
                uncovered = sum((max(Decimal(0), self._cost(row["reported_input_tokens"], row["reported_output_tokens"])
                                    - Decimal(row["charged_cny"]))
                                 for row in rows if row["status"] == "overrun"), Decimal(0))
                total = self._total(state) + uncovered
                result = {"configuration": dict(self._config),
                          "blocked": bool(state["blocked_reason"]),
                          "blocked_reason": state["blocked_reason"],
                          "reservation_count": len(rows),
                          "conservative_total_cny": _decimal_string(total),
                          "remaining_cny": _decimal_string(max(Decimal(0), Decimal(self._config["ceiling_cny"]) - total)),
                          "uncovered_cny": _decimal_string(uncovered)}
                for status_name in ("pending", "reconciled", "overrun"):
                    matching = [row for row in rows if row["status"] == status_name]
                    result[status_name + "_count"] = len(matching)
                    result[status_name + "_cny"] = _decimal_string(sum((Decimal(row["charged_cny"]) for row in matching), Decimal(0)))
                return result
