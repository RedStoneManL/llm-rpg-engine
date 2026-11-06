"""A named walk-on is addressable exactly where the encounter placed them."""
from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import _build_scene
from context.native_authoring import host_player_statement_receipt
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy


def test_walkon_can_be_addressed_after_reload_but_a_mentioned_person_cannot(tmp_path, monkeypatch):
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density'):
        monkeypatch.setattr('loop.turn.' + name, lambda *args, **kwargs: [])
    engine = build_engine(tmp_path, embedder=None)
    try:
        _seed_demo_campaign(engine)
        here = _build_scene(engine)['location']
        engine.provider = FakeLLMProvider(json_responses=[{
            'narration': '你遇见织工阿棠。她提起远方的弟弟。',
            'moves': [{'who': 'weaver', 'to': here}], 'places': [], 'facts': [],
            'cast': [{'op':'create', 'id':'weaver', 'name':'阿棠', 'sketch':'街边的织工', 'goal':'收工'},
                     {'op':'create', 'id':'brother', 'name':'阿榆', 'sketch':'远方的弟弟', 'goal':'回乡'}],
            'clock': [{'advance':False, 'reason':'只是交谈'}],
        }])
        dashboard = PlayerDashboard(engine, strategy=AuthorStrategy())
        state = dashboard.action(player_input='看看街边的人', action_id='meet',
                                 expected_revision=dashboard.state()['revision'])['state']
        expected = [{'id':'weaver', 'label':'阿棠（街边的织工）'}]
        assert state['speech_recipients'] == expected
        engine.store.close()
        engine = build_engine(tmp_path, embedder=None)
        assert PlayerDashboard(engine).state()['speech_recipients'] == expected
        receipt = host_player_statement_receipt(engine.world, _build_scene(engine), recipient='weaver',
                                                statement='这匹布是谁织的？')
        assert receipt['recipient'] == 'weaver'
        assert engine.world['systems']['ontology'].neighbors('brother', 'located_in', 1) == []
    finally:
        engine.store.close()
