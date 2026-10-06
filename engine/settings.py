"""engine.settings — process-global, runtime-mutable settings holder.

Settings are read from env at startup (or on reset_from_env()) and can be
mutated at runtime via the set_* functions.  Env vars are NOT re-read on
every access (read once into module-level state).

Usage:
    from engine import settings
    settings.get_verbosity()          # "medium"
    settings.set_verbosity("concise") # True  (returns False on invalid)
    settings.get_max_tool_rounds()    # 12

Runtime adjustment interface (future adaptive layer + /verbosity OOC command):
    settings.set_verbosity(level)     # called by play.dispatch_ooc and adaptive layer
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Valid levels
# ---------------------------------------------------------------------------

VERBOSITY_LEVELS = ("concise", "medium", "rich")
CONVERSATION_MODES = ("multiturn", "stateless")

# ---------------------------------------------------------------------------
# Module-level state (process-global, populated on first import or reset_from_env)
# ---------------------------------------------------------------------------

_verbosity: str = "medium"
_max_tool_rounds: int = 12
# Narration STYLE/voice (free text, e.g. "日式轻小说"); "" = neutral (default).
# Orthogonal to verbosity (which is LENGTH). (#R8)
_style: str = ""
# Flavor-pack default voice (set by the flavor resolver at launch). Used by
# get_style() only when no explicit _style is set — explicit style always wins.
_pack_voice: str = ""

_STYLE_MAXLEN = 200

# Turn-authoring conversation mode: "multiturn" keeps one running DM conversation
# across turns + compacts at 70% of the window; "stateless" rebuilds full context
# every turn (the original behavior; the byte-identical fallback).
_conversation_mode: str = "multiturn"


def _parse_verbosity(raw: str) -> str:
    """Return *raw* if it is a valid verbosity level, else 'medium'."""
    v = raw.strip().lower()
    return v if v in VERBOSITY_LEVELS else "medium"


def _parse_style(raw: str) -> str:
    """Normalize a free-text style string ('' = neutral); length-capped."""
    return (raw or "").strip()[:_STYLE_MAXLEN]


def _parse_max_tool_rounds(raw: str) -> int:
    """Return the int value of *raw*, defaulting to 12 on parse error."""
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 12


def _parse_conversation_mode(raw: str) -> str:
    """Return *raw* if a valid conversation mode, else 'multiturn'."""
    v = (raw or "").strip().lower()
    return v if v in CONVERSATION_MODES else "multiturn"


def reset_from_env() -> None:
    """Re-read RPG_NARRATION_VERBOSITY and RPG_MAX_TOOL_ROUNDS from the env.

    Useful at startup (called automatically on module import) and in tests
    (called to reset state after monkeypatching env vars).
    """
    global _verbosity, _max_tool_rounds, _style, _conversation_mode, _pack_voice
    _verbosity = _parse_verbosity(
        os.environ.get("RPG_NARRATION_VERBOSITY", "medium")
    )
    _max_tool_rounds = _parse_max_tool_rounds(
        os.environ.get("RPG_MAX_TOOL_ROUNDS", "12")
    )
    _style = _parse_style(os.environ.get("RPG_NARRATION_STYLE", ""))
    _pack_voice = ""   # cleared on reset; the flavor resolver re-sets it at launch
    _conversation_mode = _parse_conversation_mode(
        os.environ.get("RPG_CONVERSATION_MODE", "multiturn")
    )


# Initialise from env on import
reset_from_env()


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

def get_verbosity() -> str:
    """Return the current narration verbosity level: 'concise', 'medium', or 'rich'."""
    return _verbosity


def set_verbosity(level: str) -> bool:
    """Set the narration verbosity level.

    Args:
        level: One of 'concise', 'medium', 'rich'.

    Returns:
        True if the level was valid and applied; False if invalid (state unchanged).
    """
    global _verbosity
    v = level.strip().lower() if level else ""
    if v not in VERBOSITY_LEVELS:
        return False
    _verbosity = v
    return True


def get_max_tool_rounds() -> int:
    """Return the current max-tool-rounds ceiling for the tool loop."""
    return _max_tool_rounds


def set_max_tool_rounds(n) -> bool:
    """Set the max-tool-rounds ceiling (>=0). Returns False on invalid input."""
    global _max_tool_rounds
    try:
        v = int(n)
    except (ValueError, TypeError):
        return False
    if v < 0:
        return False
    _max_tool_rounds = v
    return True


def get_style() -> str:
    """Return the current narration style/voice ('' = neutral).

    Explicit style (RPG_NARRATION_STYLE / --style / /style) wins over the flavor
    pack's default voice."""
    return _style or _pack_voice


def set_pack_voice(v: str) -> None:
    """Set the flavor pack's default voice; used by get_style() when no explicit
    style is set. Called by the flavor resolver at launch."""
    global _pack_voice
    _pack_voice = _parse_style(v)


def set_style(style: str) -> bool:
    """Set the narration style/voice (free text; '' clears to neutral).

    Returns True always (any string is accepted; it's normalized + capped).
    """
    global _style
    _style = _parse_style(style)
    return True


def get_conversation_mode() -> str:
    """Return the current turn-authoring conversation mode ('multiturn'|'stateless')."""
    return _conversation_mode


def set_conversation_mode(mode: str) -> bool:
    """Set the conversation mode. Returns False on invalid input (state unchanged)."""
    global _conversation_mode
    v = mode.strip().lower() if mode else ""
    if v not in CONVERSATION_MODES:
        return False
    _conversation_mode = v
    return True
