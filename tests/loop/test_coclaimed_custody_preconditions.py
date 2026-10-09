"""Offline, exact-quote custody qualifiers must never reorder actual handoffs."""
import copy
import json

import pytest

from loop.semantic_commit import COMPARATOR_POLICY, VERSION, evaluate_extraction
from loop.repair_outcome import CUSTODY_INTERVAL_POLICY


HANDOFF = 'The keeper took the mallet and pad from her tools and handed them to you.'
DROP = 'You set the mallet and pad beside the workshop.'


def _packet(transfers=None, *, initial=None, before=None):
    transfers = transfers or [
        ('mallet', 'keeper', 'player'), ('pad', 'keeper', 'player'),
        ('mallet', 'player', 'workshop'), ('pad', 'player', 'workshop'),
    ]
    holders = dict(initial if initial is not None else {'mallet': 'keeper', 'pad': 'keeper'})

    def state(custody):
        return {'actor_location': 'workshop', 'passages': [],
                'positions': [{'who': who, 'location': 'workshop'}
                              for who in ('keeper', 'player', 'visitor')],
                'held_by': [{'item': item, 'holder': holder} for item, holder in custody.items()]}

    transitions = []
    for item, source, destination in transfers:
        previous = state(holders)
        holders[item] = destination
        transitions.append({'index': len(transitions), 'kind': 'item_transfer',
                            'item': item, 'from': source, 'to': destination,
                            'before_state': previous, 'after_state': state(holders)})
    packet = {'version': VERSION, 'scope': 'primary_turn_before_background_hooks',
            'actor_id': 'player', 'candidate_refs': [], 'continuous_custody': [],
            'entities': [{'id': who, 'type': 'Person'} for who in ('keeper', 'player', 'visitor')]
                + [{'id': 'workshop', 'type': 'Place'}]
                + [{'id': item, 'type': 'Object'} for item in ('mallet', 'pad')],
            # Newly initialized tools need not exist at the primary-turn start.
            'before': state(before or {}), 'after': state(holders),
            'transitions': transitions,
            'narration_spans': [
                {'id': 's0', 'start': 0, 'end': len(HANDOFF), 'text': HANDOFF},
                {'id': 's1', 'start': len(HANDOFF) + 2,
                 'end': len(HANDOFF) + 2 + len(DROP), 'text': DROP}]}
    # This synthetic fixture explicitly models EVERY event. Production packets
    # must obtain these positive ranges from the private event-level preview.
    checkpoints = [packet['before']]
    for row in transitions:
        checkpoints.extend([row['before_state'], row['after_state']])
    checkpoints.append(packet['after'])
    intervals, active = [], {}
    for position, snapshot in enumerate(checkpoints):
        pairs = {(row['item'], row['holder']) for row in snapshot['held_by']}
        active = {pair: row for pair, row in active.items() if pair in pairs}
        for pair in sorted(pairs):
            if pair not in active:
                row = {'item': pair[0], 'holder': pair[1], 'first': position, 'last': position}
                intervals.append(row)
                active[pair] = row
            active[pair]['last'] = position
    packet.update(custody_interval_policy=CUSTODY_INTERVAL_POLICY, custody_intervals=intervals)
    return packet


def _possession(item, index, **updates):
    return {'id': 'held_' + item, 'span_id': 's0', 'quote': HANDOFF,
            'occurrence': 0, 'kind': 'possession', 'scope': 'canonical_transition',
            'binding_reason': 'The identified tool is in its source holder before the handoff.',
            'mode': 'completed', 'moment': 'transition_before', 'transition_index': index,
            'refs': {'item': item, 'holder': 'keeper'}, 'present': True, **updates}


def _transfer(item, source='keeper', destination='player', *, index=None, **updates):
    dropped = destination == 'workshop'
    return {'id': ('drop_' if dropped else 'receive_') + item,
            'span_id': 's1' if dropped else 's0', 'quote': DROP if dropped else HANDOFF,
            'occurrence': 0, 'kind': 'transfer', 'scope': 'canonical_transition',
            'binding_reason': 'The quote asserts the identified physical handoff.',
            'mode': 'completed', 'moment': 'unknown' if index is None else 'transition_after',
            'transition_index': index, 'refs': {'item': item, 'from': source, 'to': destination},
            **updates}


def _claims(*, second_index=1, reverse_preconditions=False, explicit=False):
    preconditions = [_possession('mallet', 0), _possession('pad', second_index)]
    if reverse_preconditions:
        preconditions.reverse()
    return preconditions + [
        _transfer('mallet', index=0 if explicit else None),
        _transfer('pad', index=second_index if explicit else None),
        _transfer('mallet', 'player', 'workshop'),
        _transfer('pad', 'player', 'workshop'),
    ]


def _evaluate(packet, claims):
    raw = {'version': VERSION, 'claims': claims, 'scene_state_support': [],
           'coverage': {'complete': True, 'spans': []}}
    for span in packet['narration_spans']:
        selected = [claim for claim in claims if claim['span_id'] == span['id']]
        raw['coverage']['spans'].append({
            'span_id': span['id'], 'claim_ids': [claim['id'] for claim in selected],
            'status': 'checked', 'no_critical_claims': not any(
                claim['mode'] in {'current', 'completed', 'uncertain'}
                and claim['scope'] == 'canonical_transition' for claim in selected),
            'context_span_ids': [], 'context_complete': True})
    before = copy.deepcopy((packet, raw))
    report = evaluate_extraction(packet, json.dumps(raw))
    assert (packet, raw) == before
    return report, {claim['id']: claim for claim in report['claims']}


@pytest.mark.parametrize('reverse_preconditions', [False, True])
@pytest.mark.parametrize('explicit', [False, True])
def test_compound_source_custody_does_not_consume_other_tools_handoff(
        reverse_preconditions, explicit):
    packet = _packet()
    report, claims = _evaluate(packet, _claims(
        reverse_preconditions=reverse_preconditions, explicit=explicit))
    assert report['passed'], report['issues']
    assert report['comparator_policy'] == COMPARATOR_POLICY
    assert packet['before']['held_by'] == []
    for item, index, anchor, count in [('mallet', 0, 1, 1), ('pad', 1, 3, 3)]:
        claim = claims['held_' + item]
        assert claim['co_claimed_transfer_precondition'] == {
            'transfer_claim_id': 'receive_' + item, 'transition_index': index,
            'anchor_position': anchor, 'continuous_from_position': 1,
            'checked_checkpoints': count}
        assert claim['matched_narrative_position'] == claim['selected_narrative_position'] == anchor
        assert claim['narrative_cursor_before'] == claim['narrative_cursor_after'] == 0
        assert 'does not advance' in claim['reason']
    assert [claims[name]['matched_transition_index'] for name in (
        'receive_mallet', 'receive_pad', 'drop_mallet', 'drop_pad')] == [0, 1, 2, 3]
    assert [claims[name]['narrative_cursor_after'] for name in (
        'receive_mallet', 'receive_pad', 'drop_mallet', 'drop_pad')] == [2, 4, 6, 8]


def test_original_grouped_drop_before_receipt_still_fails():
    packet = _packet([
        ('mallet', 'keeper', 'player'), ('mallet', 'player', 'workshop'),
        ('pad', 'keeper', 'player'), ('pad', 'player', 'workshop')])
    # Reproduce the captured grouped declarations: pad is only initialized
    # after mallet has already been handed over and put down.
    for transition in packet['transitions'][:2]:
        for side in ('before_state', 'after_state'):
            transition[side]['held_by'] = [row for row in transition[side]['held_by']
                                         if row['item'] != 'pad']
    report, claims = _evaluate(packet, _claims(second_index=2))
    assert not report['passed']
    assert claims['held_pad']['co_claimed_transfer_precondition'] is None
    assert claims['receive_mallet']['verdict'] == 'contradiction'
    assert claims['drop_mallet']['verdict'] == 'contradiction'


def test_qualification_does_not_hide_real_drop_before_second_receipt():
    # Even with both tools initially present, the true transfer order remains
    # wrong. Qualifying both preconditions must not make all same-quote actions
    # simultaneous or allow the later narrated mallet drop to rewind.
    packet = _packet([
        ('mallet', 'keeper', 'player'), ('mallet', 'player', 'workshop'),
        ('pad', 'keeper', 'player'), ('pad', 'player', 'workshop')])
    report, claims = _evaluate(packet, _claims(second_index=2))
    assert not report['passed']
    assert claims['held_pad']['co_claimed_transfer_precondition'] is not None
    assert claims['receive_mallet']['verdict'] == 'supported'
    assert claims['drop_mallet']['verdict'] == 'contradiction'


@pytest.mark.parametrize('missing', [False, True])
def test_custody_must_hold_through_every_canonical_checkpoint(missing):
    packet = _packet([
        ('mallet', 'keeper', 'player'), ('pad', 'visitor', 'keeper'),
        ('pad', 'keeper', 'player'), ('mallet', 'player', 'workshop'),
        ('pad', 'player', 'workshop')],
        initial={'mallet': 'keeper', 'pad': 'visitor'})
    if missing:
        for transition in packet['transitions'][:2]:
            for side in ('before_state', 'after_state'):
                if transition['index'] == 1 and side == 'after_state':
                    continue
                transition[side]['held_by'] = [row for row in transition[side]['held_by']
                                             if row['item'] != 'pad']
    report, claims = _evaluate(packet, _claims(second_index=2))
    assert not report['passed']
    assert claims['held_pad']['verdict'] == 'supported'  # True only at its later anchor.
    assert claims['held_pad']['co_claimed_transfer_precondition'] is None
    assert claims['held_pad']['narrative_cursor_after'] == 5
    assert claims['receive_mallet']['verdict'] == 'contradiction'


def test_equal_endpoint_custody_does_not_hide_intervening_transfer():
    packet = _packet([
        ('mallet', 'keeper', 'player'), ('pad', 'keeper', 'visitor'),
        ('pad', 'visitor', 'keeper'), ('pad', 'keeper', 'player'),
        ('mallet', 'player', 'workshop'), ('pad', 'player', 'workshop')])
    report, claims = _evaluate(packet, _claims(second_index=3))
    assert not report['passed']
    assert claims['held_pad']['co_claimed_transfer_precondition'] is None
    assert claims['receive_mallet']['verdict'] == 'contradiction'


@pytest.mark.parametrize('explicit', [False, True])
def test_repeated_identical_transition_requires_explicit_companion_binding(explicit):
    packet = _packet([
        ('pad', 'keeper', 'player'), ('pad', 'player', 'keeper'),
        ('pad', 'keeper', 'player')])
    report, claims = _evaluate(packet, [
        _possession('pad', 2), _transfer('pad', index=2 if explicit else None)])
    assert report['passed']
    # Unindexed repeated handoffs retain ordinary chronology, even though the
    # normal action comparator can find the later handoff after that cursor.
    assert bool(claims['held_pad']['co_claimed_transfer_precondition']) is explicit
    assert claims['held_pad']['narrative_cursor_after'] == (0 if explicit else 5)


@pytest.mark.parametrize('difference', ['quote', 'span_id', 'occurrence'])
def test_companion_requires_identical_source_evidence(difference):
    packet, claims = _packet(), _claims()
    companion = claims[3]
    if difference == 'quote':
        companion['quote'] = HANDOFF[:-1]  # Same offset, distinct quotation.
    elif difference == 'span_id':
        second = packet['narration_spans'][1]
        second['text'] = HANDOFF + ' ' + DROP
        second['end'] = second['start'] + len(second['text'])
        companion['span_id'] = 's1'
    else:
        first = packet['narration_spans'][0]
        first['text'] = HANDOFF + ' ' + HANDOFF
        first['end'] = len(first['text'])
        packet['narration_spans'][1]['start'] = first['end'] + 2
        packet['narration_spans'][1]['end'] = first['end'] + 2 + len(DROP)
        companion['occurrence'] = 1
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None
    assert assessed['receive_mallet']['verdict'] == 'contradiction'


@pytest.mark.parametrize('mode', ['current', 'historical', 'reported', 'conditional', 'future', 'uncertain'])
@pytest.mark.parametrize('target', ['possession', 'transfer'])
def test_only_completed_companions_and_completed_possession_qualify(mode, target):
    packet, claims = _packet(), _claims()
    claims[1 if target == 'possession' else 3]['mode'] = mode
    _, assessed = _evaluate(packet, claims)
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None
    if target == 'transfer':
        assert assessed['receive_mallet']['verdict'] == 'contradiction'


def test_negative_possession_is_not_a_handoff_precondition():
    packet, claims = _packet(), _claims()
    claims[1]['present'] = False
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['held_pad']['verdict'] == 'contradiction'
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None


@pytest.mark.parametrize('holder', ['visitor', None])
def test_wrong_or_unbound_holder_cannot_qualify(holder):
    packet, claims = _packet(), _claims()
    claims[1]['refs']['holder'] = holder
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['held_pad']['verdict'] == ('unsupported' if holder is None else 'contradiction')
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None


@pytest.mark.parametrize('field,value', [('item', 'mallet'), ('from', 'visitor'),
                                        ('to', 'visitor'), ('from', None), ('to', None)])
def test_companion_requires_complete_exact_item_and_endpoints(field, value):
    packet, claims = _packet(), _claims()
    claims[3]['refs'][field] = value
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None
    assert assessed['receive_mallet']['verdict'] == 'contradiction'


@pytest.mark.parametrize('moment,index', [('unknown', None), ('throughout', None),
                                        ('before', None), ('after', None), ('transition_after', 1)])
def test_other_custody_moments_keep_existing_rules(moment, index):
    packet, claims = _packet(), _claims()
    claims[1].update(moment=moment, transition_index=index)
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None
    assert assessed['held_pad']['verdict'] in {'unsupported', 'contradiction'}


def test_same_quote_transfer_order_is_not_relaxed():
    packet, claims = _packet(), _claims()
    claims[2:4] = reversed(claims[2:4])
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['receive_pad']['verdict'] == 'supported'
    assert assessed['receive_mallet']['verdict'] == 'contradiction'


def test_earlier_narration_cursor_cannot_be_rewound_by_same_quote_precondition():
    packet, claims = _packet(), _claims()
    # A prior quote already establishes an actual later action. Neither the
    # custody qualifier nor its companion may rewind across that narration.
    prefix = 'You already set the mallet down.'
    for span in packet['narration_spans']:
        span['start'] += len(prefix) + 2
        span['end'] += len(prefix) + 2
    packet['narration_spans'].insert(0, {'id': 'prior', 'start': 0,
                                        'end': len(prefix), 'text': prefix})
    claims.insert(0, _transfer('mallet', 'player', 'workshop', id='earlier_drop',
                              span_id='prior', quote=prefix))
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['earlier_drop']['verdict'] == 'supported'
    for name in ('held_mallet', 'held_pad', 'receive_mallet', 'receive_pad'):
        assert assessed[name]['verdict'] == 'contradiction'
        assert assessed[name]['co_claimed_transfer_precondition'] is None


def test_incoming_cursor_limits_qualified_custody_interval():
    packet = _packet()
    prior = 'You received the mallet.'
    for span in packet['narration_spans']:
        span['start'] += len(prior) + 2
        span['end'] += len(prior) + 2
    packet['narration_spans'].insert(0, {'id': 'prior', 'start': 0,
                                        'end': len(prior), 'text': prior})
    report, claims = _evaluate(packet, [
        _transfer('mallet', id='earlier_receive', span_id='prior', quote=prior),
        _possession('pad', 1), _transfer('mallet'), _transfer('pad')])
    assert report['passed']
    claim = claims['held_pad']
    assert claim['narrative_cursor_before'] == claim['narrative_cursor_after'] == 2
    assert claim['co_claimed_transfer_precondition']['continuous_from_position'] == 2
    assert claim['co_claimed_transfer_precondition']['checked_checkpoints'] == 2


@pytest.mark.parametrize('missing', ['policy', 'intervals', 'wrong_policy', 'empty_intervals'])
def test_public_snapshots_without_event_level_certificate_fail_closed(missing):
    packet = _packet()
    if missing == 'policy':
        packet.pop('custody_interval_policy')
    elif missing == 'intervals':
        packet.pop('custody_intervals')
    elif missing == 'wrong_policy':
        packet['custody_interval_policy'] = 'untrusted-old-policy'
    else:
        packet['custody_intervals'] = []
    report, assessed = _evaluate(packet, _claims())
    assert not report['passed']
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None
    assert assessed['receive_mallet']['verdict'] == 'contradiction'
