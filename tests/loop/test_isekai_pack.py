"""The isekai flavor pack: modern-transported protagonist archetypes + light-novel voice."""
from engine.oracle import Oracle, load_table, load_pack_manifest
from loop.genesis.cast import _roll_protagonist_seeds
from loop.genesis.world import _tone_for_pitch


def test_isekai_origins_are_modern_transported():
    names = [e["name"] for e in load_table("protagonist_origins", "isekai", base="classic")]
    assert any(("社畜" in n or "高中生" in n or "穿越" in n or "魂穿" in n or "大学生" in n)
               for n in names), names


def test_isekai_seeds_roll_from_isekai_pool():
    seeds = _roll_protagonist_seeds(Oracle(3), "isekai")
    origins = {e["name"] for e in load_table("protagonist_origins", "isekai", base="classic")}
    hooks = {e["name"] for e in load_table("protagonist_hooks", "isekai", base="classic")}
    assert seeds["origin"] in origins
    assert seeds["hook"] in hooks


def test_isekai_manifest_voice_is_light_novel():
    assert "轻小说" in load_pack_manifest("isekai")["voice"]


def test_isekai_bright_pitch_draws_isekai_bright_tone():
    cfg = load_pack_manifest("isekai")["tone"]
    table = load_table("tone_axes", "isekai", base="classic")
    for seed in range(20):
        t = _tone_for_pitch(Oracle(seed), "社畜穿越异世界的轻松冒险", table, cfg)
        assert t in set(cfg["bright"]), f"seed={seed} drew {t!r}"


def test_isekai_falls_back_to_classic_for_unlisted_tables():
    # isekai ships no world_magic table -> falls back to classic's
    magic = load_table("world_magic", "isekai", base="classic")
    assert isinstance(magic, list) and magic
