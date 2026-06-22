from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem
from loop.lore import fetch_lore


def _reg():
    r = Registry(); r.register(OntologySystem()); r.register(LoreSystem()); return r


_SK = {"id": "caravan", "complexity": "medium", "about": "商队失踪",
       "secret": "首领卷款潜逃", "anchor": "qingshi_town", "l3_anchor": "qingshi_market",
       "description": "集市上关于失踪商队的窃窃私语",
       "trigger": "玩家在集市打听商队/货物/失踪的人",
       "stages": [{"hint": "有人在打听商队下落"}, {"hint": "城门记录显示商队从没出城"}],
       "threshold": 60}


def test_skeleton_stores_disclosure_fields():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    assert ln["description"] == "集市上关于失踪商队的窃窃私语"
    assert ln["trigger"].startswith("玩家在集市")
    assert ln["l3_anchor"] == "qingshi_market"


def test_fetch_lore_depth0_index():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1),
                         kernel_event("lore_advanced", day=1, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 0,
                                              "hint": "有人在打听商队下落"}, turn=2)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    d0 = fetch_lore(ln, 0)
    assert set(d0) == {"id", "description", "trigger"}
    assert d0["id"] == "caravan"


def test_fetch_lore_depth1_current_beat():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1),
                         kernel_event("lore_advanced", day=1, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 0,
                                              "hint": "有人在打听商队下落"}, turn=2)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    d1 = fetch_lore(ln, 1)
    assert d1["stage_idx"] == 0
    assert d1["latest_clue"] == "有人在打听商队下落"
    assert d1["about"] == "商队失踪"


def test_fetch_lore_depth2_history_and_secret_edge():
    w = project(_reg(), [kernel_event("lore_created", day=1, scene="s", summary="x",
                                      deltas=_SK, turn=1),
                         kernel_event("lore_advanced", day=1, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 0,
                                              "hint": "有人在打听商队下落"}, turn=2),
                         kernel_event("lore_advanced", day=2, scene="s", summary="a",
                                      deltas={"id": "caravan", "stage_idx": 1,
                                              "hint": "城门记录显示商队从没出城"}, turn=3)])
    ln = w["systems"]["lore"]["lines"]["caravan"]
    d2 = fetch_lore(ln, 2)
    assert d2["clues"] == ["有人在打听商队下落", "城门记录显示商队从没出城"]
    # secret_edge is a DENIABLE nudge — present, non-None, and NEVER the secret verbatim.
    assert d2["secret_edge"] is not None
    assert ln["secret"] not in d2["secret_edge"]
    assert d2["secret_edge"] != ln["secret"]


def test_create_lore_line_requires_disclosure_fields():
    import pytest, tempfile, os
    from kernel.events import open_store
    from loop.lore import create_lore_line
    r = _reg()
    d = tempfile.mkdtemp()
    store = open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                       allowed_types=r.event_types())
    with pytest.raises(ValueError):
        create_lore_line(store, {"id": "x", "complexity": "simple", "about": "a",
                                 "stages": [], "threshold": 50, "anchor": "t"},
                         day=1, scene="s", turn=1)  # missing description/trigger/l3_anchor
