from __future__ import annotations

from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from engine.log import get_logger

log = get_logger("kernel.digest")


def digest_extract(registry: Registry, prose: str, world: dict) -> TurnCommit:
    """Ask every system to extract its turn-commit sections from narration prose.
    (Used by strategy 乙; the LLM-backed extractor is a system concern, the kernel
    only fans out and merges.)"""
    sections: dict = {}
    for s in registry.systems:
        for name, decl in s.digest_extract(prose, world).items():
            sections[name] = decl
    log.debug("digest_extract sections=%s", sorted(sections))
    return TurnCommit(narration=prose, sections=sections)
