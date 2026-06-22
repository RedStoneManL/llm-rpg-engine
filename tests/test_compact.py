from engine.store import EventStore
from engine.schema import make_event
from engine.compact import compact, build_working_memory

def _seed(campaign):
    s = EventStore(campaign / "events.db", campaign / "events.jsonl")
    s.append(make_event("location_change", 3, "s3", ["雷德"], "到王都",
                        deltas={"location": "royal_capital"}))
    s.append(make_event("relationship_change", 3, "s3", ["艾拉"], "信任升",
                        deltas={"艾拉.trust": "中→高"}))
    s.append(make_event("promise_made", 3, "s3", ["雷德"], "答应带艾拉看海", id="ev_pr"))
    s.append(make_event("thread_open", 3, "s3", [], "银的身世", thread_refs=["th_s"],
                        deltas={"endpoint": "恢复记忆", "beats": ["真名"], "reveal_conditions": ["Lv15"]}))
    return s

def test_build_working_memory_contains_key_state(campaign):
    _seed(campaign)
    wm = build_working_memory(campaign)
    assert "royal_capital" in wm        # 当前位置
    assert "艾拉" in wm                  # 在场/近期角色
    assert "银的身世" in wm              # 活跃暗线
    assert "看海" in wm                  # 未兑现承诺

def test_compact_writes_working_memory_file(campaign):
    _seed(campaign)
    compact(campaign)
    wm_path = campaign / "working_memory.md"
    assert wm_path.exists() and "royal_capital" in wm_path.read_text(encoding="utf-8")
    assert (campaign / "projections" / "state.json").exists()
