# tests/test_embed.py
import logging
from engine.embed import FakeEmbedder, get_embedder

def test_fake_embedder_deterministic():
    e = FakeEmbedder()
    a = e.embed(["艾拉笑了"])[0]
    b = e.embed(["艾拉笑了"])[0]
    assert a == b                       # 同文本→同向量
    assert len(a) == e.dim

def test_fake_embedder_different_texts_differ():
    e = FakeEmbedder()
    assert e.embed(["甲"])[0] != e.embed(["乙"])[0]

def test_fake_embedder_unit_norm():
    e = FakeEmbedder()
    v = e.embed(["x"])[0]
    assert abs(sum(c*c for c in v) ** 0.5 - 1.0) < 1e-6   # 单位向量

def test_get_embedder_fake_by_name():
    assert isinstance(get_embedder("fake"), FakeEmbedder)

def test_get_embedder_none_when_disabled():
    assert get_embedder("none") is None

def test_get_embedder_env(monkeypatch):
    monkeypatch.setenv("RPG_EMBEDDER", "fake")
    assert isinstance(get_embedder(), FakeEmbedder)

def test_debug_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    FakeEmbedder().embed(["x", "y"])
    assert any("embed" in r.message for r in caplog.records)
