"""Bounded cross-item interleaving, never new or edited item declarations.

Only ordinary, visible create/transfer chains in a stationary, resource-free
candidate are eligible. Unknown/custom effects and source materialization stay
outside this lane. The model receives indexes and a whitelist, not raw rows.
"""
from __future__ import annotations

import copy
from collections import Counter


def _evidence(row):
    return (row.get('span_id'), row.get('quote'), row.get('occurrence'))


def _item(row):
    if not isinstance(row, dict):
        return None
    return row.get('item') if row.get('op', 'create') == 'transfer' else row.get('id')


def ordering_offer(packet, report, commit, scene, world):
    """Return an opaque-indexed offer, or None when its narrow scope is absent."""
    if (commit._semantic_context.get('policy') == 'opening_text_only'
            or commit._semantic_context.get('immutable_prose') is True
            or scene.get('_semantic_return_commitment')
            or packet.get('materialization_origins')
            or packet.get('incidental_policy', {}).get('resource_action') != 'none'
            or any(rows for name, rows in commit.sections.items()
                   if name not in {'items', 'facts', 'knowledge', 'clock'})
            or packet.get('scene_fact_context', {}).get('before')
                != packet.get('scene_fact_context', {}).get('after')):
        return None
    rows = commit.sections.get('items')
    if not isinstance(rows, list) or not 2 <= len(rows) <= 64:
        return None
    if any(not isinstance(row, dict) or row.get('op') == 'materialize' for row in rows):
        return None
    types = {row['id']: row['type'] for row in packet['entities'] + packet['candidate_refs']}
    # New holders/movement can carry cross-section dependencies. Only existing
    # admitted holders are eligible; object creation remains local to its chain.
    holders = {row['id'] for row in packet['entities'] if row['type'] in {'Person', 'Place'}}
    failed_keys = {_evidence(issue.get('claim') or {}) for issue in report['issues']
                   if (issue.get('claim') or {}).get('kind') in {'transfer', 'possession'}
                   and (issue.get('claim') or {}).get('mode') == 'completed'
                   and (issue.get('claim') or {}).get('scope') == 'canonical_transition'}
    cohort = {claim.get('refs', {}).get('item') for claim in report['claims']
              if _evidence(claim) in failed_keys
              and claim.get('kind') in {'transfer', 'possession'}
              and claim.get('mode') == 'completed'
              and claim.get('scope') == 'canonical_transition'}
    cohort = {item for item in cohort if types.get(item) == 'Object'}
    records = world.get('systems', {}).get('return_commitments', {}).get('records', {})
    if any(isinstance(record, dict) and record.get('status') == 'open'
           and record.get('item') in cohort for record in records.values()):
        return None
    # Holder identity visibility alone cannot authorize disclosure of custody.
    # Explicit handoffs need public transition evidence with full multiplicity.
    handoffs = Counter((row.get('item'), row.get('from'), row.get('to'))
                       for row in packet.get('transitions', [])
                       if row.get('kind') == 'item_transfer'
                       and row.get('from') in holders and row.get('to') in holders
                       and row.get('from') != row.get('to'))
    checkpoints = [packet.get('before', {}), packet.get('after', {})]
    for transition in packet.get('transitions', []):
        checkpoints.extend(transition.get(side, {}) for side in ('before_state', 'after_state'))
    visible_custody = {(row.get('item'), row.get('holder')) for state in checkpoints
                       for row in state.get('held_by', [])}
    creation_sources = {row['id']: row.get('source', {})
                        for row in packet['candidate_refs'] if row['type'] == 'Object'}
    initializable = set()
    for index, row in enumerate(rows):
        item, op = _item(row), row.get('op', 'create')
        valid = isinstance(item, str) and bool(item)
        if op == 'create':
            valid &= not (set(row) - {'op', 'id'})
            # Candidate refs authenticate first creation and exclude objects
            # already present in the source graph. Re-declaration cannot reset
            # hidden custody or authorize another source-less initialization.
            if valid and creation_sources.get(item) == {'section': 'items', 'index': index}:
                initializable.add(item)
        elif op == 'transfer':
            valid &= (not (set(row) - {'op', 'item', 'from', 'to'})
                      and row.get('to') in holders
                      and (row.get('from') is None or row.get('from') in holders))
            if valid and row.get('from') is None:
                valid = item in initializable and (item, row['to']) in visible_custody
            elif valid:
                key = (item, row['from'], row['to'])
                valid = handoffs[key] > 0
                if valid:
                    handoffs[key] -= 1
            initializable.discard(item)
        else:
            valid = False
        if not valid:
            cohort.discard(item)
    cohort.intersection_update(_item(row) for row in rows)
    if len(cohort) < 2:
        return None
    offered = []
    for index, row in enumerate(rows):
        movable = _item(row) in cohort
        entry = {'index': index, 'movable': movable}
        if movable:
            op = row.get('op', 'create')
            entry.update(op=op, item=_item(row))
            if op == 'transfer':
                entry.update({'from': row.get('from'), 'to': row['to']})
        offered.append(entry)
    return {'rows': offered, 'rule': 'complete_index_permutation; fixed_indexes_unchanged; '
            'each_item_original_subsequence_unchanged; no_declaration_changes'}


def apply_order(rows, order, offer):
    """Select original rows only, preserving identity, multiplicity and chain."""
    if (offer is None or not isinstance(order, list) or len(order) != len(rows)
            or any(type(index) is not int for index in order)
            or sorted(order) != list(range(len(rows)))):
        raise ValueError('Item order must be a complete eligible index permutation')
    fixed = {entry['index'] for entry in offer['rows'] if not entry['movable']}
    if any(order[index] != index for index in fixed):
        raise ValueError('Item order cannot move unrelated or opaque declarations')
    prior = {}
    for index in order:
        item = _item(rows[index])
        if index <= prior.get(item, -1):
            raise ValueError('Item order must preserve every individual item chain')
        prior[item] = index
    return [copy.deepcopy(rows[index]) for index in order]


def same_endpoint(before_packet, after_packet, order=None):
    """Check the full admitted physical endpoint and uninterrupted custody.

Only minimal built-in per-item declarations can move, and each item's complete
chain is conserved. Non-item effects are byte-preserved by the caller. Rebuilt
packets additionally verify all typed preconditions and the resulting endpoint.
    """
    before_refs = copy.deepcopy(before_packet.get('candidate_refs', []))
    if order is not None:
        # Bind to the exact original declaration at its new slot. Dropping the
        # source index would erase provenance rather than conserve it.
        if (not isinstance(order, list) or any(type(i) is not int for i in order)
                or sorted(order) != list(range(len(order)))):
            return False
        positions = {old: new for new, old in enumerate(order)}
        for ref in before_refs:
            source = ref.get('source', {})
            if source.get('section') == 'items':
                if source.get('index') not in positions:
                    return False
                source['index'] = positions[source['index']]
    canonical_refs = lambda rows: sorted(rows, key=lambda row: row['id'])
    if canonical_refs(before_refs) != canonical_refs(after_packet.get('candidate_refs', [])):
        return False
    return all(before_packet.get(key) == after_packet.get(key) for key in
               ('actor_id', 'entities', 'before', 'after',
                'continuous_custody', 'scene_fact_context', 'scene_fact_sources'))
