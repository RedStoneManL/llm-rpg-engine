"""NPC state and directional relationships must survive the next author call."""
import pytest

from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import _build_scene
from context.assembler import assemble_context
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy


@pytest.fixture
def game(tmp_path, monkeypatch):
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup',
                 'run_lore', 'run_density', '_run_demote_on_leave'):
        monkeypatch.setattr('loop.turn.' + name, lambda *a, **kw: [])
    engine = build_engine(tmp_path / 'campaign', embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    engine.store.append_many([
        kernel_event('character_created', day=1, scene='s1', turn=0,
            summary='witness', deltas={'id': 'witness', 'sketch': '守桥的老兵',
                                      'goal': '守住桥口', 'tier': 'tracked'}),
        kernel_event('entity_moved', day=1, scene='s1', turn=0,
            summary='witness at bridge', deltas={'who': 'witness', 'to': scene['location']}),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    yield engine
    engine.store.close()


def candidate(state=None, attitude=None):
    cast = []
    if state:
        cast.extend([
            {'op': 'evolve', 'id': 'witness', 'predicate': 'state', 'value': state},
            {'op': 'evolve', 'id': 'witness', 'predicate': 'mood', 'value': '警惕但愿意交谈'},
        ])
    if attitude:
        cast.append({'op': 'relationship', 'id': 'protagonist', 'toward': 'witness', 'value': attitude})
    return {'narration': '老兵把手从剑柄上移开，等着你继续说。', 'moves': [],
            'places': [], 'cast': cast, 'facts': [],
            'clock': [{'advance': False, 'days': 0, 'bands': 0, 'reason': '短暂交谈'}]}


def act(dashboard, action_id):
    return dashboard.action(player_input='继续交谈', action_id=action_id,
                            expected_revision=dashboard.state()['revision'])


def author_context(engine):
    return assemble_context(engine.registry, engine.world, _build_scene(engine))


def test_normal_next_turn_receives_saved_npc_state_and_player_attitude(game):
    provider = FakeLLMProvider(json_responses=[candidate('已收剑，等你解释来意', '愿意给他一次解释的机会'), candidate()])
    game.provider = provider
    dashboard = PlayerDashboard(game, strategy=AuthorStrategy())
    act(dashboard, 'calm-guard')
    act(dashboard, 'continue-talk')
    # The second actual author request, not a separately hand-assembled fixture.
    prompt = provider.calls[1][1]
    assert '状态：已收剑，等你解释来意' in prompt
    assert '主角对其态度：愿意给他一次解释的机会' in prompt
    assert '此刻：警惕但愿意交谈' in prompt
    assert len(provider.calls) == 2


def test_state_survives_cold_reopen_and_undo_restores_previous_card(game):
    game.provider = FakeLLMProvider(json_responses=[candidate('已收剑', '暂时相信他'), candidate('已坐下包扎', '更加信任他')])
    dashboard = PlayerDashboard(game, strategy=AuthorStrategy())
    act(dashboard, 'first-contact')
    act(dashboard, 'tend-wound')
    game.store.close()
    reopened = build_engine(game.store.jsonl_path.parent, provider=FakeLLMProvider(), embedder=None)
    try:
        text = author_context(reopened)
        assert '状态：已坐下包扎' in text
        assert '主角对其态度：更加信任他' in text
        controller = PlayerDashboard(reopened)
        controller.undo(expected_revision=controller.state()['revision'])
        text = author_context(reopened)
        assert '状态：已收剑' in text and '状态：已坐下包扎' not in text
        assert '主角对其态度：暂时相信他' in text
        assert '主角对其态度：更加信任他' not in text
    finally:
        reopened.store.close()


def test_known_state_and_attitude_are_not_turned_into_an_npc_private_belief(game):
    graph = game.world['systems']['ontology']
    graph.assert_fact('witness', 'state', 'PUBLIC_GUARD_STANDS_ASIDE', day=1, turn=0, source_event='state')
    graph.assert_fact('protagonist', 'trust:witness', 'PLAYER_CAUTION', day=1, turn=0, source_event='outgoing')
    graph.assert_fact('witness', 'trust:protagonist', 'NPC_PRIVATE_DISTRUST', day=1, turn=0, source_event='incoming')
    graph.assert_fact('witness', 'goal', 'NPC_PRIVATE_GOAL', day=1, turn=0, source_event='goal')
    text = author_context(game)
    assert '状态：PUBLIC_GUARD_STANDS_ASIDE' in text
    assert '主角对其态度：PLAYER_CAUTION' in text
    assert 'NPC_PRIVATE_DISTRUST' not in text
    assert 'NPC_PRIVATE_GOAL' not in text


@pytest.mark.parametrize('mode', ['left_scene', 'replaced', 'no_state'])
def test_cards_use_current_state_in_current_scene_only(game, mode):
    graph = game.world['systems']['ontology']
    if mode != 'no_state':
        graph.assert_fact('witness', 'state', 'OLD_POSTURE', day=1, turn=0, source_event='old')
    if mode == 'left_scene':
        game.store.append(kernel_event('entity_moved', day=1, scene='s1', turn=1,
            summary='guard leaves', deltas={'who': 'witness', 'to': 'stonebridge_market'}))
        # Persist the state too, so the absence is due to co-presence, not setup loss.
        game.store.append(kernel_event('character_evolved', day=1, scene='s1', turn=1,
            summary='guard state', deltas={'id': 'witness', 'predicate': 'state', 'value': 'OLD_POSTURE'}))
        game.world = project(game.registry, game.store.iter_events())
    elif mode == 'replaced':
        graph.assert_fact('witness', 'state', 'NEW_POSTURE', day=1, turn=1, source_event='new')
    text = author_context(game)
    assert '状态：OLD_POSTURE' not in text
    if mode == 'replaced':
        assert '状态：NEW_POSTURE' in text
    else:
        assert '状态：NEW_POSTURE' not in text
