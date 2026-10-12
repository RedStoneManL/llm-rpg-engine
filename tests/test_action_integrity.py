"""Persistence, rewind and visibility guarantees at the real engine boundary."""
import copy
import json
import asyncio
import sqlite3
from dataclasses import asdict
import pytest

from app.engine import build_engine, rewind
from app.play import _build_scene
from context.assembler import assemble_context
from engine import settings
from engine.store import ActionAlreadyCommitted, EventStore, EventBatch, RevisionConflict
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from kernel.validation import validate_commit
from llm.provider import FakeLLMProvider, DeepSeekProvider, _openai_parse, _openai_append_result
from llm.tools import build_tool_registry
from loop.strategy import AuthorStrategy
from loop.turn import run_turn, apply_turn, TurnRejected, REQUIRED_SECTIONS, _turn_request_fingerprint
from systems.interpretive_memory import HostNarrationBinding, InterpretiveMemorySystem, OfflineInterpretationGenerator, stage_offline_interpretation


def event(kind='fact_asserted', **data):
    return kernel_event(kind, day=1, scene='inn', summary='fixture', deltas=data, turn=0)


@pytest.fixture
def engine(tmp_path):
    e = build_engine(tmp_path/'campaign')
    e.store.append_many([
        event('entity_created', id='hero', etype='Person', tier='tracked'),
        event('entity_created', id='inn', etype='Place', tier='tracked', attrs={'level':3,'kind':'venue','seed':'inn'}),
        event('relation_added', src='hero', rel='located_in', dst='inn'),
        event(subject='hero', predicate='coins', value=10, secrecy='public'),
    ])
    e.world = project(e.registry, e.store.iter_events())
    yield e
    e.store.close()


def commit(coins=7):
    return {'narration':'你支付三枚金币，钱袋里还剩七枚。',
        'moves':[], 'places':[], 'cast':[],
        'facts':[{'subject':'hero','predicate':'coins','value':coins,'secrecy':'public'}],
        'clock':[{'advance':False,'days':0,'bands':0,'reason':'片刻'}]}


def world_signature(world):
    assert set(world) <= {'meta', 'systems', '_revision'}
    graph = world['systems']['ontology']
    return {'meta': world['meta'], 'systems': {name: value for name, value in world['systems'].items() if name != 'ontology'}, 'ontology': {'entities': {key: asdict(value) for key, value in graph.entities.items()}, 'facts': [asdict(value) for value in graph.facts], 'relations': [asdict(value) for value in graph.relations]}}


def quiet(monkeypatch):
    import loop.turn as turns
    for name in ['digest_fleet','run_director','run_cascade','run_catchup','run_lore','run_density','_run_demote_on_leave']:
        monkeypatch.setattr(turns, name, lambda *a, **kw: [])


def test_legacy_empty_discovery_fingerprint_recovers_but_nonempty_does_not(engine, monkeypatch):
    quiet(monkeypatch)
    old_world = engine.world
    scene = _build_scene(engine)
    first = run_turn(engine.registry, engine.store, old_world, scene, '买酒',
                     strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
                     action_id='legacy-discovery', required_sections=REQUIRED_SECTIONS)
    legacy = _turn_request_fingerprint(scene, '买酒', None, None, prev_scene=None,
        required_sections=REQUIRED_SECTIONS, max_repairs=3, memory_generator=None, legacy=True)
    row = engine.store._conn.execute('SELECT metadata FROM action_result_metadata WHERE action_id=?',
                                    ('legacy-discovery',)).fetchone()
    metadata = json.loads(row['metadata']); metadata['request_fingerprint'] = legacy
    engine.store._conn.execute('UPDATE action_result_metadata SET metadata=? WHERE action_id=?',
        (json.dumps(metadata, sort_keys=True, ensure_ascii=False), 'legacy-discovery'))
    engine.store._conn.commit()
    class MustNotRun:
        def produce(self, *args, **kwargs): raise AssertionError('strategy called')
    recovered = run_turn(engine.registry, engine.store, old_world, scene, '买酒', strategy=MustNotRun(),
        provider=FakeLLMProvider(), action_id='legacy-discovery', required_sections=REQUIRED_SECTIONS)
    assert recovered.receipt['duplicate'] is True and recovered.events == first.events
    with pytest.raises(RevisionConflict):
        run_turn(engine.registry, engine.store, old_world, scene, '买酒', strategy=MustNotRun(),
            provider=FakeLLMProvider(), action_id='legacy-discovery', required_sections=REQUIRED_SECTIONS,
            trusted_discovery_keys=frozenset({'x.discovered'}))


def test_second_insert_failure_rolls_back_whole_action_and_conversation(engine, monkeypatch):
    quiet(monkeypatch)
    settings.set_conversation_mode('multiturn')
    strategy = AuthorStrategy()
    original = engine.store._insert
    calls = 0
    def fail_second(ev):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('disk write failed')
        return original(ev)
    before = list(engine.store.iter_events())
    revision = engine.store.revision
    monkeypatch.setattr(engine.store, '_insert', fail_second)
    with pytest.raises(OSError):
        run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
                 strategy=strategy, provider=FakeLLMProvider(json_responses=[commit()]), required_sections=REQUIRED_SECTIONS)
    assert list(engine.store.iter_events()) == before
    assert engine.store.revision == revision
    assert strategy._thread is None


def test_bad_event_is_rejected_before_it_can_poison_reopen(engine):
    before = list(engine.store.iter_events())
    bad = TurnCommit('new person', {'entities':[{'id':'bad','etype':'Person','attrs':'poison'}]})
    assert any(e.field.endswith('attrs') for e in validate_commit(engine.registry,bad,engine.world))
    with pytest.raises(TypeError):
        apply_turn(engine.registry, engine.store, bad, day=1, scene='inn')
    assert list(engine.store.iter_events()) == before
    assert project(engine.registry, engine.store.iter_events())['systems']['ontology'].value_at('hero','coins',1) == 10


def test_apply_turn_rejects_unowned_sections_before_any_owner_runs(engine, monkeypatch):
    calls = []
    owner = engine.registry.owner_of_section('facts')
    original = owner.to_events

    def record(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, 'to_events', record)
    before = list(engine.store.iter_events())
    proposed = TurnCommit('plain prose', {
        'facts': [{'subject':'hero', 'predicate':'coins', 'value':7, 'secrecy':'public'}],
        'missing_owner': [],
    })

    with pytest.raises(TurnRejected, match='missing_owner'):
        apply_turn(engine.registry, engine.store, proposed, day=1, scene='inn')

    assert calls == []
    assert list(engine.store.iter_events()) == before


def test_mirror_failure_is_a_saved_receipt_and_retry_is_idempotent(engine, monkeypatch):
    def broken():
        raise OSError('mirror disk unavailable')
    monkeypatch.setattr(engine.store, '_rewrite_jsonl', broken)
    events = [event(subject='hero',predicate='coins',value=7)]
    receipt = engine.store.append_many(events, action_id='pay', expected_revision=engine.store.revision)
    assert receipt['mirror_pending']
    retry = engine.store.append_many(events, action_id='pay', expected_revision=0)
    assert retry['duplicate']
    assert len([e for e in engine.store.iter_events() if e['id']==events[0]['id']]) == 1
    with pytest.raises(RevisionConflict):
        engine.store.append_many([event(subject='hero',predicate='coins',value=2)], action_id='pay')


def test_action_result_metadata_commits_with_event_batch_receipt(engine):
    batch = EventBatch(engine.store)
    batch.append(event(subject='hero', predicate='coins', value=7))
    metadata = {'version': 1, 'outcome': {'reply': 'paid'}}

    receipt = batch.publish(action_id='metadata-commit', result_metadata=metadata)

    stored_receipt = engine.store._conn.execute(
        'SELECT * FROM action_receipts WHERE action_id=?', ('metadata-commit',)).fetchone()
    stored_metadata = engine.store._conn.execute(
        'SELECT metadata FROM action_result_metadata WHERE action_id=?', ('metadata-commit',)
    ).fetchone()
    assert stored_receipt['revision'] == receipt['revision'] == engine.store.revision
    assert (stored_receipt['first_seq'], stored_receipt['last_seq']) == (
        receipt['first_seq'], receipt['last_seq'])
    assert json.loads(stored_metadata['metadata']) == metadata


def test_action_result_metadata_write_failure_rolls_back_entire_action(engine, monkeypatch):
    before = list(engine.store.iter_events(include_retracted=True))
    revision = engine.store.revision
    monkeypatch.setattr(engine.store, '_insert_action_result_metadata',
                        lambda *args: (_ for _ in ()).throw(OSError('metadata disk failed')))

    with pytest.raises(OSError, match='metadata disk failed'):
        engine.store.append_many(
            [event(subject='hero', predicate='coins', value=7)], action_id='metadata-failure',
            result_metadata={'version': 1, 'outcome': 'paid'})

    assert list(engine.store.iter_events(include_retracted=True)) == before
    assert engine.store.revision == revision
    assert engine.store._conn.execute(
        'SELECT 1 FROM action_receipts WHERE action_id=?', ('metadata-failure',)).fetchone() is None
    assert engine.store._conn.execute(
        'SELECT 1 FROM action_result_metadata WHERE action_id=?', ('metadata-failure',)).fetchone() is None


def test_duplicate_action_does_not_overwrite_original_result_metadata(engine):
    events = [event(subject='hero', predicate='coins', value=7)]
    original = {'version': 1, 'outcome': {'reply': 'original'}}
    engine.store.append_many(events, action_id='metadata-duplicate', result_metadata=original)

    receipt = engine.store.append_many(
        events, action_id='metadata-duplicate', expected_revision=0,
        result_metadata={'version': 1, 'outcome': {'reply': 'replacement'}})

    stored = engine.store._conn.execute(
        'SELECT metadata FROM action_result_metadata WHERE action_id=?', ('metadata-duplicate',)
    ).fetchone()
    assert receipt['duplicate']
    assert json.loads(stored['metadata']) == original


def test_action_result_metadata_sidecar_is_created_for_legacy_database(tmp_path):
    db_path = tmp_path / 'legacy.db'
    with sqlite3.connect(db_path) as legacy:
        legacy.execute('''CREATE TABLE action_receipts (
            action_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL, revision INTEGER NOT NULL
        )''')

    with EventStore(db_path, tmp_path / 'legacy.jsonl') as store:
        store.append_many(
            [event('world_fact', subject='hero', predicate='coins', value=7)], action_id='legacy-sidecar',
            result_metadata={'version': 1, 'outcome': 'saved'})
        row = store._conn.execute(
            'SELECT metadata FROM action_result_metadata WHERE action_id=?', ('legacy-sidecar',)
        ).fetchone()

    assert json.loads(row['metadata']) == {'version': 1, 'outcome': 'saved'}


def test_action_result_metadata_requires_action_id_before_transaction(engine):
    before = list(engine.store.iter_events(include_retracted=True))
    revision = engine.store.revision

    with pytest.raises(ValueError, match='requires action_id'):
        engine.store.append_many(
            [event(subject='hero', predicate='coins', value=7)],
            result_metadata={'version': 1, 'outcome': 'orphan'})

    assert list(engine.store.iter_events(include_retracted=True)) == before
    assert engine.store.revision == revision
    assert engine.store._conn.execute('SELECT COUNT(*) FROM action_result_metadata').fetchone()[0] == 0


@pytest.mark.parametrize('metadata', [
    [],
    {},
    {'version': True},
    {'version': 1, 'outcome': object()},
    {'version': 1, 'score': float('nan')},
])
def test_invalid_action_result_metadata_is_rejected_without_writes(engine, metadata):
    before = list(engine.store.iter_events(include_retracted=True))
    revision = engine.store.revision

    with pytest.raises(ValueError):
        engine.store.append_many(
            [event(subject='hero', predicate='coins', value=7)], action_id='invalid-metadata',
            result_metadata=metadata)

    assert list(engine.store.iter_events(include_retracted=True)) == before
    assert engine.store.revision == revision
    assert engine.store._conn.execute(
        'SELECT 1 FROM action_receipts WHERE action_id=?', ('invalid-metadata',)
    ).fetchone() is None
    assert engine.store._conn.execute(
        'SELECT 1 FROM action_result_metadata WHERE action_id=?', ('invalid-metadata',)
    ).fetchone() is None


def test_run_turn_duplicate_action_id_stops_before_strategy_or_provider(engine, monkeypatch):
    quiet(monkeypatch)
    first = run_turn(
        engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='preflight-retry')

    class MustNotRun:
        def produce(self, *args, **kwargs):
            pytest.fail('strategy called for duplicate action')

    retry = run_turn(
        engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
        strategy=MustNotRun(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='preflight-retry')

    assert retry.receipt['action_id'] == first.receipt['action_id']
    assert retry.receipt['duplicate'] is True
    assert retry.narration == first.narration


def test_response_loss_retry_restores_offline_turn_without_any_generation(engine, monkeypatch):
    """A committed turn can be returned after the caller lost its first response."""
    quiet(monkeypatch)
    engine.registry.register(InterpretiveMemorySystem())
    engine.store.allowed_types = engine.registry.event_types()
    old_world = engine.world
    original_scene = copy.deepcopy(_build_scene(engine))
    generator = OfflineInterpretationGenerator('private recollection')
    first = run_turn(
        engine.registry, engine.store, old_world, original_scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='response-lost', memory_role='hero',
        memory_generator=generator)
    assert first.narration == commit()['narration']
    assert any(event['type'] == 'character_interpretation_recorded' for event in first.events)

    def sql_state():
        return {
            table: [tuple(row) for row in engine.store._conn.execute(
                f'SELECT * FROM {table} ORDER BY 1')]
            for table in ('events', 'action_receipts', 'action_result_metadata', 'store_revision')
        }

    before_sql = sql_state()
    before_events = list(engine.store.iter_events(include_retracted=True))
    before_receipt = engine.store.get_action_receipt('response-lost')
    before_metadata = engine.store._conn.execute(
        'SELECT metadata FROM action_result_metadata WHERE action_id=?', ('response-lost',)
    ).fetchone()['metadata']
    stored_result = json.loads(before_metadata)['result']
    assert stored_result['commit'] == {
        'narration': first.commit.narration,
        'sections': first.commit.sections,
    }
    assert stored_result['events'] == first.events
    before_revision = engine.store.revision
    before_mirror = engine.store.jsonl_path.read_bytes()
    calls = {name: 0 for name in ('batch', 'resources', 'staged', 'strategy', 'provider',
                                  'generator', 'backstage', 'thread')}

    def forbidden(name):
        def call(*args, **kwargs):
            calls[name] += 1
            raise AssertionError(f'{name} called while recovering a committed action')
        return call

    class MustNotRun:
        produce = staticmethod(forbidden('strategy'))
        commit_to_thread = staticmethod(forbidden('thread'))

    monkeypatch.setattr('loop.turn.EventBatch', forbidden('batch'))
    monkeypatch.setattr('loop.turn.prepare_resources', forbidden('resources'))
    monkeypatch.setattr('loop.turn._run_turn_staged', forbidden('staged'))
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_lore',
                 'run_density', '_run_demote_on_leave'):
        monkeypatch.setattr(f'loop.turn.{name}', forbidden('backstage'))
    monkeypatch.setattr(generator, 'generate', forbidden('generator'))
    provider = type('MustNotProvide', (), {'complete_json': staticmethod(forbidden('provider'))})()

    retry = run_turn(
        engine.registry, engine.store, old_world, copy.deepcopy(original_scene), '买酒',
        strategy=MustNotRun(), provider=provider, action_id='response-lost', memory_role='hero',
        memory_generator=generator)

    assert calls == {name: 0 for name in calls}
    assert retry.receipt == {**first.receipt, 'duplicate': True, 'mirror_pending': False}
    assert retry.commit == first.commit
    assert retry.repair_attempts == first.repair_attempts
    assert retry.dropped_sections == first.dropped_sections
    assert retry.events == first.events
    assert world_signature(retry.world) == world_signature(first.world)
    assert list(engine.store.iter_events(include_retracted=True)) == before_events
    assert engine.store.get_action_receipt('response-lost') == before_receipt
    assert engine.store._conn.execute(
        'SELECT metadata FROM action_result_metadata WHERE action_id=?', ('response-lost',)
    ).fetchone()['metadata'] == before_metadata
    assert engine.store.revision == before_revision
    assert engine.store.jsonl_path.read_bytes() == before_mirror
    assert sql_state() == before_sql


@pytest.mark.parametrize('changed', ('input', 'scene', 'memory_role'))
def test_response_loss_retry_rejects_changed_request_before_generation(engine, monkeypatch, changed):
    quiet(monkeypatch)
    engine.registry.register(InterpretiveMemorySystem())
    engine.store.allowed_types = engine.registry.event_types()
    old_world = engine.world
    scene = _build_scene(engine)
    generator = OfflineInterpretationGenerator('private recollection')
    run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='request-changed', memory_role='hero',
        memory_generator=generator)
    before = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
              engine.store.jsonl_path.read_bytes())
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('generation path called for a changed action envelope')

    retry_scene = copy.deepcopy(scene)
    retry_input = '买酒'
    retry_role = 'hero'
    if changed == 'input':
        retry_input = '改买另一坛酒'
    elif changed == 'scene':
        retry_scene['id'] = 'other-scene'
    else:
        retry_role = 'other'

    monkeypatch.setattr('loop.turn.EventBatch', forbidden)
    monkeypatch.setattr('loop.turn.prepare_resources', forbidden)
    monkeypatch.setattr('loop.turn._run_turn_staged', forbidden)
    monkeypatch.setattr(generator, 'generate', forbidden)
    with pytest.raises(RevisionConflict, match='different request'):
        run_turn(
            engine.registry, engine.store, old_world, retry_scene, retry_input,
            strategy=type('MustNotRun', (), {'produce': staticmethod(forbidden),
                                              'commit_to_thread': staticmethod(forbidden)})(),
            provider=type('MustNotProvide', (), {'complete_json': staticmethod(forbidden)})(),
            action_id='request-changed', memory_role=retry_role,
            memory_generator=generator)

    assert calls == []
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes()) == before


@pytest.mark.parametrize('changed', ('prev_scene', 'max_repairs', 'required_sections',
                                     'memory_enabled', 'offline_generator_config'))
def test_response_loss_retry_rejects_changed_semantic_options_before_generation(
        engine, monkeypatch, changed):
    quiet(monkeypatch)
    engine.registry.register(InterpretiveMemorySystem())
    engine.store.allowed_types = engine.registry.event_types()
    old_world = engine.world
    scene = _build_scene(engine)
    generator = OfflineInterpretationGenerator('first private recollection')
    base = {
        'prev_scene': {'id': 'previous-scene', 'present': ['innkeeper']},
        'required_sections': frozenset({'clock', 'facts'}),
        'max_repairs': 2,
        'memory_role': 'hero',
        'memory_generator': generator,
    }
    run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='semantic-options', **base)
    before = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
              engine.store.jsonl_path.read_bytes())
    retry = dict(base)
    if changed == 'prev_scene':
        retry['prev_scene'] = {'id': 'different-previous-scene'}
    elif changed == 'max_repairs':
        retry['max_repairs'] = 1
    elif changed == 'required_sections':
        retry['required_sections'] = frozenset({'facts'})
    elif changed == 'memory_enabled':
        retry['memory_generator'] = None
    else:
        retry['memory_generator'] = OfflineInterpretationGenerator('other private recollection')
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('changed semantic option reached generation')

    monkeypatch.setattr('loop.turn.EventBatch', forbidden)
    monkeypatch.setattr('loop.turn.prepare_resources', forbidden)
    monkeypatch.setattr('loop.turn._run_turn_staged', forbidden)
    monkeypatch.setattr(generator, 'generate', forbidden)
    if retry['memory_generator'] is not None and retry['memory_generator'] is not generator:
        monkeypatch.setattr(retry['memory_generator'], 'generate', forbidden)
    with pytest.raises(RevisionConflict, match='different request'):
        run_turn(
            engine.registry, engine.store, old_world, copy.deepcopy(scene), '买酒',
            strategy=type('MustNotRun', (), {'produce': staticmethod(forbidden),
                                              'commit_to_thread': staticmethod(forbidden)})(),
            provider=type('MustNotProvide', (), {'complete_json': staticmethod(forbidden)})(),
            action_id='semantic-options', **retry)

    assert calls == []
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes()) == before


def test_response_loss_retry_normalizes_required_section_order(engine, monkeypatch):
    quiet(monkeypatch)
    old_world = engine.world
    scene = _build_scene(engine)
    first = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='normalized-required-sections',
        required_sections=frozenset({'clock', 'facts'}))
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('equivalent required_sections reached generation')

    retry = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=type('MustNotRun', (), {'produce': staticmethod(forbidden)})(),
        provider=type('MustNotProvide', (), {'complete_json': staticmethod(forbidden)})(),
        action_id='normalized-required-sections', required_sections=('facts', 'clock'))
    assert calls == []
    assert retry.receipt['duplicate'] and retry.events == first.events


def test_response_loss_retry_preserves_nonzero_repair_result(engine, monkeypatch):
    quiet(monkeypatch)
    old_world = engine.world
    scene = _build_scene(engine)
    invalid = commit()
    invalid['facts'] = [{'subject': 'missing', 'predicate': 'mood', 'value': 'worried'}]
    first = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(
            json_responses=[invalid, {'facts': []}]),
        action_id='repaired-response', max_repairs=1)
    assert first.repair_attempts == 1 and first.dropped_sections == []

    class MustNotRun:
        def produce(self, *args, **kwargs):
            pytest.fail('recovery re-ran repaired action')

    retry = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=MustNotRun(), provider=FakeLLMProvider(json_responses=[]),
        action_id='repaired-response', max_repairs=1)
    assert retry.repair_attempts == first.repair_attempts
    assert retry.dropped_sections == first.dropped_sections
    assert retry.commit == first.commit and retry.events == first.events


def test_response_loss_retry_reports_pending_mirror_until_explicit_sync(engine, monkeypatch):
    quiet(monkeypatch)
    old_world = engine.world
    scene = _build_scene(engine)
    original_rewrite = engine.store._rewrite_jsonl
    monkeypatch.setattr(engine.store, '_rewrite_jsonl',
                        lambda: (_ for _ in ()).throw(OSError('mirror unavailable')))
    first = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='mirror-response')
    assert first.receipt['mirror_pending'] is True
    before = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
              engine.store.jsonl_path.read_bytes())

    class MustNotRun:
        def produce(self, *args, **kwargs):
            pytest.fail('mirror-status recovery generated a turn')

    retry = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=MustNotRun(), provider=FakeLLMProvider(json_responses=[]), action_id='mirror-response')
    assert retry.receipt['duplicate'] and retry.receipt['mirror_pending'] is True
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes()) == before

    monkeypatch.setattr(engine.store, '_rewrite_jsonl', original_rewrite)
    engine.store.sync_jsonl()
    synced = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
              engine.store.jsonl_path.read_bytes())
    retry_after_sync = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=MustNotRun(), provider=FakeLLMProvider(json_responses=[]), action_id='mirror-response')
    assert retry_after_sync.receipt['duplicate']
    assert retry_after_sync.receipt['mirror_pending'] is False
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes()) == synced


def test_committed_action_mirror_check_is_read_only_and_conservative(engine):
    history = list(engine.store.iter_events(include_retracted=True))
    engine.store.jsonl_path.unlink()
    assert engine.store.mirror_pending_for_history(history) is True
    engine.store.jsonl_path.write_text('{not json}\n', encoding='utf-8')
    assert engine.store.mirror_pending_for_history(history) is True
    engine.store.jsonl_path.write_text(
        ''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in history), encoding='utf-8')
    assert engine.store.mirror_pending_for_history(history) is False
    engine.store.jsonl_path.write_text(json.dumps({**history[0], 'summary': 'different'}, ensure_ascii=False) + '\n',
                                       encoding='utf-8')
    assert engine.store.mirror_pending_for_history(history) is True


@pytest.mark.parametrize('mutate', ('nonempty_container', 'old_turnless_structure',
                                    'negative_repairs', 'missing_event_structure',
                                    'missing_base_revision', 'null_recap_status',
                                    'list_recap_status', 'dict_recap_status',
                                    'invalid_recap_status'))
def test_response_loss_retry_rejects_corrupt_result_metadata_before_generation(
        engine, monkeypatch, mutate):
    quiet(monkeypatch)
    old_world = engine.world
    scene = _build_scene(engine)
    run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='corrupt-result')
    row = engine.store._conn.execute(
        'SELECT metadata FROM action_result_metadata WHERE action_id=?', ('corrupt-result',)
    ).fetchone()
    metadata = json.loads(row['metadata'])
    if mutate == 'nonempty_container':
        metadata['result'] = ['not', 'an', 'object']
    elif mutate == 'old_turnless_structure':
        metadata = {'version': 1, 'outcome': 'legacy'}
    elif mutate == 'negative_repairs':
        metadata['result']['repair_attempts'] = -1
    elif mutate == 'missing_base_revision':
        metadata.pop('base_revision')
    elif mutate == 'null_recap_status':
        metadata['result']['recap_status'] = None
    elif mutate == 'list_recap_status':
        metadata['result']['recap_status'] = []
    elif mutate == 'dict_recap_status':
        metadata['result']['recap_status'] = {}
    elif mutate == 'invalid_recap_status':
        metadata['result']['recap_status'] = 'invented_status'
    else:
        metadata['result']['events'][0].pop('summary')
    engine.store._conn.execute(
        'UPDATE action_result_metadata SET metadata=? WHERE action_id=?',
        (json.dumps(metadata, sort_keys=True, ensure_ascii=False), 'corrupt-result'))
    engine.store._conn.commit()
    before = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
              engine.store.jsonl_path.read_bytes(), engine.store._conn.execute(
                  'SELECT metadata FROM action_result_metadata WHERE action_id=?',
                  ('corrupt-result',)).fetchone()['metadata'])
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('corrupt completed result reached generation')

    monkeypatch.setattr('loop.turn.EventBatch', forbidden)
    monkeypatch.setattr('loop.turn.prepare_resources', forbidden)
    monkeypatch.setattr('loop.turn._run_turn_staged', forbidden)
    with pytest.raises(ActionAlreadyCommitted) as exc:
        run_turn(
            engine.registry, engine.store, old_world, scene, '买酒',
            strategy=type('MustNotRun', (), {'produce': staticmethod(forbidden),
                                              'commit_to_thread': staticmethod(forbidden)})(),
            provider=type('MustNotProvide', (), {'complete_json': staticmethod(forbidden)})(),
            action_id='corrupt-result')
    assert exc.value.metadata_status == 'corrupt'
    assert calls == []
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes(), engine.store._conn.execute(
                'SELECT metadata FROM action_result_metadata WHERE action_id=?',
                ('corrupt-result',)).fetchone()['metadata']) == before


def test_response_loss_retry_keeps_current_world_after_second_action_and_reopen(engine, monkeypatch):
    quiet(monkeypatch)
    old_world = engine.world
    original_scene = copy.deepcopy(_build_scene(engine))
    first = run_turn(
        engine.registry, engine.store, old_world, original_scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit(7)]),
        action_id='old-response')
    engine.world = first.world
    second = run_turn(
        engine.registry, engine.store, engine.world, _build_scene(engine), '再买一杯',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit(4)]))

    class MustNotRun:
        def produce(self, *args, **kwargs):
            pytest.fail('old response recovery generated a new turn')

    retry = run_turn(
        engine.registry, engine.store, old_world, original_scene, '买酒',
        strategy=MustNotRun(), provider=FakeLLMProvider(json_responses=[]), action_id='old-response')
    assert retry.receipt['duplicate'] is True
    assert retry.commit == first.commit and retry.events == first.events
    assert world_signature(retry.world) == world_signature(second.world)

    campaign_dir = engine.store.db_path.parent
    engine.store.close()
    reopened = build_engine(campaign_dir, provider=FakeLLMProvider(json_responses=[]))
    try:
        reopened_retry = run_turn(
            reopened.registry, reopened.store, old_world, original_scene, '买酒',
            strategy=MustNotRun(), provider=FakeLLMProvider(json_responses=[]), action_id='old-response')
        assert reopened_retry.receipt['duplicate'] is True
        assert reopened_retry.commit == first.commit and reopened_retry.events == first.events
        assert world_signature(reopened_retry.world) == world_signature(second.world)
    finally:
        reopened.store.close()


def test_run_turn_retracted_action_id_requires_new_id_before_strategy(engine, monkeypatch):
    quiet(monkeypatch)
    result = run_turn(
        engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='retracted-preflight')
    engine.store.retract_from_turn(result.events[0]['turn'])

    class MustNotRun:
        def produce(self, *args, **kwargs):
            pytest.fail('strategy called for retracted duplicate')

    with pytest.raises(RevisionConflict, match='retracted; submit a new action_id'):
        run_turn(
            engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
            strategy=MustNotRun(), provider=FakeLLMProvider(json_responses=[commit()]),
            action_id='retracted-preflight')


@pytest.mark.parametrize(('metadata', 'raw_metadata', 'expected_status'), [
    (None, None, 'missing'),
    ({'version': 1, 'outcome': 'saved'}, None, 'available'),
    ({'version': 2, 'outcome': 'future'}, None, 'unsupported_version'),
    (None, '{not json', 'corrupt'),
])
def test_completed_action_preflight_stops_all_generation_paths(
        engine, monkeypatch, metadata, raw_metadata, expected_status):
    action_id = f'completed-{expected_status}'
    event_payload = event(subject='hero', predicate='coins', value=7)
    receipt = engine.store.append_many([event_payload], action_id=action_id,
                                       result_metadata=metadata)
    if raw_metadata is not None:
        engine.store._conn.execute(
            'INSERT INTO action_result_metadata VALUES (?,?)', (action_id, raw_metadata))
        engine.store._conn.commit()
    before_events = list(engine.store.iter_events(include_retracted=True))
    before_revision = engine.store.revision
    before_receipts = list(engine.store._conn.execute('SELECT * FROM action_receipts'))
    before_mirror = engine.store.jsonl_path.read_bytes()
    calls = {name: 0 for name in ('batch', 'resources', 'strategy', 'provider', 'generator', 'thread')}

    def forbidden(name):
        def call(*args, **kwargs):
            calls[name] += 1
            raise AssertionError(f'{name} called for committed action')
        return call

    class MustNotRun:
        produce = staticmethod(forbidden('strategy'))
        commit_to_thread = staticmethod(forbidden('thread'))

    monkeypatch.setattr('loop.turn.EventBatch', forbidden('batch'))
    monkeypatch.setattr('loop.turn.prepare_resources', forbidden('resources'))
    provider = type('MustNotProvide', (), {'complete_json': staticmethod(forbidden('provider'))})()

    with pytest.raises(ActionAlreadyCommitted) as exc:
        run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
                 strategy=MustNotRun(), provider=provider, action_id=action_id,
                 memory_role='hero')

    expected_metadata_status = 'corrupt' if expected_status == 'available' else expected_status
    assert exc.value.receipt['action_id'] == action_id
    assert exc.value.metadata_status == expected_metadata_status
    assert exc.value.result_metadata is None
    assert calls == {name: 0 for name in calls}
    assert list(engine.store.iter_events(include_retracted=True)) == before_events
    assert engine.store.revision == before_revision
    assert list(engine.store._conn.execute('SELECT * FROM action_receipts')) == before_receipts
    assert engine.store.jsonl_path.read_bytes() == before_mirror


def test_response_loss_retry_recovers_legacy_missing_recap_status_without_writes(
        engine, monkeypatch):
    """Only an absent status is legacy-compatible; it must not regenerate."""
    quiet(monkeypatch)
    old_world = engine.world
    scene = _build_scene(engine)
    first = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        action_id='legacy-recap-status')
    row = engine.store._conn.execute(
        'SELECT metadata FROM action_result_metadata WHERE action_id=?',
        ('legacy-recap-status',)).fetchone()
    metadata = json.loads(row['metadata'])
    metadata['result'].pop('recap_status')
    engine.store._conn.execute(
        'UPDATE action_result_metadata SET metadata=? WHERE action_id=?',
        (json.dumps(metadata, sort_keys=True, ensure_ascii=False), 'legacy-recap-status'))
    engine.store._conn.commit()
    before = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
              engine.store.jsonl_path.read_bytes(), engine.store._conn.execute(
                  'SELECT metadata FROM action_result_metadata WHERE action_id=?',
                  ('legacy-recap-status',)).fetchone()['metadata'])

    def forbidden(*args, **kwargs):
        raise AssertionError('legacy completed action reached generation')

    recovered = run_turn(
        engine.registry, engine.store, old_world, scene, '买酒',
        strategy=type('MustNotRun', (), {'produce': staticmethod(forbidden),
                                          'commit_to_thread': staticmethod(forbidden)})(),
        provider=type('MustNotProvide', (), {'complete_json': staticmethod(forbidden)})(),
        action_id='legacy-recap-status')

    assert recovered.receipt['duplicate']
    assert recovered.recap_status == 'not_due'
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision,
            engine.store.jsonl_path.read_bytes(), engine.store._conn.execute(
                'SELECT metadata FROM action_result_metadata WHERE action_id=?',
                ('legacy-recap-status',)).fetchone()['metadata']) == before


def test_retracted_action_preflight_stops_all_generation_paths(engine, monkeypatch):
    receipt = engine.store.append_many(
        [event(subject='hero', predicate='coins', value=7)], action_id='completed-retracted')
    engine.store.retract_from_seq(receipt['first_seq'])
    before_events = list(engine.store.iter_events(include_retracted=True))
    before_revision = engine.store.revision
    before_mirror = engine.store.jsonl_path.read_bytes()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('generation path called for retracted action')

    monkeypatch.setattr('loop.turn.EventBatch', forbidden)
    monkeypatch.setattr('loop.turn.prepare_resources', forbidden)

    with pytest.raises(RevisionConflict, match='retracted; submit a new action_id'):
        run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
                 strategy=type('MustNotRun', (), {'produce': staticmethod(forbidden),
                                                   'commit_to_thread': staticmethod(forbidden)})(),
                 provider=type('MustNotProvide', (), {'complete_json': staticmethod(forbidden)})(),
                 action_id='completed-retracted', memory_role='hero')

    assert calls == []
    assert list(engine.store.iter_events(include_retracted=True)) == before_events
    assert engine.store.revision == before_revision
    assert engine.store.jsonl_path.read_bytes() == before_mirror


def test_committed_action_read_uses_one_sqlite_snapshot(engine, monkeypatch):
    receipt = engine.store.append_many(
        [event(subject='hero', predicate='coins', value=7)], action_id='snapshot-action',
        result_metadata={'version': 1, 'outcome': 'saved'})
    peer = EventStore(engine.store.db_path, engine.store.jsonl_path, engine.store.allowed_types)
    try:
        engine.store._conn.execute('PRAGMA journal_mode=WAL')
        peer._conn.execute('PRAGMA journal_mode=WAL')
        original = engine.store.action_receipt_is_retracted

        def retract_after_receipt_read(receipt_row):
            peer.retract_from_seq(receipt['first_seq'])
            return original(receipt_row)

        monkeypatch.setattr(engine.store, 'action_receipt_is_retracted', retract_after_receipt_read)
        observed = engine.store.read_committed_action('snapshot-action')

        assert observed['retracted'] is False
        assert observed['metadata_raw'] == '{"outcome":"saved","version":1}'
        assert original(receipt) is True
    finally:
        peer.close()


def test_concurrent_snapshot_and_undo_cannot_accept_stale_proposals(engine):
    left = EventBatch(engine.store)
    right = EventBatch(engine.store)
    left.append(event(subject='hero',predicate='coins',value=7))
    right.append(event(subject='hero',predicate='coins',value=8))
    left.publish(action_id='left')
    with pytest.raises(RevisionConflict):
        right.publish(action_id='right')
    stale = EventBatch(engine.store)
    stale.append(event(subject='hero',predicate='coins',value=6))
    rewind(engine, left.turn)
    with pytest.raises(RevisionConflict):
        stale.publish(action_id='after-undo')


def test_backstage_failure_drops_only_its_partial_proposal_and_keeps_narration(engine, monkeypatch):
    quiet(monkeypatch)
    def broken(registry, store, *args, **kwargs):
        store.append(event(subject='hero',predicate='coins',value=0))
        raise RuntimeError('failed after first write')
    monkeypatch.setattr('loop.turn.digest_fleet', broken)
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
                     strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]))
    assert result.world['systems']['ontology'].value_at('hero','coins',1) == 7
    assert len([e for e in result.events if e['type']=='narration_recorded']) == 1
    assert {e['turn'] for e in result.events} == {1}
    engine.world = result.world
    rewind(engine, 1)
    assert engine.world['systems']['ontology'].value_at('hero','coins',1) == 10
    assert not any(e['type']=='narration_recorded' for e in engine.store.iter_events())


def test_digest_cancellation_is_not_reclassified_as_pending_or_published(engine, monkeypatch):
    """Cancellation remains control flow; the enclosing turn batch must abort."""
    quiet(monkeypatch)
    before = (list(engine.store.iter_events(include_retracted=True)), engine.store.revision)

    def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr('loop.turn.digest_fleet', cancelled)
    with pytest.raises(asyncio.CancelledError):
        run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
                 strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]))
    assert (list(engine.store.iter_events(include_retracted=True)), engine.store.revision) == before


def test_run_turn_memory_default_does_not_call_generator(engine, monkeypatch):
    quiet(monkeypatch)
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
             strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
             memory_role='hero', memory_generator=generator)
    assert not any(e['type'] == 'character_interpretation_recorded' for e in engine.store.iter_events())


def test_run_turn_without_canonical_narration_continues_backstage(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types(); calls = []
    monkeypatch.setattr('loop.turn.digest_fleet', lambda *args, **kwargs: calls.append(1) or [])
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    blank = commit(); blank['narration'] = ''
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[blank]), memory_role='hero', memory_generator=generator)
    assert calls == [1] and result.receipt['revision'] == engine.store.revision


def test_run_turn_memory_no_owner_does_not_call_generator(engine, monkeypatch):
    quiet(monkeypatch)
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
             strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
             memory_role='hero', memory_generator=generator)
    assert not any(e['type'] == 'character_interpretation_recorded' for e in engine.store.iter_events())


def test_run_turn_memory_nonexistent_role_does_not_call_generator(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    scene = {**_build_scene(engine), 'protagonist': 'missing'}
    with pytest.raises(PermissionError):
        run_turn(engine.registry, engine.store, engine.world, scene, '买酒', strategy=AuthorStrategy(),
                 provider=FakeLLMProvider(json_responses=[commit()]), memory_role='missing', memory_generator=generator)
    assert not any(e['type'] == 'character_interpretation_recorded' for e in engine.store.iter_events())


def test_run_turn_memory_captured_protagonist_rejects_later_peer_role(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    engine.store.append(event('entity_created', id='peer', etype='Person', tier='tracked'))
    engine.world = project(engine.registry, engine.store.iter_events())
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    with pytest.raises(PermissionError):
        run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(),
                 provider=FakeLLMProvider(json_responses=[commit()]), memory_role='peer', memory_generator=generator)


def test_memory_generator_ignores_old_turn_narration(engine, monkeypatch):
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    engine.store.append(kernel_event('narration_recorded', day=1, scene='inn', summary='old', deltas={'text':'old'}, turn=1))
    batch = EventBatch(engine.store, turn=2)
    old_source = next(event for event in engine.store.iter_events()
                      if event['type'] == 'narration_recorded')
    binding = HostNarrationBinding(batch, old_source, old_source['id'], 1, 'hero')
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    with pytest.raises(PermissionError):
        stage_offline_interpretation(batch, binding, engine.registry, generator,
                                     protagonist='hero', role_exists=True)


def test_run_turn_memory_generator_error_keeps_foreground(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: (_ for _ in ()).throw(RuntimeError('offline failure')))
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=generator)
    assert any(e['type'] == 'narration_recorded' for e in result.events)
    assert not any(e['type'] == 'character_interpretation_recorded' for e in result.events)
    assert engine.store._conn.execute('SELECT COUNT(*) FROM action_receipts').fetchone()[0] == 1


def test_run_turn_memory_private_error_is_not_logged(engine, monkeypatch, caplog):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    sentinel = 'PRIVATE_SENTINEL_DO_NOT_LOG'
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: (_ for _ in ()).throw(RuntimeError(sentinel)))
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=generator)
    assert any(e['type'] == 'narration_recorded' for e in result.events)
    assert sentinel not in caplog.text and 'Traceback' not in caplog.text


def test_run_turn_memory_generator_cancelled_rejects_whole_turn(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: (_ for _ in ()).throw(asyncio.CancelledError()))
    before = list(engine.store.iter_events(include_retracted=True)); revision = engine.store.revision
    with pytest.raises(asyncio.CancelledError):
        run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=generator)
    assert list(engine.store.iter_events(include_retracted=True)) == before and engine.store.revision == revision
    assert engine.store._conn.execute('SELECT COUNT(*) FROM action_receipts').fetchone()[0] == 0


def test_run_turn_memory_generator_permission_error_rejects(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: (_ for _ in ()).throw(PermissionError('integrity')))
    before = list(engine.store.iter_events(include_retracted=True)); revision = engine.store.revision
    with pytest.raises(PermissionError):
        run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=generator)
    assert list(engine.store.iter_events(include_retracted=True)) == before and engine.store.revision == revision
    assert engine.store._conn.execute('SELECT COUNT(*) FROM action_receipts').fetchone()[0] == 0


def test_run_turn_memory_opt_in_stages_private_record_with_narration(engine, monkeypatch):
    quiet(monkeypatch)
    engine.registry.register(InterpretiveMemorySystem())
    engine.store.allowed_types = engine.registry.event_types()
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
                      strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
                      memory_role='hero', memory_generator=OfflineInterpretationGenerator('private'))
    memory = [e for e in result.events if e['type'] == 'character_interpretation_recorded']
    narration = [e for e in result.events if e['type'] == 'narration_recorded']
    assert len(memory) == len(narration) == 1
    assert memory[0]['turn'] == narration[0]['turn'] == 1
    assert memory[0]['deltas']['source_event_id'] == narration[0]['id']


def test_run_turn_memory_generator_receives_deep_isolated_source(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    generator = OfflineInterpretationGenerator('private')
    def mutate(source):
        source['deltas']['text'] = 'mutated'; source['actors'].append('peer')
        return 'private'
    monkeypatch.setattr(generator, 'generate', mutate)
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=generator)
    narration = next(e for e in result.events if e['type'] == 'narration_recorded')
    memory = next(e for e in result.events if e['type'] == 'character_interpretation_recorded')
    assert narration['deltas']['text'] == commit()['narration'] and narration['actors'] == []
    assert memory['deltas']['source_event_id'] == narration['id']


def test_memory_generator_source_toctou_is_rejected_before_publish(engine, monkeypatch):
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    batch = EventBatch(engine.store, turn=1)
    source = kernel_event('narration_recorded', day=1, scene='inn', summary='narration recorded', deltas={'text':'original'}, turn=1)
    batch.append(source); actual = batch.events[0]; before = list(engine.store.iter_events(include_retracted=True)); revision = engine.store.revision; mirror = engine.store.jsonl_path.read_bytes()
    binding = HostNarrationBinding(batch, actual, actual['id'], batch.turn, 'hero')
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda dto: (actual['deltas'].__setitem__('text', 'tampered'), 'private')[1])
    with pytest.raises(PermissionError):
        stage_offline_interpretation(batch, binding, engine.registry, generator, protagonist='hero', role_exists=True)
    assert list(engine.store.iter_events(include_retracted=True)) == before and engine.store.revision == revision
    assert engine.store._conn.execute('SELECT COUNT(*) FROM action_receipts').fetchone()[0] == 0
    assert engine.store.jsonl_path.read_bytes() == mirror


def test_memory_binding_rejects_copied_source_before_generation(engine, monkeypatch):
    import copy
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    batch = EventBatch(engine.store, turn=1)
    batch.append(kernel_event('narration_recorded', day=1, scene='inn', summary='narration recorded', deltas={'text':'original'}, turn=1))
    actual = batch.events[0]
    binding = HostNarrationBinding(batch, copy.deepcopy(actual), actual['id'], batch.turn, 'hero')
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    with pytest.raises(PermissionError):
        stage_offline_interpretation(batch, binding, engine.registry, generator, protagonist='hero', role_exists=True)


def test_memory_binding_rejects_wrong_turn_before_generation(engine, monkeypatch):
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    batch = EventBatch(engine.store, turn=1)
    batch.append(kernel_event('narration_recorded', day=1, scene='inn', summary='narration recorded', deltas={'text':'original'}, turn=1))
    actual = batch.events[0]
    binding = HostNarrationBinding(batch, actual, actual['id'], batch.turn + 1, 'hero')
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    with pytest.raises(PermissionError):
        stage_offline_interpretation(batch, binding, engine.registry, generator, protagonist='hero', role_exists=True)


def test_memory_binding_rejects_wrong_role_before_generation(engine, monkeypatch):
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    batch = EventBatch(engine.store, turn=1)
    batch.append(kernel_event('narration_recorded', day=1, scene='inn', summary='narration recorded', deltas={'text':'original'}, turn=1))
    actual = batch.events[0]
    binding = HostNarrationBinding(batch, actual, actual['id'], batch.turn, 'peer')
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    with pytest.raises(PermissionError):
        stage_offline_interpretation(batch, binding, engine.registry, generator, protagonist='hero', role_exists=True)


def test_memory_binding_rejects_cross_batch_before_generation(engine, monkeypatch):
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    left, right = EventBatch(engine.store, turn=1), EventBatch(engine.store, turn=1)
    left.append(kernel_event('narration_recorded', day=1, scene='inn', summary='narration recorded', deltas={'text':'original', 'provenance': {'role':'hero'}}, turn=1))
    actual = left.events[0]
    binding = HostNarrationBinding(right, actual, actual['id'], right.turn, 'hero')
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    with pytest.raises(PermissionError):
        stage_offline_interpretation(right, binding, engine.registry, generator, protagonist='hero', role_exists=True)


def test_memory_binding_rejects_valid_other_batch_before_generation(engine, monkeypatch):
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    left, right = EventBatch(engine.store, turn=1), EventBatch(engine.store, turn=1)
    for batch in (left, right):
        batch.append(kernel_event('narration_recorded', day=1, scene='inn', summary='narration recorded', deltas={'text':'original'}, turn=1))
    source = left.events[0]
    binding = HostNarrationBinding(left, source, source['id'], left.turn, 'hero')
    calls = []; generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda dto: calls.append(dto) or pytest.fail('generator called'))
    before = list(engine.store.iter_events(include_retracted=True)); revision = engine.store.revision
    with pytest.raises(PermissionError):
        stage_offline_interpretation(right, binding, engine.registry, generator, protagonist='hero', role_exists=True)
    assert not calls and len(left.events) == len(right.events) == 1
    assert left.publish_validators == right.publish_validators == [] and left.retractions == right.retractions == []
    assert list(engine.store.iter_events(include_retracted=True)) == before and engine.store.revision == revision


@pytest.mark.parametrize('binding', [None, {'batch': 'forged'}])
def test_memory_binding_rejects_malformed_before_generation(engine, monkeypatch, binding):
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    batch = EventBatch(engine.store, turn=1)
    generator = OfflineInterpretationGenerator('private')
    monkeypatch.setattr(generator, 'generate', lambda source: pytest.fail('generator called'))
    with pytest.raises(PermissionError):
        stage_offline_interpretation(batch, binding, engine.registry, generator, protagonist='hero', role_exists=True)


def test_run_turn_memory_result_world_matches_persisted_replay(engine, monkeypatch):
    quiet(monkeypatch); engine.registry._by_event.pop('quest_demoted', None)
    assert engine.registry.owner_of_event('quest_demoted') is None
    engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=OfflineInterpretationGenerator('private'))
    assert [json.loads(line) for line in engine.store.jsonl_path.read_text(encoding='utf-8').splitlines()] == list(engine.store.iter_events(include_retracted=True))
    replay = project(engine.registry, engine.store.iter_events())
    assert any(e['type'] == 'character_interpretation_recorded' for e in engine.store.iter_events())
    def signature(world):
        graph = world['systems']['ontology']
        return {'meta': world['meta'], 'systems': {name: value for name, value in world['systems'].items() if name != 'ontology'}, 'ontology': {'entities': {key: asdict(value) for key, value in graph.entities.items()}, 'facts': [asdict(value) for value in graph.facts], 'relations': [asdict(value) for value in graph.relations]}}
    assert result.world['_revision'] == engine.store.revision
    assert signature(result.world) == signature(replay)
    assert set(result.world) == {'meta', 'systems', '_revision'}
    db_path, jsonl_path = engine.store.db_path, engine.store.jsonl_path
    engine.store.close()
    reopened = EventStore(db_path, jsonl_path, engine.registry.event_types())
    try:
        assert signature(result.world) == signature(project(engine.registry, reopened.iter_events()))
    finally:
        reopened.close()


def test_rewind_retracts_memory_with_its_foreground_turn(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    before = list(engine.store.iter_events(include_retracted=True))
    seed_world = project(engine.registry, engine.store.iter_events())
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=OfflineInterpretationGenerator('private'))
    narration = [e for e in result.events if e['type'] == 'narration_recorded']; memory = [e for e in result.events if e['type'] == 'character_interpretation_recorded']
    assert len(narration) == len(memory) == 1 and narration[0]['turn'] == memory[0]['turn']
    assert memory[0]['deltas']['source_event_id'] == narration[0]['id']
    assert result.world['systems']['interpretive_memory']['by_actor']['hero']
    receipts_before = [dict(row) for row in engine.store._conn.execute('SELECT * FROM action_receipts')]
    assert len(receipts_before) == 1 and receipts_before[0]['action_id'] == result.receipt['action_id']
    turn = result.events[0]['turn']; receipt_count = engine.store._conn.execute('SELECT COUNT(*) FROM action_receipts').fetchone()[0]
    rewind(engine, turn)
    history = list(engine.store.iter_events(include_retracted=True))
    assert [json.loads(line) for line in engine.store.jsonl_path.read_text(encoding='utf-8').splitlines()] == history
    assert all(event['retracted'] for event in history if event.get('turn') == turn)
    assert history[:len(before)] == before
    assert engine.store._conn.execute('SELECT COUNT(*) FROM action_receipts').fetchone()[0] == receipt_count
    assert project(engine.registry, engine.store.iter_events())['systems']['interpretive_memory']['by_actor'] == {}
    revision = engine.store.revision; db_path, jsonl_path = engine.store.db_path, engine.store.jsonl_path
    receipt_rows = [dict(row) for row in engine.store._conn.execute('SELECT * FROM action_receipts')]
    assert receipt_rows == receipts_before
    engine.store.close(); reopened = EventStore(db_path, jsonl_path, engine.registry.event_types())
    try:
        replay = project(engine.registry, reopened.iter_events())
        assert set(replay) == {'meta', 'systems'}
        assert reopened.revision == revision
        assert replay['systems']['interpretive_memory']['by_actor'] == {}
        assert [(e['id'], e['retracted']) for e in reopened.iter_events(include_retracted=True)] == [(e['id'], e['retracted']) for e in history]
        assert [dict(row) for row in reopened._conn.execute('SELECT * FROM action_receipts')] == receipt_rows
        assert world_signature(replay) == world_signature(seed_world)
        mirror = [json.loads(line) for line in jsonl_path.read_text(encoding='utf-8').splitlines()]
        assert mirror == list(reopened.iter_events(include_retracted=True))
    finally:
        reopened.close()


def test_memory_turn_mirror_failure_recovers_without_replay(engine, monkeypatch):
    quiet(monkeypatch); engine.registry.register(InterpretiveMemorySystem()); engine.store.allowed_types = engine.registry.event_types()
    old_mirror = engine.store.jsonl_path.read_bytes() if engine.store.jsonl_path.exists() else b''
    original = engine.store._rewrite_jsonl
    monkeypatch.setattr(engine.store, '_rewrite_jsonl', lambda: (_ for _ in ()).throw(OSError('mirror down')))
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '买酒', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]), memory_role='hero', memory_generator=OfflineInterpretationGenerator('private'))
    events = list(engine.store.iter_events(include_retracted=True)); revision = engine.store.revision
    receipts = [dict(row) for row in engine.store._conn.execute('SELECT * FROM action_receipts')]
    assert result.receipt['mirror_pending'] and any(e['type'] == 'character_interpretation_recorded' for e in events)
    assert (engine.store.jsonl_path.read_bytes() if engine.store.jsonl_path.exists() else b'') == old_mirror
    monkeypatch.setattr(engine.store, '_rewrite_jsonl', original)
    engine.store.sync_jsonl()
    assert [json.loads(line) for line in engine.store.jsonl_path.read_text(encoding='utf-8').splitlines()] == events
    assert list(engine.store.iter_events(include_retracted=True)) == events and engine.store.revision == revision
    assert [dict(row) for row in engine.store._conn.execute('SELECT * FROM action_receipts')] == receipts
    assert engine.store.mirror_error is None


def test_failed_backstage_retraction_cannot_leak_out_of_batch_savepoint(engine, monkeypatch):
    """A failed hook may not leave a deferred rewind behind for publish()."""
    quiet(monkeypatch)
    engine.store.append(kernel_event(
        'fact_asserted', day=1, scene='inn', summary='prior committed turn',
        deltas={'subject':'hero', 'predicate':'coins', 'value':8}, turn=1,
    ))
    history_before = list(engine.store.iter_events(include_retracted=True))

    def broken(registry, store, *args, **kwargs):
        store.retract_from_turn(1)
        raise RuntimeError('rollback after staging a rewind')

    monkeypatch.setattr('loop.turn.digest_fleet', broken)
    result = run_turn(engine.registry, engine.store, engine.world, _build_scene(engine), '查看酒馆',
                      strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]))

    persisted = list(engine.store.iter_events(include_retracted=True))
    assert persisted[len(history_before) - 1] == history_before[-1]
    assert persisted[len(history_before) - 1]['retracted'] is False
    assert any(ev['type'] == 'narration_recorded' for ev in result.events)


def test_nested_failed_savepoint_restores_only_its_own_retraction_intent(engine):
    """An inner hook failure does not erase its caller's valid staged work."""
    engine.store.append(kernel_event(
        'fact_asserted', day=1, scene='inn', summary='prior committed turn',
        deltas={'subject':'hero', 'predicate':'coins', 'value':8}, turn=1,
    ))
    batch = EventBatch(engine.store)
    batch.append(event(subject='hero', predicate='coins', value=7))
    with batch.savepoint():
        try:
            with batch.savepoint():
                batch.retract_from_turn(1)
                raise RuntimeError('inner hook failed')
        except RuntimeError:
            pass
        batch.append(event(subject='hero', predicate='coins', value=6))

    assert batch.retractions == []
    receipt = batch.publish(action_id='nested-savepoint')
    assert receipt['revision'] == engine.store.revision
    assert all(not ev['retracted'] for ev in engine.store.iter_events(include_retracted=True))


def test_reopened_store_replays_retracted_whole_turn_as_pre_turn_world(engine, monkeypatch):
    """A rewind remains complete after closing and reopening the SQLite store."""
    def signature(world):
        graph = world['systems']['ontology']
        systems = {name: value for name, value in world['systems'].items()
                   if name != 'ontology'}
        return {
            'meta': world['meta'],
            'systems': systems,
            'ontology': {
                'entities': {key: asdict(value) for key, value in graph.entities.items()},
                'facts': [asdict(value) for value in graph.facts],
                'relations': [asdict(value) for value in graph.relations],
            },
        }

    quiet(monkeypatch)
    campaign_dir = engine.store.db_path.parent
    before = project(engine.registry, engine.store.iter_events())

    result = run_turn(
        engine.registry, engine.store, engine.world, _build_scene(engine), '买酒',
        strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[commit()]),
        required_sections=REQUIRED_SECTIONS,
    )
    assert any(ev['type'] == 'narration_recorded' for ev in result.events)
    rewind(engine, 1)
    retracted = list(engine.store.iter_events(include_retracted=True))
    assert all(ev['retracted'] for ev in retracted if ev.get('turn') == 1)
    engine.store.close()

    reopened = build_engine(campaign_dir, provider=FakeLLMProvider())
    try:
        assert signature(reopened.world) == signature(before)
        full_history = list(reopened.store.iter_events(include_retracted=True))
        assert [(ev['id'], ev['retracted']) for ev in full_history] == [
            (ev['id'], ev['retracted']) for ev in retracted
        ]
        # Replaying all rows uses the same retracted-event policy as cold start.
        assert signature(project(reopened.registry, full_history)) == signature(before)
    finally:
        reopened.store.close()


def test_required_sections_and_invalid_id_shapes_are_repairable(engine):
    incomplete = TurnCommit('hello', {'clock':commit()['clock']})
    errors = validate_commit(engine.registry,incomplete,engine.world,required_sections=REQUIRED_SECTIONS)
    assert {e.section for e in errors} == {'moves','places','cast','facts'}
    bad_id = TurnCommit('bad', {'entities':[{'id':['not','hashable'],'etype':'Person'}]})
    assert validate_commit(engine.registry,bad_id,engine.world)


def test_pov_search_cannot_match_private_goal_or_hidden_place(engine):
    engine.store.append_many([
        event('entity_created',id='npc',etype='Person',tier='tracked'),
        event('relation_added',src='npc',rel='located_in',dst='inn'),
        event(subject='npc',predicate='sketch',value='戴蓝帽的人',secrecy='public'),
        event(subject='npc',predicate='goal',value='CANARY_POISON_KING',secrecy='secret'),
        event('entity_created',id='hidden_room',etype='Place',attrs={'visibility':'hidden','seed':'CANARY_DIAMOND_VAULT','level':3,'kind':'venue'}),
    ])
    engine.world=project(engine.registry,engine.store.iter_events())
    scene=_build_scene(engine)
    tools=build_tool_registry(engine.registry,engine.world,scene)
    for name,q in [('recall_query','CANARY'),('map_query','CANARY'),('ambient_query','CANARY')]:
        answer=tools.execute(name,{'q':q})
        assert 'CANARY_POISON' not in answer and 'CANARY_DIAMOND' not in answer
    context=assemble_context(engine.registry,engine.world,scene,query='CANARY')
    assert 'CANARY' not in context
    assert '戴蓝帽' in context
    # A present NPC is not an authorization to read its private perspective.
    assert 'error' in json.loads(tools.execute('recall_query',{'q':'CANARY','pov':'npc'}))
    assert engine.world['systems']['ontology'].value_at('npc','goal',1)=='CANARY_POISON_KING'


def test_native_tool_group_preserves_reasoning_and_parallel_call_shape():
    message={'role':'assistant','content':None,'reasoning_content':'private protocol field',
             'tool_calls':[{'id':str(i),'type':'function','function':{'name':'lookup','arguments':'{}'}} for i in range(2)]}
    _,calls=_openai_parse({'choices':[{'finish_reason':'tool_calls','message':message}]})
    history=[]
    for call in calls:
        _openai_append_result(history,call,'{}')
    assert [m['role'] for m in history]==['assistant','tool','tool']
    assert len(history[0]['tool_calls'])==2
    assert history[0]['reasoning_content']==message['reasoning_content']


def test_deepseek_profile_explicitly_disables_thinking_by_default(monkeypatch):
    captured=[]
    def fake(url,headers,body,**kw):
        captured.append((url,body))
        return {'choices':[{'message':{'content':'OK'}}]}
    monkeypatch.setattr('llm.provider._do_post',fake)
    provider=DeepSeekProvider('deepseek-flash','placeholder')
    assert provider.complete('sys','user')=='OK'
    assert captured[0][0]=='https://api.deepseek.com/chat/completions'
    assert captured[0][1]['thinking']=={'type':'disabled'}


def test_malformed_envelope_cannot_be_preserved_as_player_prose(engine):
    strategy = AuthorStrategy()
    strategy._messages = []
    raw = '{"narration":"你好。"}\n"facts":[{"secret":"hidden"}]}'
    repaired = strategy._reask_json(raw, FakeLLMProvider(json_responses=[commit()]))
    assert json.loads(repaired)['narration'] == commit()['narration']
    contaminated = TurnCommit(raw, commit())
    assert any(e.code=='structured_prose' for e in validate_commit(engine.registry,contaminated,engine.world))


def test_json_mode_is_scoped_to_structured_deepseek_calls(monkeypatch):
    from llm.provider import json_call
    captured=[]
    def fake(url, headers, body, **kw):
        captured.append(body)
        return {'choices':[{'message':{'content':'{}'}}]}
    monkeypatch.setattr('llm.provider._do_post',fake)
    provider=DeepSeekProvider('deepseek-flash','placeholder')
    json_call(provider.complete_messages, [{'role':'user','content':'Return JSON {}'}])
    provider.complete('Write prose','hello')
    assert captured[0]['response_format']=={'type':'json_object'}
    assert 'response_format' not in captured[1]


def test_current_self_resources_override_stale_knowledge_cache(engine):
    engine.store.append_many([
        event('knowledge_set',knower='hero',fact_key='hero.coins',value=10),
        event(subject='hero',predicate='coins',value=7,secrecy='public'),
    ])
    engine.world=project(engine.registry,engine.store.iter_events())
    context=assemble_context(engine.registry,engine.world,_build_scene(engine))
    assert 'hero.coins = 7' in context
    assert 'hero.coins = 10' not in context


def test_repeating_previous_narration_is_repaired_before_publication(engine,monkeypatch):
    quiet(monkeypatch)
    previous='上一回合，你付清了账款，坐回原位，观察窗外来来往往的人。'*12
    engine.store.append(event('narration_recorded',scene='inn',text=previous))
    engine.world=project(engine.registry,engine.store.iter_events())
    first=commit();first['narration']=previous
    provider=FakeLLMProvider(json_responses=[first,{'narration':'这次你起身走到窗前，老板摇头谢绝了新的报价。'}])
    answer=run_turn(engine.registry,engine.store,engine.world,_build_scene(engine),'提出新的报价',
                    strategy=AuthorStrategy(),provider=provider,required_sections=REQUIRED_SECTIONS)
    assert answer.repair_attempts==1
    assert answer.narration!=previous
