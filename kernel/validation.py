from __future__ import annotations

from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from kernel.contextsystem import ValidationError
from engine.log import get_logger

log = get_logger("kernel.validation")


def _section_shape_errors(section: str, decl) -> list[ValidationError]:
    """Enforce the universal section contract: a list of objects (list[dict]).

    Every system's validate() iterates `decl` and calls item.get(...), so a
    malformed shape from the LLM (a bare string, a list of strings, a dict)
    would crash the turn with AttributeError. We convert that into a repairable
    ValidationError with a concrete hint instead — keeping the strict gate
    crash-proof and routing the fix through the repair loop.
    """
    if decl is None:
        return []  # absent section is fine; owners handle `decl or []`
    if not isinstance(decl, list):
        return [ValidationError(
            section, "", "bad_shape",
            f"段 {section!r} 必须是对象数组 [{{...}}]，当前类型是 "
            f"{type(decl).__name__};请改成数组，每个元素为一个对象")]
    errs: list[ValidationError] = []
    for i, item in enumerate(decl):
        if not isinstance(item, dict):
            errs.append(ValidationError(
                section, f"[{i}]", "bad_shape",
                f"段 {section!r} 第 {i} 个元素必须是对象 {{...}}，当前是 "
                f"{type(item).__name__} {item!r:.40}"))
    return errs


def validate_commit(registry: Registry, commit: TurnCommit, world: dict, *,
                    required_sections: frozenset = frozenset()) -> list[ValidationError]:
    """Dispatch each section to its owning system. Unowned section => error.

    The universal section shape (a list of objects) is enforced centrally
    before dispatch, and each owner.validate() call is wrapped defensively, so
    malformed LLM output becomes a repairable error rather than an exception
    that kills the turn.

    required_sections: section keys that MUST be explicitly present (an empty []
    counts). A missing one => 'missing_section' error, so the LLM cannot silently
    omit a section it forgot — it must affirmatively declare "no change" via [].
    """
    errors: list[ValidationError] = []
    g = world.get("systems", {}).get("ontology")

    # Same-commit cross-references: collect the ids this commit will create and
    # temporarily stub them into the graph for the duration of validation, so a
    # move to a just-created place (etc.) resolves instead of bouncing forever as
    # dangling_ref. Stubs are removed in `finally` — the real world is untouched.
    pending: set[str] = set()
    for section, decl in commit.sections.items():
        owner = registry.owner_of_section(section)
        if owner is not None:
            pending |= owner.created_ids(section, decl)

    stubbed: list[str] = []
    try:
        if g is not None:
            for pid in pending:
                if g.get_entity(pid) is None:
                    g.add_entity(pid, "_pending")
                    stubbed.append(pid)

        for section, decl in commit.sections.items():
            owner = registry.owner_of_section(section)
            if owner is None:
                errors.append(ValidationError(section, "", "unknown_section",
                                              f"没有系统拥有段 {section!r};删掉或改用已知段"))
                continue
            shape_errs = _section_shape_errors(section, decl)
            if shape_errs:
                errors.extend(shape_errs)
                continue
            try:
                errors.extend(owner.validate(section, decl, world))
            except Exception as exc:  # defensive backstop — never crash on LLM output
                log.exception("validate_commit: %s.validate crashed on section %r",
                              type(owner).__name__, section)
                errors.append(ValidationError(
                    section, "", "validator_error",
                    f"段 {section!r} 校验时出错（{type(exc).__name__}: {exc}）;"
                    f"请检查该段格式后重发"))
    finally:
        for pid in stubbed:
            g.entities.pop(pid, None)

    # Presence + reason requirement: every required section must EITHER carry
    # content OR be explained in `reasons` (why it's empty this turn). Forces the
    # model to consciously confirm "nothing happened here" rather than silently
    # omit (or reflexively dump []) a section it actually forgot.
    reasons = commit.reasons or {}
    for s in required_sections:
        decl = commit.sections.get(s)
        has_content = isinstance(decl, list) and len(decl) > 0
        has_reason = bool(str(reasons.get(s, "")).strip())
        if not has_content and not has_reason:
            errors.append(ValidationError(
                s, "", "empty_no_reason",
                f"段 {s!r} 为空;若本回合确无变化,必须在顶层 reasons 里写明【为什么】没有"
                f"(强制确认你不是漏写)，例如 reasons:{{\"{s}\":\"主角停在原地,未移动\"}}"))

    log.debug("validate_commit sections=%d errors=%d pending=%d required=%d",
              len(commit.sections), len(errors), len(pending), len(required_sections))
    return errors


def build_repair_request(errors: list[ValidationError]) -> str:
    """Render a compact, LLM-facing repair instruction grouped by section."""
    by_section: dict[str, list[ValidationError]] = {}
    for e in errors:
        by_section.setdefault(e.section, []).append(e)
    lines = ["turn-commit 校验未过,只修正以下字段后重发:"]
    for section, errs in by_section.items():
        lines.append(f"[{section}]")
        for e in errs:
            loc = f"{section}{e.field}" if e.field else section
            lines.append(f"  - {loc} ({e.code}): {e.hint}")
    return "\n".join(lines)
