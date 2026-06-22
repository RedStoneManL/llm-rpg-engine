# tests/test_recall.py
from engine.store import EventStore
from engine.schema import make_event
from engine.archive import ArchiveStore
from engine.recall import recall, recall_anchor

def _setup(campaign):
    arc = ArchiveStore(campaign / "archive.db")
    store = EventStore(campaign / "events.db", campaign / "events.jsonl")
    return arc, store

def test_recall_returns_verbatim_chunk(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=5, scene="s5", turn=10, text="艾拉第一次说『别再为我拼命』", entities=["艾拉"])
    hits = recall(campaign, "拼命")
    assert hits and "别再为我拼命" in hits[0]["text"]   # 逐字

def test_recall_anchor_first_meeting(campaign):
    arc, store = _setup(campaign)
    cid = arc.add_chunk(day=1, scene="s1", turn=1,
                        text="「初次见面,我叫雷德。」", entities=["雷德","Monika"])
    store.append(make_event("landmark", 1, "s1", ["雷德","Monika"], "初次见面",
                            deltas={"anchor": "first_meeting"}, chunk_ids=[cid]))
    hits = recall_anchor(campaign, "first_meeting", actor="Monika")
    assert hits and "初次见面,我叫雷德" in hits[0]["text"]   # 锚点→chunk→逐字

def test_recall_anchor_missing_returns_empty(campaign):
    _setup(campaign)
    assert recall_anchor(campaign, "first_meeting", actor="无此人") == []

from engine.embed import FakeEmbedder
from engine.recall import reindex
from engine.vectorstore import VectorStore

def test_reindex_embeds_all_chunks(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="艾拉在酒馆")
    arc.add_chunk(day=2, scene="s2", turn=2, text="雷德在图书馆")
    n = reindex(campaign, embedder=FakeEmbedder())
    assert n == 2
    with VectorStore(campaign / "vectors.db") as vs:
        assert len(vs.search(FakeEmbedder().embed(["艾拉在酒馆"])[0], k=5)) == 2

def test_reindex_is_rebuildable(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="一")
    reindex(campaign, embedder=FakeEmbedder())
    reindex(campaign, embedder=FakeEmbedder())   # 重跑不翻倍
    with VectorStore(campaign / "vectors.db") as vs:
        assert len(vs.search(FakeEmbedder().embed(["一"])[0], k=10)) == 1

def test_recall_semantic_path_finds_exact(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="独一无二的句子甲")
    arc.add_chunk(day=2, scene="s2", turn=2, text="另一句乙")
    reindex(campaign, embedder=FakeEmbedder())
    hits = recall(campaign, "独一无二的句子甲", semantic=True, embedder=FakeEmbedder())
    assert any("独一无二的句子甲" in h["text"] for h in hits)

def test_recall_fuses_and_dedups(campaign):
    # 同一块既被 FTS(子串)又被语义命中,只出现一次
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="共同关键词出现在此")
    reindex(campaign, embedder=FakeEmbedder())
    hits = recall(campaign, "共同关键词", semantic=True, embedder=FakeEmbedder())
    ids = [h["chunk_id"] for h in hits]
    assert ids.count("c_s1_1") == 1

def test_recall_no_embedder_is_fts_only(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="只有FTS能命中的词")
    hits = recall(campaign, "只有FTS能命中的词", semantic=True, embedder=None)  # 无 embedder
    assert any("只有FTS" in h["text"] for h in hits)   # 优雅退回 FTS
