from engine.store import EventStore
from engine.schema import make_event
from engine.archive import ArchiveStore
from engine.embed import FakeEmbedder
from engine.projection import project
from engine.recall import recall
from engine.rewind import rewind, last_turn


def _setup(campaign):
    s = EventStore(campaign / "events.db", campaign / "events.jsonl")
    a = ArchiveStore(campaign / "archive.db")
    return s, a


def test_rewind_rolls_back_events_chunks_projection_and_recall(campaign):
    s, a = _setup(campaign)
    # 回合1:初次见面,信任建立
    a.add_chunk(day=1, scene="s1", turn=1, text="初次见面台词")
    s.append(make_event("relationship_change", 1, "s1", ["艾拉"], "信任建立",
                        deltas={"艾拉.trust": "无→中"}, turn=1))
    # 回合2:被误解的剧情(要倒带掉)
    a.add_chunk(day=2, scene="s2", turn=2, text="被理解歪的台词")
    s.append(make_event("relationship_change", 2, "s2", ["艾拉"], "信任崩坏",
                        deltas={"艾拉.trust": "中→敌对"}, turn=2))
    # 倒带前:trust=敌对,能召回回合2台词
    assert project(s.iter_events())["characters"]["艾拉"]["trust"] == "敌对"
    assert any("被理解歪" in h["text"] for h in recall(campaign, "被理解歪的台词", embedder=None))
    # 倒带回合2
    res = rewind(campaign, 2, embedder=FakeEmbedder())
    assert res["events_retracted"] == 1 and res["chunks_removed"] == 1
    # 倒带后:trust 自动回到中,回合2台词召回不到
    s2 = EventStore(campaign / "events.db", campaign / "events.jsonl")
    assert project(s2.iter_events())["characters"]["艾拉"]["trust"] == "中"
    assert recall(campaign, "被理解歪的台词", embedder=None) == []
    # 工作记忆已重建
    assert (campaign / "working_memory.md").exists()


def test_last_turn(campaign):
    s, a = _setup(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="一")
    a.add_chunk(day=1, scene="s2", turn=2, text="二")
    assert last_turn(campaign) == 2


def test_rewind_last_is_noop_safe_when_empty(campaign):
    _setup(campaign)
    res = rewind(campaign, 1, embedder=FakeEmbedder())
    assert res["events_retracted"] == 0 and res["chunks_removed"] == 0


def test_last_turn_counts_event_turns_not_just_chunks(campaign):
    # 导演事件可能无对应原文块;last_turn 必须也看事件 turn(/veto 契约)
    s, a = _setup(campaign)
    s.append(make_event("director_fired", 1, "s1", [], "突发事件", turn=3))  # 仅事件,无 chunk
    assert last_turn(campaign) == 3


def test_veto_rewinds_director_event_without_chunk(campaign):
    # /veto:撤掉刚掷出的导演事件所在回合,即使该回合没产出前台原文块
    s, a = _setup(campaign)
    s.append(make_event("oracle_roll", 1, "s1", [], "暗骰", turn=1))
    s.append(make_event("thread_open", 1, "s1", [], "休眠埋线种子", turn=1,
                        deltas={"dormant": True}))
    t = last_turn(campaign)
    res = rewind(campaign, t, embedder=FakeEmbedder())
    assert res["events_retracted"] == 2          # 两条导演事件都被撤
    s2 = EventStore(campaign / "events.db", campaign / "events.jsonl")
    assert list(s2.iter_events()) == []          # 投影回到从前
