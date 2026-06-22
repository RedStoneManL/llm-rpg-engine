# tests/test_scheduler.py
from engine.oracle import Oracle
from engine.director import thread_due_scores, pick_thread_to_advance

def _threads():
    return {
        "fast": {"id": "fast", "speed": "快", "status": "活跃", "dormant": False, "last_advanced_scene": "s1"},
        "slow": {"id": "slow", "speed": "慢", "status": "活跃", "dormant": False, "last_advanced_scene": "s1"},
        "done": {"id": "done", "speed": "中", "status": "已解锁", "dormant": False, "last_advanced_scene": "s1"},
        "hidden": {"id": "hidden", "speed": "快", "status": "活跃", "dormant": True, "last_advanced_scene": "s1"},
    }

def _events(n_scenes):
    from engine.schema import make_event
    return [make_event("action", i+1, f"s{i+1}", ["x"], f"场景{i+1}") for i in range(n_scenes)]

def test_scheduler_skips_resolved_and_dormant():
    scores = thread_due_scores(_events(10), _threads(), Oracle(1))
    ids = [s[0] for s in scores]
    assert "done" not in ids and "hidden" not in ids        # 已解锁/休眠不参与
    assert set(ids) == {"fast", "slow"}

def test_fast_thread_more_due_than_slow_after_many_scenes():
    # 都从 s1 起,过 10 场;快线该推度应高于慢线(用同 seed 去掉 jitter 差异看趋势,多 seed 统计)
    fast_wins = sum(1 for s in range(200)
                    if dict(thread_due_scores(_events(10), _threads(), Oracle(s)))["fast"]
                    >= dict(thread_due_scores(_events(10), _threads(), Oracle(s)))["slow"])
    assert fast_wins > 150                                    # 绝大多数情况下快线更该推

def test_pick_returns_none_when_nothing_due():
    # 才过 1 场,没线 overdue
    assert pick_thread_to_advance(_events(1), _threads(), Oracle(1)) is None

def test_pick_returns_a_thread_when_overdue():
    tid = pick_thread_to_advance(_events(20), _threads(), Oracle(1))
    assert tid in ("fast", "slow")
