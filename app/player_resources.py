"""Narrow player-facing presentation for a durable resource adjudication.

The resource resolver is the authority for balances and affordability.  Prose
can describe a scene, but it must not be the only place a player learns whether
an explicitly requested spend succeeded.  This adapter exposes only the most
recent, protagonist-owned ``spent`` or ``insufficient`` outcome already folded
into the projected ``ResourceSystem`` state.  It never consults EventStore or
the raw action text.
"""
from __future__ import annotations

import math
from typing import Any


def player_resource_outcome(world: dict[str, Any], protagonist: str | None) -> dict[str, Any] | None:
    """Return a bounded public resource outcome for the current protagonist.

    ``last_resolution`` can contain arbitrary host-side fields.  Keep the
    player DTO deliberately small and require a finite balance for the resolved
    resource.  An ordinary ``none`` resolution is omitted so unrelated actions
    do not look like a failed transaction.
    """
    if not isinstance(protagonist, str) or not protagonist:
        return None
    systems = world.get("systems") if isinstance(world, dict) else None
    resource_state = systems.get("resources") if isinstance(systems, dict) else None
    result = resource_state.get("last_resolution") if isinstance(resource_state, dict) else None
    if not isinstance(result, dict) or result.get("subject") != protagonist:
        return None
    outcome = result.get("outcome")
    resource = result.get("resource")
    requested = result.get("requested_amount")
    turn = result.get("turn")
    after = result.get("after")
    balance = after.get(resource) if isinstance(after, dict) and isinstance(resource, str) else None
    if (outcome not in {"spent", "insufficient"}
            or not isinstance(resource, str) or not resource
            or not _finite_number(requested) or not _finite_number(balance)
            or type(turn) is not int or turn < 1):
        return None
    return {
        "turn": turn,
        "outcome": outcome,
        "resource": resource,
        "requested_amount": requested,
        "balance": balance,
    }


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
