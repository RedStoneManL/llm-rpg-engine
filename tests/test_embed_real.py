# tests/test_embed_real.py
import pytest
from engine.embed import FastEmbedEmbedder

@pytest.mark.slow
def test_real_embedding():
    try:
        emb = FastEmbedEmbedder()
        v = emb.embed(["第一次见面你对我说的话"])[0]
    except Exception as e:
        pytest.skip(f"local embedder unavailable (offline?): {e}")
    assert len(v) == emb.dim
    # 语义合理性:近义句的 cosine 高于无关句
    import numpy as np
    a, b, c = emb.embed(["我害怕黑暗", "我对黑暗感到恐惧", "今天天气很好"])
    cos = lambda x, y: float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y)))
    assert cos(a, b) > cos(a, c)
