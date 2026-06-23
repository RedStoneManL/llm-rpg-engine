"""#R1 — on loading an existing save, print a compact 'continue' recap built
ONLY from world state (option A: reuse the narrative scene summaries). No LLM."""
import types
from collections import namedtuple

from app.__main__ import _print_resume_recap

_F = namedtuple("_F", "predicate value")


class _FakeGraph:
    def current_facts(self, subject):
        return [_F("真名", "凛"), _F("目标", "查清祭坛遗址之谜")]


def _world_with_journey():
    return {"systems": {
        "ontology": _FakeGraph(),
        "narrative": {
            "scenes": [
                {"scene": "s1", "raw": ["开端。"], "summary": "在码头卷入一桩走私疑云"},
                {"scene": "s2", "raw": ["你坐在残烛酒馆角落，对面的人按住了腰间的刀。"],
                 "summary": None},
            ],
            "super_summary": None,
        },
    }}


def test_resume_recap_renders_name_objective_journey_recent():
    eng = types.SimpleNamespace(world=_world_with_journey())
    out = []
    _print_resume_recap(eng, out.append)
    combined = "\n".join(out)
    assert "继续游戏" in combined
    assert "凛" in combined                                   # name
    assert "查清祭坛遗址之谜" in combined                       # objective
    assert "在码头卷入一桩走私疑云" in combined                 # journey summary
    assert "对面的人按住了腰间的刀" in combined                 # last-scene raw (上次)


def test_resume_recap_silent_on_empty_world():
    out = []
    _print_resume_recap(types.SimpleNamespace(world={}), out.append)
    assert out == []                                          # nothing -> no output


def test_resume_recap_uses_super_summary():
    w = _world_with_journey()
    w["systems"]["narrative"]["super_summary"] = "前情：家族覆灭，你隐姓埋名复仇"
    out = []
    _print_resume_recap(types.SimpleNamespace(world=w), out.append)
    assert "家族覆灭" in "\n".join(out)


def test_resume_recap_never_raises_on_corrupt_state():
    # Runs before play_loop on every load — a corrupted/old-format save must
    # degrade gracefully, never raise (review #1).
    class _NoFacts:  # ontology without current_facts -> graph try/except
        pass
    for world in (
        None,
        {},
        {"systems": None},
        {"systems": {"narrative": {"scenes": "oops not a list"}}},
        {"systems": {"narrative": {"scenes": ["not a dict", None, 42]}}},
        {"systems": {"narrative": {"scenes": [{"raw": None}]}}},
        {"systems": {"ontology": _NoFacts()}},
    ):
        out = []
        _print_resume_recap(types.SimpleNamespace(world=world), out.append)  # must not raise
