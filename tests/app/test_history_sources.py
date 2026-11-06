import pytest
from app.engine import build_engine
from app.gui import _seed_demo_campaign, PlayerDashboard, GuiRequestError
from engine.history import PlayerHistory, StaleSource
from kernel.events import kernel_event
from systems.knowledge import player_statement_fact_key


def test_literal_sources_separate_narration_speech_and_neighbor_visibility(tmp_path):
    engine=build_engine(tmp_path,enable_interpretive_memory=True)
    _seed_demo_campaign(engine)
    def add(kind,d,**kwargs):
        engine.store.append(kernel_event(kind,day=1,scene='s',turn=1,summary='fixture',deltas=d,**kwargs))
    try:
        add('character_interpretation_recorded',{'actor':'protagonist','text':'禁读甲'},id='private')
        add('narration_recorded',{'text':'禁读乙','scene':'s'},thread_refs=['private'])
        add('narration_recorded',{'text':'禁读丙','scene':'s'},secrecy='secret')
        add('narration_recorded',{'text':'旧原文 黑檀%信物','scene':'s'})
        add('knowledge_set',{'knower':'npc','source_kind':'player_statement','via':'protagonist',
            'fact_key':player_statement_fact_key('protagonist','黑檀%信物'),'value':'黑檀%信物'})
        dash=PlayerDashboard(engine);results=dash.recall('黑檀%信物')['sources']
        assert [r['kind'] for r in results] == ['通信收据','公开旁白']
        assert dash.recall('禁读').get('sources', []) == []
        ref=results[0]['ref']; opened=dash.open_source(ref)
        assert opened['source']['recipient']=='npc' and opened['before'][-1]['text']=='旧原文 黑檀%信物'
        assert '禁读' not in str(opened)
        with pytest.raises(StaleSource): PlayerHistory(engine.store,'stranger').open(ref)
        engine.store.close();engine=build_engine(tmp_path,enable_interpretive_memory=True)
        assert PlayerDashboard(engine).open_source(ref)['source']['text']=='黑檀%信物'
        engine.store.retract_from_turn(1)
        with pytest.raises(GuiRequestError): PlayerDashboard(engine).open_source(ref)
        assert PlayerDashboard(engine).recall('黑檀%信物').get('sources', []) == []
    finally:
        engine.store.close()


def test_input_is_actor_only_and_scoped_author_opens_literal_source(tmp_path):
    from app.gui import build_dashboard
    from engine.history import bind_author_history, author_source_tools
    from engine.store import EventBatch

    dashboard = build_dashboard(tmp_path, demo=True)
    ledger = dashboard.engine.store
    intent = "去集市看看，在心里记下银灰纽扣。"
    try:
        dashboard.action(player_input=intent, action_id="source-input", expected_revision=ledger.revision)
        source = next(row for row in dashboard.recall("银灰纽扣")["sources"] if row["kind"] == "玩家输入")
        assert source["recipient"] is None
        assert all(row["kind"] != "玩家输入" for row in PlayerHistory(ledger, "merchant").search("银灰纽扣"))
        assert all(row["type"] != "knowledge_set" or row["deltas"].get("value") != intent
                   for row in ledger.iter_events())
        batch = EventBatch(ledger)
        with bind_author_history(batch, "protagonist"):
            locate, read = author_source_tools("protagonist")
            assert not author_source_tools("merchant")
            ref = next(hit["ref"] for hit in locate.fn("银灰纽扣")["matches"] if hit["kind"] == "玩家输入")
            assert read.fn(ref)["source"]["text"] == intent
        with pytest.raises(StaleSource):
            read.fn(ref)
        dashboard.undo(expected_revision=ledger.revision)
        assert not PlayerHistory(ledger, "protagonist").search("银灰纽扣")
    finally:
        ledger.close()
