"""Unknown-time tool use needs a fully certified, narratively bounded interval.

These are offline host-comparator tests. Exact receipt/release evidence never
licenses choosing a convenient snapshot, rewinding, or relaxing either transfer.
"""
import copy
import json

import pytest

from kernel.turncommit import TurnCommit
from loop.item_order_repair import apply_order, ordering_offer, same_endpoint
from tests.loop.test_coclaimed_custody_preconditions import (
    DROP, HANDOFF, _claims, _evaluate, _packet, _possession, _transfer,
)
from tests.loop.test_event_custody_intervals import (
    _authorize_no_resources, _packet as _real_packet, _real_fixture,
)


USE = 'You worked with the mallet and pad in your hands.'


def _narration(packet, spans=None):
    """Give every test exact, nonoverlapping source offsets by default."""
    spans = spans or [('s0', HANDOFF), ('use', USE), ('s1', DROP)]
    offset, result = 0, []
    for sid, text in spans:
        result.append({'id': sid, 'start': offset, 'end': offset + len(text), 'text': text})
        offset += len(text) + 2
    packet['narration_spans'] = result
    return packet


def _usage(item='mallet', **updates):
    return _possession(
        item, None, id='use_' + item, span_id='use', quote=USE,
        moment='unknown', refs={'item': item, 'holder': 'player'},
        binding_reason='Using this tool in hand asserts completed physical custody.',
        **updates)


def _usage_claims(*, source_preconditions=False, second_index=1):
    claims = (_claims(second_index=second_index) if source_preconditions else [
        _transfer('mallet'), _transfer('pad'),
        _transfer('mallet', 'player', 'workshop'),
        _transfer('pad', 'player', 'workshop'),
    ])
    return claims[:-2] + [_usage('mallet'), _usage('pad')] + claims[-2:]


def _assert_unqualified(assessed, name='use_mallet'):
    claim = assessed[name]
    assert claim['bracketed_custody_interval'] is None
    assert claim['verdict'] in {'unsupported', 'contradiction'}
    return claim


@pytest.mark.parametrize('source_preconditions', [False, True])
def test_completed_use_between_take_and_drop_preserves_cursor(source_preconditions):
    packet = _narration(_packet())
    report, assessed = _evaluate(packet, _usage_claims(
        source_preconditions=source_preconditions))
    assert report['passed'], report['issues']
    for item, first, last in [('mallet', 2, 5), ('pad', 4, 7)]:
        claim = assessed['use_' + item]
        assert claim['verdict'] == 'supported'
        assert claim['bracketed_custody_interval'] == {
            'receipt_claim_id': 'receive_' + item,
            'release_claim_id': 'drop_' + item,
            'first_position': first, 'last_position': last,
            'checked_checkpoints': last - first + 1,
        }
        assert claim['selected_narrative_position'] is None
        assert claim['matched_transition_index'] is None
        assert claim['narrative_cursor_before'] == claim['narrative_cursor_after'] == 4
        assert claim['co_claimed_transfer_precondition'] is None
    for name, index in [('receive_mallet', 0), ('receive_pad', 1),
                        ('drop_mallet', 2), ('drop_pad', 3)]:
        assert assessed[name]['matched_transition_index'] == index
        assert assessed[name]['narrative_cursor_after'] == 2 * index + 2
        assert assessed[name]['bracketed_custody_interval'] is None


@pytest.mark.parametrize('missing', ['receipt', 'release', 'both'])
def test_both_narrative_boundaries_are_required(missing):
    packet, claims = _narration(_packet()), _usage_claims()
    omitted = {'receipt': {'receive_mallet'}, 'release': {'drop_mallet'},
               'both': {'receive_mallet', 'drop_mallet'}}[missing]
    report, assessed = _evaluate(packet, [row for row in claims if row['id'] not in omitted])
    assert not report['passed']
    _assert_unqualified(assessed)


@pytest.mark.parametrize('holder', ['keeper', 'visitor', None])
def test_wrong_or_unbound_holder_cannot_borrow_another_holders_interval(holder):
    packet, claims = _narration(_packet()), _usage_claims()
    next(row for row in claims if row['id'] == 'use_mallet')['refs']['holder'] = holder
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    _assert_unqualified(assessed)


@pytest.mark.parametrize('boundary', ['receive_mallet', 'drop_mallet'])
@pytest.mark.parametrize('field,value', [('item', 'pad'), ('from', 'visitor'),
                                       ('to', 'visitor'), ('from', None), ('to', None)])
def test_boundaries_require_the_same_item_and_fully_bound_endpoints(boundary, field, value):
    packet, claims = _narration(_packet()), _usage_claims()
    # Remove the other item's boundary claims so they cannot replace a mutated
    # boundary. The item's public snapshots alone must not supply the evidence.
    claims = [row for row in claims if row['id'] not in {'receive_pad', 'drop_pad', 'use_pad'}]
    next(row for row in claims if row['id'] == boundary)['refs'][field] = value
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    _assert_unqualified(assessed)


@pytest.mark.parametrize('missing', ['policy', 'intervals', 'wrong_policy', 'empty_intervals'])
def test_public_snapshots_cannot_replace_the_event_level_certificate(missing):
    packet = _narration(_packet())
    if missing == 'policy':
        packet.pop('custody_interval_policy')
    elif missing == 'intervals':
        packet.pop('custody_intervals')
    elif missing == 'wrong_policy':
        packet['custody_interval_policy'] = 'untrusted-custody-policy'
    else:
        packet['custody_intervals'] = []
    report, assessed = _evaluate(packet, _usage_claims())
    assert not report['passed']
    _assert_unqualified(assessed)


@pytest.mark.parametrize('change', ['late_start', 'early_end', 'split', 'wrong_holder',
                                   'boolean_bound', 'negative_bound', 'beyond_packet'])
def test_certificate_must_cover_the_entire_receipt_to_release_interval(change):
    packet = _narration(_packet())
    certificate = next(row for row in packet['custody_intervals']
                       if row['item'] == 'mallet' and row['holder'] == 'player')
    assert (certificate['first'], certificate['last']) == (2, 5)
    if change == 'late_start':
        certificate['first'] = 3
    elif change == 'early_end':
        certificate['last'] = 4
    elif change == 'split':
        certificate['last'] = 3
        packet['custody_intervals'].append({**certificate, 'first': 4, 'last': 5})
    elif change == 'wrong_holder':
        certificate['holder'] = 'keeper'
    elif change == 'boolean_bound':
        certificate['first'] = False
    elif change == 'negative_bound':
        certificate['first'] = -1
    else:
        certificate['last'] = 2 * len(packet['transitions']) + 2
    report, assessed = _evaluate(packet, _usage_claims())
    assert not report['passed']
    _assert_unqualified(assessed)


@pytest.mark.parametrize('position', [2, 3, 4, 5])
@pytest.mark.parametrize('change', ['missing', 'wrong_holder', 'ambiguous'])
def test_every_public_checkpoint_must_agree_even_with_a_certificate(change, position):
    packet = _narration(_packet())
    # Mallet's full [2, 5] interval includes both ends and positions before and
    # after the incoming cursor (4). Checking just a favorable suffix is bad.
    index = (position - 1) // 2
    side = 'before_state' if position % 2 else 'after_state'
    snapshot = packet['transitions'][index][side]
    held = next(row for row in snapshot['held_by'] if row['item'] == 'mallet')
    if change == 'missing':
        snapshot['held_by'].remove(held)
    elif change == 'wrong_holder':
        held['holder'] = 'visitor'
    else:
        snapshot['held_by'].append({'item': 'mallet', 'holder': 'visitor'})
    report, assessed = _evaluate(packet, _usage_claims())
    assert not report['passed']
    _assert_unqualified(assessed)


@pytest.mark.parametrize('excursion', ['remote', 'hidden_relation'])
def test_hidden_third_party_break_is_not_erased_by_identical_public_snapshots(excursion):
    registry, world, scene, original = _real_fixture(excursion)
    base_rows = [original.sections['items'][0], *original.sections['items'][-3:]]
    hidden_out = {'op': 'transfer', 'item': 'mallet', 'from': 'player', 'to': 'visitor'}
    if excursion == 'hidden_relation':
        hidden_out['visibility'] = 'hidden'
    rows = [base_rows[0], hidden_out,
            {'op': 'transfer', 'item': 'mallet', 'from': 'visitor', 'to': 'player'},
            *base_rows[1:]]
    commit = TurnCommit('\n\n'.join((HANDOFF, USE, DROP)), {'items': rows})
    packet = _narration(_real_packet((registry, world, scene, commit)))
    assert [(row['item'], row['from'], row['to']) for row in packet['transitions']] == [
        ('mallet', 'keeper', 'player'), ('pad', 'keeper', 'player'),
        ('mallet', 'player', 'workshop'), ('pad', 'player', 'workshop')]
    for state in [packet['transitions'][0]['after_state'],
                  packet['transitions'][1]['before_state'],
                  packet['transitions'][1]['after_state'],
                  packet['transitions'][2]['before_state']]:
        assert {'item': 'mallet', 'holder': 'player'} in state['held_by']
    report, assessed = _evaluate(packet, _usage_claims())
    assert not report['passed']
    _assert_unqualified(assessed)
    assert not any(row['holder'] == 'visitor' for row in packet['custody_intervals'])
    assert '_custody_run' not in json.dumps(packet)
    if excursion == 'remote':
        assert 'visitor' not in json.dumps(packet)


@pytest.mark.parametrize('boundary', ['receipt', 'release'])
@pytest.mark.parametrize('explicit', [False, True])
def test_repeated_boundary_transitions_need_exact_explicit_binding(boundary, explicit):
    if boundary == 'receipt':
        transfers = [('mallet', 'keeper', 'player'), ('mallet', 'player', 'keeper'),
                     ('mallet', 'keeper', 'player'), ('mallet', 'player', 'workshop')]
        receive = _transfer('mallet', index=2 if explicit else None)
        release = _transfer('mallet', 'player', 'workshop')
    else:
        transfers = [('mallet', 'keeper', 'player'), ('mallet', 'player', 'workshop'),
                     ('mallet', 'workshop', 'player'), ('mallet', 'player', 'workshop')]
        receive = _transfer('mallet')
        release = _transfer('mallet', 'player', 'workshop', index=1 if explicit else None)
    packet = _narration(_packet(transfers))
    report, assessed = _evaluate(packet, [receive, _usage(), release])
    assert report['passed'] is explicit
    assert bool(assessed['use_mallet']['bracketed_custody_interval']) is explicit
    if not explicit:
        _assert_unqualified(assessed)


@pytest.mark.parametrize('overlap', ['same_receipt_quote', 'same_release_quote',
                                    'receipt_overlaps_use', 'release_overlaps_use'])
def test_same_quote_or_overlapping_evidence_cannot_bound_use(overlap):
    text = ' '.join((HANDOFF, USE, DROP))
    packet = _narration(_packet(), [('all', text)])
    claims = [_transfer('mallet'), _usage(), _transfer('mallet', 'player', 'workshop')]
    for claim in claims:
        claim['span_id'] = 'all'
    if overlap == 'same_receipt_quote':
        claims[1]['quote'] = HANDOFF
    elif overlap == 'same_release_quote':
        claims[1]['quote'] = DROP
    elif overlap == 'receipt_overlaps_use':
        claims[0]['quote'] = HANDOFF + ' ' + USE
    else:
        claims[2]['quote'] = USE + ' ' + DROP
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    _assert_unqualified(assessed)


def test_disjoint_exact_quotes_within_one_span_can_bound_use():
    packet = _narration(_packet(), [('all', ' '.join((HANDOFF, USE, DROP)))])
    claims = _usage_claims()
    for claim in claims:
        claim['span_id'] = 'all'
    report, assessed = _evaluate(packet, claims)
    assert report['passed'], report['issues']
    assert assessed['use_mallet']['bracketed_custody_interval'] is not None
    assert assessed['use_pad']['bracketed_custody_interval'] is not None


@pytest.mark.parametrize('boundary', ['receive_mallet', 'drop_mallet'])
def test_duplicate_nearest_boundary_claims_do_not_choose_a_favorable_binding(boundary):
    packet, claims = _narration(_packet()), _usage_claims()
    duplicate = copy.deepcopy(next(row for row in claims if row['id'] == boundary))
    duplicate['id'] = 'duplicate_' + boundary
    claims.append(duplicate)
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    _assert_unqualified(assessed)


def test_receipt_must_already_have_a_supported_verdict():
    prior = 'You received the pad first.'
    packet = _narration(_packet(), [('prior', prior), ('s0', HANDOFF),
                                    ('use', USE), ('s1', DROP)])
    report, assessed = _evaluate(packet, [
        _transfer('pad', span_id='prior', quote=prior), _transfer('mallet'),
        _usage(), _transfer('mallet', 'player', 'workshop')])
    assert not report['passed']
    assert assessed['receive_pad']['verdict'] == 'supported'
    assert assessed['receive_mallet']['verdict'] == 'contradiction'
    assert _assert_unqualified(assessed)['narrative_cursor_before'] == 4


def test_later_incoming_cursor_cannot_be_rewound_into_a_valid_interval():
    prior = 'You already set the pad down.'
    packet = _narration(_packet(), [('s0', HANDOFF), ('prior', prior),
                                    ('use', USE), ('s1', DROP)])
    claims = _usage_claims()
    claims.insert(2, _transfer('pad', 'player', 'workshop', id='prior_drop_pad',
                               span_id='prior', quote=prior))
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['prior_drop_pad']['verdict'] == 'supported'
    claim = _assert_unqualified(assessed)
    assert claim['narrative_cursor_before'] == claim['narrative_cursor_after'] == 8
    assert assessed['drop_mallet']['verdict'] == 'contradiction'


@pytest.mark.parametrize('mode', ['current', 'historical', 'reported', 'conditional', 'future', 'uncertain'])
@pytest.mark.parametrize('target', ['use_mallet', 'receive_mallet', 'drop_mallet'])
def test_only_completed_use_and_completed_boundaries_qualify(mode, target):
    packet, claims = _narration(_packet()), _usage_claims()
    next(row for row in claims if row['id'] == target)['mode'] = mode
    _, assessed = _evaluate(packet, claims)
    assert assessed['use_mallet']['bracketed_custody_interval'] is None
    if target == 'use_mallet' and mode not in {'current', 'uncertain'}:
        assert assessed['use_mallet']['verdict'] == 'out_of_scope'
    else:
        _assert_unqualified(assessed)


def test_negative_possession_is_not_proved_by_a_positive_interval():
    packet, claims = _narration(_packet()), _usage_claims()
    next(row for row in claims if row['id'] == 'use_mallet')['present'] = False
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    _assert_unqualified(assessed)


def test_same_quote_boundary_transfer_order_is_still_enforced():
    packet, claims = _narration(_packet()), _usage_claims()
    claims[-2:] = reversed(claims[-2:])
    report, assessed = _evaluate(packet, claims)
    assert not report['passed']
    assert assessed['use_mallet']['bracketed_custody_interval'] is not None
    assert assessed['drop_pad']['verdict'] == 'supported'
    assert assessed['drop_mallet']['verdict'] == 'contradiction'


@pytest.mark.parametrize('create', [False, True])
def test_real_packet_full_final_order_supports_both_tools_usage(create):
    registry, world, scene, original = _real_fixture(create=create)
    commit = TurnCommit('\n\n'.join((HANDOFF, USE, DROP)), copy.deepcopy(original.sections))
    packet = _narration(_real_packet((registry, world, scene, commit)))
    report, assessed = _evaluate(packet, _usage_claims(source_preconditions=True))
    assert report['passed'], report['issues']
    assert all(assessed['use_' + item]['bracketed_custody_interval'] is not None
               for item in ('mallet', 'pad'))


def test_original_grouped_chains_fail_and_conserved_full_order_succeeds():
    registry, world, scene, original = _real_fixture(create=True)
    _authorize_no_resources(world, scene)
    rows = original.sections['items']
    grouped = TurnCommit('\n\n'.join((HANDOFF, USE, DROP)), {
        'items': [rows[index] for index in [0, 1, 4, 6, 2, 3, 5, 7]]})
    packet = _narration(_real_packet((registry, world, scene, grouped)))
    report, assessed = _evaluate(packet, _usage_claims(
        source_preconditions=True, second_index=2))
    assert not report['passed']
    _assert_unqualified(assessed)
    assert assessed['drop_mallet']['verdict'] == 'contradiction'
    offer = ordering_offer(packet, report, grouped, scene, world)
    assert offer is not None
    order = [0, 1, 4, 5, 2, 6, 3, 7]
    fixed = TurnCommit(grouped.narration, {
        'items': apply_order(grouped.sections['items'], order, offer)})
    final_packet = _narration(_real_packet((registry, world, scene, fixed)))
    assert same_endpoint(packet, final_packet, order)
    final_report, final_claims = _evaluate(final_packet, _usage_claims(source_preconditions=True))
    assert final_report['passed'], final_report['issues']
    assert all(final_claims['use_' + item]['bracketed_custody_interval'] is not None
               for item in ('mallet', 'pad'))
