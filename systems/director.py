"""DirectorSystem — owns the 暗骰 director's first-class events so they pass the
strict store, projects the most-recent fired directive into a pending queue, and
injects that pending directive as a 导演 context Fragment for the NEXT turn.

B1 scope: campaign_seeded (genesis seed) + oracle_roll (audit) + director_fired
(the directive). Director events are harness-authored (appended directly by
loop/director.run_director), so this system declares NO commit sections in B1.

World slice (world["systems"]["director"]):
    {"pending": [<directive dict>, ...], "consumed_through_turn": <int>}
campaign_seed is surfaced into world["meta"]["campaign_seed"] by apply().
"""
from __future__ import annotations

from typing import Any

from kernel.contextsystem import ContextSystem, ValidationError, Fragment
from engine.log import get_logger

log = get_logger("systems.director")


class DirectorSystem(ContextSystem):
    name = "director"

    def event_types(self) -> set[str]:
        return {"campaign_seeded", "oracle_roll", "director_fired",
                "thread_open", "thread_advance", "directive_consumed"}

    def commit_sections(self) -> set[str]:
        # B1: director events are harness-authored, not LLM-authored.
        return set()

    def empty_state(self) -> dict:
        return {"pending": [], "consumed_through_turn": 0, "threads": {}}

    def apply(self, world: dict, event: dict) -> None:
        t = event["type"]
        d = event.get("deltas", {})
        if t == "campaign_seeded":
            seed = d.get("campaign_seed")
            if seed is not None:
                world.setdefault("meta", {})["campaign_seed"] = seed
                log.debug("campaign_seeded → meta campaign_seed=%s", seed)
            return
        if t == "oracle_roll":
            # Audit-only: recorded for reproducibility/importance, no state change.
            return
        if t == "director_fired":
            directive = {
                "type": d.get("type"),
                "magnitude": d.get("magnitude"),
                "valence": d.get("valence"),
                "event_type": d.get("event_type"),
                "event_hint": d.get("event_hint"),
                "twist": d.get("twist"),
                "twist_hint": d.get("twist_hint"),
                "turn": event.get("turn") or 0,
                "scene": event.get("scene"),
                "consumed": False,
            }
            slice_ = world["systems"][self.name]
            slice_["pending"].append(directive)
            log.debug("director_fired → enqueued directive %s/%s turn=%s",
                      directive["event_type"], directive["twist"], directive["turn"])
            return
        if t == "directive_consumed":
            through_turn = d.get("through_turn")
            if through_turn is not None:
                slice_ = world["systems"][self.name]
                slice_["consumed_through_turn"] = max(
                    slice_.get("consumed_through_turn", 0), int(through_turn)
                )
                log.debug("directive_consumed → consumed_through_turn=%d", through_turn)
            return
        if t == "thread_open":
            tid = d.get("id")
            if not tid:
                log.warning("thread_open missing id; skipped (%s)", event.get("id"))
                return
            threads = world["systems"][self.name].setdefault("threads", {})
            threads[tid] = {
                "id": tid,
                "status": d.get("status", "活跃"),
                "speed": d.get("speed", "中"),
                "dormant": bool(d.get("dormant", False)),
                "trait": d.get("trait"),
                "archetype": d.get("archetype"),
                "event_type": d.get("event_type"),
                "last_advanced_scene": d.get("last_advanced_scene", event.get("scene")),
            }
            log.debug("thread_open id=%s dormant=%s", tid, threads[tid]["dormant"])
            return
        if t == "thread_advance":
            tid = d.get("id")
            threads = world["systems"][self.name].setdefault("threads", {})
            if tid in threads:
                if "last_advanced_scene" in d:
                    threads[tid]["last_advanced_scene"] = d["last_advanced_scene"]
                if d.get("dormant") is not None:
                    threads[tid]["dormant"] = bool(d["dormant"])
                log.debug("thread_advance id=%s → scene=%s", tid,
                          threads[tid]["last_advanced_scene"])
                if d.get("surface") and tid in threads:
                    th = threads[tid]
                    world["systems"][self.name]["pending"].append({
                        "type": "dormant_thread",
                        "magnitude": "small",
                        "valence": None,
                        "event_type": th.get("archetype"),
                        "event_hint": f"暗线浮现（{th.get('event_type') or ''}）",
                        "twist": th.get("trait") or "",
                        "twist_hint": "让这条暗线以一个具体细节浮出水面",
                        "turn": event.get("turn") or 0,
                        "scene": event.get("scene"),
                        "consumed": False,
                    })
                    log.debug("thread_advance surface → enqueued thread directive for %s", tid)
            else:
                log.warning("thread_advance for unknown thread %s; skipped", tid)
            return

    _MAG_LABEL = {"small": "小", "big": "大", "crit": "暴击(高潮)"}

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        """Render the newest UN-consumed directive as a backstage 导演 instruction.

        The narrator must weave the seed (event_type + twist + magnitude) into
        prose naturally; resulting world-changes flow through the normal commit.
        A directive is shown exactly once: the director hook marks prior pending
        directives consumed at the start of its next run (see loop/director)."""
        slice_ = world.get("systems", {}).get(self.name) or {}
        consumed_through = slice_.get("consumed_through_turn", 0)
        pending = [
            d for d in slice_.get("pending", [])
            if not d.get("consumed") and d.get("turn", 0) > consumed_through
        ]
        if not pending:
            return None
        d = pending[-1]  # newest
        magnitude_raw = d.get("magnitude") or ""
        mag_label = self._MAG_LABEL.get(magnitude_raw, magnitude_raw)
        # Include raw key so downstream checks (e.g. tests) can match either form.
        mag = f"{mag_label}({magnitude_raw})" if mag_label != magnitude_raw else mag_label
        valence = d.get("valence")
        val_txt = ""
        if valence == "boon":
            val_txt = "（基调:意外之喜/转机）"
        elif valence == "disaster":
            val_txt = "（基调:灾祸/危局）"
        text = (
            "【导演·暗骰】本回合请自然地引入一个转折，不要直白说明这是系统安排：\n"
            f"  事件原型：{d.get('event_type')} — {d.get('event_hint') or ''}\n"
            f"  反转：{d.get('twist')} — {d.get('twist_hint') or ''}\n"
            f"  量级：{mag}{val_txt}\n"
            "  把它写成主角此刻可感知、可回应的具体情节；但绝不替玩家决定下一步。"
        )
        affordance = "本回合应体现上述【导演·暗骰】转折"
        log.debug("inject directive %s/%s turn=%s", d.get("event_type"),
                  d.get("twist"), d.get("turn"))
        return Fragment(system="director", layer="scene", text=text, affordance=affordance)
