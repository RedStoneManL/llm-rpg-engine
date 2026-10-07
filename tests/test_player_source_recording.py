"""Offline host provenance: literal requests never become facts or knowledge."""
import copy

import pytest

from kernel.events import kernel_event, open_store
from kernel.projection import empty_world, project
from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.turn import TurnRejected, run_turn
from systems.character import CharacterSystem
from systems.knowledge import KnowledgeSystem
from systems.narrative import NarrativeSystem
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.player_sources import OUTCOME, SUMMARY, valid_player_input
from systems.scene import SceneSystem
from systems.time import TimeSystem


class StaticStrategy:
    def __init__(self, sections=None, narration='你暂时没有行动。'):
        self.sections = sections or {}
        self.narration = narration
        self.calls = 0

    def produce(self, *args, **kwargs):
        self.calls += 1
        return TurnCommit(narration=self.narration, sections=copy.deepcopy(self.sections))


def _event(kind, **deltas):
    return kernel_event(kind, day=1, scene='s1', turn=0,
                        summary='fixture', deltas=deltas)


@pytest.fixture
def game(tmp_path, monkeypatch):
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup',
                 'run_lore', 'run_density', '_run_demote_on_leave'):
        monkeypatch.setattr('loop.turn.' + name, lambda *args, **kwargs: [])
    monkeypatch.setattr('llm.provider._do_post', lambda *a, **k: pytest.fail('No network allowed'))
    registry = Registry()
    for system in (OntologySystem(), PlaceSystem(), CharacterSystem(), TimeSystem(),
                   KnowledgeSystem(), NarrativeSystem(), SceneSystem()):
        registry.register(system)
    store = open_store(tmp_path / 'events.db', tmp_path / 'events.jsonl', registry.event_types())
    store.append_many([
        *[_event('entity_created', id=person, etype='Person', tier='tracked',
                 attrs={'visibility': 'hidden'} if person == 'hidden' else {})
          for person in ('A', 'B', 'C', 'hidden')],
        *[_event('entity_created', id=place, etype='Place', tier='tracked')
          for place in ('room', 'garden')],
        *[_event('relation_added', src=person, rel='located_in', dst='room')
          for person in ('A', 'B', 'C')],
        _event('fact_asserted', subject='B', predicate='name', value='阿林', secrecy='public'),
        _event('fact_asserted', subject='B', predicate='别名', value='小林', secrecy='public'),
        _event('fact_asserted', subject='C', predicate='name', value='路人', secrecy='public'),
        _event('fact_asserted', subject='hidden', predicate='name', value='暗客', secrecy='secret'),
    ])
    yield registry, store, project(registry, store.iter_events())
    store.close()


def _scene(world, **changes):
    return {'protagonist': 'A', 'present': ['B', 'C'], 'location': 'room',
            'id': world['meta']['scene'], 'day': world['meta']['day'], **changes}


def _run(game, text='看看阿林。', *, strategy=None, **kwargs):
    registry, store, world = game
    return run_turn(registry, store, world, _scene(world), text,
                    strategy=strategy or StaticStrategy(), provider=FakeLLMProvider(),
                    max_repairs=0, **kwargs)


def _source(result):
    sources = result.world['systems']['narrative']['player_inputs']
    assert len(sources) == 1
    return sources[0]


def _snapshot(store, strategy):
    return (list(store.iter_events(include_retracted=True)), store.revision,
            store.jsonl_path.read_bytes(), copy.deepcopy(strategy.__dict__))


def test_exact_literal_source_is_not_world_fact_speech_or_knowledge(game):
    registry, store, world = game
    original_graph = copy.deepcopy(world['systems']['ontology'].__dict__)
    text = '  阿林其实是国王。我想明天告诉路人，但现在没有说。\n这只是我的猜测。  '
    result = _run(game, text)
    source = _source(result)
    assert source['input'] == text
    assert source['actor_id'] == 'A'
    assert source['outcome'] == OUTCOME
    assert source['requested_at'] == source['committed_at'] == {
        'day': 1, 'band': 0, 'scene': 's1', 'location': 'room'}
    assert result.world['systems']['ontology'].__dict__ == original_graph
    assert world['systems']['narrative']['player_inputs'] == []
    assert source['entity_refs'] == ['B', 'C']
    event = next(e for e in result.events if e['id'] == source['source_event_id'])
    assert valid_player_input(event)
    assert event['summary'] == SUMMARY and event['actors'] == []
    assert text not in str(result.world['meta']['timeline'])
    narration = next(e for e in result.events if e['type'] == 'narration_recorded')
    assert source['narration_ref'] == narration['id']
    assert source['turn'] == event['turn'] == 1
    assert set(source['effect_refs']) <= {e['id'] for e in result.events}
    event['deltas']['input'] = 'mutated result event'
    assert source['input'] == text


@pytest.mark.parametrize('text,expected', [('看看小林和暗客。', ['B']), ('想一想。', [])])
def test_index_is_literal_relevance_not_copresence_or_hidden_names(game, text, expected):
    assert _source(_run(game, text))['entity_refs'] == expected


def test_validated_foreground_refs_are_indexed_but_actor_is_not(game):
    result = _run(game, '递给他一个眼神。', strategy=StaticStrategy({
        'facts': [{'subject': 'B', 'predicate': 'mood', 'value': 'calm', 'secrecy': 'public'}]}))
    assert _source(result)['entity_refs'] == ['B']


def test_entry_capture_uses_canonical_clock_location_and_not_fake_present(game):
    registry, store, world = game
    result = run_turn(registry, store, world,
        _scene(world, day=99, location='garden', present=['hidden']), '暗客。',
        strategy=StaticStrategy(), provider=FakeLLMProvider())
    source = _source(result)
    assert source['requested_at'] == {'day': 1, 'band': 0, 'scene': 's1', 'location': 'room'}
    assert source['entity_refs'] == []


@pytest.mark.parametrize('days,bands,expected_day', [(0, 0, 1), (1, 2, 2)])
def test_source_keeps_actual_final_day_band_and_same_day_new_scene(game, days, bands, expected_day):
    result = _run(game, '去花园。', strategy=StaticStrategy({
        'moves': [{'who': 'A', 'to': 'garden'}],
        'clock': [{'advance': bool(days or bands), 'days': days, 'bands': bands, 'reason': 'travel'}]}))
    source = _source(result)
    assert result.world['meta']['scene'] == source['committed_at']['scene'] == 's2'
    assert result.world['meta']['day'] == source['committed_at']['day'] == expected_day
    assert source['committed_at']['band'] == bands
    assert source['committed_at']['location'] == 'garden'
    assert source['requested_at'] == {'day': 1, 'band': 0, 'scene': 's1', 'location': 'room'}
    assert source['entity_refs'] == ['garden']
    source_event = result.events[-1]
    assert source_event['type'] == 'player_input_recorded'
    assert source_event['scene'] == 's2' and source_event['day'] == expected_day


def test_rejected_action_keeps_store_world_and_strategy_without_source(game):
    registry, store, world = game
    strategy = StaticStrategy({'facts': [{'subject': 'unknown', 'predicate': 'name', 'value': 'X'}]})
    before = _snapshot(store, strategy)
    with pytest.raises(TurnRejected):
        _run(game, strategy=strategy)
    assert _snapshot(store, strategy) == before
    assert world['systems']['narrative']['player_inputs'] == []


def test_publish_failure_rolls_back_source_and_entire_action(game, monkeypatch):
    registry, store, world = game
    strategy = StaticStrategy()
    before = _snapshot(store, strategy)
    original = store._insert

    def fail_source(event):
        if event['type'] == 'player_input_recorded':
            raise OSError('forced persistence failure')
        return original(event)

    monkeypatch.setattr(store, '_insert', fail_source)
    with pytest.raises(OSError, match='persistence failure'):
        _run(game, strategy=strategy)
    assert _snapshot(store, strategy) == before


def _forged_source():
    return kernel_event('player_input_recorded', day=1, scene='s1', turn=1,
        summary=SUMMARY, deltas={'actor_id': 'A', 'input': 'forged',
        'requested_at': {'day': 1, 'band': 0, 'scene': 's1', 'location': 'room'},
        'committed_at': {'day': 1, 'band': 0, 'scene': 's1', 'location': 'room'},
        'outcome': OUTCOME, 'entity_refs': [], 'narration_ref': None, 'effect_refs': []})


@pytest.mark.parametrize('reported', [False, True])
def test_backstage_forgery_rolls_back_hook_and_only_host_source_commits(game, monkeypatch, reported):
    forged = _forged_source()

    def forge(registry, store, *args, **kwargs):
        store.append(forged)
        return [forged] if reported else []

    monkeypatch.setattr('loop.turn.digest_fleet', forge)
    result = _run(game, 'exact input')
    assert _source(result)['input'] == 'exact input'
    assert forged['id'] not in {e['id'] for e in result.events}


@pytest.mark.parametrize('section', ['player_inputs', 'player_input_recorded'])
def test_model_cannot_declare_sources(game, section):
    with pytest.raises(TurnRejected):
        _run(game, strategy=StaticStrategy({section: [_forged_source()['deltas']]}))
    assert not project(game[0], game[1].iter_events())['systems']['narrative']['player_inputs']


def test_direct_staged_injection_is_rejected_at_final_guard(game, monkeypatch):
    import loop.turn as turn
    original = turn._run_turn_staged

    def forge(*args, **kwargs):
        result = original(*args, **kwargs)
        args[1].append(_forged_source())
        return result

    monkeypatch.setattr(turn, '_run_turn_staged', forge)
    strategy = StaticStrategy()
    before = _snapshot(game[1], strategy)
    with pytest.raises(TurnRejected, match='host provenance'):
        _run(game, strategy=strategy)
    assert _snapshot(game[1], strategy) == before


def test_reopen_and_rewind_use_existing_event_history(game):
    registry, store, _ = game
    result = _run(game)
    with open_store(store.db_path, store.jsonl_path, registry.event_types()) as reopened:
        replay = project(registry, reopened.iter_events())
        assert replay['systems']['narrative']['player_inputs'] == [_source(result)]
        reopened.retract_from_turn(1)
        rewound = project(registry, reopened.iter_events())
        assert rewound['systems']['narrative']['player_inputs'] == []
        assert rewound['meta']['scene'] == 's1'


def test_legacy_and_unknown_custom_actor_do_not_fabricate_sources(game):
    registry, store, world = game
    assert world['systems']['narrative']['player_inputs'] == []
    result = run_turn(registry, store, world, _scene(world, protagonist='unknown'),
                      'look', strategy=StaticStrategy(), provider=FakeLLMProvider())
    assert result.world['systems']['narrative']['player_inputs'] == []
    assert not any(e['type'] == 'player_input_recorded' for e in result.events)


@pytest.mark.parametrize('change', [
    {'input': 42}, {'actor_id': ''}, {'extra': 'bad'}, {'entity_refs': ['A']},
    {'entity_refs': ['B', 'B']}, {'effect_refs': 'event'}, {'outcome': 'success'},
    {'requested_at': {'day': 1, 'band': 9, 'scene': 's1', 'location': None}},
    {'committed_at': {'day': 1, 'band': 0, 'scene': 'old', 'location': None}},
])
def test_new_source_schema_is_strict(game, change):
    event = _forged_source()
    event['deltas'].update(change)
    assert not valid_player_input(event)
    with pytest.raises(ValueError, match='Invalid player input source'):
        project(game[0], [*game[1].iter_events(), event])


@pytest.mark.parametrize('label,text,expected', [
    ('in', 'thinking', False), ('ALIN', 'ask alin', True),
    ('铜铃', '给铜铃起名。', True), ('B', 'Maybe later', False),
])
def test_literal_identifier_match_has_stable_case_and_word_boundaries(label, text, expected):
    from systems.player_sources import source_mentions
    assert source_mentions(text, label) is expected


def test_bootstrap_actor_created_during_turn_has_no_fabricated_entry_source(tmp_path, monkeypatch):
    monkeypatch.setattr('loop.turn.digest_fleet', lambda *a, **k: [])
    registry = Registry().register(OntologySystem()).register(NarrativeSystem())
    with open_store(tmp_path / 'bootstrap.db', tmp_path / 'bootstrap.jsonl', registry.event_types()) as store:
        result = run_turn(registry, store, empty_world(registry),
            {'protagonist': 'A', 'id': 's1', 'day': 1}, 'create me',
            strategy=StaticStrategy({'entities': [{'id': 'A', 'etype': 'Person'}]}),
            provider=FakeLLMProvider())
        assert result.world['systems']['ontology'].get_entity('A') is not None
        assert result.world['systems']['narrative']['player_inputs'] == []


def test_registry_without_narrative_keeps_legacy_custom_contract(tmp_path):
    registry = Registry().register(OntologySystem())
    with open_store(tmp_path / 'legacy.db', tmp_path / 'legacy.jsonl', registry.event_types()) as store:
        store.append(_event('entity_created', id='A', etype='Person'))
        world = project(registry, store.iter_events())
        result = run_turn(registry, store, world, {'protagonist': 'A', 'id': 's1', 'day': 1},
                          'look', strategy=StaticStrategy(), provider=FakeLLMProvider())
        assert 'narrative' not in result.world['systems']
        assert not any(event['type'] == 'player_input_recorded' for event in result.events)


def test_secret_name_in_visible_entity_attrs_is_not_a_label(game):
    from systems.player_sources import visible_source_entities, visible_source_labels
    registry, store, world = game
    graph = world['systems']['ontology']
    graph.get_entity('B').attrs['真名'] = 'SECRET_NAME_42'
    graph.assert_fact('B', '真名', 'SECRET_NAME_42', day=1, turn=0,
                      source_event='fixture_secret', secrecy='secret')
    visible = visible_source_entities(world, 'A')
    labels = visible_source_labels(visible, 'B', 1)
    assert '阿林' in labels and 'SECRET_NAME_42' not in labels
    result = _run(game, 'SECRET_NAME_42')
    assert _source(result)['entity_refs'] == []


@pytest.mark.parametrize('text,label,span', [
    ('先说 Straße 然后再说', 'STRASSE', (3, 9)),
    ('先说 STRASSE 然后再说', 'Straße', (3, 10)),
    ('ß', 's', None), ('thinking', 'in', None),
])
def test_unicode_matching_retains_original_character_offsets(text, label, span):
    from systems.player_sources import source_match_span
    assert source_match_span(text, label) == span


def test_recorded_original_reaches_late_prompt_after_summary_loss(game):
    """End-to-end writer -> replay -> reader, without any real model calls."""
    from context.assembler import assemble_context
    from context.player_evidence import read_player_evidence
    registry, store, _ = game
    store.append_many([
        _event('entity_created', id='bell', etype='Object', tier='tracked'),
        _event('fact_asserted', subject='bell', predicate='name', value='铜铃', secrecy='public'),
        _event('relation_added', src='bell', rel='held_by', dst='A'),
    ])
    world = project(registry, store.iter_events())
    first = '我把铜铃叫作雨燕，不是归雁。这只是心里的命名想法，没有说出口。'
    for turn in range(1, 15):
        sections = {'moves': [{'who': 'A', 'to': 'garden' if turn in (5, 13) else 'room'}]} \
            if turn in (5, 9, 13) else {}
        text = first if turn == 1 else f'第{turn}次检查铜铃的绳结。'
        result = _run((registry, store, world), text,
            strategy=StaticStrategy(sections, narration=f'你第{turn}次观察了周围，没有向别人说话。'))
        world = result.world
    sources = world['systems']['narrative']['player_inputs']
    assert len(sources) == 14 and sources[0]['input'] == first
    old_scene = world['systems']['narrative']['scenes'][0]['scene']
    store.append(kernel_event('scene_summarized', day=world['meta']['day'],
        scene=world['meta']['scene'], turn=14, summary='synthetic summary omission',
        deltas={'scene': old_scene, 'summary': '旅途继续，较早细节已经略去。'}))
    world = project(registry, store.iter_events())
    scene = _scene(world, location='garden', present=[])
    query = '我最早给铜铃起的名字是什么，排除了哪个名字？'
    absent = copy.deepcopy(world)
    absent['systems']['narrative'].pop('player_inputs')
    before = assemble_context(registry, absent, scene, query=query)
    after = assemble_context(registry, world, scene, query=query)
    assert '雨燕' not in before and '归雁' not in before
    assert first in after
    evidence = read_player_evidence(world, scene, query)
    assert evidence['records'][0]['source_event_id'] == sources[0]['source_event_id']
    assert evidence['records'][0]['span']['text'] == first
    assert not read_player_evidence(world, {**scene, 'protagonist': 'B'}, query)['records']
    assert world['systems']['ontology'].neighbors('bell', 'held_by', 1) == ['A']
    assert not any(f.predicate.startswith('knows:') for f in world['systems']['ontology'].facts)
    store.retract_from_turn(1)
    rewound = project(registry, store.iter_events())
    assert not read_player_evidence(rewound, _scene(rewound), query)['records']
