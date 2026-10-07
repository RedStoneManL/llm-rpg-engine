"""systems.time — TimeSystem: owns time_advanced event type.

Phase D: harness-authored carrier event for time elapse + currency stamps.
No commit sections in D1 (harness-authored only).

apply() bumps entity.attrs["last_update"] = event["day"] when a scoped id
is present in deltas. This keeps the lazy catch-up contract: an entity is
asked at most once per jump it is present for.

For pure elapse carriers (no deltas.id), nothing is written to the graph —
projection still sets meta.day from the event's day field as normal.
"""
from __future__ import annotations

from copy import deepcopy

from kernel.contextsystem import ContextSystem, ValidationError, Fragment
from kernel.events import kernel_event
from kernel.clock import advance, band_name, elapsed, to_units
from engine.log import get_logger

log = get_logger("systems.time")


def _target_errors(target, field: str = "[0].target") -> list[ValidationError]:
    if not isinstance(target, dict):
        return [ValidationError(
            "clock", field, "bad_shape", "target 必须为 {day:整数>=1,band:整数0..3}")]
    errors = []
    day, band = target.get("day"), target.get("band")
    if not (isinstance(day, int) and not isinstance(day, bool) and day >= 1):
        errors.append(ValidationError(
            "clock", f"{field}.day", "bad_range", "target.day 必须为 >=1 整数"))
    if not (isinstance(band, int) and not isinstance(band, bool) and 0 <= band <= 3):
        errors.append(ValidationError(
            "clock", f"{field}.band", "bad_range", "target.band 必须为 0..3 整数"))
    return errors


def _current_clock(world: dict) -> tuple[int, int]:
    meta = world.get("meta", {})
    if not isinstance(meta, dict):
        raise ValueError("world.meta must be a clock object")
    # Match the clock defaults used for an empty or legacy world.
    day = meta.get("day") if meta.get("day") is not None else 1
    band = meta.get("band") if meta.get("band") is not None else 0
    if _target_errors({"day": day, "band": band}):
        raise ValueError("world clock must have day >= 1 and band in 0..3")
    return day, band


def normalize_clock(decl, world: dict) -> list[dict]:
    """Copy a valid declaration, resolving an absolute target to legacy deltas.

    Resolution uses only the supplied world, never validation-time state. The
    source proposal and world are untouched. Invalid declarations raise
    ValueError; callers should run the usual validation/repair gate first.
    Absent/empty optional clock sections remain empty for legacy callers.
    """
    if decl is None or (isinstance(decl, list) and not decl):
        return []
    errors = TimeSystem().validate("clock", decl, world)
    if errors:
        raise ValueError("; ".join(error.hint for error in errors))
    result = deepcopy(decl)
    item = result[0]
    if "target" in item:
        target = item.pop("target")
        day, band = _current_clock(world)
        delta = elapsed(to_units(day, band), to_units(target["day"], target["band"]))
        item["days"], item["bands"] = divmod(delta, 4)
    return result


def validate_resolved_time(commit, target, world: dict) -> list[ValidationError]:
    """Check an already-resolved wait endpoint against either clock form.

    Malformed proposals or resolver output become repairable errors rather
    than arithmetic or indexing exceptions. No wait means no extra constraint.
    """
    if target is None:
        return []
    if _target_errors(target):
        return [ValidationError(
            "clock", "", "resolved_time", "等待终点必须为 {day:整数>=1,band:整数0..3}")]
    expected = (target["day"], target["band"])
    try:
        sections = commit.sections
        if not isinstance(sections, dict):
            raise ValueError("commit.sections must be an object")
        item = normalize_clock(sections.get("clock"), world)[0]
        day, band = _current_clock(world)
        actual = advance(day, band, item.get("days", 0), item.get("bands", 0))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        actual = None
    if actual == expected:
        return []
    return [ValidationError(
        "clock", "", "resolved_time",
        f'明确等待必须抵达第{target["day"]}天时段{target["band"]}；'
        f'使用 target={{"day":{target["day"]},"band":{target["band"]}}}，'
        '未来终点须 advance=true，当前时刻须 advance=false；不要同时给 days/bands')]


class TimeSystem(ContextSystem):
    """Owns time_advanced event. Harness-authored; no commit sections in D1.

    apply() stamps entity.attrs["last_update"] = event["day"] for the named
    entity (deltas.id), without asserting any drift fact. This keeps the
    lazy catch-up contract: an entity is asked at most once per jump.
    """

    name = "time"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"time_advanced", "clock_advanced"}

    def commit_sections(self) -> set[str]:
        return {"clock"}

    def empty_state(self) -> dict:
        return {}

    def apply(self, world: dict, event: dict) -> None:
        if event["type"] == "clock_advanced":
            # Band depends only on dbands (whole days never move the band).
            # meta.day is set by projection from event["day"]; we fold band here.
            d = event.get("deltas", {})
            old_band = world["meta"].get("band") or 0
            world["meta"]["band"] = (old_band + int(d.get("bands", 0) or 0)) % 4
            log.debug("clock_advanced -> day=%s band=%d", event["day"], world["meta"]["band"])
            return

        g = world["systems"]["ontology"]
        d = event.get("deltas", {})
        pid = d.get("id")
        if pid:
            entity = g.get_entity(pid)
            if entity is None:
                log.warning("time_advanced dangling id=%s; last_update not stamped", pid)
            else:
                entity.attrs["last_update"] = max(entity.attrs.get('last_update', 0), event["day"])
                log.debug("time_advanced stamped last_update=%d for id=%s", event["day"], pid)
        # If no id: pure elapse carrier — projection sets meta.day via kernel

    def validate(self, section: str, decl, world: dict) -> list[ValidationError]:
        if section != "clock":
            return []
        if decl is None:
            decl = []
        if not isinstance(decl, list):
            return [ValidationError(
                "clock", "", "bad_shape", "clock 段必须是对象数组 [{...}]")]
        if len(decl) != 1:
            return [ValidationError(
                "clock", "", "bad_count",
                f"clock 段必须恰好 1 个元素（本回合的时间推进），当前 {len(decl)} 个")]
        item = decl[0]
        if not isinstance(item, dict):
            return [ValidationError(
                "clock", "[0]", "bad_shape", "clock 的元素必须是对象 {...}")]
        errs: list[ValidationError] = []

        adv = item.get("advance")
        if not isinstance(adv, bool):
            errs.append(ValidationError(
                "clock", "[0].advance", "missing",
                "clock 必须含布尔 'advance'（本回合时间是否推进）"))

        reason = item.get("reason")
        if not (isinstance(reason, str) and reason.strip()):
            errs.append(ValidationError(
                "clock", "[0].reason", "missing",
                "clock 必须含非空 'reason'（推进多少的依据，或为何不推进）"))

        if "target" in item:
            if "days" in item or "bands" in item:
                errs.append(ValidationError(
                    "clock", "[0]", "conflicting_mode",
                    "target 与 days/bands 互斥；使用绝对终点时删除 days 和 bands（即使为 0）"))
            target = item["target"]
            target_errors = _target_errors(target)
            errs.extend(target_errors)
            if target_errors:
                return errs
            try:
                day, band = _current_clock(world)
            except (AttributeError, TypeError, ValueError):
                errs.append(ValidationError(
                    "clock", "[0].target", "bad_clock", "当前世界时间必须为 day>=1、band=0..3"))
                return errs
            delta = elapsed(to_units(day, band), to_units(target["day"], target["band"]))
            if delta < 0:
                errs.append(ValidationError(
                    "clock", "[0].target", "past_target", "target 不能早于当前世界时间"))
            elif isinstance(adv, bool) and adv != (delta > 0):
                errs.append(ValidationError(
                    "clock", "[0].advance", "bad_advance",
                    "未来 target 必须 advance=true；target 等于当前时刻时须 advance=false"))
            return errs

        days = item.get("days", 0)
        bands = item.get("bands", 0)
        days_ok = isinstance(days, int) and not isinstance(days, bool) and days >= 0
        bands_ok = isinstance(bands, int) and not isinstance(bands, bool) and bands >= 0
        if not days_ok:
            errs.append(ValidationError(
                "clock", "[0].days", "bad_range", f"days 必须为 >=0 整数，当前 {days!r}"))
        if not bands_ok:
            errs.append(ValidationError(
                "clock", "[0].bands", "bad_range", f"bands 必须为 >=0 整数，当前 {bands!r}"))

        if isinstance(adv, bool) and days_ok and bands_ok:
            if adv and days == 0 and bands == 0:
                errs.append(ValidationError(
                    "clock", "[0]", "bad_advance",
                    "advance=true 但 days/bands 全为 0；给出推进量，或改 advance=false"))
            if not adv and (days != 0 or bands != 0):
                errs.append(ValidationError(
                    "clock", "[0]", "bad_advance",
                    "advance=false 但 days/bands 非 0；不推进时两者须为 0"))
        return errs

    def to_events(self, section: str, decl, *, turn: int, day: int, scene: str) -> list[dict]:
        if section != "clock":
            return []
        out: list[dict] = []
        for item in (decl or [])[:1]:
            if "target" in item:
                raise ValueError("absolute clock targets must pass through normalize_clock before to_events")
            adv = bool(item.get("advance"))
            days = int(item.get("days", 0) or 0)
            bands = int(item.get("bands", 0) or 0)
            reason = str(item.get("reason", ""))
            summary = (f"时间 +{days}天{bands}段：{reason}" if adv
                       else f"时间未推进：{reason}")
            out.append(kernel_event(
                "clock_advanced", day=day, scene=scene, summary=summary,
                deltas={"advance": adv, "days": days, "bands": bands, "reason": reason},
                turn=turn,
            ))
        return out

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        meta = world.get("meta", {})
        day = meta.get("day") or 1
        band = meta.get("band") or 0
        text = f"【此刻】第 {day} 天 · {band_name(band)}"
        affordance = (
            'clock（每回合必填，恰好 1 个元素）：'
            '[{"advance":true/false,"days":整天数,"bands":时段数,"reason":"理由"}]。'
            '明确等待/休息到某个终点时，优先使用绝对时间 '
            '[{"advance":true,"target":{"day":绝对天数,"band":0至3},"reason":"理由"}]；'
            'target.day>=1，band 为晨=0、中午=1、下午=2、夜晚=3。'
            'target 与 days/bands 互斥，不能同时提供（即使为 0）；终点不能早于此刻，'
            '未来终点须 advance:true，等于此刻须 advance:false。'
            '只有经过时长而没有明确终点时继续使用 days/bands。'
            f'当前 {band_name(band)}（晨→中午→下午→夜晚）；bands 是推进的时段数，'
            '可大于 3，引擎自动进位。即使时间不动（连续场景）也要 advance:false 且给 reason。'
        )
        return Fragment("time", "scene", text, affordance)
