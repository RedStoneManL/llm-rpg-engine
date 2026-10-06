from __future__ import annotations
import re

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
    if not isinstance(commit.narration, str) or (required_sections and not commit.narration.strip()):
        errors.append(ValidationError('narration', '', 'missing_narration', 'narration 必须是非空的散文字符串'))
    elif re.search(r'\\?"(?:narration|facts|knowledge|clock)\\?"\s*:', commit.narration or ''):
        errors.append(ValidationError('narration', '', 'structured_prose',
            'narration 只能是给玩家阅读的散文，不能包含 JSON 结构、内部字段或原始提交；重新提取纯正文'))
    g = world.get("systems", {}).get("ontology")

    # Same-commit cross-references: collect the ids this commit will create and
    # temporarily stub them into the graph for the duration of validation, so a
    # move to a just-created place (etc.) resolves instead of bouncing forever as
    # dangling_ref. Stubs are removed in `finally` — the real world is untouched.
    pending: set[str] = set()
    for section, decl in commit.sections.items():
        owner = registry.owner_of_section(section)
        if owner is not None and not _section_shape_errors(section, decl):
            try:
                pending |= {pid for pid in owner.created_ids(section, decl)
                            if isinstance(pid, str) and pid.strip()}
            except (TypeError, ValueError, AttributeError) as exc:
                errors.append(ValidationError(section, '', 'bad_shape',
                    f'无法读取本段声明的实体 id：{exc}；id 必须是非空字符串'))

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

    # Required-section presence. An empty section is given as a bare [] meaning
    # "no change this turn" — that is VALID, no `reasons` entry needed (the
    # narration prompt teaches "[] = no change"; the old empty_no_reason rule
    # contradicted that prompt and burned a repair round on every quiet turn). The
    # sole exception is clock: time must be accounted for every turn, so clock must
    # be present as a non-empty array (TimeSystem.validate then checks its shape;
    # a reasons-only clock does not count).
    for section in sorted(required_sections - {'clock'}):
        if section not in commit.sections or commit.sections[section] is None:
            errors.append(ValidationError(section, '', 'missing_section',
                f'{section} 段必须显式给出；没有变化时给空数组 []'))
    if "clock" in required_sections:
        decl = commit.sections.get("clock")
        if not (isinstance(decl, list) and len(decl) > 0):
            errors.append(ValidationError(
                "clock", "", "clock_required",
                "clock 段每回合必给（恰好一个元素，描述本回合时间是否推进）"))

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
