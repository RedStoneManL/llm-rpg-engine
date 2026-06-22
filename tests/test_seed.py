from engine.oracle import Oracle
from engine.seed import seed_campaign

def test_seed_structure():
    s = seed_campaign("default", Oracle(1))
    assert "frame" in s and "tone" in s["frame"]
    assert 3 <= len(s["threads"]) <= 5
    for th in s["threads"]:
        assert th["speed"] in ("快", "中", "慢")
        assert th["endpoint"] and th["archetype"]
    assert 2 <= len(s["npcs"]) <= 4
    for n in s["npcs"]:
        assert len(n["traits"]) == 2 and n["role"]
    assert len(s["protagonist_hooks"]) >= 1

def test_seed_deterministic_same_seed():
    assert seed_campaign("default", Oracle(42)) == seed_campaign("default", Oracle(42))

def test_seed_varies_by_seed():
    a = seed_campaign("default", Oracle(1))
    b = seed_campaign("default", Oracle(2))
    assert a != b                       # 不同 seed → 不同骨架(防趋同)

def test_seed_no_duplicates_within_opening():
    # 多个 seed 都不应在同一开局里出现重复暗线 / 同一 NPC 重复特质
    for s in range(50):
        seed = seed_campaign("default", Oracle(s))
        arche = [t["archetype"] for t in seed["threads"]]
        assert len(set(arche)) == len(arche), f"seed {s}: duplicate thread archetypes"
        for n in seed["npcs"]:
            assert len(set(n["traits"])) == len(n["traits"]), f"seed {s}: duplicate traits"
