"""Real host packets prove continuity through even non-public item events."""
import copy
import json

import pytest

from kernel.projection import empty_world
from kernel.registry import Registry
from kernel.turncommit import TurnCommit
from loop.repair_outcome import build_repair_outcome, CUSTODY_INTERVAL_POLICY
from loop.semantic_commit import build_semantic_packet
from loop.item_order_repair import ordering_offer, apply_order, same_endpoint
from systems.object import ObjectSystem
from systems.ontology import OntologySystem
from tests.loop.test_coclaimed_custody_preconditions import HANDOFF, DROP, _claims, _evaluate


def _real_fixture(excursion=None, *, create=False):
    registry = Registry()
    registry.register(OntologySystem())
    registry.register(ObjectSystem())
    world = empty_world(registry)
    graph = world['systems']['ontology']
    for eid, kind in [('player', 'Person'), ('keeper', 'Person'), ('visitor', 'Person'),
                      ('workshop', 'Place'), ('elsewhere', 'Place')]:
        graph.add_entity(eid, kind)
    for eid in ('player', 'keeper', 'visitor'):
        graph.add_relation(eid, 'located_in',
            'elsewhere' if eid == 'visitor' and excursion == 'remote' else 'workshop',
            day=1, turn=0, source_event='setup')
    items = []
    for item in ('mallet', 'pad'):
        if create:
            items.extend([{'op': 'create', 'id': item},
                          {'op': 'transfer', 'item': item, 'to': 'keeper'}])
        else:
            graph.add_entity(item, 'Object')
            graph.add_relation(item, 'held_by', 'keeper', day=1, turn=0, source_event='setup')
    transfers = [('mallet', 'keeper', 'player')]
    if excursion:
        transfers.extend([('pad', 'keeper', 'visitor'), ('pad', 'visitor', 'keeper')])
    transfers.extend([('pad', 'keeper', 'player'),
                      ('mallet', 'player', 'workshop'), ('pad', 'player', 'workshop')])
    for item, source, target in transfers:
        row = {'op': 'transfer', 'item': item, 'from': source, 'to': target}
        if target == 'visitor' and excursion == 'hidden_relation':
            row['visibility'] = 'hidden'
        items.append(row)
    scene = {'protagonist': 'player', 'location': 'workshop', 'day': 1}
    commit = TurnCommit(HANDOFF + '\n\n' + DROP, {'items': items})
    return registry, world, scene, commit


def _packet(fixture):
    registry, world, scene, commit = fixture
    def source_state():
        return (world['meta'], vars(world['systems']['ontology']),
                world['systems']['object'], scene, commit.sections)
    before = copy.deepcopy(source_state())
    packet = build_semantic_packet(registry, world, scene, commit, 'borrow tools')
    assert source_state() == before
    # Only source span IDs differ from the synthetic extraction fixture.
    for span, name in zip(packet['narration_spans'], ('s0', 's1')):
        span['id'] = name
    return packet


@pytest.mark.parametrize('create', [False, True])
def test_real_event_certificates_accept_continuous_source_custody(create):
    packet = _packet(_real_fixture(create=create))
    report, assessed = _evaluate(packet, _claims())
    assert report['passed'], report['issues']
    assert packet['custody_interval_policy'] == CUSTODY_INTERVAL_POLICY
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is not None
    assert '_custody_run' not in json.dumps(packet)
    if create:
        assert packet['before']['held_by'] == []


@pytest.mark.parametrize('excursion', ['remote', 'hidden_relation'])
def test_hidden_third_party_custody_breaks_certificate_without_exposing_holder(excursion):
    packet = _packet(_real_fixture(excursion))
    # Public snapshots look identical to uninterrupted custody. The host proof
    # must still notice both omitted transitions, without revealing them.
    assert [(row['item'], row['from'], row['to']) for row in packet['transitions']] == [
        ('mallet', 'keeper', 'player'), ('pad', 'keeper', 'player'),
        ('mallet', 'player', 'workshop'), ('pad', 'player', 'workshop')]
    assert all(any(row == {'item': 'pad', 'holder': 'keeper'} for row in state['held_by'])
               for state in [packet['transitions'][0]['before_state'],
                             packet['transitions'][0]['after_state'],
                             packet['transitions'][1]['before_state']])
    report, assessed = _evaluate(packet, _claims())
    assert not report['passed']
    assert assessed['held_pad']['co_claimed_transfer_precondition'] is None
    assert assessed['receive_mallet']['verdict'] == 'contradiction'
    assert not any(row['holder'] == 'visitor' for row in packet['custody_intervals'])
    assert all(set(row) == {'item', 'holder', 'first', 'last'} for row in packet['custody_intervals'])
    assert '_custody_run' not in json.dumps(packet)
    if excursion == 'remote':
        assert 'visitor' not in json.dumps(packet)


def test_ordinary_narration_outcome_has_no_private_custody_run_marks():
    registry, world, scene, commit = _real_fixture('remote')
    outcome = build_repair_outcome(registry, world, scene, commit, 'borrow tools')
    assert '_custody_run' not in json.dumps(outcome)


def test_real_certificate_is_stable_under_cross_item_interleaving():
    registry, world, scene, commit = _real_fixture(create=True)
    before = build_semantic_packet(registry, world, scene, commit, 'borrow tools')
    # Keep each item's chain, move its initial creation/placement as a group.
    rows = commit.sections['items']
    reordered = TurnCommit(commit.narration, {'items': [rows[i] for i in [2, 3, 0, 1, 4, 5, 6, 7]]})
    after = build_semantic_packet(registry, world, scene, reordered, 'borrow tools')
    assert before['before'] == after['before']
    assert before['after'] == after['after']
    assert before['custody_intervals'] == after['custody_intervals']


def _authorize_no_resources(world, scene):
    world['_action_turn'] = 1
    scene['_semantic_resource_scope'] = {
        'actor': 'player', 'turn': 1, 'status': 'none', 'no_resources': True}


def test_real_hidden_initial_custody_is_not_disclosed_by_ordering_offer():
    fixture = _real_fixture()
    registry, world, scene, commit = fixture
    _authorize_no_resources(world, scene)
    for relation in world['systems']['ontology'].relations:
        if relation.rel == 'held_by':
            relation.attrs['visibility'] = 'hidden'
    packet = _packet(fixture)
    assert packet['before']['held_by'] == []
    assert [(row['from'], row['to']) for row in packet['transitions']] == [
        ('player', 'workshop'), ('player', 'workshop')]
    claims = [{'kind': 'transfer', 'mode': 'completed', 'scope': 'canonical_transition',
               'span_id': 's0', 'quote': HANDOFF, 'occurrence': 0,
               'refs': {'item': item, 'from': None, 'to': 'player'}}
              for item in ('mallet', 'pad')]
    report = {'issues': [{'claim': claims[0], 'verdict': 'unsupported'}], 'claims': claims}
    assert ordering_offer(packet, report, commit, scene, world) is None


def test_real_grouped_eight_row_chains_remain_repairable_with_visible_evidence():
    registry, world, scene, commit = _real_fixture(create=True)
    _authorize_no_resources(world, scene)
    rows = commit.sections['items']
    # Reproduce complete per-item chains: create/place/receive/drop each tool.
    commit = TurnCommit(commit.narration, {'items': [rows[i] for i in [0, 1, 4, 6, 2, 3, 5, 7]]})
    packet = _packet((registry, world, scene, commit))
    report, _ = _evaluate(packet, _claims(second_index=2))
    assert not report['passed']
    offer = ordering_offer(packet, report, commit, scene, world)
    assert offer is not None and all(row['movable'] for row in offer['rows'])
    order = [0, 1, 4, 5, 2, 6, 3, 7]
    fixed = TurnCommit(commit.narration, {'items': apply_order(commit.sections['items'], order, offer)})
    repaired = _packet((registry, world, scene, fixed))
    assert same_endpoint(packet, repaired, order)
    final_report, _ = _evaluate(repaired, _claims())
    assert final_report['passed'], final_report['issues']
