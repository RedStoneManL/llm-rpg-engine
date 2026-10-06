"""loop.genesis.common — shared genesis helpers."""
from __future__ import annotations

from engine.oracle import Oracle, scene_seed, load_table  # noqa: F401
from engine.log import get_logger
from kernel.events import kernel_event  # noqa: F401
from kernel.observability import get_tracer  # noqa: F401
from llm.structured import complete_structured  # noqa: F401

log = get_logger("loop.genesis")

def _draw_distinct(oracle, entries, k):
    """Weighted draw of up to k DISTINCT entries (sample without replacement)."""
    pool = list(entries)
    out = []
    for _ in range(min(k, len(pool))):
        e = oracle.draw(pool)
        out.append(e)
        pool.remove(e)
    return out


def _empty_str(v) -> bool:
    """True when v is not a non-blank string (None / non-str / blank)."""
    return not (isinstance(v, str) and v.strip())
