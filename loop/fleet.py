"""loop.fleet — backstage digest_fleet: importance scoring + reflection write-back
+ P2 recap maintenance (narration recording, scene summarization, quest backstop).

digest_fleet(registry, store, new_events, world, *, provider, threshold=30,
             importance_provider=None, narration_text=None, scene=None,
             recap_provider=None) -> list[events_appended]:

  Arc-reflection phase (original):
    For each new event:
      1. Score importance via memory.importance.score(event, provider=provider).
      2. Accumulate score per primary subject (actors[0] or deltas.get("id")).
      3. For any subject whose accumulated score crosses threshold:
         a. Call memory.reflection.reflect(subject, relevant_events, provider=provider)
            → {"predicate": "arc", "value": "..."}.
         b. Build a character_evolved arc event (predicate="arc").
         c. Append it to the store.

  P2 recap/quest maintenance phase (new, runs after arc phase, each step non-fatal):
    1. Record narration: if narration_text+scene given, append narration_recorded event.
    2. Summarize aged scene: if a scene aged out of the recent-N window AND recap_provider
       is given, append a scene_summarized event (cheap-model); if summaries exceed
       RECAP_SUMMARY_FANOUT, append a recap_recompressed event.
    3. Quest backstop: if no active threads AND substantive player event, append
       a quest_created event with state:"暗" (conservative FLAG, not auto-open活跃).

  Return value: all events appended by this call (arc + narration_recorded +
  scene_summarized + recap_recompressed + backstop quest events).
"""
from __future__ import annotations

import hashlib
from typing import Any

from kernel.registry import Registry
from kernel.events import kernel_event
from kernel.projection import project
from engine.log import get_logger
import memory.importance as importance_mod
import memory.reflection as reflection_mod
import systems.narrative as nmod
from llm.structured import complete_structured

log = get_logger("loop.fleet")

_SUMMARIZE_SYSTEM = (
    "你是一名剧情摘要员。把下面这一场景的全部原文压缩成一句话中文摘要。"
    "只输出 JSON，格式：{\"summary\": \"一句话摘要\"}"
)

_RECOMPRESS_SYSTEM = (
    "你是一名剧情摘要员。把下面多场景的摘要进一步压缩成一段话总概要。"
    "只输出 JSON，格式：{\"summary\": \"总概要\"}"
)

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def _primary_subject(event: dict) -> str | None:
    """Extract the primary subject of an event.

    Precedence: actors[0] → deltas["id"] → deltas["subject"] → None.
    """
    actors = event.get("actors") or []
    if actors:
        return actors[0]
    deltas = event.get("deltas") or {}
    return deltas.get("id") or deltas.get("subject") or None


def _next_turn_in_store(store) -> int:
    """Return max existing event turn + 1, or 1 if none."""
    max_t = 0
    for ev in store.iter_events():
        t = ev.get("turn") or 0
        if t > max_t:
            max_t = t
    return max_t + 1


def _summary_validate(obj):
    return ([] if isinstance(obj, dict) and isinstance(obj.get("summary"), str)
            and obj["summary"].strip()
            else ['missing or empty string field "summary"'])


def summarize_scene(provider, scene_id: str, raw_texts: list[str]) -> dict | None:
    """Cheap-model summarize a scene's raw narration texts into one-line summary.

    Returns a scene_summarized kernel_event, or None on failure.
    """
    try:
        user = f"场景 {scene_id} 的原文如下：\n\n" + "\n".join(raw_texts)
        obj, errors = complete_structured(
            provider,
            system=_SUMMARIZE_SYSTEM,
            user=user,
            validate=_summary_validate,
            max_repairs=1,
            schema_reminder='Required: {"summary": "一句话摘要"}',
            log_label="summarize",
        )
        summary = (obj.get("summary") or "").strip() if isinstance(obj, dict) else ""
        if not summary or errors:
            log.warning("summarize_scene: empty summary returned for scene=%s", scene_id)
            return None
        return kernel_event(
            "scene_summarized",
            day=1,
            scene=scene_id,
            summary=f"scene summary: {scene_id}",
            deltas={"scene": scene_id, "summary": summary},
        )
    except Exception:
        log.exception("summarize_scene: failed for scene=%s (non-fatal)", scene_id)
        return None


def backstop_quests(world: dict, new_events: list[dict]) -> dict | None:
    """Conservative backstop: flag a 暗 quest line only when ZERO 明 lines exist
    AND the turn had a substantive player event (heuristic_floor >= 2).

    Returns a harness-authored quest_created kernel_event with state:"暗",
    or None if the conditions aren't met.
    """
    lines: dict = (world.get("systems", {}).get("lore") or {}).get("lines", {})
    # Stay silent when any 明 (active) line exists (narrator is doing their job)
    if any(ln.get("state") == "明" for ln in lines.values()):
        return None

    # Find the most-significant new event
    best_ev = None
    best_floor = 0
    for ev in new_events:
        floor = importance_mod.heuristic_floor(ev)
        if floor > best_floor:
            best_floor = floor
            best_ev = ev

    if best_ev is None or best_floor < 2:
        return None

    # Coin a stable id from the event summary (unique enough for a backstop flag)
    raw_id = best_ev.get("summary", "")[:64]
    short_hash = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:8]
    tid = f"th_auto_{short_hash}"

    # Drop-on-dup: don't create a duplicate flag for the same event summary
    if tid in lines:
        log.debug("backstop_quests: tid=%s already exists in lore lines, skipping", tid)
        return None

    day = max((ev.get("day", 1) for ev in new_events), default=1)
    scene = best_ev.get("scene", "unknown")

    ev = kernel_event(
        "quest_created",
        day=day,
        scene=scene,
        summary=f"backstop 暗 flag: {best_ev.get('summary', '')}",
        deltas={
            "id": tid,
            "summary": best_ev.get("summary", ""),
            "state": "暗",
        },
    )
    log.debug("backstop_quests: flagging tid=%s from event summary=%r",
              tid, best_ev.get("summary", "")[:40])
    return ev


def digest_fleet(
    registry: Registry,
    store,
    new_events: list[dict],
    world: dict,
    *,
    provider,
    threshold: float = 30,
    importance_provider=None,
    narration_text: str | None = None,
    scene: str | None = None,
    recap_provider=None,
) -> list[dict]:
    """Score importance per new event, accumulate per subject, trigger reflection on threshold.
    Then maintain recap (narration recording + scene summarization) and storyline backstop.

    Args:
        registry:          Kernel registry (for event type validation and projection).
        store:             Open EventStore — arc/narration/summary events appended here.
        new_events:        List of newly appended event dicts (already in store;
                           we score them and may add arc events on top).
        world:             Current projected world (before arc events).
        provider:          LLMProvider for arc reflection.
        threshold:         Accumulated importance score that triggers reflection.
        importance_provider: Provider for importance scoring (defaults to heuristic-only).
        narration_text:    This turn's verbatim narration prose (P2 recap).
        scene:             Current scene id (P2 recap).
        recap_provider:    Cheap LLMProvider for scene summarization (P2 recap).

    Returns:
        List of all events appended to the store by this call.
    """
    appended_all: list[dict] = []

    # ------------------------------------------------------------------
    # Phase 1: Arc reflection (unchanged from original)
    # ------------------------------------------------------------------
    if new_events:
        accumulator: dict[str, tuple[float, list[dict]]] = {}

        for ev in new_events:
            subject = _primary_subject(ev)
            if subject is None:
                log.debug("digest_fleet: event %s has no subject, skipping", ev.get("id"))
                continue

            score = importance_mod.score(ev, provider=importance_provider)
            log.debug("digest_fleet: subject=%s event_type=%s score=%d",
                      subject, ev.get("type"), score)

            if subject in accumulator:
                old_total, old_events = accumulator[subject]
                accumulator[subject] = (old_total + score, old_events + [ev])
            else:
                accumulator[subject] = (float(score), [ev])

        arc_day = max((ev.get("day", 1) for ev in new_events), default=1)
        arc_scene = new_events[0].get("scene", "fleet") if new_events else "fleet"

        for subject, (total_score, subject_events) in accumulator.items():
            log.debug("digest_fleet: subject=%s total_score=%.1f threshold=%.1f",
                      subject, total_score, threshold)

            if not reflection_mod.should_reflect(total_score, threshold=threshold):
                continue

            log.debug("digest_fleet: threshold crossed for subject=%s, calling reflect()", subject)

            try:
                arc_delta = reflection_mod.reflect(subject, subject_events, provider=provider)
            except ValueError as exc:
                log.error("digest_fleet: reflection failed for subject=%s: %s", subject, exc)
                continue

            arc_ev = kernel_event(
                "character_evolved",
                day=arc_day,
                scene=arc_scene,
                summary=f"{subject} arc: {arc_delta.get('value', '')}",
                actors=[subject],
                deltas={
                    "id": subject,
                    "predicate": arc_delta.get("predicate", "arc"),
                    "value": arc_delta.get("value", ""),
                    "op": "evolve",
                },
            )
            store.append(arc_ev)
            appended_all.append(arc_ev)
            log.debug("digest_fleet: arc event appended for subject=%s", subject)

    # ------------------------------------------------------------------
    # Phase 2: P2 recap maintenance (narration recording)
    # ------------------------------------------------------------------
    if narration_text and scene:
        try:
            # Get the next turn number to stamp the narration event
            next_turn = _next_turn_in_store(store)
            arc_day = max((ev.get("day", 1) for ev in new_events), default=1) if new_events else 1
            narr_ev = kernel_event(
                "narration_recorded",
                day=arc_day,
                scene=scene,
                summary="narration recorded",
                deltas={"scene": scene, "text": narration_text},
                turn=next_turn,
            )
            store.append(narr_ev)
            appended_all.append(narr_ev)
            log.debug("digest_fleet: narration_recorded scene=%s len=%d", scene, len(narration_text))
        except Exception:
            log.exception("digest_fleet: narration recording failed (non-fatal)")

    # ------------------------------------------------------------------
    # Phase 3: Summarize aged scene (gated — only when a scene ages out)
    # ------------------------------------------------------------------
    if recap_provider is not None:
        try:
            # Re-project once to get post-narration state
            post = project(registry, store.iter_events())
            ns = post.get("systems", {}).get("narrative") or {}
            aged = nmod.aged_out_scene(ns)

            if aged is not None:
                # Find the aged bucket's raw texts
                aged_bucket = next(
                    (b for b in ns.get("scenes", []) if b["scene"] == aged),
                    None,
                )
                if aged_bucket and aged_bucket.get("raw"):
                    summ_ev = summarize_scene(recap_provider, aged, aged_bucket["raw"])
                    if summ_ev is not None:
                        next_turn = _next_turn_in_store(store)
                        summ_ev["turn"] = next_turn
                        arc_day = max((ev.get("day", 1) for ev in new_events), default=1) if new_events else 1
                        summ_ev["day"] = arc_day
                        store.append(summ_ev)
                        appended_all.append(summ_ev)
                        log.debug("digest_fleet: scene_summarized scene=%s", aged)

                        # Recompress check: if #summarized buckets > RECAP_SUMMARY_FANOUT
                        try:
                            post2 = project(registry, store.iter_events())
                            ns2 = post2.get("systems", {}).get("narrative") or {}
                            buckets2 = ns2.get("scenes", [])
                            summarized_buckets = [
                                b for b in buckets2
                                if b.get("summary") is not None
                            ]
                            if len(summarized_buckets) > nmod.RECAP_SUMMARY_FANOUT:
                                # Summarize the oldest summaries into super_summary
                                oldest_summaries = [
                                    f"〔{b['scene']}〕{b['summary']}"
                                    for b in summarized_buckets[: nmod.RECAP_SUMMARY_FANOUT]
                                ]
                                user_rc = "以下是多个场景的摘要，请压成一段总概要：\n\n" + "\n".join(oldest_summaries)
                                rc_obj, rc_errors = complete_structured(
                                    recap_provider,
                                    system=_RECOMPRESS_SYSTEM,
                                    user=user_rc,
                                    validate=_summary_validate,
                                    max_repairs=1,
                                    schema_reminder='Required: {"summary": "总概要"}',
                                    log_label="recap",
                                )
                                rc_summary = (rc_obj.get("summary") or "").strip() if isinstance(rc_obj, dict) else ""
                                if rc_summary and not rc_errors:
                                    rc_ev = kernel_event(
                                        "recap_recompressed",
                                        day=arc_day,
                                        scene=scene or aged,
                                        summary="recap recompressed",
                                        deltas={
                                            "super_summary": rc_summary,
                                            "summarized_through_index": len(summarized_buckets),
                                        },
                                        turn=_next_turn_in_store(store),
                                    )
                                    store.append(rc_ev)
                                    appended_all.append(rc_ev)
                                    log.debug("digest_fleet: recap_recompressed through_index=%d",
                                              len(summarized_buckets))
                        except Exception:
                            log.exception("digest_fleet: recompress check failed (non-fatal)")
        except Exception:
            log.exception("digest_fleet: scene summarization failed (non-fatal)")

    # ------------------------------------------------------------------
    # Phase 4: Storyline backstop (conservative 休眠 flag)
    # ------------------------------------------------------------------
    if new_events:
        try:
            post_world = project(registry, store.iter_events())
            backstop_ev = backstop_quests(post_world, new_events)
            if backstop_ev is not None:
                backstop_ev["turn"] = _next_turn_in_store(store)
                store.append(backstop_ev)
                appended_all.append(backstop_ev)
                log.debug("digest_fleet: backstop quest event appended")
        except Exception:
            log.exception("digest_fleet: backstop_quests failed (non-fatal)")

    log.debug("digest_fleet: done; total_appended=%d", len(appended_all))
    return appended_all
