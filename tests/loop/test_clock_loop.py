import os
import tempfile

from kernel.registry import Registry
from kernel.projection import empty_world
from kernel.events import open_store
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.time import TimeSystem


def _registry():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(CharacterSystem())
    r.register(TimeSystem())
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=registry.event_types())


def _scene():
    return {"protagonist": "hero", "present": [], "day": 1, "id": "town", "location": "town"}


def test_clock_in_required_sections():
    assert "clock" in REQUIRED_SECTIONS


def test_clock_advance_moves_band_within_day():
    r = _registry()
    world = empty_world(r)
    canned = {"narration": "日头偏西。",
              "clock": [{"advance": True, "days": 0, "bands": 2, "reason": "蹲守到入夜"}]}
    store = _store(r)
    try:
        result = run_turn(r, store, world, _scene(), "等",
                          strategy=AuthorStrategy(),
                          provider=FakeLLMProvider(json_responses=[canned]))
    finally:
        store.close()
    assert result.world["meta"]["day"] == 1     # no whole days
    assert result.world["meta"]["band"] == 2     # 晨(0)+2 -> 下午(2)


def test_clock_advance_moves_multiple_days():
    r = _registry()
    world = empty_world(r)
    canned = {"narration": "三日兼程。",
              "clock": [{"advance": True, "days": 3, "bands": 1, "reason": "翻山三日，至次日中午"}]}
    store = _store(r)
    try:
        result = run_turn(r, store, world, _scene(), "赶路",
                          strategy=AuthorStrategy(),
                          provider=FakeLLMProvider(json_responses=[canned]))
    finally:
        store.close()
    assert result.world["meta"]["day"] == 4      # 1 + 3
    assert result.world["meta"]["band"] == 1      # 晨(0)+1 -> 中午(1)


def test_clock_no_advance_keeps_clock():
    r = _registry()
    world = empty_world(r)
    canned = {"narration": "紧接着。",
              "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "紧接上一刻"}]}
    store = _store(r)
    try:
        result = run_turn(r, store, world, _scene(), "继续",
                          strategy=AuthorStrategy(),
                          provider=FakeLLMProvider(json_responses=[canned]))
    finally:
        store.close()
    assert result.world["meta"]["day"] == 1
    assert result.world["meta"].get("band", 0) == 0


def test_clock_advance_persists_across_two_turns():
    r = _registry()
    world = empty_world(r)
    t1 = {"narration": "入夜。", "clock": [{"advance": True, "days": 0, "bands": 3, "reason": "黄昏到深夜"}]}
    t2 = {"narration": "翌日。", "clock": [{"advance": True, "days": 0, "bands": 1, "reason": "熬到天亮"}]}
    store = _store(r)
    try:
        provider = FakeLLMProvider(json_responses=[t1, t2])
        w1 = run_turn(r, store, world, _scene(), "守夜",
                      strategy=AuthorStrategy(), provider=provider).world
        # 晨(0)+3 -> 夜晚(3), still day 1
        assert (w1["meta"]["day"], w1["meta"]["band"]) == (1, 3)
        w2 = run_turn(r, store, w1, _scene(), "再守",
                      strategy=AuthorStrategy(), provider=provider).world
        # 夜晚(3)+1 -> 次日 晨(0)
        assert (w2["meta"]["day"], w2["meta"]["band"]) == (2, 0)
    finally:
        store.close()
