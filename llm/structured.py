"""llm.structured — the harness contract for EVERY structured LLM return.

Real reasoning models (glm-4.7/5.1) return valid JSON but impose their own key
vocabulary, omit fields, and guess shapes. Rather than guessing back at synonyms,
the harness ENFORCES the schema: call the model, validate the parsed object, and
on any problem feed back a repair turn that NAMES the exact missing/wrong fields
so the model self-corrects — the same validate→repair loop the turn-commit uses
(kernel.validation.build_repair_request + produce_turn), generalized to any
single-object structured return.

Usage:
    def _validate(obj) -> list[str]:
        errs = []
        if not isinstance(obj.get("foo"), str) or not obj["foo"].strip():
            errs.append('missing or empty string field "foo"')
        return errs

    obj, errors = complete_structured(
        provider, system=SYS, user=usr, validate=_validate,
        max_repairs=1, log_label="cascade")
    if errors or obj is None:
        ... fallback ...        # never conformed / provider failed
    else:
        ... use obj ...

Returns (obj, errors): `errors == []` iff the object fully conformed. `obj` is the
last parsed object (or None if nothing parsed / no provider). NEVER raises.
"""
from __future__ import annotations

from engine.log import get_logger
from llm.provider import _parse_json_object

log = get_logger("llm.structured")

# Default repair budget. Per-item backstage calls (cascade/catchup, one per
# child/entity) should pass max_repairs=1 to bound cost; content generation uses 2.
DEFAULT_MAX_REPAIRS = 2


def build_structured_repair(errors: list[str], *, schema_reminder: str = "") -> str:
    """Build the repair turn fed back to the model — names every problem found.

    Mirrors kernel.validation.build_repair_request, for plain string errors.
    """
    body = "\n".join(f"  - {e}" for e in errors)
    msg = (
        "Your previous JSON did NOT conform to the required schema and was REJECTED "
        "by the game engine. Fix ALL of the following and return the COMPLETE "
        "corrected JSON object, with NO markdown fences and NO commentary:\n" + body
    )
    if schema_reminder:
        msg += "\n" + schema_reminder
    return msg


def complete_structured(
    provider,
    *,
    system: str,
    user: str,
    validate,
    max_repairs: int = DEFAULT_MAX_REPAIRS,
    schema_reminder: str = "",
    log_label: str = "",
) -> tuple[object, list[str]]:
    """Structured-JSON call with a validate→name-errors→repair loop.

    Args:
        provider: an LLM provider exposing ``complete_messages``.
        system/user: the prompt; ``user`` SHOULD declare each required field by
            name + type (the explicit field-by-field contract).
        validate: ``callable(obj) -> list[str]`` returning human-readable problems
            that NAME the missing/wrong fields ([] means the object conforms).
        max_repairs: max self-correction rounds (total LLM calls = max_repairs+1).
        schema_reminder: optional one-line reminder appended to every repair turn.
        log_label: short tag for log lines.

    Returns ``(obj, errors)``. ``errors == []`` iff fully conformed. ``obj`` is the
    last parsed object (or None). NEVER raises.
    """
    if provider is None:
        return None, ["no provider"]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    obj: object = None
    errors: list[str] = ["no response"]
    for attempt in range(max_repairs + 1):
        try:
            raw = provider.complete_messages(messages)
        except Exception:
            log.exception("complete_structured[%s]: complete_messages failed (attempt %d)",
                          log_label, attempt)
            return obj, errors
        obj = _parse_json_object(raw)
        if not isinstance(obj, dict):
            errors = ['The response must be a single JSON object (no prose, no fences).']
        else:
            try:
                errors = validate(obj) or []
            except Exception:
                log.exception("complete_structured[%s]: validate() crashed — treating as malformed",
                              log_label)
                errors = ["The response could not be validated; return a clean JSON object."]
        if not errors:
            return obj, []
        log.debug("complete_structured[%s]: attempt %d had %d error(s)",
                  log_label, attempt, len(errors))
        if attempt < max_repairs:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user",
                             "content": build_structured_repair(errors, schema_reminder=schema_reminder)})
    log.warning("complete_structured[%s]: did not conform after %d attempt(s): %s",
                log_label, max_repairs + 1, "; ".join(errors)[:200])
    return obj, errors
