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

# ---------------------------------------------------------------------------
# Module-level state (process-global, populated on first import or reset_from_env)
# ---------------------------------------------------------------------------

_verbosity: str = "medium"
_max_tool_rounds: int = 12


def _parse_verbosity(raw: str) -> str:
    """Return *raw* if it is a valid verbosity level, else 'medium'."""
    v = raw.strip().lower()
    return v if v in VERBOSITY_LEVELS else "medium"


def _parse_max_tool_rounds(raw: str) -> int:
    """Return the int value of *raw*, defaulting to 12 on parse error."""
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 12


def reset_from_env() -> None:
    """Re-read RPG_NARRATION_VERBOSITY and RPG_MAX_TOOL_ROUNDS from the env.

    Useful at startup (called automatically on module import) and in tests
    (called to reset state after monkeypatching env vars).
    """
    global _verbosity, _max_tool_rounds
    _verbosity = _parse_verbosity(
        os.environ.get("RPG_NARRATION_VERBOSITY", "medium")
    )
    _max_tool_rounds = _parse_max_tool_rounds(
        os.environ.get("RPG_MAX_TOOL_ROUNDS", "12")
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
