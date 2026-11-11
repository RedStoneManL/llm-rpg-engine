"""A public newcomer can speak on arrival without gaining remote authority."""
import pytest

from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy
from loop.turn import TurnRejected


def candidate(destination='stonebridge_inn', travel=False):
    return {'narration': '你遇见信使小棠，她说船契今天得送到。',
            'moves': ([{'who': 'protagonist', 'to': destination}] if travel else []) + [{'who': 'courier', 'to': destination}],
            'places': [], 'cast': [{'op': 'create', 'id': 'courier', 'name': '小棠', 'sketch': '背包袱的信使', 'goal': '送信'}],
            'facts': [{'subject': 'courier', 'predicate': 'reported:船契', 'value': '船契今天得送到', 'secrecy': 'public'}],
            'knowledge': [{'op': 'told', 'knower': 'protagonist', 'fact_key': 'courier.reported:船契', 'value': '船契今天得送到', 'via': 'courier'}],
            'clock': [{'advance': False, 'days': 0, 'bands': 0, 'reason': '当面交谈'}]}


def dashboard(tmp_path, proposal):
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = build_engine(tmp_path / 'campaign', provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    return PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False, max_repairs=0)


@pytest.mark.parametrize('travel', [False, True])
def test_new_speaker_report_persists_at_current_or_arrival_and_survives_reopen(tmp_path, travel):
    target = 'stonebridge_market' if travel else 'stonebridge_inn'
    d = dashboard(tmp_path, candidate(target, travel))
    try:
        result = d.action(player_input='我与眼前的信使交谈。', action_id='new-speaker', expected_revision=d.state()['revision'])
        assert result['ok']
        graph = d.engine.world['systems']['ontology']
        day = d.engine.world['meta']['day']
        assert graph.neighbors('courier', 'located_in', day) == [target]
        assert graph.value_at('courier', 'reported:船契', day) == '船契今天得送到'
        assert graph.value_at('protagonist', 'knows:courier.reported:船契', day) == '船契今天得送到'
    finally:
        d.engine.store.close()
    e = build_engine(tmp_path / 'campaign', provider=FakeLLMProvider(), embedder=None)
    try:
        assert e.world['systems']['ontology'].value_at('courier', 'reported:船契', e.world['meta']['day']) == '船契今天得送到'
    finally:
        e.store.close()


@pytest.mark.parametrize('variant', ['absent', 'remote', 'hidden', 'canonical', 'reused', 'conflicting_moves'])
def test_same_turn_identity_is_not_a_blanket_report_grant(tmp_path, variant):
    proposal = candidate()
    if variant == 'absent': proposal['moves'] = []
    if variant == 'remote': proposal['moves'][0]['to'] = 'stonebridge_market'
    if variant == 'hidden': proposal['cast'][0]['hidden'] = 'hidden identity'
    if variant == 'canonical': proposal['facts'][0]['predicate'] = 'goal'
    if variant == 'conflicting_moves': proposal['moves'].append({'who': 'courier', 'to': 'stonebridge_market'})
    d = dashboard(tmp_path, proposal)
    try:
        if variant == 'reused':
            d.engine.store.append(kernel_event('entity_created', day=1, scene='s1', turn=0, summary='hidden existing identity',
                                              deltas={'id': 'courier', 'etype': 'Person', 'tier': 'tracked', 'attrs': {'visibility': 'hidden'}}))
            d.engine.world = project(d.engine.registry, d.engine.store.iter_events())
            d.engine.world['_revision'] = d.engine.store.revision
        before = d.engine.store.revision
        with pytest.raises(TurnRejected):
            d.action(player_input='与信使交谈。', action_id='blocked-speaker', expected_revision=before)
        assert d.engine.store.revision == before
        assert d.engine.store.get_action_receipt('blocked-speaker') is None
    finally:
        d.engine.store.close()


def test_final_materializer_cannot_drop_the_new_speakers_arrival(tmp_path, monkeypatch):
    d = dashboard(tmp_path, candidate())
    owner = d.engine.registry.owner_of_section('moves')
    original = owner.to_events
    def without_arrival(section, decl, **kwargs):
        return [e for e in original(section, decl, **kwargs)
                if not (e['type'] == 'entity_moved' and e['deltas'].get('who') == 'courier')]
    monkeypatch.setattr(owner, 'to_events', without_arrival)
    before = d.engine.store.revision
    try:
        with pytest.raises(TurnRejected):
            d.action(player_input='与信使交谈。', action_id='tampered-speaker', expected_revision=before)
        assert d.engine.store.revision == before
    finally:
        d.engine.store.close()


def test_report_at_new_public_frontier_uses_the_same_encounter_boundary(tmp_path):
    proposal = candidate('inn_steps', travel=True)
    proposal['places'] = [{'id': 'inn_steps', 'level': 3, 'kind': 'venue',
                          'seed': '客栈门外的石阶', 'visibility': 'public'}]
    proposal['links'] = [{'a': 'stonebridge_inn', 'b': 'inn_steps', 'visibility': 'public'}]
    d = dashboard(tmp_path, proposal)
    try:
        result = d.action(player_input='走到门外石阶，与信使交谈。', action_id='frontier-speaker', expected_revision=d.state()['revision'])
        assert result['ok']
        graph = d.engine.world['systems']['ontology']
        day = d.engine.world['meta']['day']
        assert graph.neighbors('protagonist', 'located_in', day) == ['inn_steps']
        assert graph.value_at('courier', 'reported:船契', day) == '船契今天得送到'
    finally:
        d.engine.store.close()


def test_existing_public_person_can_report_after_actual_legal_arrival(tmp_path):
    proposal = candidate('stonebridge_market', travel=True)
    proposal['cast'] = []
    proposal['moves'] = [proposal['moves'][0]]
    d = dashboard(tmp_path, proposal)
    try:
        d.engine.store.append_many([
            kernel_event('character_created', day=1, scene='s1', turn=0, summary='public courier',
                         deltas={'id': 'courier', 'sketch': '公开信使', 'goal': '等候收件人', 'tier': 'tracked'}),
            kernel_event('entity_moved', day=1, scene='s1', turn=0, summary='courier at market',
                         deltas={'who': 'courier', 'to': 'stonebridge_market'}),
        ])
        d.engine.world = project(d.engine.registry, d.engine.store.iter_events())
        d.engine.world['_revision'] = d.engine.store.revision
        result = d.action(player_input='前往集市，与信使交谈。', action_id='arrival-speaker', expected_revision=d.state()['revision'])
        assert result['ok']
        assert d.engine.world['systems']['ontology'].value_at('courier', 'reported:船契', d.engine.world['meta']['day']) == '船契今天得送到'
    finally:
        d.engine.store.close()
