# engine/embed.py
import hashlib
import math
import os

from engine.log import get_logger

log = get_logger("embed")

class FakeEmbedder:
    """Deterministic, offline embedder for tests. Same text → same unit vector.
    Not semantically meaningful — only for plumbing/ranking tests."""
    dim = 64

    def embed(self, texts):
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [((h[i % len(h)] / 255.0) * 2.0 - 1.0) for i in range(self.dim)]
            n = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / n for x in vec])
        log.debug("FakeEmbedder.embed n=%d dim=%d", len(texts), self.dim)
        return out

class FastEmbedEmbedder:
    """Real local Chinese embedder via fastembed (ONNX, no torch). Lazy-loads.

    Default = BAAI/bge-small-zh-v1.5 (BGE family, Chinese-native, dim 512, ~90MB).
    NOTE: BAAI/bge-m3 is NOT available in fastembed's dense TextEmbedding API
    (it is a hybrid dense+sparse model); use sentence-transformers if bge-m3 is
    strictly required. Override model_name+dim for other fastembed models, e.g.
    jinaai/jina-embeddings-v2-base-zh (768) or intfloat/multilingual-e5-large (1024).
    """
    def __init__(self, model_name="BAAI/bge-small-zh-v1.5", dim=512):
        self.model_name = model_name
        self._model = None
        self.dim = dim

    def _ensure(self):
        if self._model is None:
            from fastembed import TextEmbedding
            log.debug("loading fastembed model %s", self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts):
        model = self._ensure()
        vecs = [list(map(float, v)) for v in model.embed(list(texts))]
        log.debug("FastEmbedEmbedder.embed n=%d", len(texts))
        return vecs

def get_embedder(name=None):
    """name (or RPG_EMBEDDER env): 'fake' | 'fastembed' | 'none'. Default: none if unset."""
    name = (name or os.environ.get("RPG_EMBEDDER") or "none").lower()
    if name == "fake":
        return FakeEmbedder()
    if name == "fastembed":
        return FastEmbedEmbedder()
    return None
