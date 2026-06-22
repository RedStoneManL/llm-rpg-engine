"""Importance scorer for RPG events.

heuristic_floor(event) -> int  (0-10)
    Heuristic estimate based on event type + deltas + thread_refs.
    High-signal event types (thread_open, relationship_change, etc.) get
    elevated base scores. Events with deltas or thread_refs get a bonus.

score(event, *, provider=None) -> int  (1-10)
    max(heuristic_floor, LLM rubric score if provider given).
    LLM rubric: anchored 1-10
        1  寒暄/赶路
        3  有信息对话
        6  关系转折
        8  重大抉择
        10 背叛/死亡
    Parses the first integer from the LLM's response; clamps to [1, 10].
    With provider=None returns heuristic only (may be 0).
"""

from __future__ import annotations

import re
from typing import Any

from engine.log import get_logger

log = get_logger("memory.importance")

# ---------------------------------------------------------------------------
# Event-type base scores (heuristic)
# ---------------------------------------------------------------------------

_TYPE_BASE: dict[str, int] = {
    # trivial
    "action": 1,
    "location_change": 1,
    "oracle_roll": 2,
    # informational
    "dialogue_beat": 2,
    "world_fact": 3,
    "item_change": 3,
    # meaningful developments
    "thread_advance": 4,
    "combat_result": 4,
    "promise_made": 5,
    "promise_kept": 5,
    "level_change": 4,
    "player_choice": 5,
    # significant
    "thread_open": 6,
    "thread_resolve": 5,
    "landmark": 5,
    "villain_knowledge_gain": 6,
    # high-signal
    "relationship_change": 7,
    "character_development": 6,
    "character_reveal": 7,
    # meta
    "director_fired": 3,
}

_DELTA_BONUS = 1      # having any deltas adds 1
_THREAD_REF_BONUS = 1  # having thread_refs adds 1
_MAX_SCORE = 10
_MIN_SCORE_HEURISTIC = 0


def heuristic_floor(event: dict[str, Any]) -> int:
    """Return a heuristic importance floor in [0, 10].

    Higher for event types that carry meaningful narrative weight;
    bonus for events with non-empty deltas or thread_refs.
    """
    base = _TYPE_BASE.get(event.get("type", ""), 1)
    bonus = 0
    if event.get("deltas"):
        bonus += _DELTA_BONUS
    if event.get("thread_refs"):
        bonus += _THREAD_REF_BONUS
    result = min(base + bonus, _MAX_SCORE)
    log.debug("heuristic_floor type=%s base=%d bonus=%d → %d",
              event.get("type"), base, bonus, result)
    return result


# ---------------------------------------------------------------------------
# LLM rubric
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a TRPG narrative analyst. Rate the importance of the following event \
on a 1-10 scale using these anchors:
1  = 寒暄/赶路 (trivial chitchat or travel)
3  = 有信息对话 (informative dialogue, minor discoveries)
6  = 关系转折 (relationship shift, thread opened/resolved)
8  = 重大抉择 (major decision, significant consequence)
10 = 背叛/死亡 (betrayal, death, world-altering revelation)
Reply with ONLY a single integer, e.g. "7".\
"""


def _extract_int(text: str) -> int | None:
    """Extract the first integer from a string, or None if none found."""
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def score(event: dict[str, Any], *, provider=None) -> int:
    """Return importance in [1, 10] (heuristic only if provider is None).

    With provider: max(heuristic_floor, clamped_llm_score).
    Without provider: returns heuristic_floor (may be 0).
    """
    floor = heuristic_floor(event)

    if provider is None:
        log.debug("score heuristic-only → %d", floor)
        return floor

    user_prompt = (
        f"事件类型: {event.get('type')}\n"
        f"场景: {event.get('scene')}\n"
        f"参与者: {', '.join(event.get('actors', []))}\n"
        f"摘要: {event.get('summary')}\n"
        f"变化: {event.get('deltas')}\n"
        f"线索: {event.get('thread_refs')}"
    )

    # No explicit cap: reasoning models burn completion tokens on hidden
    # reasoning before the score, so a tiny cap yields empty content. Inherit
    # the provider default; the model stops right after the number anyway.
    raw = provider.complete(_SYSTEM_PROMPT, user_prompt)
    log.debug("score LLM raw=%r", raw[:40])

    parsed = _extract_int(raw)
    if parsed is None:
        log.warning("score: could not parse int from LLM response %r; using heuristic", raw)
        return floor

    llm_score = max(1, min(10, parsed))
    result = max(floor, llm_score)
    log.debug("score floor=%d llm=%d → %d", floor, llm_score, result)
    return result
