import pytest
from loop.flavor import resolve_flavor, available_flavors


def test_available_includes_classic_and_isekai():
    av = available_flavors()
    assert "classic" in av and "isekai" in av


def test_explicit_wins():
    assert resolve_flavor("isekai", "普通西幻") == "isekai"


def test_unknown_explicit_raises():
    with pytest.raises(ValueError):
        resolve_flavor("nope", "")


def test_pitch_autoselect_isekai():
    assert resolve_flavor(None, "社畜穿越异世界的轻小说冒险") == "isekai"


def test_pitch_neutral_is_classic():
    assert resolve_flavor(None, "一个剑与魔法的世界") == "classic"


def test_grounded_pitch_stays_classic():
    assert resolve_flavor(None, "东方武侠权谋") == "classic"


def test_stored_flavor_reads_campaign_seeded():
    from loop.flavor import stored_flavor
    class FakeStore:
        def iter_events(self):
            return [{"type": "campaign_seeded", "deltas": {"flavor": "isekai"}}]
    assert stored_flavor(FakeStore()) == "isekai"


def test_stored_flavor_defaults_classic_when_absent():
    from loop.flavor import stored_flavor
    class FakeStore:
        def iter_events(self):
            return [{"type": "narration_recorded", "deltas": {}}]
    assert stored_flavor(FakeStore()) == "classic"
