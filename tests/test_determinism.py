from engine.store import EventStore
from engine.projection import project
from engine.schema import make_event


def _store(d):
    return EventStore(d / "events.db", d / "events.jsonl")


def test_reproject_is_idempotent_and_order_stable(campaign):
    s = _store(campaign)
    evs = [
        make_event("character_reveal", 1, "s1", ["艾拉"], "登场", deltas={"艾拉.trait": "敏感"}),
        make_event("relationship_change", 5, "s5", ["艾拉"], "信任↑", deltas={"艾拉.trust": "中→高"}),
        make_event("thread_open", 1, "s1", [], "银的身世", thread_refs=["th_s"],
                   deltas={"endpoint": "恢复记忆", "beats": ["真名"], "reveal_conditions": ["Lv15"]}),
        make_event("relationship_change", 9, "s9", ["艾拉"], "信任↑↑", deltas={"艾拉.trust": "高→极高"}),
    ]
    for e in evs:
        s.append(e)
    a = project(s.iter_events())
    b = project(s.iter_events())   # project again
    assert a == b                  # idempotent
    assert a["characters"]["艾拉"]["trust"] == "极高"
    assert len(a["characters"]["艾拉"]["evolution"]) == 3


def test_retracted_events_do_not_affect_projection(campaign):
    s = _store(campaign)
    s.append(make_event("relationship_change", 1, "s1", ["艾拉"], "信任↑", deltas={"艾拉.trust": "低→中"}))
    bad = s.append(make_event("relationship_change", 2, "s2", ["艾拉"], "理解歪了的一回合",
                              deltas={"艾拉.trust": "中→敌对"}))
    before = project(s.iter_events())
    assert before["characters"]["艾拉"]["trust"] == "敌对"
    s.retract_from_seq(bad)        # rewind the bad round
    after = project(s.iter_events())
    assert after["characters"]["艾拉"]["trust"] == "中"        # state auto-rollback (pure function)
    assert len(after["characters"]["艾拉"]["evolution"]) == 1
