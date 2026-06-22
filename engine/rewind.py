# engine/rewind.py
from pathlib import Path

from engine.store import EventStore
from engine.archive import ArchiveStore
from engine.compact import compact
from engine.recall import reindex
from engine.log import get_logger

log = get_logger("rewind")


def last_turn(campaign_dir):
    """Max turn across BOTH chunks and events, so /veto (rewind --last) reaches
    director-emitted events even when a turn produced no front-stage chunk."""
    cd = Path(campaign_dir)
    with ArchiveStore(cd / "archive.db") as a:
        ct = a.max_turn()
    with EventStore(cd / "events.db", cd / "events.jsonl") as s:
        et = max((e.get("turn") or 0 for e in s.iter_events()), default=0)
    return max(ct, et)


def rewind(campaign_dir, turn, *, embedder=None):
    """Retract events + remove chunks with turn>=`turn`, then reproject /
    recompact / reindex. State, working memory and vectors roll back automatically."""
    cd = Path(campaign_dir)
    with EventStore(cd / "events.db", cd / "events.jsonl") as s:
        n_ev = s.retract_from_turn(turn)
    with ArchiveStore(cd / "archive.db") as a:
        n_ch = a.delete_from_turn(turn)
    compact(cd)                      # reproject + working_memory
    reindex(cd, embedder=embedder)   # rebuild vector index (no-op if no embedder)
    log.debug("rewind turn>=%s: events=%d chunks=%d", turn, n_ev, n_ch)
    return {"events_retracted": n_ev, "chunks_removed": n_ch}
