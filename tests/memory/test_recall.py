"""Tests for memory/recall.py (Task 4).

Uses FakeEmbedder — no fastembed, no network.
"""

import pytest
from engine.embed import FakeEmbedder


def _fake_embedder():
    return FakeEmbedder()


def _embed(text):
    return _fake_embedder().embed([text])[0]


def _make_candidate(text, day, importance, embedder=None):
    """Build a candidate dict; pre-embed if embedder given."""
    c = {"text": text, "day": day, "importance": importance}
    if embedder is not None:
        c["vec"] = embedder.embed([text])[0]
    return c


class TestRankBasic:
    def test_returns_list_of_tuples(self):
        from memory.recall import rank
        emb = _fake_embedder()
        query_vec = emb.embed(["探索任务"])[0]
        candidates = [
            _make_candidate("英雄接受了探索任务", 10, 8, emb),
            _make_candidate("英雄在路上闲逛", 1, 1, emb),
        ]
        results = rank(candidates, query_vec, now_day=10)
        assert isinstance(results, list)
        assert len(results) == len(candidates)
        for item in results:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_recent_high_importance_ontopic_ranks_first(self):
        """A recent, high-importance, on-topic candidate should beat an
        old, low-importance, off-topic one."""
        from memory.recall import rank
        emb = _fake_embedder()
        query = "探索任务"
        query_vec = emb.embed([query])[0]
        good = _make_candidate("英雄接受了探索任务", day=10, importance=8, embedder=emb)
        bad = _make_candidate("村民在集市购物", day=1, importance=1, embedder=emb)
        results = rank([good, bad], query_vec, now_day=10)
        # good should come first
        assert results[0][0] is good

    def test_old_low_importance_ranks_last(self):
        from memory.recall import rank
        emb = _fake_embedder()
        query_vec = emb.embed(["重要线索"])[0]
        top = _make_candidate("英雄发现了关键证据", day=20, importance=9, embedder=emb)
        bottom = _make_candidate("旅行者路过小镇", day=1, importance=1, embedder=emb)
        results = rank([top, bottom], query_vec, now_day=20)
        assert results[0][0] is top
        assert results[-1][0] is bottom

    def test_empty_candidates_returns_empty(self):
        from memory.recall import rank
        emb = _fake_embedder()
        query_vec = emb.embed(["test"])[0]
        results = rank([], query_vec, now_day=5)
        assert results == []

    def test_scores_are_floats(self):
        from memory.recall import rank
        emb = _fake_embedder()
        query_vec = emb.embed(["test"])[0]
        candidates = [_make_candidate("something", 5, 5, emb)]
        results = rank(candidates, query_vec, now_day=10)
        _, s = results[0]
        assert isinstance(s, float)

    def test_sorted_descending(self):
        from memory.recall import rank
        emb = _fake_embedder()
        query_vec = emb.embed(["test"])[0]
        candidates = [
            _make_candidate("a", 1, 1, emb),
            _make_candidate("b", 5, 5, emb),
            _make_candidate("c", 3, 7, emb),
        ]
        results = rank(candidates, query_vec, now_day=5)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)


class TestRankRecencyAxis:
    def test_newer_beats_older_all_else_equal(self):
        """With identical text and importance, newer day should rank higher."""
        from memory.recall import rank
        emb = _fake_embedder()
        text = "完全相同的事件"
        query_vec = emb.embed([text])[0]
        old = {"text": text, "day": 1, "importance": 5, "vec": emb.embed([text])[0]}
        new = {"text": text, "day": 100, "importance": 5, "vec": emb.embed([text])[0]}
        results = rank([old, new], query_vec, now_day=100)
        assert results[0][0] is new


class TestRankImportanceAxis:
    def test_high_importance_beats_low_same_age_same_text(self):
        """With same text and same day, higher importance should rank higher."""
        from memory.recall import rank
        emb = _fake_embedder()
        text = "同样的事件"
        query_vec = emb.embed([text])[0]
        low = {"text": text, "day": 5, "importance": 1, "vec": emb.embed([text])[0]}
        high = {"text": text, "day": 5, "importance": 9, "vec": emb.embed([text])[0]}
        results = rank([low, high], query_vec, now_day=5)
        assert results[0][0] is high


class TestEmbedQuery:
    def test_embed_query_returns_vector(self):
        from memory.recall import embed_query
        emb = _fake_embedder()
        vec = embed_query("探索任务", emb)
        assert isinstance(vec, list)
        assert len(vec) == emb.dim

    def test_embed_query_same_text_same_vec(self):
        from memory.recall import embed_query
        emb = _fake_embedder()
        v1 = embed_query("测试文本", emb)
        v2 = embed_query("测试文本", emb)
        assert v1 == v2

    def test_embed_query_different_text_different_vec(self):
        from memory.recall import embed_query
        emb = _fake_embedder()
        v1 = embed_query("英雄旅程", emb)
        v2 = embed_query("反派阴谋", emb)
        assert v1 != v2


class TestRankWithoutPrecomputedVec:
    def test_rank_embeds_on_the_fly_with_embedder(self):
        """Candidates without pre-computed vecs can be ranked if embedder provided."""
        from memory.recall import rank
        emb = _fake_embedder()
        query_vec = emb.embed(["任务"])[0]
        # No "vec" key — embedder must be provided via kwarg
        candidates = [
            {"text": "接受了新任务", "day": 10, "importance": 7},
            {"text": "在路边休息", "day": 1, "importance": 1},
        ]
        results = rank(candidates, query_vec, now_day=10, embedder=emb)
        assert len(results) == 2
        assert results[0][0]["day"] == 10 or results[0][1] > results[1][1]

    def test_rank_skips_candidate_without_vec_and_no_embedder(self):
        """If no embedder and candidate has no vec, it still gets ranked
        (with zero relevance contribution)."""
        from memory.recall import rank
        emb = _fake_embedder()
        query_vec = emb.embed(["任务"])[0]
        candidates = [
            {"text": "没有向量的事件", "day": 5, "importance": 5},
        ]
        # Should not crash; returns result with 0 relevance component
        results = rank(candidates, query_vec, now_day=5)
        assert len(results) == 1
