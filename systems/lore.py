"""systems.lore — LoreSystem: unified quest system (暗/明/了结 lifecycle).

Owns the full quest/event-line lifecycle: 暗骰 advance (loop/lore.run_lore),
暗→明 surfacing, narrator-driven 明 ops (quests section: open/surface/advance/resolve),
明→暗 demote, lifespan/expiry (quest_expired/quest_finale_due), density-based
generation (lore_seeded/density_refreshed), and complex-line endgame
(quest_world_resolved/quest_catastrophe).

Slice world["systems"]["lore"] = {"lines": {<id>: {
    complexity, about, secret, anchor, stages:[{hint,...}], threshold,
    stage_idx (int, -1 = not started),
    state ("暗"|"明"|"了结"), clues_dropped:[str],
    summary (str, 明账 one-liner, set by quest_advanced),
    surfaced_turn (int, set by quest_surfaced),
    resolved ({"by": str, "summary": str}, set by quest_resolved),
}}}

Rewind-safe: the slice folds entirely from the event log.
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem, ValidationError, Fragment
from kernel.events import kernel_event
from engine.log import get_logger

log = get_logger("systems.lore")

_QUEST_OPS = {"open", "surface", "advance", "resolve"}
_OP_EVENT = {
    "open": "quest_opened",
    "surface": "quest_surfaced",
    "advance": "quest_advanced",
    "resolve": "quest_resolved",
}

_LIFESPAN_DEFAULTS = {"simple": 3, "medium": 7, "complex": 20}


class LoreSystem(ContextSystem):
    name = "lore"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"lore_created", "lore_advanced",
                "quest_created",
                "quest_opened", "quest_surfaced", "quest_advanced", "quest_resolved",
                "quest_demoted",
                "quest_expired", "quest_finale_due",
                "quest_world_resolved", "quest_catastrophe",
                "lore_seeded", "density_refreshed"}

    def commit_sections(self) -> set[str]:
        return {"quests"}

    def empty_state(self) -> dict:
        return {"lines": {}, "gen": {}}

    def apply(self, world: dict, event: dict) -> None:
        t = event["type"]
        d = event.get("deltas", {})
        lines = world["systems"][self.name]["lines"]

        if t == "lore_created":
            lid = d.get("id")
            if not lid:
                log.warning("lore_created missing id; skipped (%s)", event.get("id"))
                return
            complexity = d.get("complexity")
            born_day = event.get("day")
            lifespan_days = d.get("lifespan_days") or _LIFESPAN_DEFAULTS.get(complexity)
            lines[lid] = {
                "id": lid,
                "complexity": complexity,
                "about": d.get("about"),
                "secret": d.get("secret"),
                "anchor": d.get("anchor"),
                "description": d.get("description"),
                "trigger": d.get("trigger"),
                "l3_anchor": d.get("l3_anchor"),
                "stages": d.get("stages", []),
                "threshold": d.get("threshold", 50),
                "stage_idx": -1,
                "state": d.get("state", "暗"),
                "clues_dropped": [],
                "born_day": born_day,
                "lifespan_days": lifespan_days,
            }
            log.debug("lore_created id=%s complexity=%s born_day=%s lifespan_days=%s",
                      lid, lines[lid]["complexity"], born_day, lifespan_days)
            return

        if t == "quest_created":
            # Backstop-emitted: creates a line with explicit state (default 暗).
            lid = d.get("id")
            if not lid:
                log.warning("quest_created missing id; skipped (%s)", event.get("id"))
                return
            summary = d.get("summary", "")
            complexity = d.get("complexity")
            born_day = event.get("day")
            lifespan_days = d.get("lifespan_days") or _LIFESPAN_DEFAULTS.get(complexity)
            lines[lid] = {
                "id": lid,
                "complexity": complexity,
                "about": d.get("about") or summary,
                "secret": d.get("secret"),
                "anchor": d.get("anchor"),
                "description": d.get("description"),
                "trigger": d.get("trigger"),
                "l3_anchor": d.get("l3_anchor"),
                "stages": d.get("stages", []),
                "threshold": d.get("threshold", 50),
                "stage_idx": -1,
                "state": d.get("state", "暗"),
                "clues_dropped": [],
                "summary": summary,
                "born_day": born_day,
                "lifespan_days": lifespan_days,
            }
            log.debug("quest_created id=%s state=%s born_day=%s", lid, lines[lid]["state"], born_day)
            return

        if t == "quest_opened":
            # Narrator-created brand-new 明 quest (NPC托付/玩家接取).
            lid = d.get("id")
            if not lid:
                log.warning("quest_opened missing id; skipped (%s)", event.get("id"))
                return
            summary = d.get("summary", "")
            complexity = d.get("complexity")
            born_day = event.get("day")
            lifespan_days = d.get("lifespan_days") or _LIFESPAN_DEFAULTS.get(complexity)
            lines[lid] = {
                "id": lid,
                "complexity": complexity,
                "about": d.get("about") or summary,
                "secret": d.get("secret"),
                "anchor": d.get("anchor"),
                "description": d.get("description"),
                "trigger": d.get("trigger"),
                "l3_anchor": d.get("l3_anchor"),
                "stages": d.get("stages", []),
                "threshold": d.get("threshold", 50),
                "stage_idx": -1,
                "state": "明",
                "clues_dropped": [],
                "summary": summary,
                "surfaced_turn": event.get("turn"),
                "born_day": born_day,
                "lifespan_days": lifespan_days,
                "last_advanced_day": born_day,
            }
            log.debug("quest_opened id=%s summary=%r born_day=%s", lid, summary, born_day)
            return

        if t == "lore_advanced":
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("lore_advanced for unknown line %s; skipped", lid)
                return
            si = d.get("stage_idx")
            if si is not None:
                ln["stage_idx"] = si
            hint = d.get("hint")
            if hint and hint not in ln["clues_dropped"]:
                ln["clues_dropped"].append(hint)
            ln["last_advanced_day"] = event.get("day")
            log.debug("lore_advanced id=%s → stage=%s last_advanced_day=%s",
                      lid, ln["stage_idx"], event.get("day"))
            return

        if t == "quest_surfaced":
            # 暗 → 明 transition; only valid on 暗 lines (enforce in validate,
            # but apply is defensive: skip non-暗 lines with a warning).
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_surfaced for unknown line %s; skipped", lid)
                return
            if ln.get("state") != "暗":
                log.warning("quest_surfaced on non-暗 line %s (state=%s); skipped",
                            lid, ln.get("state"))
                return
            ln["state"] = "明"
            turn = event.get("turn")
            ln["surfaced_turn"] = turn
            ln["last_advanced_day"] = event.get("day")
            log.debug("quest_surfaced id=%s → state=明 at turn=%s day=%s",
                      lid, turn, event.get("day"))
            return

        if t == "quest_advanced":
            # Narrator-driven advance on a 明 line; updates the 明账 summary.
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_advanced for unknown line %s; skipped", lid)
                return
            if ln.get("state") != "明":
                log.warning("quest_advanced on non-明 line %s (state=%s); skipped",
                            lid, ln.get("state"))
                return
            if "summary" in d:
                ln["summary"] = d["summary"]
            ln["last_advanced_day"] = event.get("day")
            log.debug("quest_advanced id=%s summary set last_advanced_day=%s",
                      lid, event.get("day"))
            return

        if t == "quest_resolved":
            # 明 → 了结; record by/summary.
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_resolved for unknown line %s; skipped", lid)
                return
            if ln.get("state") != "明":
                log.warning("quest_resolved on non-明 line %s (state=%s); skipped",
                            lid, ln.get("state"))
                return
            ln["state"] = "了结"
            ln["resolved"] = {
                "by": d.get("by"),
                "summary": d.get("summary"),
            }
            log.debug("quest_resolved id=%s by=%s", lid, d.get("by"))
            return

        if t == "quest_demoted":
            # 明 → 暗; replace stages with new_stages, reset stage_idx; keep clues_dropped.
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_demoted for unknown line %s; skipped", lid)
                return
            if ln.get("state") != "明":
                log.warning("quest_demoted on non-明 line %s (state=%s); skipped",
                            lid, ln.get("state"))
                return
            ln["state"] = "暗"
            new_stages = d.get("new_stages")
            if isinstance(new_stages, list):
                ln["stages"] = new_stages
            ln["stage_idx"] = -1
            # clues_dropped is intentionally preserved (history of what was dropped)
            log.debug("quest_demoted id=%s → state=暗 new_stages=%s",
                      lid, len(new_stages) if isinstance(new_stages, list) else None)
            return

        if t == "quest_expired":
            # 暗 simple/medium line whose lifespan elapsed → 了结(by:expiry).
            # Replay-safe guard: skip if already 了结.
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_expired for unknown line %s; skipped", lid)
                return
            if ln.get("state") == "了结":
                log.debug("quest_expired id=%s already 了结; skipped (replay-safe)", lid)
                return
            ln["state"] = "了结"
            ln["resolved"] = {"by": "expiry"}
            log.debug("quest_expired id=%s → 了结 by:expiry", lid)
            return

        if t == "quest_finale_due":
            # 暗 complex line whose lifespan elapsed → mark pending_finale=True (L4 consumes).
            # Replay-safe guard: skip if already pending_finale or 了结.
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_finale_due for unknown line %s; skipped", lid)
                return
            if ln.get("pending_finale") or ln.get("state") == "了结":
                log.debug("quest_finale_due id=%s already pending or 了结; skipped", lid)
                return
            ln["pending_finale"] = True
            log.debug("quest_finale_due id=%s → pending_finale=True", lid)
            return

        if t == "quest_world_resolved":
            # Autonomous world-rescue resolution of a complex 暗 line.
            # Replay-safe: if line is already 了结, skip without change.
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_world_resolved for unknown line %s; skipped", lid)
                return
            if ln.get("state") == "了结":
                log.debug("quest_world_resolved id=%s already 了结; skipped (replay-safe)", lid)
                return
            ln["state"] = "了结"
            ln["resolved"] = {
                "by": d.get("by", "world_rescue"),
                "summary": d.get("summary"),
            }
            ln["pending_finale"] = False
            log.debug("quest_world_resolved id=%s by=%s", lid, ln["resolved"]["by"])
            return

        if t == "quest_catastrophe":
            # Catastrophe resolution of a complex 暗 line (lifespan ended, all rescues failed).
            # Replay-safe: if line is already 了结, skip without change.
            lid = d.get("id")
            ln = lines.get(lid)
            if ln is None:
                log.warning("quest_catastrophe for unknown line %s; skipped", lid)
                return
            if ln.get("state") == "了结":
                log.debug("quest_catastrophe id=%s already 了结; skipped (replay-safe)", lid)
                return
            ln["state"] = "了结"
            ln["resolved"] = {
                "by": d.get("by", "catastrophe"),
                "summary": d.get("summary"),
            }
            ln["pending_finale"] = False
            log.debug("quest_catastrophe id=%s by=%s", lid, ln["resolved"]["by"])
            return

        if t == "lore_seeded":
            # Mark a town as seeded with initial lore lines.
            # Idempotent: re-applying an event is a harmless overwrite.
            # Initializes last_refresh_day from event.day so refresh interval
            # counts from seeding time.
            gen = world["systems"][self.name]["gen"]
            town = d.get("town")
            if not town:
                log.warning("lore_seeded missing town; skipped (%s)", event.get("id"))
                return
            gen.setdefault(town, {})
            gen[town]["seeded"] = True
            gen[town]["last_refresh_day"] = event.get("day")
            log.debug("lore_seeded town=%s day=%s", town, event.get("day"))
            return

        if t == "density_refreshed":
            # Record that the density refresh check ran for this town today.
            # Folds last_refresh_day so the next check is REFRESH_INTERVAL_DAYS from now.
            gen = world["systems"][self.name]["gen"]
            town = d.get("town")
            if not town:
                log.warning("density_refreshed missing town; skipped (%s)", event.get("id"))
                return
            gen.setdefault(town, {})["last_refresh_day"] = event.get("day")
            log.debug("density_refreshed town=%s day=%s", town, event.get("day"))
            return

    # ------------------------------------------------------------------
    # Write path (quests commit section)
    # ------------------------------------------------------------------

    def validate(self, section: str, decl: list, world: dict) -> list[ValidationError]:
        """Validate 'quests' section: each item is {op, id, summary?}.

        Mechanical strictness (op/id/state); creative leniency (summary not required).
        State partition bug-guard:
          surface → id must be a 暗 line.
          advance/resolve → id must be a 明 line.
        """
        if section != "quests":
            return []
        errs: list[ValidationError] = []
        lines = (world.get("systems", {}).get(self.name) or {}).get("lines", {})
        for i, item in enumerate(decl or []):
            op = item.get("op")
            # op validation
            if op not in _QUEST_OPS:
                errs.append(ValidationError(
                    section=section,
                    field=f"[{i}].op",
                    code="bad_enum",
                    hint=f"op 必须是 open/surface/advance/resolve 之一，当前值: {op!r}",
                ))
                # Can't validate state without a valid op; skip further checks for this item
                continue

            if op == "open":
                # open: validate id (must be non-empty AND not already exist) + summary
                lid = item.get("id")
                if not isinstance(lid, str) or not lid:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="quests 每项必须提供 id（事件线标识）",
                    ))
                elif lid in lines:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="dangling_ref",
                        hint=f"id {lid!r} 已存在于事件线中; open 只能用于全新 id",
                    ))
                if not isinstance(item.get("summary"), str) or not item.get("summary"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].summary",
                        code="missing",
                        hint="open 必须提供 summary（一句话任务摘要）",
                    ))
                continue

            # For surface/advance/resolve: id must be a non-empty string
            lid = item.get("id")
            if not isinstance(lid, str) or not lid:
                errs.append(ValidationError(
                    section=section,
                    field=f"[{i}].id",
                    code="missing",
                    hint="quests 每项必须提供 id（事件线标识）",
                ))
                continue

            # For surface/advance/resolve: id must exist in world lines
            ln = lines.get(lid)
            if ln is None:
                errs.append(ValidationError(
                    section=section,
                    field=f"[{i}].id",
                    code="dangling_ref",
                    hint=f"id {lid!r} 不是已有的事件线;请检查 id 是否正确",
                ))
                continue

            # State-partition bug-guard
            state = ln.get("state")
            if op == "surface" and state != "暗":
                errs.append(ValidationError(
                    section=section,
                    field=f"[{i}].id",
                    code="wrong_state",
                    hint=f"只能 surface 一条暗态线（id={lid!r} 当前 state={state!r}）",
                ))
            elif op in {"advance", "resolve"} and state != "明":
                errs.append(ValidationError(
                    section=section,
                    field=f"[{i}].id",
                    code="wrong_state",
                    hint=(f"只能推进/收束明态线（id={lid!r} 当前 state={state!r}）;"
                          f" 若要接取一条暗线请用 surface"),
                ))

        return errs

    def to_events(self, section: str, decl: list, *, turn: int, day: int, scene: str) -> list[dict]:
        """Map well-formed 'quests' items to kernel events."""
        if section != "quests":
            return []
        events: list[dict] = []
        for item in decl or []:
            op = item.get("op")
            lid = item.get("id")
            if op not in _OP_EVENT or not lid:
                log.warning("lore.to_events: skipping malformed item op=%r id=%r", op, lid)
                continue
            ev_type = _OP_EVENT[op]
            deltas: dict = {"id": lid}
            if item.get("summary"):
                deltas["summary"] = item["summary"]
            if op == "resolve":
                deltas["by"] = "player"
            ev = kernel_event(
                ev_type,
                day=day,
                scene=scene,
                summary=f"{op} {lid}",
                deltas=deltas,
                turn=turn,
            )
            events.append(ev)
        return events

    # ------------------------------------------------------------------
    # inject: 明账 only (明 lines as active-quest ledger).
    # The 暗 ambient clue push is handled by station_push_fragment
    # appended by AuthorStrategy.produce — no double-inject here.
    # ------------------------------------------------------------------

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        lines = (world.get("systems", {}).get(self.name) or {}).get("lines", {})

        # Render 明 lines as active-quest ledger.
        ming_lines = [ln for ln in lines.values() if ln.get("state") == "明"]
        if not ming_lines:
            return None

        ledger_lines = ["【任务·明账】（活跃任务，每回合必看）"]
        for ln in ming_lines:
            summary = ln.get("summary") or "（暂无摘要）"
            ledger_lines.append(f"  [{ln['id']}] {summary}")
        text = "\n".join(ledger_lines)
        affordance = ("本回合若推进/收束了某条明态任务，用 quests 段声明 advance/resolve；"
                      "若玩家接取了暗线，用 surface 声明")
        return Fragment(system="lore", layer="scene", text=text, affordance=affordance)
