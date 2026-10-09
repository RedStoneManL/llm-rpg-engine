"""Sharing instructions must leave prose and every safe source record intact."""
import copy
import json

import pytest

from app.engine import build_engine
from context.assembler import assemble_context
from context.narrative_evidence import (
    _NARRATIVE_EVIDENCE_POLICY as POLICY,
    format_narrative_evidence,
    read_narrative_evidence,
)

HEADER = '【历史叙述来源 / historical authored narration record】\n'


def packet_json(text):
    return json.loads(text.splitlines()[-1])


def rich_packet():
    return {'version': 1, 'category': 'historical_narration',
        'groups': [{'bucket_id': 'bucket-a', 'scene': 'old-scene',
            'source_turn_range': [2, 9], 'source_day_range': [1, 3],
            'coverage': {'source_count': 3, 'unknown_source_count': 1,
                         'omitted_relation_count': 2, 'partial': True, 'truncated': True},
            'sources': [{'raw_index': 0, 'narration_ref': 'narration-a',
                'turn': 2, 'day': 1, 'scene': 'old-scene', 'actor_binding': 'bound',
                'actor_id': 'PRIVATE_ACTOR', 'private_note': 'SECRET_NOTE',
                'historical_relations': [{'relation': 'held_by', 'visibility': 'public',
                    'basis': 'canonical_at_narration_recorded', 'visibility_basis': 'explicit_public',
                    'subject': {'id': 'tool', 'type': 'Object', 'labels': ['木槌']},
                    'holder': {'id': 'person', 'type': 'Person', 'labels': ['修补匠'],
                        'label_sources': [{'label': '修补匠', 'category': 'published_display_binding'}]}}]}]}],
        'coverage': {'source_count': 3, 'unknown_source_count': 1, 'partial': True, 'truncated': True},
        'summary_created': {'id': 'summary-later', 'turn': 20, 'day': 7}}


def test_default_and_shared_render_preserve_exact_safe_packet_without_mutation():
    packet = rich_packet()
    before = copy.deepcopy(packet)
    full = format_narrative_evidence(packet)
    shared = format_narrative_evidence(packet, include_policy=False)
    assert full == HEADER + POLICY + shared[len(HEADER):]
    assert packet_json(full) == packet_json(shared)
    safe = packet_json(shared)
    assert safe['source_turn_range'] == [2, 9]
    assert safe['summary_created'] == {'id': 'summary-later', 'turn': 20, 'day': 7}
    assert safe['coverage']['partial'] and safe['coverage']['truncated']
    source = safe['groups'][0]['sources'][0]
    assert source['actor_binding'] == 'bound'
    assert source['historical_relations'][0]['holder']['label_sources'][0]['category'] == 'published_display_binding'
    assert 'PRIVATE_ACTOR' not in full and 'SECRET_NOTE' not in full
    assert packet == before


@pytest.mark.parametrize('mutation', ['unknown_actor', 'private_relation', 'invalid_endpoint'])
def test_policy_sharing_never_reintroduces_disallowed_history(mutation):
    packet = rich_packet()
    source = packet['groups'][0]['sources'][0]
    if mutation == 'unknown_actor':
        source['actor_binding'] = 'unknown'
    elif mutation == 'private_relation':
        source['historical_relations'][0]['visibility'] = 'private'
    else:
        source['historical_relations'][0]['subject']['type'] = 'Person'
    for include in (True, False):
        safe = packet_json(format_narrative_evidence(packet, include_policy=include))
        assert 'historical_relations' not in safe['groups'][0]['sources'][0]
        assert safe['coverage']['partial'] is True


@pytest.mark.parametrize('value', [None, {}, {'summary': '旧摘要'}])
def test_legacy_unknown_evidence_does_not_gain_identity(value):
    packet = read_narrative_evidence(value, summary=True)
    safe = packet_json(format_narrative_evidence(packet, include_policy=False))
    assert safe['coverage']['partial'] is True
    assert safe['coverage']['returned_bound_source_count'] == 0
    assert safe['summary_created'] == {'id': None, 'turn': None, 'day': None}


@pytest.mark.parametrize('super_summary', [None, '更早的摘要'])
@pytest.mark.parametrize('empty_first_raw', [False, True])
def test_assembled_groups_share_only_policy_and_preserve_every_other_character(
        tmp_path, monkeypatch, super_summary, empty_first_raw):
    engine = build_engine(tmp_path / 'world')
    ns = engine.world['systems']['narrative']
    ns['super_summary'] = super_summary
    ns['scenes'] = [
        {'scene': 'old-a', 'raw': ['过去甲'], 'summary': '摘要甲'},
        {'scene': 'old-b', 'raw': ['过去乙'], 'summary': '摘要乙'},
        {'scene': 'recent-a', 'raw': [] if empty_first_raw else ['原文甲']},
        {'scene': 'recent-b', 'raw': ['原文乙']},
    ]
    scene = {'day': 1, 'location': 'room'}
    compact = assemble_context(engine.registry, engine.world, scene)
    import context.narrative_evidence as evidence
    real = evidence.format_narrative_evidence
    monkeypatch.setattr(evidence, 'format_narrative_evidence',
                        lambda packet, **kwargs: real(packet))
    repeated = assemble_context(engine.registry, engine.world, scene)
    assert compact.count(POLICY) == 2  # stable and scene remain self-contained
    assert compact.replace(POLICY, '') == repeated.replace(POLICY, '')
    assert len(repeated) - len(compact) == (repeated.count(POLICY) - 2) * len(POLICY)
    assert compact.count(HEADER) == repeated.count(HEADER)
    assert '原文乙' in compact and '摘要甲' in compact and '摘要乙' in compact
    engine.store.close()


def test_standalone_recent_fragment_includes_policy_when_first_bucket_is_empty(tmp_path):
    engine = build_engine(tmp_path / 'world')
    ns = engine.world['systems']['narrative']
    ns['scenes'] = [{'scene': 'empty', 'raw': []}, {'scene': 'recent', 'raw': ['可见原文']}]
    from systems.narrative import NarrativeSystem
    fragment = NarrativeSystem().inject({'day': 1}, engine.world)
    assert fragment.text.count(POLICY) == 1
    assert '可见原文' in fragment.text
    engine.store.close()
