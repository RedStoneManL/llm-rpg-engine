# tests/test_oracle.py
import logging
from engine.oracle import Oracle, load_table, scene_seed

def test_d100_range_and_deterministic():
    a = [Oracle(42).d100() for _ in range(5)]
    b = [Oracle(42).d100() for _ in range(5)]
    assert a == b                                  # 同 seed → 同序列
    assert all(1 <= x <= 100 for x in a)

def test_chance_deterministic():
    assert Oracle(7).chance(0.5) == Oracle(7).chance(0.5)

def test_weighted_draw_respects_weights():
    entries = [{"weight": 99, "name": "common"}, {"weight": 1, "name": "rare"}]
    counts = {"common": 0, "rare": 0}
    for s in range(500):
        counts[Oracle(s).draw(entries)["name"]] += 1
    assert counts["common"] > counts["rare"] * 5      # 重的明显多

def test_draw_deterministic_same_seed():
    entries = [{"weight": 1, "name": "a"}, {"weight": 1, "name": "b"}, {"weight": 1, "name": "c"}]
    assert Oracle(123).draw(entries) == Oracle(123).draw(entries)

def test_load_default_table():
    t = load_table("event_types")
    assert any(e["name"] == "人物" for e in t)

def test_scene_seed_deterministic_and_varies():
    assert scene_seed(1000, 5) == scene_seed(1000, 5)
    assert scene_seed(1000, 5) != scene_seed(1000, 6)

def test_debug_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    Oracle(1).draw([{"weight": 1, "name": "x"}])
    assert any("draw" in r.message for r in caplog.records)
