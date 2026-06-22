# tests/test_vectorstore.py
import logging
from engine.vectorstore import VectorStore

def _vs(campaign):
    return VectorStore(campaign / "vectors.db")

def test_add_and_search_returns_nearest(campaign):
    vs = _vs(campaign)
    vs.add("a", [1.0, 0.0, 0.0])
    vs.add("b", [0.0, 1.0, 0.0])
    vs.add("c", [0.9, 0.1, 0.0])
    hits = vs.search([1.0, 0.0, 0.0], k=2)
    ids = [h[0] for h in hits]
    assert ids[0] == "a" and "c" in ids          # 最近的是自己,其次方向相近的 c
    assert hits[0][1] >= hits[1][1]              # 分数降序

def test_search_empty_store(campaign):
    assert _vs(campaign).search([1.0, 0.0], k=3) == []

def test_persist_across_reopen(campaign):
    _vs(campaign).add("a", [1.0, 0.0])
    assert _vs(campaign).search([1.0, 0.0], k=1)[0][0] == "a"

def test_add_replaces_same_id(campaign):
    vs = _vs(campaign)
    vs.add("a", [1.0, 0.0]); vs.add("a", [0.0, 1.0])
    assert len(vs.search([0.0, 1.0], k=5)) == 1   # 不重复

def test_debug_logs(campaign, caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    vs = _vs(campaign); vs.add("a", [1.0, 0.0]); vs.search([1.0, 0.0], k=1)
    assert any("search" in r.message for r in caplog.records)

def test_context_manager_closes(campaign):
    import sqlite3
    with _vs(campaign) as vs:
        vs.add("a", [1.0, 0.0])
    try:
        vs.add("b", [0.0, 1.0]); assert False
    except sqlite3.ProgrammingError:
        pass
