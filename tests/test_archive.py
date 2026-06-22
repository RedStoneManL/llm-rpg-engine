# tests/test_archive.py
import logging
from engine.archive import ArchiveStore

def _arc(campaign):
    return ArchiveStore(campaign / "archive.db")

def test_add_and_get_chunk_verbatim(campaign):
    a = _arc(campaign)
    cid = a.add_chunk(day=1, scene="s1", turn=1,
                      text="「我叫雷德。」他第一次开口。", entities=["雷德"])
    got = a.get_chunk(cid)
    assert got["text"] == "「我叫雷德。」他第一次开口。"   # 逐字
    assert got["entities"] == ["雷德"] and got["day"] == 1

def test_fts_search_finds_by_keyword(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="艾拉在金狮酒馆笨拙地打翻了酒杯")
    a.add_chunk(day=2, scene="s2", turn=2, text="雷德在国立大学的图书馆读书")
    hits = a.fts_search("酒馆")
    assert len(hits) == 1 and "金狮酒馆" in hits[0]["text"]

def test_fts_search_entity_filter(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="雷德练剑", entities=["雷德"])
    a.add_chunk(day=1, scene="s1", turn=2, text="练剑的还有艾拉", entities=["艾拉"])
    hits = a.fts_search("练剑", entity="艾拉")
    assert len(hits) == 1 and hits[0]["entities"] == ["艾拉"]

def test_chunk_id_stable_and_unique(campaign):
    a = _arc(campaign)
    c1 = a.add_chunk(day=1, scene="s1", turn=1, text="一")
    c2 = a.add_chunk(day=1, scene="s1", turn=2, text="二")
    assert c1 != c2

def test_debug_logs_on_add(campaign, caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    _arc(campaign).add_chunk(day=1, scene="s1", turn=1, text="x")
    assert any("add_chunk" in r.message for r in caplog.records)

def test_context_manager_closes(campaign):
    import sqlite3
    with _arc(campaign) as a:
        a.add_chunk(day=1, scene="s1", turn=1, text="x")
    try:
        a.add_chunk(day=1, scene="s1", turn=2, text="y")
        assert False, "should be closed"
    except sqlite3.ProgrammingError:
        pass


def test_fts_search_operator_chars_no_crash(campaign):
    """Operator chars in FTS5 query must not raise; result must be a list."""
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="some content here 1995")
    for q in ["time:1995", "cat OR", "(((x", 'a "b']:
        result = a.fts_search(q)
        assert isinstance(result, list), f"fts_search({q!r}) raised or returned non-list"


def test_relog_same_turn_keeps_fts_consistent(campaign):
    """Re-inserting same (scene, turn) must update FTS — new text found, old text gone,
    and no stale orphan remains in the FTS docsize shadow table."""
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="旧的初次见面台词")
    a.add_chunk(day=1, scene="s1", turn=1, text="新的初次见面台词")  # same chunk_id → REPLACE
    new_hits = a.fts_search("新的初次见面台词")
    old_hits = a.fts_search("旧的初次见面台词")
    assert len(new_hits) == 1 and new_hits[0]["text"] == "新的初次见面台词"
    assert len(old_hits) == 0
    # FTS docsize table must have exactly 1 entry (no stale orphan from the deleted row)
    fts_docs = list(a._conn.execute("SELECT rowid FROM chunks_fts_docsize"))
    assert len(fts_docs) == 1, f"FTS has {len(fts_docs)} docs but chunks has 1 — stale orphan found"


def test_entity_filter_finds_target_among_many(campaign):
    """Entity filter must find the target even when it sits beyond the k*4 pre-filter window.
    The old code used k*4 widening which fails when the target chunk is beyond position k*4."""
    a = _arc(campaign)
    # Add 20 non-艾拉 chunks first (k=5, old k*4=20 limit), then one 艾拉 chunk at turn=21
    # which falls outside the k*4=20 window under the old code
    for i in range(1, 21):
        a.add_chunk(day=1, scene="s1", turn=i, text=f"练剑的人第{i}次", entities=["其他人"])
    a.add_chunk(day=1, scene="s1", turn=21, text="练剑的人第21次", entities=["艾拉"])
    hits = a.fts_search("练剑", entity="艾拉", k=5)
    assert len(hits) == 1 and hits[0]["entities"] == ["艾拉"]


def test_delete_from_turn_removes_and_keeps_fts_consistent(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="第一回合台词")
    a.add_chunk(day=1, scene="s2", turn=2, text="第二回合台词")
    a.add_chunk(day=1, scene="s3", turn=3, text="第三回合台词")
    n = a.delete_from_turn(2)
    assert n == 2
    assert a.fts_search("第三回合台词") == []      # 已删,且 FTS 无残留
    assert len(a.fts_search("第一回合台词")) == 1   # 保留

def test_max_turn(campaign):
    a = _arc(campaign)
    assert a.max_turn() == 0
    a.add_chunk(day=1, scene="s1", turn=5, text="x")
    a.add_chunk(day=1, scene="s2", turn=9, text="y")
    assert a.max_turn() == 9

def test_min_turn_of_scene(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="sA", turn=3, text="a1")
    a.add_chunk(day=1, scene="sA", turn=4, text="a2")
    a.add_chunk(day=1, scene="sB", turn=5, text="b1")
    assert a.min_turn_of_scene("sA") == 3
    assert a.min_turn_of_scene("missing") is None

def test_next_turn(campaign):
    a = _arc(campaign)
    assert a.next_turn() == 1
    a.add_chunk(day=1, scene="s1", turn=1, text="x")
    assert a.next_turn() == 2
