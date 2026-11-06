import pytest

from app.gui import build_dashboard
from docs.verification.rcv130.probe import game, act, propose, seed, INN, MARKET, HERO
from loop.turn import TurnRejected


def open_case(dash, alternatives=()):
    objective = {"kind":"investigate", "place":INN, "feature":"旧柜", "alternatives":list(alternatives)}
    return act(dash, "寻找账簿", propose(quests=[{"op":"open","id":"books","summary":"找到账簿的存放处",
                                              "objective":objective}]), "open")


def observe(place, feature, quote, result, follow_up=None):
    tag = {"quest":"books", "result": result}
    if follow_up:
        tag["follow_up"] = follow_up
    return {"areas":[place],"level":1,"public_observation":True,"visibility":"public","summary":quote,
            "observation_range":"眼前","observation_kind":"single_observation","feature":feature,"investigation":tag}


def close_case(dash, source, action_id):
    claim = {"op":"resolve","id":"books","summary":"已核实账簿的去处",
             "evidence":{"source_event_id":source["source_event_id"], "quote":source["quote"]}}
    return act(dash,"我整理证据结案",propose(quests=[claim]),action_id)


def notebook(dash):
    return dash.state()["objectives"]["active_quests"][0]["leads"]


def test_miss_reveals_a_playable_second_route_and_survives_reopen_and_undo():
    with game() as (engine, dash):
        open_case(dash)
        quote = "柜里只有一张把账簿送往集市的收条。"
        miss = observe(INN, "旧柜", quote, "clue", {"place":MARKET, "feature":"寄存簿"})
        act(dash, notebook(dash)[0]["action"], propose(world=[miss]), "first-search")
        leads = notebook(dash)
        assert leads[0]["status"] == "有线索，待核实" and leads[0]["quote"] == quote
        assert leads[1]["feature"] == "寄存簿" and leads[1]["action"] == "前往石桥集市。"
        with pytest.raises(TurnRejected):
            close_case(dash, leads[0], "premature")
        reopened = build_dashboard(engine.store.db_path.parent, provider=engine.provider)
        try:
            assert notebook(reopened) == leads
        finally:
            reopened.engine.store.close()
        act(dash, leads[1]["action"], propose(moves=[{"who":HERO, "to":MARKET}]), "arrive")
        clue = observe(MARKET, "寄存簿", "寄存簿写明原账簿存放在七号柜。", "confirmed")
        act(dash, notebook(dash)[1]["action"], propose(world=[clue]), "market-search")
        source = notebook(dash)[1]
        close_case(dash, source, "finish")
        assert engine.world['systems']['lore']['lines']['books']['resolved']['evidence']['quote'] == source['quote']
        dash.undo(expected_revision=engine.store.revision)
        assert notebook(dash)[1]["source_event_id"] == source["source_event_id"]
        dash.undo(expected_revision=engine.store.revision)
        assert notebook(dash)[1]["status"] == "待查"


def test_negative_or_missing_observation_never_becomes_proof_and_hidden_leads_fail():
    with game() as (engine, dash):
        seed(engine, "character_created", id="guard", name="守门人", sketch="门卫", goal="值守", tier="tracked")
        seed(engine, "entity_moved", who="guard", to=INN)
        open_case(dash)
        act(dash,"查证 books 1",propose("你暂时没能辨清柜内的字迹。"),"inconclusive")
        assert notebook(dash)[0]["status"] == "本次未取得可核验记录"
        assert "打听" in dash.state()["objectives"]["active_quests"][0]["alternative"]
        assert not any(e["type"] == "world_change" and (e.get("deltas") or {}).get("investigation") for e in engine.store.iter_events())
        miss = observe(INN, "旧柜", "柜内空无一物，没有账簿。", "miss")
        act(dash,"查证 books 1",propose(world=[miss]),"empty")
        with pytest.raises(TurnRejected):
            close_case(dash, notebook(dash)[0], "false-positive")
        seed(engine, "place_created", id="SECRET_SITE", seed="地下", tier="tracked", level=3, visibility="hidden")
        before = engine.store.revision
        with pytest.raises(TurnRejected):
            act(dash,"查证 books 1",propose(world=[{**miss,"investigation":{
                **miss["investigation"],"follow_up":{"place":"SECRET_SITE","feature":"密匣"}}}]),"forged")
        assert engine.store.revision == before and "SECRET_SITE" not in str(notebook(dash))


def test_route_closed_mid_case_is_not_offered_until_it_reopens():
    with game() as (engine, dash):
        open_case(dash, [{"place":MARKET,"feature":"寄存簿"}])
        seed(engine, "route_changed", a=INN, b=MARKET, passable=False)
        lead = notebook(dash)[1]
        assert lead["availability"] == "当前没有已知道路" and "action" not in lead
        with pytest.raises(TurnRejected):
            act(dash,"查证 books 2",propose(),"remote")
        seed(engine, "route_changed", a=INN, b=MARKET, passable=True)
        assert notebook(dash)[1]["action"] == "前往石桥集市。"


def test_open_source_uses_original_observation_actor_and_revision_with_old_index():
    from app.engine import build_engine
    from engine.history import PlayerHistory, StaleSource
    from kernel.events import kernel_event

    with game() as (engine, dash):
        open_case(dash)
        quote = "西側柜中只有红蜡。"
        act(dash, "查证 books 1", propose(world=[observe(INN,"旧柜",quote,"miss")]), "store-source")
        lead = notebook(dash)[0]
        raw = dash.open_source(lead["ref"])["source"]
        assert (raw["text"], raw["kind"], raw["source_event_id"]) == (quote, "现场观察", lead["source_event_id"])
        assert dash.recall("红蜡")["sources"][0]["ref"] == lead["ref"]
        assert not PlayerHistory(engine.store, "stranger").search("红蜡")
        # An out-of-action row that claims an observer cannot impersonate their original.
        forged = kernel_event("world_change", day=1, scene=INN, turn=3, summary="伪造现场原文",
                              deltas={"place":INN, "observer":HERO, "source":"author_world",
                                      "public_observation":True,"visibility":"public","level":1,
                                      "observation_kind":"single_observation","observation_range":"眼前"})
        engine.store.append(forged)
        assert not PlayerHistory(engine.store, HERO).search("伪造现场原文")

        db = engine.store._conn
        if engine.store.history_indexed:
            db.execute("DROP TRIGGER play_history_insert")
            db.execute("DELETE FROM play_history_fts WHERE rowid=(SELECT seq FROM events WHERE id=?)",
                       (lead["source_event_id"],))
            db.execute("CREATE TRIGGER play_history_insert AFTER INSERT ON events BEGIN SELECT 1; END")
        db.commit()
        reopened = build_engine(engine.store.db_path.parent)
        try:
            reader = PlayerHistory(reopened.store, HERO)
            assert reader.search("柜中只有红蜡")[0]["text"] == quote
            with pytest.raises(StaleSource):
                reader.open(lead["ref"])
            reopened.store.retract_from_turn(2)
            assert not reader.search("柜中只有红蜡")
        finally:
            reopened.store.close()


def test_default_evidence_review_cannot_close_on_an_unrelated_claim():
    from loop.outcome_narration import settle_investigation_claims
    from kernel.turncommit import TurnCommit
    from llm.provider import FakeLLMProvider

    world = {"systems":{"lore":{"lines":{"books":{"id":"books","question":"找到账簿","summary":"当前位置待查"}}}}}
    observation = observe(INN,"旧柜","柜内空空。","confirmed")
    close = {"op":"resolve","id":"books","summary":"找到了账簿","evidence":{"quote":"柜内空空。"}}
    proposal = TurnCommit("找到啦", {"world":[observation],"quests":[close],"moves":[]})
    for verdict in ({"world/0":False,"quests/0":False}, {}):
        result = settle_investigation_claims(proposal, world, FakeLLMProvider(json_responses=[verdict]))
        assert result.sections["world"][0]["investigation"]["result"] == "clue"
        assert result.sections["quests"] == [] and result.sections["moves"] == []
        assert proposal.sections["quests"] == [close]
