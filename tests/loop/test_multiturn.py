"""Multi-turn conversation + 70% compaction (loop/strategy.py).

The autouse conftest fixture pins RPG_CONVERSATION_MODE=stateless; the multi-turn
tests below opt in explicitly via settings.set_conversation_mode("multiturn").
"""
import json

from engine import settings
from llm.provider import FakeLLMProvider
from loop.strategy import _build_delta, AuthorStrategy


def _commit_json(narr):
    return json.dumps({"narration": narr, "moves": [], "places": [], "cast": [],
                       "facts": [], "clock": [{"advance": False, "days": 0,
                       "bands": 0, "reason": "原地"}]})


def _scene():
    return {"protagonist": "protagonist", "present": [], "day": 3, "location": "town_a"}


def _world():
    return {"meta": {"day": 3, "band": 1}, "systems": {}}


# ---------------------------------------------------------------------------
# Task 2: _build_delta
# ---------------------------------------------------------------------------

def test_build_delta_has_header_push_and_player():
    out = _build_delta(None, _world(), _scene(), "我推门进去")
    assert "【此刻】" in out
    assert "第 3 天" in out
    assert "[player] 我推门进去" in out


def test_build_delta_is_short_no_full_context():
    # The delta must not carry a full assembled context block; it is a few lines.
    out = _build_delta(None, _world(), _scene(), "看看四周")
    assert out.count("\n") < 8


def test_build_delta_never_raises_on_sparse_world():
    out = _build_delta(None, {}, {"location": ""}, "等待")
    assert "[player] 等待" in out


# ---------------------------------------------------------------------------
# Task 3: commit_to_thread
# ---------------------------------------------------------------------------

def test_commit_to_thread_appends_pair_with_narration():
    s = AuthorStrategy()
    s._thread = [{"role": "system", "content": "sys"}]
    s._pending_user = "【此刻】... [player] 开门"
    s.commit_to_thread("你推开了门，门后是一条窄巷。")
    assert len(s._thread) == 3
    assert s._thread[1] == {"role": "user", "content": "【此刻】... [player] 开门"}
    assert s._thread[2] == {"role": "assistant", "content": "你推开了门，门后是一条窄巷。"}
    assert s._pending_user is None  # consumed


def test_commit_to_thread_noop_when_no_thread():
    s = AuthorStrategy()
    assert s._thread is None
    s._pending_user = "x"
    s.commit_to_thread("narr")  # must not raise, must not create a thread
    assert s._thread is None


def test_commit_to_thread_noop_when_no_pending():
    s = AuthorStrategy()
    s._thread = [{"role": "system", "content": "sys"}]
    s._pending_user = None
    s.commit_to_thread("narr")
    assert len(s._thread) == 1  # unchanged


# ---------------------------------------------------------------------------
# Task 4: produce multiturn restructure
# ---------------------------------------------------------------------------

def _make_registry():
    from kernel.registry import Registry
    from systems.ontology import OntologySystem
    from systems.place import PlaceSystem
    from systems.character import CharacterSystem
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    return r


def _rws():
    # Real registry + empty_world so assemble_context (first-turn rebuild) runs.
    from kernel.projection import empty_world
    reg = _make_registry()
    world = empty_world(reg)
    scene = {"protagonist": "protagonist", "present": [], "day": 1, "location": "town"}
    return reg, world, scene


def test_multiturn_first_turn_opens_thread_then_continuing_uses_delta():
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("二")])
    reg, world, scene = _rws()

    c1 = s.produce(reg, world, scene, "开局动作", provider=prov)
    assert c1.narration == "一"
    # First turn: working list is [system, full_user]; thread reset to [system].
    assert s._messages[0]["role"] == "system"
    assert len(s._thread) == 1 and s._thread[0]["role"] == "system"
    s.commit_to_thread(c1.narration)            # simulate produce_turn success
    assert len(s._thread) == 3                   # system + user + assistant

    c2 = s.produce(reg, world, scene, "第二步", provider=prov)
    assert c2.narration == "二"
    # Continuing turn: working = thread(3) + delta(1) = 4; + assistant = 5.
    assert len(s._messages) == 5
    assert "[player] 第二步" in s._messages[3]["content"]
    assert "【此刻】" in s._messages[3]["content"]


def test_multiturn_thread_stores_narration_not_raw_json():
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("散文一")])
    reg, world, scene = _rws()
    c1 = s.produce(reg, world, scene, "动作", provider=prov)
    s.commit_to_thread(c1.narration)
    assert s._thread[2] == {"role": "assistant", "content": "散文一"}
    assert "moves" not in s._thread[2]["content"]   # not the raw JSON commit


def test_repair_appends_to_working_not_thread():
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("一修")])
    reg, world, scene = _rws()
    s.produce(reg, world, scene, "动作", provider=prov)
    before = len(s._thread)
    s.produce(reg, world, scene, "动作", provider=prov, repair="补 clock 段")
    assert len(s._thread) == before              # repair never grows the thread
    assert s._messages[-2]["content"] == "补 clock 段"


def test_stateless_mode_keeps_thread_none():
    settings.set_conversation_mode("stateless")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("二")])
    reg, world, scene = _rws()
    s.produce(reg, world, scene, "a", provider=prov)
    s.commit_to_thread("一")
    s.produce(reg, world, scene, "b", provider=prov)
    assert s._thread is None                      # stateless never opens a thread
    settings.set_conversation_mode("multiturn")


# ---------------------------------------------------------------------------
# Task 7: compaction trigger
# ---------------------------------------------------------------------------

def test_maybe_flag_compaction_over_threshold():
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[])
    prov.last_usage = {"input": 150000, "output": 10, "total": 150010}  # > 140k
    s._maybe_flag_compaction(prov)
    assert s._compaction_due is True


def test_maybe_flag_compaction_under_threshold():
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[])
    prov.last_usage = {"input": 1000, "output": 10, "total": 1010}
    s._maybe_flag_compaction(prov)
    assert s._compaction_due is False


def test_maybe_flag_compaction_none_usage():
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[])
    prov.last_usage = None
    s._maybe_flag_compaction(prov)
    assert s._compaction_due is False


def test_produce_flags_compaction_when_call_over_threshold():
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一")])
    reg, world, scene = _rws()
    prov.last_usage = {"input": 150000, "output": 10}  # the call came back huge
    s.produce(reg, world, scene, "动作", provider=prov)
    assert s._compaction_due is True   # produce called _maybe_flag_compaction


def test_compaction_due_triggers_full_rebuild():
    settings.set_conversation_mode("multiturn")
    s = AuthorStrategy()
    prov = FakeLLMProvider(responses=[_commit_json("一"), _commit_json("二")])
    reg, world, scene = _rws()
    c1 = s.produce(reg, world, scene, "动作一", provider=prov)
    s.commit_to_thread(c1.narration)        # thread now len 3
    # Simulate the last call having crossed the threshold; the post-compaction
    # call is small so it won't immediately re-flag.
    s._compaction_due = True
    prov.last_usage = {"input": 2000, "output": 10}
    s.produce(reg, world, scene, "动作二", provider=prov)
    assert s._compaction_due is False          # consumed by the rebuild
    assert len(s._thread) == 1                  # reset to bare system base (compacted)
    assert s._messages[0]["role"] == "system"   # full rebuild, not a delta
