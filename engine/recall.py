# engine/recall.py
from pathlib import Path

from engine.archive import ArchiveStore
from engine.store import EventStore
from engine.log import get_logger
from engine.vectorstore import VectorStore
from engine.embed import get_embedder

log = get_logger("recall")

def _archive(campaign_dir):
    return ArchiveStore(Path(campaign_dir) / "archive.db")

def _vectors(campaign_dir):
    return VectorStore(Path(campaign_dir) / "vectors.db")

def _events(campaign_dir):
    cd = Path(campaign_dir)
    return EventStore(cd / "events.db", cd / "events.jsonl")

def reindex(campaign_dir, *, embedder=None):
    """Embed all archive chunks into the vector store. Returns count. Rebuilds from scratch."""
    emb = embedder or get_embedder()
    if emb is None:
        log.debug("reindex skipped: no embedder")
        return 0
    with _archive(campaign_dir) as a:
        chunks = list(a.iter_chunks())
    texts = [c["text"] for c in chunks]
    vecs = emb.embed(texts) if texts else []
    with _vectors(campaign_dir) as vs:
        vs.clear()
        for c, v in zip(chunks, vecs):
            vs.add(c["chunk_id"], v)
    log.debug("reindex embedded=%d", len(chunks))
    return len(chunks)

def recall(campaign_dir, query, *, k=5, entity=None, day=None, semantic=True, embedder=None):
    """FTS + structured + (optional) semantic recall over the verbatim archive.
    Returns verbatim chunks, deduped by chunk_id (FTS hits first)."""
    with _archive(campaign_dir) as a:
        fts_hits = a.fts_search(query, k=k, entity=entity, day=day)
        sem_hits = []
        emb = embedder if embedder is not None else get_embedder()
        if semantic and emb is not None:
            try:
                qv = emb.embed([query])[0]
                with _vectors(campaign_dir) as vs:
                    for cid, _score in vs.search(qv, k):
                        ch = a.get_chunk(cid)
                        if ch:
                            sem_hits.append(ch)
            except Exception as e:           # 向量路失败不应连累 FTS
                log.debug("semantic path failed: %s", e)
        seen, merged = set(), []
        for h in fts_hits + sem_hits:
            if h["chunk_id"] not in seen:
                seen.add(h["chunk_id"]); merged.append(h)
    log.debug("recall q=%r fts=%d sem=%d merged=%d", query, len(fts_hits), len(sem_hits), len(merged))
    return merged

def recall_anchor(campaign_dir, anchor_type, *, actor=None):
    """Resolve a landmark anchor (e.g. first_meeting) → its verbatim chunk(s)."""
    with _events(campaign_dir) as store, _archive(campaign_dir) as a:
        match = None
        for ev in store.iter_events():
            if ev["type"] != "landmark":
                continue
            if ev.get("deltas", {}).get("anchor") != anchor_type:
                continue
            if actor and actor not in ev["actors"]:
                continue
            match = ev
            break   # earliest matching landmark
        if not match:
            log.debug("recall_anchor type=%s actor=%s → none", anchor_type, actor)
            return []
        chunks = [a.get_chunk(cid) for cid in match.get("chunk_ids", [])]
        chunks = [c for c in chunks if c]
    log.debug("recall_anchor type=%s actor=%s → %d chunk(s)", anchor_type, actor, len(chunks))
    return chunks
