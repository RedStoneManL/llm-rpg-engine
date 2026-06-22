"""Reflection: importance-accumulation trigger + LLM arc synthesis.

should_reflect(accumulated_importance, *, threshold=30) -> bool
    True when accumulated importance meets or exceeds threshold.

reflect(subject, recent_events, *, provider) -> dict
    Synthesise a higher-order "arc" fact-delta for the subject from
    recent events.  Returns {"predicate": "arc", "value": "<summary>"}.
    Caller asserts the returned fact-delta on the subject entity.

Design:
- Uses provider.complete() (text mode) + JSON parsing with one retry.
- Prompts LLM to respond ONLY with a JSON object; parses the first
  {...} block found in the response to tolerate minor prose wrapping.
- On total failure after two attempts, raises ValueError.
"""

from __future__ import annotations

import json
import re
from typing import Any

from engine.log import get_logger

log = get_logger("memory.reflection")

# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------

def should_reflect(accumulated_importance: float, *, threshold: float = 30) -> bool:
    """Return True when accumulated_importance >= threshold."""
    result = accumulated_importance >= threshold
    log.debug("should_reflect acc=%.1f threshold=%.1f → %s",
              accumulated_importance, threshold, result)
    return result


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是一位资深TRPG叙事分析师。根据提供的近期事件，为指定角色生成一条高层次的叙事弧（arc）总结。

要求：
- 仅输出一个JSON对象，格式为 {{"predicate": "arc", "value": "<简洁的叙事弧总结>"}}
- value 应简洁（不超过50字），描述角色在这段时间经历的核心转变或成长
- 不要输出JSON以外的任何文字
"""


def _extract_json_object(text: str) -> dict | None:
    """Extract the first JSON object {...} from text, or None."""
    # Try direct parse first
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    # Try to find first {...} block
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def reflect(subject: str,
            recent_events: list[dict[str, Any]],
            *,
            provider) -> dict:
    """Synthesise a higher-order arc fact-delta for the subject.

    Args:
        subject:       Entity name (e.g. "艾拉").
        recent_events: List of event dicts (need at least 'summary' key).
        provider:      LLMProvider instance (must not be None).

    Returns:
        dict with {"predicate": "arc", "value": "<synthesis>"}.

    Raises:
        ValueError: if both LLM attempts fail to produce valid JSON.
    """
    # Build the user prompt
    event_lines = "\n".join(
        f"- [Day {e.get('day', '?')}] {e.get('summary', '')}"
        for e in recent_events
    )
    user_prompt = (
        f"角色: {subject}\n\n"
        f"近期事件:\n{event_lines}\n\n"
        f"请为 {subject} 生成叙事弧总结（仅输出JSON）："
    )

    log.debug("reflect subject=%s events=%d", subject, len(recent_events))

    # Two attempts: use provider.complete() directly so we can parse flexibly
    last_raw = ""
    for attempt in range(2):
        # No explicit cap: reasoning models need room before emitting the arc
        # JSON; a tiny cap made this always fail. Inherit the provider default.
        raw = provider.complete(_SYSTEM_PROMPT, user_prompt)
        last_raw = raw
        log.debug("reflect attempt=%d raw=%r", attempt, raw[:80])

        result = _extract_json_object(raw)
        if result is not None and isinstance(result, dict):
            # Ensure required keys exist
            if "predicate" not in result:
                result["predicate"] = "arc"
            if "value" not in result:
                result["value"] = raw.strip()
            return result

    # Both attempts failed
    raise ValueError(
        f"reflect: failed to parse LLM arc synthesis after 2 attempts; "
        f"last raw: {last_raw!r:.120}"
    )
