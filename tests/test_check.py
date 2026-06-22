# tests/test_check.py
from engine.schema import make_event
from engine.projection import project
from engine.check import (check, check_thread_completeness, check_villain_omniscience,
                          check_timeline, check_dangling_refs, BLOCK, WARN)

def _proj(evs):
    return project(evs)

def test_thread_completeness_flags_incomplete_active_thread():
    evs = [make_event("thread_open", 1, "s1", [], "残缺暗线", thread_refs=["t1"],
                      deltas={})]  # 无 endpoint/beats/reveal
    f = check_thread_completeness(evs, _proj(evs))
    assert len(f) == 1 and f[0]["severity"] == BLOCK and "缺" in f[0]["message"]

def test_thread_completeness_ok_when_full():
    evs = [make_event("thread_open", 1, "s1", [], "完整暗线", thread_refs=["t1"],
                      deltas={"endpoint":"终","beats":["a"],"reveal_conditions":["x"]})]
    assert check_thread_completeness(evs, _proj(evs)) == []

def test_thread_completeness_skips_dormant():
    evs = [make_event("thread_open", 1, "s1", [], "休眠种子", thread_refs=["t1"],
                      deltas={"dormant": True})]
    assert check_thread_completeness(evs, _proj(evs)) == []   # 休眠暗线豁免(待激活补全)

def test_villain_omniscience_flags_missing_source():
    evs = [make_event("villain_knowledge_gain", 5, "s5", ["反派"], "得知行踪", deltas={})]
    f = check_villain_omniscience(evs, _proj(evs))
    assert len(f) == 1 and f[0]["severity"] == BLOCK

def test_villain_omniscience_ok_with_source():
    evs = [make_event("villain_knowledge_gain", 5, "s5", ["反派"], "得知",
                      deltas={"source":"内线","channel":"口信","delay":"半天"})]
    assert check_villain_omniscience(evs, _proj(evs)) == []

def test_timeline_flags_day_regression():
    evs = [make_event("action", 5, "s1", ["x"], "a"), make_event("action", 3, "s2", ["x"], "b")]
    f = check_timeline(evs, _proj(evs))
    assert len(f) == 1 and f[0]["severity"] == BLOCK and "时间" in f[0]["message"]

def test_dangling_refs_flags_advance_without_open():
    evs = [make_event("thread_advance", 1, "s1", [], "推进不存在的线", thread_refs=["ghost"])]
    f = check_dangling_refs(evs, _proj(evs))
    assert any("ghost" in x["message"] for x in f)

def test_check_aggregates_and_sorts_block_first():
    evs = [make_event("thread_open", 1, "s1", [], "残缺", thread_refs=["t1"], deltas={}),
           make_event("action", 1, "s1", ["x"], "ok")]
    res = check(evs, _proj(evs))
    assert res and res[0]["severity"] == BLOCK     # 🔴 排前
    assert all("linter" in r and "message" in r for r in res)

# 追加到 tests/test_check.py
from engine.check import check_thread_followup, check_character_staleness, check_promise_aging

def test_thread_followup_flags_stale_thread():
    evs = [make_event("thread_open", 1, "s1", [], "久未推进", thread_refs=["t1"],
                      deltas={"endpoint":"e","beats":["b"],"reveal_conditions":["c"]})]
    evs += [make_event("action", i+2, f"s{i+2}", ["x"], f"日常{i}") for i in range(10)]  # 10 场没推
    f = check_thread_followup(evs, _proj(evs))
    assert any("久未推进" in x["message"] and x["severity"] == WARN for x in f)

def test_character_staleness_flags_unevolved():
    evs = [make_event("character_reveal", 1, "s1", ["阿土"], "登场", deltas={"阿土.x":"y"})]
    evs += [make_event("action", i+2, f"s{i+2}", ["阿土"], f"阿土做事{i}") for i in range(6)]  # 6 事件没演化
    f = check_character_staleness(evs, _proj(evs))
    assert any("阿土" in x["message"] and x["severity"] == WARN for x in f)

def test_character_staleness_ok_if_recently_evolved():
    evs = [make_event("action", i+1, f"s{i+1}", ["阿土"], f"做事{i}") for i in range(6)]
    evs.append(make_event("relationship_change", 9, "s9", ["阿土"], "演化", deltas={"阿土.trust":"低→高"}))
    assert check_character_staleness(evs, _proj(evs)) == []   # 刚演化过

def test_promise_aging_flags_old_open_promise():
    evs = [make_event("promise_made", 1, "s1", ["雷德"], "答应看海", id="ev_p"),
           make_event("action", 40, "s40", ["雷德"], "过了很久")]
    f = check_promise_aging(evs, _proj(evs))
    assert any("看海" in x["message"] for x in f)

def test_promise_aging_ok_if_kept():
    evs = [make_event("promise_made", 1, "s1", ["雷德"], "答应看海", id="ev_p"),
           make_event("promise_kept", 5, "s5", ["雷德"], "兑现", deltas={"promise_id":"ev_p"}),
           make_event("action", 40, "s40", ["雷德"], "很久后")]
    assert check_promise_aging(evs, _proj(evs)) == []
