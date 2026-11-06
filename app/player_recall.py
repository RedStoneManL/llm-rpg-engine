"""Narrow, protagonist-POV recall query shared by player-facing adapters.

This module intentionally returns only rendered recall text.  Player CLI and the
loopback dashboard must not receive canonical graph objects, recall references, or
an alternate discovery path merely because they expose the same read affordance.
"""
from __future__ import annotations

from context.access import pov_world
from kernel.recall import recall as kernel_recall


def player_recall(registry, world: dict, scene: dict, query: str, *, limit: int = 5) -> list[str]:
    """Search only the current protagonist's redacted POV and return display text.

    ``kernel_recall`` fans out through registered systems.  Give it the same
    redacted projection that normal player context uses, then keep only bounded
    strings for presentation.  The function is read-only: it creates no event,
    receipt, transcript, or discovery grant.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    if type(limit) is not int or limit < 1:
        raise ValueError("recall limit must be a positive integer")
    visible_world = pov_world(
        world, scene, pov=scene.get("protagonist"), redact_facts=True,
    )
    return [
        hit.text for hit in kernel_recall(registry, query.strip(), visible_world, k=limit)
        if isinstance(getattr(hit, "text", None), str)
    ][:limit]


def recall_with_sources(engine, scene, query):
    from engine.history import PlayerHistory
    records = PlayerHistory(engine.store,scene.get('protagonist')).search(query)
    hits = [f"[第{r['day']}天 · 回合{r['turn']} · {r['kind']}] {r['text'][:180]}" for r in records]
    hits.extend(player_recall(engine.registry,engine.world,scene,query))
    result = {'hits':hits[:10]}
    if records:
        result['sources'] = records
    return result
