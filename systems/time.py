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

from kernel.contextsystem import ContextSystem, ValidationError, Fragment
from kernel.events import kernel_event
from kernel.clock import band_name
from engine.log import get_logger

log = get_logger("systems.time")


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
                entity.attrs["last_update"] = event["day"]
                log.debug("time_advanced stamped last_update=%d for id=%s", event["day"], pid)
        # If no id: pure elapse carrier — projection sets meta.day via kernel

    def validate(self, section: str, decl, world: dict) -> list[ValidationError]:
        if section != "clock":
            return []
        decl = decl or []
        if len(decl) != 1:
            return [ValidationError(
                "clock", "", "bad_count",
                f"clock 段必须恰好 1 个元素（本回合的时间推进），当前 {len(decl)} 个")]
        item = decl[0]
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
            f'当前 {band_name(band)}（晨→中午→下午→夜晚）；bands 是推进的时段数，'
            '可大于 3，引擎自动进位。即使时间不动（连续场景）也要 advance:false 且给 reason。'
        )
        return Fragment("time", "scene", text, affordance)
