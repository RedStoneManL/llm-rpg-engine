from engine.oracle import Oracle, scene_seed
from llm.provider import FakeLLMProvider
import loop.bootstrap as B


def _oracle(step="frame", attempt=0):
    return Oracle(scene_seed(12345, f"genesis:{step}", attempt))


# ---------------------------------------------------------------------------
# Task 3 — gen_frame / gen_protagonist / gen_opening
# ---------------------------------------------------------------------------

def test_gen_frame_provided_scalars_win_and_skip_llm():
    # Both authored fields provided -> no provider needed, values used verbatim.
    evs, frame = B.gen_frame(
        provider=None, oracle=_oracle(), pitch="ignored",
        provided={"genre": "日式西幻", "world_name": "阿斯特兰",
                  "central_conflict": "魔王复苏", "n_regions": 4, "n_factions": 3},
    )
    assert frame["genre"] == "日式西幻"
    assert frame["world_name"] == "阿斯特兰"
    assert frame["central_conflict"] == "魔王复苏"
    assert frame["n_regions"] == 4 and frame["n_factions"] == 3


def test_gen_frame_no_provided_matches_pitch_path():
    # provided=None must behave exactly as the pitch-only path (deterministic).
    e1, f1 = B.gen_frame(provider=None, oracle=_oracle(), pitch="武侠")
    e2, f2 = B.gen_frame(provider=None, oracle=_oracle(), pitch="武侠", provided=None)
    assert f1 == f2
    assert f1["genre"] == "武侠"


def test_gen_protagonist_provided_name_kept_objective_authored():
    prov = FakeLLMProvider(json_responses=[{
        "name": "应被覆盖", "origin": "应被覆盖",
        "goal": "应被覆盖", "objective": "前往酒馆打听消息"}])
    frame = {"world_name": "阿斯特兰", "tone": "史诗", "central_conflict": "魔王复苏"}
    local_map = {"start_town": "town_0", "venues": ["venue_0"],
                 "venue_names": {"venue_0": "酒馆"}, "l2": [{"id": "town_0", "name": "起点镇"}]}
    _, authored = B.gen_protagonist(
        prov, _oracle("protagonist"), frame, local_map,
        provided={"name": "凛", "origin": "流浪剑士"})
    assert authored["name"] == "凛"               # provided wins
    assert authored["origin"] == "流浪剑士"
    assert authored["objective"] == "前往酒馆打听消息"  # authored (not provided)


def test_gen_opening_provided_used_verbatim():
    evs, narration = B.gen_opening(
        provider=None, frame={"world_name": "X"}, world_summary="...",
        scene_loc="venue_0", scene_loc_name="酒馆",
        provided="这是玩家自定义的开场白。")
    assert narration == "这是玩家自定义的开场白。"
    assert evs[0]["deltas"]["text"] == "这是玩家自定义的开场白。"


# ---------------------------------------------------------------------------
# Task 4 — gen_regions / gen_local_map
# ---------------------------------------------------------------------------

def test_gen_regions_provided_names_kept_and_count_topped_up():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c", "n_regions": 3}
    evs, summary = B.gen_regions(
        provider=None, oracle=_oracle("regions"), frame=frame,
        provided=[{"name": "王都", "terrain": "平原"}, {"name": "北境冰原"}])
    names = [r["name"] for r in summary["regions"]]
    assert names[0] == "王都" and names[1] == "北境冰原"   # provided kept, in order
    assert len(summary["regions"]) == 3                    # topped up to rolled n
    assert summary["regions"][0]["terrain"] == "平原"       # provided terrain kept
    assert summary["start_region"] == "region_0"


def test_gen_regions_more_provided_than_rolled_expands():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c", "n_regions": 3}
    prov = [{"name": f"地域{i}"} for i in range(6)]
    _, summary = B.gen_regions(provider=None, oracle=_oracle("regions"),
                               frame=frame, provided=prov)
    assert len(summary["regions"]) == 6                    # max(6, 3)


def test_gen_local_map_provided_town_and_venue_augment():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    regions = {"start_region": "region_0"}
    _, lm = B.gen_local_map(
        provider=None, oracle=_oracle("local_map"), frame=frame,
        regions_summary=regions,
        provided={"town": {"name": "晨曦镇"}, "venues": [{"name": "魔法学院"}]})
    town = next(e for e in lm["l2"] if e["id"] == "town_0")
    assert town["name"] == "晨曦镇"
    assert "魔法学院" in lm["venue_names"].values()        # provided venue kept
    assert len(lm["venues"]) >= 2                          # still >= rolled minimum


# ---------------------------------------------------------------------------
# Task 5 — gen_factions / gen_npcs
# ---------------------------------------------------------------------------

def test_gen_factions_provided_kept_and_topped_up():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c", "n_factions": 3}
    _, summary = B.gen_factions(
        provider=None, oracle=_oracle("factions"), frame=frame,
        regions_summary={}, provided=[{"name": "光明教会", "motivation": "净化魔物"}])
    names = [f["name"] for f in summary["factions"]]
    assert names[0] == "光明教会"
    assert len(summary["factions"]) == 3


def test_gen_npcs_provided_secret_emits_secret_fact():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    local_map = {"venues": ["venue_0", "venue_1"]}
    factions = {"factions": [{"id": "faction_0", "name": "教会"}]}
    evs, summary = B.gen_npcs(
        provider=None, oracle=_oracle("npcs"), frame=frame,
        local_map=local_map, factions=factions,
        provided=[{"sketch": "白发老者", "goal": "守护遗物", "secret": "他是堕落的圣骑士"}])
    assert summary["npcs"][0]["sketch"] == "白发老者"
    secret_facts = [e for e in evs if e["type"] == "fact_asserted"
                    and e["deltas"].get("secrecy") == "secret"
                    and e["deltas"].get("value") == "他是堕落的圣骑士"]
    assert len(secret_facts) == 1


# ---------------------------------------------------------------------------
# Task 6 — gen_threads
# ---------------------------------------------------------------------------

def test_gen_threads_provided_campaign_line_kept():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    local_map = {"venues": ["venue_0", "venue_1"], "start_town": "town_0"}
    prov = [{"about": "教堂地下的低语", "description": "异常的祷声",
             "trigger": "夜探教堂", "secret": "封印松动",
             "l3_anchor": "venue_0", "stages": ["听到声响", "发现密道"],
             "bound": "campaign"}]
    skeletons, summary = B.gen_threads(
        provider=None, oracle=_oracle("threads"), frame=frame,
        local_map=local_map, protagonist="protagonist", provided=prov)
    abouts = [t["about"] for t in summary["threads"]]
    assert "教堂地下的低语" in abouts
    kept = next(s for s in skeletons if s["about"] == "教堂地下的低语")
    assert kept["l3_anchor"] == "venue_0"
    assert [st["hint"] for st in kept["stages"]] == ["听到声响", "发现密道"]


def test_gen_threads_provided_bad_anchor_falls_back_to_venue():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    local_map = {"venues": ["venue_0"], "start_town": "town_0"}
    prov = [{"about": "x", "description": "y", "trigger": "z", "secret": "s",
             "l3_anchor": "不存在的地点", "stages": ["a"], "bound": "campaign"}]
    skeletons, _ = B.gen_threads(provider=None, oracle=_oracle("threads"),
                                 frame=frame, local_map=local_map,
                                 protagonist="protagonist", provided=prov)
    kept = next(s for s in skeletons if s["about"] == "x")
    assert kept["l3_anchor"] == "venue_0"        # invalid anchor repaired


def test_gen_threads_provided_protagonist_bound_line():
    frame = {"world_name": "X", "tone": "t", "central_conflict": "c"}
    local_map = {"venues": ["venue_0"], "start_town": "town_0"}
    prov = [{"about": "主角的宿命", "description": "d", "trigger": "t",
             "secret": "血脉的真相", "l3_anchor": "venue_0",
             "stages": ["征兆"], "bound": "protagonist"}]
    skeletons, summary = B.gen_threads(
        provider=None, oracle=_oracle("threads"), frame=frame,
        local_map=local_map, protagonist="protagonist", provided=prov)
    kept = next(s for s in skeletons if s["about"] == "主角的宿命")
    assert kept["anchor"] == "protagonist"       # routed to protagonist-bound
    ptypes = [t for t in summary["threads"] if t["about"] == "主角的宿命"]
    assert ptypes and ptypes[0]["type"] == "protagonist"


# ---------------------------------------------------------------------------
# Task 7 — bootstrap_world(spec=) threading
# ---------------------------------------------------------------------------

def test_bootstrap_world_spec_threads_protagonist(tmp_path):
    from app.engine import build_engine
    from loop.bootstrap import bootstrap_world
    engine = build_engine(tmp_path / "c")
    spec = {"world_premise": {"genre": "日式西幻"},
            "protagonist": {"name": "凛", "origin": "流浪剑士"}}
    result = bootstrap_world(engine, "", spec=spec)
    assert result["summary"]["protagonist_name"] == "凛"
    assert result["_state"]["spec"]["protagonist"]["name"] == "凛"


def test_bootstrap_world_same_seed_same_spec_deterministic(tmp_path):
    from app.engine import build_engine
    from loop.bootstrap import bootstrap_world
    # build_engine derives the seed from the campaign dir NAME; two dirs both
    # named "camp" share a seed, so spec=None genesis must be identical.
    e1 = build_engine(tmp_path / "x" / "camp")
    e2 = build_engine(tmp_path / "y" / "camp")
    r1 = bootstrap_world(e1, "武侠", spec=None)
    r2 = bootstrap_world(e2, "武侠", spec=None)
    assert r1["summary"]["world_name"] == r2["summary"]["world_name"]
    assert r1["summary"]["objective"] == r2["summary"]["objective"]
    assert r1["summary"]["protagonist_name"] == r2["summary"]["protagonist_name"]


def _genesis_events(engine):
    """Event stream with volatile fields stripped (event uuid/ts/seq)."""
    out = []
    for e in engine.store.iter_events():
        e = dict(e)
        for k in ("id", "ts", "seq"):
            e.pop(k, None)
        out.append(e)
    return out


def test_bootstrap_spec_none_pitch_equals_genre_spec(tmp_path):
    # The double-counting guard: pitch-only (spec=None) must be content-identical
    # to passing the same genre via the spec. Same campaign dir name -> same seed.
    from app.engine import build_engine
    from loop.bootstrap import bootstrap_world
    e1 = build_engine(tmp_path / "a" / "camp")
    e2 = build_engine(tmp_path / "b" / "camp")
    bootstrap_world(e1, "武侠", spec=None)
    bootstrap_world(e2, "", spec={"world_premise": {"genre": "武侠"}})
    assert _genesis_events(e1) == _genesis_events(e2)
