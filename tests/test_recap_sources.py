"""RCV-117: exact, replay-validated inputs to recursive public recaps."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from app.engine import build_engine, rewind
from kernel.events import kernel_event
from kernel.projection import project
from kernel.registry import Registry
from systems.narrative import NarrativeSystem, summary_source_ref


def _hash(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(',', ':')
    ).encode()).hexdigest()


def _reg():
    return Registry().register(NarrativeSystem())


def _state(events):
    return project(_reg(), events)['systems']['narrative']


def _episodes(count=3, *, legacy=False):
    events = []
    for i in range(count):
        # Repeated wording and revisits must still have distinct identities.
        scene = 'inn' if i % 2 == 0 else 'market'
        narr = kernel_event('narration_recorded', day=i + 1, scene=scene,
                            summary='public', turn=i + 1,
                            deltas={'scene': scene, 'text': '明天我会回来。'})
        events.append(narr)
        deltas = {'scene': scene, 'summary': 'A promise.'}
        if not legacy:
            deltas['source'] = summary_source_ref(_state(events)['scenes'][-1])
        events.append(kernel_event('scene_summarized', day=i + 1, scene=scene,
                                   summary='summary', turn=i + 1, deltas=deltas))
    return events


def _expected_ref(events, end):
    """Independent wire-format expectation from the source events themselves."""
    recaps = [e for e in events if e['type'] == 'recap_recompressed']
    previous = recaps[-1] if recaps else None
    start = previous['deltas']['summarized_through_index'] if previous else 0
    summaries = [e for e in events if e['type'] == 'scene_summarized'][start:end]
    narrations = [e for e in events if e['type'] == 'narration_recorded'][start:end]
    return {
        'version': 1, 'from_index': start, 'through_index': end,
        'previous': None if previous is None else {
            'event_id': previous['id'],
            'source_status': 'bound' if 'source' in previous['deltas'] else 'legacy_unbound',
            'sha256': _hash([previous['deltas']['super_summary'],
                             previous['deltas'].get('source')]),
        },
        'summaries': [
            {'bucket_id': n['id'], 'event_id': s['id'],
             'source_status': 'bound' if 'source' in s['deltas'] else 'legacy_unbound',
             'sha256': _hash([s['deltas']['scene'], s['deltas']['summary'],
                              s['deltas'].get('source')])}
            for n, s in zip(narrations, summaries)
        ],
    }


def _recap(events, end, *, text='Public overview', turn=10):
    return kernel_event('recap_recompressed', day=10, scene='inn', summary='recap',
                        turn=turn, deltas={'super_summary': text,
                                          'summarized_through_index': end,
                                          'source': _expected_ref(events, end)})


def test_recap_records_ordered_summary_chain_and_previous_recursion():
    events = _episodes()
    first = _recap(events, 2)
    events.append(first)
    second = _recap(events, 3, text='Later overview', turn=11)
    ns = _state(events + [second])
    assert ns['super_summary_source'] == second['deltas']['source']
    assert ns['super_summary_event_id'] == second['id']
    assert ns['summarized_through_index'] == 3
    assert second['deltas']['source']['previous']['event_id'] == first['id']
    assert [b['summary_event_id'] for b in ns['scenes']] == [
        e['id'] for e in events if e['type'] == 'scene_summarized']


@pytest.mark.parametrize('corruption', [
    'null', 'version', 'bool_version', 'float_index', 'wrong_start', 'wrong_end',
    'omitted', 'reordered', 'duplicate', 'wrong_summary_id', 'changed_summary',
    'missing_summary', 'retracted_summary', 'private_reference', 'empty_text',
    'out_of_bounds', 'unknown_field',
])
def test_invalid_bound_recap_is_atomic_zero_write(tmp_path, corruption):
    engine = build_engine(tmp_path / 'campaign', embedder=None)
    try:
        events = _episodes()
        recap = _recap(events, 2)
        ref = recap['deltas']['source']
        if corruption == 'null':
            recap['deltas']['source'] = None
        elif corruption == 'version':
            ref['version'] = 2
        elif corruption == 'bool_version':
            ref['version'] = True
        elif corruption == 'float_index':
            ref['through_index'] = 2.0
        elif corruption == 'wrong_start':
            ref['from_index'] = 1
        elif corruption == 'wrong_end':
            recap['deltas']['summarized_through_index'] = 3
        elif corruption == 'omitted':
            ref['summaries'].pop()
        elif corruption == 'reordered':
            ref['summaries'].reverse()
        elif corruption == 'duplicate':
            ref['summaries'][1] = copy.deepcopy(ref['summaries'][0])
        elif corruption == 'wrong_summary_id':
            ref['summaries'][0]['event_id'] = events[0]['id']
        elif corruption == 'changed_summary':
            events[1]['deltas']['summary'] = 'Different summary, unchanged id.'
        elif corruption == 'missing_summary':
            del events[1]
        elif corruption == 'retracted_summary':
            events[1]['retracted'] = True
        elif corruption == 'private_reference':
            ref['summaries'][0]['event_id'] = 'mem_private_not_a_summary'
        elif corruption == 'empty_text':
            recap['deltas']['super_summary'] = '  '
        elif corruption == 'out_of_bounds':
            recap['deltas']['summarized_through_index'] = 50
            ref['through_index'] = 50
        else:
            ref['unexpected'] = 'not part of v1'
        engine.store.append_many(events)
        before = list(engine.store.iter_events(include_retracted=True))
        revision, mirror = engine.store.revision, engine.store.jsonl_path.read_bytes()
        with pytest.raises(ValueError, match='recap source'):
            engine.store.append_many([recap], action_id='bad-recap',
                                     result_metadata={'version': 1})
        assert list(engine.store.iter_events(include_retracted=True)) == before
        assert engine.store.revision == revision
        assert engine.store.jsonl_path.read_bytes() == mirror
        assert engine.store.get_action_receipt('bad-recap') is None
        assert engine.store.get_action_result_metadata('bad-recap') is None
    finally:
        engine.store.close()


@pytest.mark.parametrize('corruption', ['id', 'text', 'source', 'missing', 'no_progress'])
def test_recap_rejects_wrong_previous_link(corruption):
    events = _episodes()
    first = _recap(events, 1)
    events.append(first)
    second = _recap(events, 2, turn=11)
    if corruption == 'id':
        second['deltas']['source']['previous']['event_id'] = 'other_recap'
    elif corruption == 'text':
        first['deltas']['super_summary'] = 'Changed predecessor text'
    elif corruption == 'source':
        # Legacy text with the same ID cannot impersonate a verified link.
        del first['deltas']['source']
    elif corruption == 'missing':
        events.pop()
    else:
        second['deltas']['summarized_through_index'] = 1
        second['deltas']['source'].update(through_index=1, summaries=[])
    with pytest.raises(ValueError, match='recap source'):
        _state(events + [second])


def test_legacy_sources_are_explicitly_unverified_and_replay_unchanged():
    events = _episodes(legacy=True)
    legacy = _recap(events, 1, text='Old overview')
    del legacy['deltas']['source']
    events.append(legacy)
    ns = _state(events)
    assert 'super_summary_source' not in ns
    assert all('summary_source' not in b for b in ns['scenes'])
    bound = _recap(events, 2, turn=11)
    state = _state(events + [bound])
    assert state['super_summary_source'] == bound['deltas']['source']
    assert state['super_summary_source']['previous']['source_status'] == 'legacy_unbound'
    assert state['super_summary_source']['summaries'][0]['source_status'] == 'legacy_unbound'
    # Later legacy replay must not retain the previous bound descriptor.
    later = _recap(events + [bound], 3, text='Another old recap', turn=12)
    del later['deltas']['source']
    assert 'super_summary_source' not in _state(events + [bound, later])


def test_recap_cold_reopen_rewind_and_rewritten_turn_reject_old_sources(tmp_path):
    path = tmp_path / 'campaign'
    engine = build_engine(path, embedder=None)
    try:
        events = _episodes()
        engine.store.append_many(events)
        first = _recap(events, 1)
        engine.store.append(first)
        second = _recap(events + [first], 3, text='New overview', turn=11)
        engine.store.append(second)
        expected = _state(events + [first, second])
        engine.store.close()
        engine = build_engine(path, embedder=None)
        assert engine.world['systems']['narrative'] == expected
        rewind(engine, 11)
        assert engine.world['systems']['narrative'] == _state(events + [first])
        rewind(engine, 3)
        replacement = _episodes(1)
        replacement[0]['turn'] = replacement[1]['turn'] = 3
        engine.store.append_many(replacement)
        # Same text/turn/scene is insufficient: the old event identity is gone.
        with pytest.raises(ValueError, match='recap source'):
            engine.store.append(_recap(events, 3, turn=12))
        engine.store.close()
        engine = build_engine(path, embedder=None)
        assert engine.world['systems']['narrative']['super_summary'] is None
    finally:
        engine.store.close()


def test_legacy_partial_recap_stays_readable_without_fabricated_predecessor():
    from systems.narrative import recap_source_ref
    events = _episodes()
    legacy = _recap(events, 1, text='Old overview')
    del legacy['deltas']['source']
    partial = kernel_event('recap_recompressed', day=10, scene='inn',
                           summary='old partial update', turn=11,
                           deltas={'summarized_through_index': 2})
    ns = _state(events + [legacy, partial])
    assert ns['super_summary'] == 'Old overview'
    assert ns['summarized_through_index'] == 2
    assert 'super_summary_event_id' not in ns
    with pytest.raises(ValueError, match='recap source predecessor'):
        recap_source_ref(ns, 3)


def test_corrupt_durable_recap_fails_closed_on_reopen_without_rewriting(tmp_path):
    engine = build_engine(tmp_path / 'campaign', embedder=None)
    events = _episodes()
    bad = _recap(events, 2)
    bad['deltas']['source']['summaries'][0]['sha256'] = '0' * 64
    # Simulate an externally damaged/older writer, not the standard publish path.
    engine.store.preflight = None
    engine.store.append_many(events + [bad])
    db, mirror = engine.store.db_path, engine.store.jsonl_path
    engine.store.close()
    before = db.read_bytes(), mirror.read_bytes()
    with pytest.raises(ValueError, match='recap source'):
        build_engine(tmp_path / 'campaign', embedder=None)
    assert (db.read_bytes(), mirror.read_bytes()) == before
