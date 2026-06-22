# tests/test_director.py
from engine.oracle import Oracle, load_table
from engine.director import pacing_probability, compute_pacing, director_check
from engine.schema import make_event

def _tables():
    return {"event_types": load_table("event_types"), "twists": load_table("twists")}

def test_pacing_probability_band():
    assert pacing_probability(0) == 0.15          # 冷却
    assert pacing_probability(1) == 0.30          # 基础
    assert abs(pacing_probability(2) - 0.36) < 1e-9
    assert pacing_probability(6) == 0.60          # 封顶
    assert pacing_probability(20) == 0.60         # 永不超 60

def test_trigger_rate_in_band_statistically():
    # scenes_since_event=1 → ~30% 触发(多 seed 统计)
    fired = sum(1 for s in range(2000)
                if director_check(1, 0.0, Oracle(s), tables=_tables())["triggered"])
    assert 0.25 < fired / 2000 < 0.35

def test_director_check_deterministic():
    a = director_check(3, 0.2, Oracle(99), tables=_tables())
    b = director_check(3, 0.2, Oracle(99), tables=_tables())
    assert a == b

def test_triggered_outcome_has_axes_and_seed():
    # 找一个会触发的 seed
    out = next(director_check(6, 0.0, Oracle(s), tables=_tables())
               for s in range(100)
               if director_check(6, 0.0, Oracle(s), tables=_tables())["triggered"])
    assert out["type"] in ("dormant_thread", "front_stage")
    assert out["magnitude"] in ("small", "big", "crit")
    assert "event_type" in out["seed"] and "twist" in out["seed"]

def test_high_tension_downgrades_frontstage_to_dormant():
    # 高张力下,非暴击的前台事件应被压成休眠(不打断大戏)
    fs_high = sum(1 for s in range(1000)
                  if (o := director_check(6, 0.9, Oracle(s), tables=_tables()))["triggered"]
                  and o["type"] == "front_stage")
    fs_low = sum(1 for s in range(1000)
                 if (o := director_check(6, 0.0, Oracle(s), tables=_tables()))["triggered"]
                 and o["type"] == "front_stage")
    assert fs_high < fs_low                       # 高张力前台更少

def test_compute_pacing_counts_scenes_since_fire():
    evs = [
        make_event("action", 1, "s1", ["雷德"], "a"),
        make_event("director_fired", 1, "s1", [], "事件", deltas={}),
        make_event("action", 2, "s2", ["雷德"], "b"),
        make_event("action", 3, "s3", ["雷德"], "c"),
    ]
    p = compute_pacing(evs)
    assert p["scene_ordinal"] == 3
    assert p["scenes_since_event"] == 2           # s2,s3 两场没事件
