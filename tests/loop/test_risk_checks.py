import pytest
from docs.verification.rcv130.probe import game, seed, act, propose, HERO, INN, MARKET
from kernel.projection import project
from loop.turn import TurnRejected
from systems.challenge import resolve_check


@pytest.mark.parametrize("approach,outcome", [("潜行","成功"),("交涉","付出代价的成功"),("强攻","受挫")])
def test_risk_rules_persist_only_settled_costs_and_effects(approach, outcome):
    with game() as (engine, dashboard):
        scene = {"protagonist": HERO, "location": INN, "id": INN}
        action = f"检定 {approach} 普通 穿过守卫看守的出口"
        turn = engine.store.next_turn()
        for campaign_seed in range(1, 100):
            check = resolve_check(action, {**engine.world, "meta":{**engine.world["meta"],"campaign_seed":campaign_seed}}, scene, turn)
            if check["deltas"]["outcome"] == outcome:
                break
        assert check["deltas"]["outcome"] == outcome
        seed(engine, "campaign_seeded", campaign_seed=campaign_seed)
        cost = check["deltas"]["bands"]
        clock = [{"advance":True, "days":0, "bands":cost, "reason":"检定时间代价"}]
        move = [{"who":HERO,"to":MARKET}]
        initial = engine.store.revision
        if outcome == "受挫":
            with pytest.raises(TurnRejected):
                act(dashboard, action, propose(clock=clock, moves=move), "forged")
            assert engine.store.revision == initial
            move = []
        proposal = propose("你尝试穿过出口。", clock=clock, moves=move)
        result = act(dashboard, action, proposal, "checked")
        assert result["state"]["scene"]["time"]["band"] in ("中午","下午")
        assert outcome in result["state"]["latest_check"]
        replayed = project(engine.registry, engine.store.iter_events())
        assert replayed["systems"]["challenge"][HERO]["roll"] == check["deltas"]["roll"]
        duplicate = dashboard.action(player_input=action, action_id="checked", expected_revision=initial)
        assert duplicate["duplicate"]
        assert sum(e["type"]=="action_checked" for e in engine.store.iter_events()) == 1
        dashboard.undo(expected_revision=engine.store.revision)
        assert not dashboard.state()["latest_check"]
