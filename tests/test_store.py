import json
import sqlite3
import pytest
from engine.store import EventStore
from engine.schema import make_event


def _store(d):
    return EventStore(d / "events.db", d / "events.jsonl")


def test_append_returns_increasing_seq(campaign):
    s = _store(campaign)
    a = s.append(make_event("action", 1, "s1", ["雷德"], "甲"))
    b = s.append(make_event("action", 1, "s1", ["雷德"], "乙"))
    assert b == a + 1


def test_iter_returns_in_seq_order_and_roundtrips(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲",
                         deltas={"x": 1}, thread_refs=["t1"]))
    s.append(make_event("dialogue_beat", 2, "s2", ["艾拉"], "乙"))
    evs = list(s.iter_events())
    assert [e["summary"] for e in evs] == ["甲", "乙"]
    assert evs[0]["deltas"] == {"x": 1} and evs[0]["thread_refs"] == ["t1"]


def test_jsonl_mirror_written(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲"))
    lines = (campaign / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["summary"] == "甲"


def test_reopen_persists(campaign):
    _store(campaign).append(make_event("action", 1, "s1", ["雷德"], "甲"))
    assert len(list(_store(campaign).iter_events())) == 1


def test_retract_hides_from_default_iter_but_keeps_history(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲"))
    seq2 = s.append(make_event("action", 1, "s2", ["雷德"], "乙"))
    s.append(make_event("action", 1, "s3", ["雷德"], "丙"))
    n = s.retract_from_seq(seq2)          # retract 乙 and 丙
    assert n == 2
    assert [e["summary"] for e in s.iter_events()] == ["甲"]
    assert [e["summary"] for e in s.iter_events(include_retracted=True)] == ["甲", "乙", "丙"]


# --- FIX 1 new tests ---

def test_append_jsonl_line_has_seq(campaign):
    s = _store(campaign)
    seq = s.append(make_event("action", 1, "s1", ["雷德"], "甲"))
    lines = (campaign / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["seq"] == seq


def test_retract_rewrites_jsonl_to_reflect_retraction(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲"))
    seq2 = s.append(make_event("action", 1, "s2", ["雷德"], "乙"))
    s.append(make_event("action", 1, "s3", ["雷德"], "丙"))
    s.retract_from_seq(seq2)  # retract 乙 and 丙
    lines = (campaign / "events.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(l) for l in lines]
    not_retracted = [p for p in parsed if p["retracted"] is False]
    retracted = [p for p in parsed if p["retracted"] is True]
    assert len(not_retracted) == 1
    assert len(retracted) == 2


def test_context_manager_closes_connection(campaign):
    with _store(campaign) as s:
        s.append(make_event("action", 1, "s1", ["雷德"], "甲"))
    # Connection should be closed; another append should raise ProgrammingError
    with pytest.raises(sqlite3.ProgrammingError):
        s.append(make_event("action", 1, "s2", ["雷德"], "乙"))


def test_turn_roundtrips(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲", turn=3))
    assert list(s.iter_events())[0]["turn"] == 3

def test_retract_from_turn(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲", turn=1))
    s.append(make_event("action", 1, "s2", ["雷德"], "乙", turn=2))
    s.append(make_event("action", 1, "s3", ["雷德"], "丙", turn=3))
    s.append(make_event("world_fact", 1, "s0", [], "无turn旧事件"))  # turn=None
    n = s.retract_from_turn(2)            # 撤 turn>=2 的 乙、丙
    assert n == 2
    kept = [e["summary"] for e in s.iter_events()]
    assert kept == ["甲", "无turn旧事件"]  # turn=None 不受影响
