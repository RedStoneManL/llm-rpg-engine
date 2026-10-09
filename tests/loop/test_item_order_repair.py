import copy
import json

import pytest

from kernel.turncommit import TurnCommit
from loop.item_order_repair import ordering_offer, apply_order, same_endpoint


def fixture():
    items = []
    for item in ('tool_a', 'tool_b'):
        items += [{'op': 'create', 'id': item},
                  {'op': 'transfer', 'item': item, 'to': 'helper'},
                  {'op': 'transfer', 'item': item, 'from': 'helper', 'to': 'actor'},
                  {'op': 'transfer', 'item': item, 'from': 'actor', 'to': 'room'}]
    commit = TurnCommit('The helper hands over both tools.', {'items': items})
    packet = {'actor_id': 'actor', 'entities': [
        {'id': 'helper', 'type': 'Person'}, {'id': 'actor', 'type': 'Person'},
        {'id': 'room', 'type': 'Place'}], 'candidate_refs': [
        {'id': 'tool_a', 'type': 'Object', 'source': {'section': 'items', 'index': 0}},
        {'id': 'tool_b', 'type': 'Object', 'source': {'section': 'items', 'index': 4}}],
        'incidental_policy': {'resource_action': 'none'},
        'scene_fact_context': {'before': {'place': 'room', 'day': 1, 'band': 0},
                               'after': {'place': 'room', 'day': 1, 'band': 0}},
        'before': {}, 'after': {'held_by': []}, 'continuous_custody': [],
        'scene_fact_sources': [],
        'transitions': [{'kind': 'item_transfer', 'item': row['item'],
                         'from': row['from'], 'to': row['to'],
                         'before_state': {'held_by': [{'item': row['item'], 'holder': row['from']}]}}
                        for row in items if row.get('from') is not None]}
    claims = [{'kind': 'transfer', 'scope': 'canonical_transition', 'mode': 'completed',
               'span_id': 's0', 'quote': 'both tools', 'occurrence': 0,
               'refs': {'item': item, 'from': 'helper', 'to': 'actor'}}
              for item in ('tool_a', 'tool_b')]
    report = {'issues': [{'claim': claims[0], 'verdict': 'contradiction'}], 'claims': claims}
    return commit, packet, report


def offer_for(commit, packet, report, scene=None, world=None):
    return ordering_offer(packet, report, commit, scene or {}, world or {})


def test_conserves_exact_rows_and_each_chain_without_mutating_original():
    commit, packet, report = fixture()
    before = copy.deepcopy(commit.sections)
    order = [0, 1, 4, 5, 2, 6, 3, 7]
    offer = offer_for(commit, packet, report)
    result = apply_order(commit.sections['items'], order, offer)
    assert result == [before['items'][i] for i in order]
    assert json.dumps(result, ensure_ascii=False) == json.dumps([before['items'][i] for i in order], ensure_ascii=False)
    assert commit.sections == before
    assert len(result) == len(before['items'])
    assert result[0] is not before['items'][0]


@pytest.mark.parametrize('order', [[], list(range(7)), [0, 1, 2, 3, 4, 5, 6, 6],
    [0, 1, 2, 3, 4, 5, 6, 8], [False, 1, 2, 3, 4, 5, 6, 7],
    [0, 2, 1, 3, 4, 5, 6, 7], [0, 1, 2, 3, 4, 5, 6, -1]])
def test_bad_permutations_and_per_item_inversions_reject(order):
    commit, packet, report = fixture()
    with pytest.raises(ValueError):
        apply_order(commit.sections['items'], order, offer_for(commit, packet, report))


def test_opaque_rows_stay_private_and_fixed():
    commit, packet, report = fixture()
    commit.sections['items'].append({'op': 'create', 'id': 'PRIVATE_ID', 'attrs': {'secret': 'PRIVATE_VALUE'}})
    offer = offer_for(commit, packet, report)
    assert offer['rows'][-1] == {'index': 8, 'movable': False}
    assert 'PRIVATE' not in json.dumps(offer)
    with pytest.raises(ValueError):
        apply_order(commit.sections['items'], [8, 0, 1, 2, 3, 4, 5, 6, 7], offer)


@pytest.mark.parametrize('change', ['materialize', 'resource', 'promise', 'motion', 'time', 'opening', 'attrs', 'other_quote', 'open_return'])
def test_conservative_scope_exclusions(change):
    commit, packet, report = fixture()
    scene, world = {}, {}
    if change == 'materialize': commit.sections['items'][0]['op'] = 'materialize'
    if change == 'resource': packet['incidental_policy']['resource_action'] = 'spend'
    if change == 'promise': scene['_semantic_return_commitment'] = True
    if change == 'motion': commit.sections['moves'] = [{'who': 'actor', 'to': 'elsewhere'}]
    if change == 'time': packet['scene_fact_context']['after']['band'] = 1
    if change == 'opening': commit._semantic_context = {'policy': 'opening_text_only'}
    if change == 'attrs': commit.sections['items'][0]['attrs'] = {'quantity': 2}
    if change == 'other_quote': report['claims'][1]['quote'] = 'a different action'
    if change == 'open_return': world = {'systems': {'return_commitments': {'records': {'r': {'status': 'open', 'item': 'tool_b'}}}}}
    assert offer_for(commit, packet, report, scene, world) is None


def test_no_offer_cannot_be_bypassed_by_identity_permutation():
    commit, packet, report = fixture()
    with pytest.raises(ValueError): apply_order(commit.sections['items'], list(range(8)), None)


def test_endpoint_and_custody_changes_are_not_equivalent():
    _, packet, _ = fixture()
    other = copy.deepcopy(packet)
    assert same_endpoint(packet, other)
    other['after']['held_by'].append({'item': 'tool_a', 'holder': 'actor'})
    assert not same_endpoint(packet, other)
    other = copy.deepcopy(packet)
    other['continuous_custody'] = [{'item': 'tool_a', 'holder': 'helper'}]
    assert not same_endpoint(packet, other)


def test_candidate_sources_follow_the_exact_permutation_without_erasing_provenance():
    _, packet, _ = fixture()
    packet['candidate_refs'] = [
        {'id': 'tool_a', 'type': 'Object', 'source': {'section': 'items', 'index': 0}},
        {'id': 'tool_b', 'type': 'Object', 'source': {'section': 'items', 'index': 4}},
    ]
    order = [4, 5, 0, 1, 2, 6, 3, 7]
    other = copy.deepcopy(packet)
    other['candidate_refs'][0]['source']['index'] = 2
    other['candidate_refs'][1]['source']['index'] = 0
    other['candidate_refs'].reverse()
    assert same_endpoint(packet, other, order)
    assert not same_endpoint(packet, other)
    other['candidate_refs'][0]['source']['index'] = 1
    assert not same_endpoint(packet, other, order)
    other['candidate_refs'][0]['source']['index'] = 0
    other['candidate_refs'][0]['source']['section'] = 'facts'
    assert not same_endpoint(packet, other, order)
    assert not same_endpoint(packet, packet, [False, 1])


@pytest.mark.parametrize('missing', ['transitions', 'handoff', 'wrong_source', 'initial_custody'])
def test_known_holder_identity_cannot_authorize_unproven_custody(missing):
    commit, packet, report = fixture()
    if missing == 'transitions':
        packet.pop('transitions')
    elif missing == 'handoff':
        packet['transitions'].pop(0)
    elif missing == 'wrong_source':
        packet['transitions'][0]['from'] = 'room'
    else:
        packet['transitions'][0].pop('before_state')
    assert offer_for(commit, packet, report) is None


@pytest.mark.parametrize('change', ['missing_create', 'existing_object', 'wrong_creation_index',
                                    'wrong_creation_section', 'second_initialization'])
def test_source_less_rows_need_exact_new_object_initialization(change):
    commit, packet, report = fixture()
    if change == 'missing_create':
        commit.sections['items'][0] = {'op': 'create', 'id': 'another_item'}
    elif change == 'existing_object':
        packet['entities'].append({'id': 'tool_a', 'type': 'Object'})
        packet['candidate_refs'] = packet['candidate_refs'][1:]
    elif change == 'wrong_creation_index':
        packet['candidate_refs'][0]['source']['index'] = 2
    elif change == 'wrong_creation_section':
        packet['candidate_refs'][0]['source']['section'] = 'entities'
    else:
        commit.sections['items'][2].pop('from')
    assert offer_for(commit, packet, report) is None


def test_repeated_visible_handoffs_require_matching_evidence_multiplicity():
    commit, packet, report = fixture()
    # A valid extra keeper-to-actor cycle before the final tool_a placement.
    commit.sections['items'][3:3] = [
        {'op': 'transfer', 'item': 'tool_a', 'from': 'actor', 'to': 'helper'},
        {'op': 'transfer', 'item': 'tool_a', 'from': 'helper', 'to': 'actor'}]
    packet['candidate_refs'][1]['source']['index'] = 6
    packet['transitions'].append({'kind': 'item_transfer', 'item': 'tool_a',
                                  'from': 'actor', 'to': 'helper'})
    assert offer_for(commit, packet, report) is None
    packet['transitions'].append(copy.deepcopy(packet['transitions'][0]))
    assert offer_for(commit, packet, report) is not None
