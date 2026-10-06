"""The world tone draw must respect the pitch's stated mood, using the active
flavor pack's tone config — a bright/王道 pitch must not roll a grim tone (both
play6 and play7 rolled 生存 despite bright pitches)."""
from engine.oracle import Oracle, load_table, load_pack_manifest
from loop.genesis.world import _tone_for_pitch

_TABLE = load_table("tone_axes", "classic")
_CFG = load_pack_manifest("classic")["tone"]
_BRIGHT = set(_CFG["bright"])
_DARK = set(_CFG["dark"])


def test_bright_pitch_always_draws_a_bright_tone():
    for seed in range(30):
        t = _tone_for_pitch(Oracle(seed), "日式西幻·轻松明亮的王道异世界冒险谭", _TABLE, _CFG)
        assert t in _BRIGHT, f"seed={seed}: bright pitch drew grim tone {t!r}"


def test_dark_pitch_always_draws_a_dark_tone():
    for seed in range(30):
        t = _tone_for_pitch(Oracle(seed), "黑暗残酷、压抑绝望的末世", _TABLE, _CFG)
        assert t in _DARK, f"seed={seed}: dark pitch drew bright tone {t!r}"


def test_neutral_pitch_uses_full_table():
    names = {e["name"] for e in _TABLE}
    seen = {_tone_for_pitch(Oracle(seed), "一个剑与魔法的世界", _TABLE, _CFG) for seed in range(40)}
    assert seen <= names
    # full table in play (not locked to one mood subset)
    assert seen & _BRIGHT and seen & _DARK


def test_contradictory_pitch_falls_back_to_full_table():
    t = _tone_for_pitch(Oracle(1), "明亮却又黑暗的冒险", _TABLE, _CFG)
    assert t in {e["name"] for e in _TABLE}


def test_empty_cfg_draws_full_table():
    # a pack with no tone config → uniform full-table draw, never crashes
    t = _tone_for_pitch(Oracle(2), "轻松明亮的冒险", _TABLE, {})
    assert t in {e["name"] for e in _TABLE}


def test_draw_consumes_one_roll_regardless_of_subset():
    # determinism guard: subset-filtering must not shift downstream rolls
    o1, o2 = Oracle(7), Oracle(7)
    _tone_for_pitch(o1, "轻松明亮的冒险", _TABLE, _CFG)   # bright → subset draw
    _tone_for_pitch(o2, "一个普通世界", _TABLE, _CFG)      # neutral → full draw
    assert o1.randint(3, 5) == o2.randint(3, 5)
