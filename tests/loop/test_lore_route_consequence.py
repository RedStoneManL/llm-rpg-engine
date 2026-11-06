from app.engine import build_engine
from app.gui import _seed_demo_campaign
from context.capabilities import scene_capabilities
from kernel.projection import project
from loop.lore import create_lore_line, run_lore
from systems.place import navigate


def test_world_day_stages_close_and_restore_a_real_route_on_replay(tmp_path):
    engine = build_engine(tmp_path)
    _seed_demo_campaign(engine)
    a, b = "stonebridge_inn", "stonebridge_market"
    ledger = engine.store
    try:
        line = {"id":"flood","complexity":"medium","anchor":a,"l3_anchor":a,"cadence":"day",
                "description":"河水上涨","about":"堤桥","trigger":"降雨","threshold":100,
                "stages":[{"hint":"漫水封桥","route_effect":{"a":a,"b":b,"passable":False}},
                          {"hint":"退水通行","route_effect":{"a":a,"b":b,"passable":True}}]}
        create_lore_line(ledger, line, day=1, turn=0, scene=a)
        scene = {"day":2,"location":a,"protagonist":"protagonist"}
        def tick(day, previous):
            world = project(engine.registry, ledger.iter_events())
            world["meta"]["day"] = day
            return run_lore(engine.registry, ledger, world, protagonist="protagonist", previous_day=previous)
        assert not tick(1, 1)
        closure = tick(2, 1)
        stages = [e for e in closure if e["type"] == "lore_advanced"]
        effect = next(e for e in closure if e["type"] == "route_changed")
        assert effect["thread_refs"] == [stages[0]["id"]]
        closed = project(engine.registry, ledger.iter_events())
        assert b not in scene_capabilities(closed, scene).allowed_move_ids
        assert not navigate(closed["systems"]["ontology"], a, b, 2)["path"]
        assert not tick(2, 2)
        reopened = tick(3, 2)
        assert any(e["type"] == "route_changed" for e in reopened)
        restored = project(engine.registry, ledger.iter_events())
        assert navigate(restored["systems"]["ontology"], a, b, 3)["path"] == [a, b]
        ledger.retract_from_turn(reopened[0]["turn"])
        rewound = project(engine.registry, ledger.iter_events())
        assert not navigate(rewound["systems"]["ontology"], a, b, 3)["path"]
    finally:
        ledger.close()
