"""Bounded provenance for first tracking of an established scene component.

These checks authenticate a current, actor-visible source and a proposed initial
Place. They do not prove that its prose describes the proposed item or entails
attachment; the semantic auditor must attest that interpretation separately.

V1 consumes one source slot (subject, predicate) per materialized Object. This
deliberately conservative rule also prevents a later version of the same fact
from materializing another Object. Historical ordinary item events are untouched.
"""
from __future__ import annotations

import copy
import re

from kernel.scene_fact_sources import (
    MAX_SOURCE_CHARS, MAX_SOURCES, VERSION, _context, _hash, _identifier,
    _source_rows,
)

_DECLARATION_KEYS = {'op', 'id', 'initial', 'source_ref', 'source_digest', 'source_quote'}
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _bindings(world):
    """Missing legacy state is empty; malformed provenance is not empty."""
    state = world.get('systems', {}).get('object', {})
    if not isinstance(state, dict):
        return None
    bindings = state.get('materializations', {})
    if not isinstance(bindings, dict):
        return None
    for item, row in bindings.items():
        source = row.get('source') if isinstance(row, dict) else None
        if (not _identifier(item) or not isinstance(source, dict)
                or not _identifier(source.get('subject'))
                or not _identifier(source.get('predicate'))):
            return None
    return bindings


def source_catalog(world, actor_id):
    """Return at most 24 complete, unconsumed current Place-fact records.

    This is a bounded subset, not an exhaustive source search. Newest eligible
    facts come first, with stable tie-breaking. Values over 2048 characters are
    omitted rather than truncated. Missing host context yields no sources.
    POV belief substitutions are not attributed to the canonical fact event.
    """
    context = _context(world, actor_id)
    if context is None:
        return []
    bindings = _bindings(world)
    if bindings is None:
        return []
    consumed = {(row['source']['subject'], row['source']['predicate'])
                for row in bindings.values()}
    return _source_rows(context, exclude_slots=consumed)


def _resolve(world, declaration, actor_id):
    """Return (safe origin, errors); keep diagnostics independent of secrets."""
    if not isinstance(declaration, dict) or set(declaration) != _DECLARATION_KEYS:
        return None, [('', 'materialization_shape',
            'materialize 仅接受 op、id、initial、source_ref、source_digest、source_quote；身份由宿主绑定')]
    if declaration.get('op') != 'materialize' or not _identifier(declaration.get('id')):
        return None, [('id', 'materialization_id', 'materialize 必须声明新的非空 Object id')]
    initial = declaration.get('initial')
    if (not isinstance(initial, dict) or set(initial) != {'kind', 'place'}
            or initial.get('kind') != 'scene_component' or not _identifier(initial.get('place'))):
        return None, [('initial', 'materialization_initial',
            'initial 必须为 {kind:scene_component, place:当前场景 Place id}')]
    context = _context(world, actor_id)
    if context is None:
        return None, [('', 'materialization_context',
            '物品首次实体化需要宿主绑定的角色、行动回合及唯一可见的当前地点')]
    graph, item = context['graph'], declaration['id']
    # Even _pending must be removed by the validation caller in its private
    # working world; this resolver never reinterprets an existing entity as new.
    if (graph.get_entity(item) is not None
            or any(relation.src == item and relation.rel == 'held_by' for relation in graph.relations)):
        return None, [('id', 'materialization_existing',
            'materialize 仅用于尚无实体或持有记录的物品；已有物品必须使用原 id 和普通转移')]
    place = graph.get_entity(initial['place'])
    if place is None or place.etype != 'Place' or initial['place'] != context['place']:
        return None, [('initial.place', 'materialization_place',
            '初始场景必须是角色当前唯一可见的已有 Place，不得推测人物保管或异地来源')]
    bindings = _bindings(world)
    if bindings is None:
        return None, [('', 'materialization_provenance', '物品来源记录不可验证')]
    if item in bindings or any(row.get('source_ref') == declaration.get('source_ref')
                               for row in bindings.values()):
        return None, [('source_ref', 'materialization_reused', '该物品来源已经实体化，不得重复生成物品')]
    source_ref, digest = declaration.get('source_ref'), declaration.get('source_digest')
    if not _identifier(source_ref, 80):
        return None, [('source_ref', 'materialization_source', 'source_ref 必须引用本回合提供的可见历史来源')]
    matches = [row for row in source_catalog(world, context['actor']) if row['source_ref'] == source_ref]
    if len(matches) != 1:
        return None, [('source_ref', 'materialization_source',
            '来源不在当前可用目录中，或已失效、不可见、重复使用；不得猜测来源')]
    source = matches[0]
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None or digest != source['source_digest']:
        return None, [('source_digest', 'materialization_digest', '来源摘要不匹配；必须使用当前目录中的完整摘要')]
    quote = declaration.get('source_quote')
    if (not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_SOURCE_CHARS
            or source['text'].count(quote) != 1):
        return None, [('source_quote', 'materialization_quote', 'source_quote 必须逐字引用来源中唯一的一段非空原文')]
    start = source['text'].index(quote)
    return {'version': VERSION, 'item': item, 'actor_id': context['actor'],
            'initial': copy.deepcopy(initial), 'source_ref': source_ref,
            'source_digest': digest, 'source_quote': quote,
            'source_start': start, 'source_end': start + len(quote),
            'source': copy.deepcopy(source)}, []


def materialization_errors(world, declaration, actor_id=None):
    """Return field/code/hint triples; never certify natural-language meaning."""
    return _resolve(world, declaration, actor_id)[1]


def materialization_origin(world, declaration, actor_id=None):
    """Return the resolved safe source record, or raise ValueError."""
    origin, errors = _resolve(world, declaration, actor_id)
    if errors:
        raise ValueError(errors[0][1] + ': ' + errors[0][2])
    return origin


def project_materialization(world, event):
    """Replay one host-approved initialization at its event-time checkpoint.

    New-event admission and semantic approval belong to the caller. The exact
    singleton actors list is persisted, host-normalized replay provenance,
    never an item JSON field. Missing provenance has no ambient-actor fallback.
    Replaying earlier events uses their own actor/day/turn, not ambient action
    metadata. No historical relation is inserted at the cited source's date.
    """
    if (not isinstance(event, dict) or event.get('type') != 'object_materialized'
            or not _identifier(event.get('id')) or type(event.get('day')) is not int
            or event['day'] < 0 or type(event.get('turn')) is not int or event['turn'] <= 0):
        raise ValueError('Invalid materialization event envelope')
    actors = event.get('actors')
    if not isinstance(actors, list) or len(actors) != 1 or not _identifier(actors[0]):
        raise ValueError('Materialization event requires exactly one persisted host actor')
    actor = actors[0]
    scoped = {**world, '_action_turn': event['turn'], '_materialization_actor': actor,
              'meta': {**world.get('meta', {}), 'day': event['day']}}
    origin = materialization_origin(scoped, event.get('deltas'), actor)
    graph = world['systems']['ontology']
    visibility = ({} if origin['source']['source_visibility'] == 'public'
                  else {'visibility': 'hidden', 'discovered_by': [origin['actor_id']]})
    graph.add_entity(origin['item'], 'Object', **visibility)
    graph.add_relation(origin['item'], 'held_by', origin['initial']['place'],
                       day=event['day'], turn=event['turn'], source_event=event['id'])
    state = world['systems'].setdefault('object', {})
    state.setdefault('materializations', {})[origin['item']] = {
        **copy.deepcopy(origin), 'materialization_event_id': event['id'],
        'materialization_day': event['day'], 'materialization_turn': event['turn']}
