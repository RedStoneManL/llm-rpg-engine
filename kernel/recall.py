from __future__ import annotations

from kernel.registry import Registry
from kernel.contextsystem import RecallHit
from engine.log import get_logger

log = get_logger("kernel.recall")


def recall(registry: Registry, query: str, world: dict, k: int | None = None) -> list[RecallHit]:
    """Fan out the query to every system's recall(), merge, sort by score desc."""
    hits: list[RecallHit] = []
    for s in registry.systems:
        hits.extend(s.recall(query, world))
    hits.sort(key=lambda h: h.score, reverse=True)
    log.debug("recall query=%r hits=%d k=%s", query, len(hits), k)
    return hits[:k] if k else hits
