"""Recall ranking: recency × importance × relevance.

score = W_REC * recency(now_day, day)
      + W_IMP * norm_importance(importance)
      + W_REL * cosine_similarity(query_vec, candidate_vec)

Weights are module-level constants; tune without changing the interface.

rank(candidates, query_vec, *, now_day, embedder=None)
    candidates: list of dicts with {text, day, importance, vec?}
    query_vec:  float list (pre-embedded query)
    now_day:    current game day (int or float)
    embedder:   optional embedder; used to embed candidates missing 'vec'
    Returns: list of (candidate, score) sorted descending.

embed_query(text, embedder) -> list[float]
    Convenience wrapper around embedder.embed([text])[0].
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from engine.log import get_logger

log = get_logger("memory.recall")

# ---------------------------------------------------------------------------
# Weights (tune here)
# ---------------------------------------------------------------------------
W_REC: float = 0.35   # recency weight
W_IMP: float = 0.35   # importance weight
W_REL: float = 0.30   # relevance (cosine) weight

# Importance normalisation bounds
IMP_MIN: float = 0.0
IMP_MAX: float = 10.0


# ---------------------------------------------------------------------------
# Component functions
# ---------------------------------------------------------------------------

def _recency(now_day: float, day: float) -> float:
    """Exponential recency decay; age=0 → 1.0, age→∞ → 0.0.

    Decay constant: half-life = 30 days (configurable via module constant).
    """
    age = max(0.0, now_day - day)
    return math.exp(-age / 30.0)


def _norm_importance(importance: float) -> float:
    """Normalise importance to [0, 1]."""
    span = IMP_MAX - IMP_MIN
    if span == 0:
        return 0.0
    return max(0.0, min(1.0, (importance - IMP_MIN) / span))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [−1, 1], returns 0.0 on zero vectors."""
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    sim = float(np.dot(va, vb) / (na * nb))
    # clamp to avoid floating-point noise outside [-1,1]
    return max(-1.0, min(1.0, sim))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_query(text: str, embedder) -> list[float]:
    """Embed a single query string using the given embedder."""
    return embedder.embed([text])[0]


def rank(candidates: list[dict[str, Any]],
         query_vec: list[float],
         *,
         now_day: float,
         embedder=None) -> list[tuple[dict[str, Any], float]]:
    """Rank candidates by weighted combination of recency, importance, relevance.

    Args:
        candidates:  List of dicts with at least {text, day, importance}.
                     May include 'vec' (pre-computed embedding); if absent and
                     embedder is provided, vectors are computed on-the-fly;
                     if both absent, relevance contribution is 0.
        query_vec:   Embedding of the query (same dim as candidate vecs).
        now_day:     Current game day for recency calculation.
        embedder:    Optional embedder for on-the-fly vector computation.

    Returns:
        List of (candidate, score) sorted by score descending.
    """
    if not candidates:
        return []

    # Pre-embed missing vecs if embedder provided
    texts_to_embed = [c["text"] for c in candidates if "vec" not in c]
    if texts_to_embed and embedder is not None:
        vecs = embedder.embed(texts_to_embed)
        vec_iter = iter(vecs)
        for c in candidates:
            if "vec" not in c:
                c = dict(c)  # shallow copy — don't mutate caller's dict

    # Build a mapping text→vec for on-the-fly embeds
    if embedder is not None and texts_to_embed:
        vecs = embedder.embed(texts_to_embed)
        on_the_fly: dict[str, list[float]] = {}
        idx = 0
        for c in candidates:
            if "vec" not in c:
                on_the_fly[id(c)] = vecs[idx]
                idx += 1
    else:
        on_the_fly = {}

    results: list[tuple[dict[str, Any], float]] = []
    for c in candidates:
        rec = _recency(now_day, c["day"])
        imp = _norm_importance(c.get("importance", 0))

        cand_vec = c.get("vec") or on_the_fly.get(id(c))
        rel = _cosine(query_vec, cand_vec) if cand_vec is not None else 0.0

        s = W_REC * rec + W_IMP * imp + W_REL * rel
        log.debug("rank candidate day=%s imp=%s rec=%.3f imp_n=%.3f rel=%.3f → %.4f",
                  c["day"], c.get("importance"), rec, imp, rel, s)
        results.append((c, float(s)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
