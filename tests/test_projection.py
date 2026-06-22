import json
import pytest
from engine.projection import project, write_projections
from engine.schema import make_event


def test_relationship_change_updates_trust_and_appends_evolution():
    evs = [
        make_event("character_reveal", 1, "s1", ["艾拉"], "艾拉登场:笨拙但敏感",
                   deltas={"艾拉.race": "人类", "艾拉.trait": "日常笨拙"}),
        make_event("relationship_change", 5, "s5", ["艾拉"], "并肩作战后信任提升",
                   deltas={"艾拉.trust": "中→高"}),
        make_event("relationship_change", 9, "s9", ["艾拉"], "舍身相护,信任再升",
                   deltas={"艾拉.trust": "高→极高"}),
    ]
    proj = project(evs)
    ella = proj["characters"]["艾拉"]
    assert ella["profile"]["trait"] == "日常笨拙"   # profile field recorded
    assert ella["trust"] == "极高"                  # evolved to latest
    assert len(ella["evolution"]) == 3              # all evolution entries logged
    assert "舍身相护" in ella["evolution"][-1]["change"]


def test_character_development_rewrites_profile_field():
    evs = [
        make_event("character_reveal", 1, "s1", ["银"], "银登场", deltas={"银.identity": "神秘少女"}),
        make_event("character_development", 30, "s30", ["银"], "记忆解封",
                   deltas={"银.identity": "艾莉西亚·卡斯兰(真名)"}),
    ]
    proj = project(evs)
    assert proj["characters"]["银"]["profile"]["identity"] == "艾莉西亚·卡斯兰(真名)"


def test_thread_open_then_advance_then_resolve():
    evs = [
        make_event("thread_open", 1, "s1", [], "银的身世", thread_refs=["th_silver"],
                   deltas={"name": "银的身世", "type": "身世线", "speed": "慢",
                           "endpoint": "恢复记忆与血脉", "beats": ["真名", "徽章", "封印者"],
                           "reveal_conditions": ["Lv15", "到北方遗迹"]}),
        make_event("thread_advance", 15, "s20", [], "梦中说出真名",
                   thread_refs=["th_silver"], deltas={"progress": 30, "clues+": ["真名"]}),
        make_event("thread_resolve", 60, "s90", [], "完全恢复记忆", thread_refs=["th_silver"]),
    ]
    th = project(evs)["threads"]["th_silver"]
    assert th["endpoint"] == "恢复记忆与血脉"
    assert th["progress"] == 30 and th["clues"] == ["真名"]
    assert th["status"] == "已解锁"


def test_promises_made_and_kept():
    evs = [
        make_event("promise_made", 1, "s1", ["雷德"], "答应带艾拉去看海", id="ev_p1"),
        make_event("promise_made", 2, "s2", ["雷德"], "答应保护银", id="ev_p2"),
        make_event("promise_kept", 40, "s40", ["雷德"], "兑现看海", deltas={"promise_id": "ev_p1"}),
    ]
    ps = project(evs)["promises"]
    kept = {p["text"]: p["kept"] for p in ps}
    assert kept["答应带艾拉去看海"] is True
    assert kept["答应保护银"] is False


def test_villain_knowledge_records_source():
    evs = [make_event("villain_knowledge_gain", 50, "s50", ["将军"], "得知雷德在国立大学",
                      deltas={"source": "内线学生", "channel": "口信", "delay": "半天"})]
    v = project(evs)["villains"]["将军"]
    assert v["knows"][0]["source"] == "内线学生"


def test_numeric_and_location_state():
    evs = [
        make_event("location_change", 1, "s1", ["雷德"], "抵达王都", deltas={"location": "royal_capital"}),
        make_event("level_change", 2, "s2", ["雷德"], "升级", deltas={"level": 43}),
        make_event("item_change", 2, "s2", ["雷德"], "获得银光剑", deltas={"gold": 420}),
    ]
    st = project(evs)["state"]
    assert st["location"] == "royal_capital"
    assert st["stats"]["level"] == 43 and st["stats"]["gold"] == 420


def test_director_fired_resets_pacing():
    evs = [make_event("director_fired", 5, "s5", [], "突发事件")]
    assert project(evs)["pacing"]["last_event_scene"] == "s5"


def test_write_projections_emits_json_and_timeline(tmp_path):
    evs = [
        make_event("character_reveal", 1, "s1", ["艾拉"], "登场", deltas={"艾拉.trait": "敏感"}),
        make_event("location_change", 1, "s1", ["雷德"], "抵达王都", deltas={"location": "royal_capital"}),
    ]
    out = tmp_path / "projections"
    write_projections(project(evs), out)
    chars = json.loads((out / "characters.json").read_text(encoding="utf-8"))
    assert chars["艾拉"]["profile"]["trait"] == "敏感"
    tl = (out / "timeline.md").read_text(encoding="utf-8")
    assert "royal_capital" not in tl and "抵达王都" in tl  # timeline uses summary
    assert (out / "state.json").exists()
