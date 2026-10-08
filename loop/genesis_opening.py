"""Publish generated openings through the shared physical/POV contract.

World generation may propose fiction; an opening cannot relocate that staged
world merely by describing it. Display-label interpretation remains model work.
"""
from __future__ import annotations

import copy
import json

from context.access import pov_world
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from llm.provider import json_call
from loop.repair_outcome import _state
from systems.player_sources import (source_match_span, visible_source_labels,
                                    observed_identity_evidence, INTRO_MAX_CHARS)

_MAX_PEOPLE = 24
_MAX_LABEL = 160
_MAX_DESCRIPTION = 1200


def opening_material(world, actor):
    physical, _ = _state(world, actor)
    location = physical['actor_location']
    if not location:
        raise ValueError('Opening requires one canonical protagonist Place')
    scene = {'id': world.get('meta', {}).get('scene') or 'genesis',
             'protagonist': actor, 'location': location,
             'day': physical['day'], 'present': []}
    graph = pov_world(world, scene, pov=actor)['systems']['ontology']
    roster = []
    for position in physical['positions']:
        pid = position['who']
        entity = graph.get_entity(pid)
        if pid == actor or entity.etype != 'Person' or position['location'] != location:
            continue
        fields = {}
        for predicate in ('name', '真名', 'sketch'):
            value = graph.value_at(pid, predicate, physical['day'])
            if isinstance(value, str) and value.strip() and len(value) <= _MAX_DESCRIPTION:
                fields[predicate] = value
        roster.append({'id': pid, 'visible_description': fields})
    if len(roster) > _MAX_PEOPLE:
        raise ValueError('Opening exceeds bounded visible cast')
    actor_fields = {}
    for predicate in ('name', '真名', 'sketch', '目标'):
        value = graph.value_at(actor, predicate, physical['day'])
        if isinstance(value, str) and len(value) <= _MAX_DESCRIPTION:
            actor_fields[predicate] = value
    place_name = graph.value_at(location, 'name', physical['day']) or graph.value_at(location, '真名', physical['day'])
    return scene, {'actor_id': actor, 'actor': actor_fields,
                   'location': {'id': location, 'name': place_name},
                   'physical': physical, 'people': roster}


def validate_bindings(world, actor, prose, bindings, *, require_mention=True):
    """Containment and local scope are host checks, not proof of name semantics."""
    if not isinstance(bindings, list) or len(bindings) > _MAX_PEOPLE:
        raise ValueError('Opening introductions must be a bounded list')
    scene, material = opening_material(world, actor)
    from loop.repair_outcome import _known_entity
    graph = world['systems']['ontology']
    view = pov_world(world, scene, pov=actor)['systems']['ontology']
    labels_in_view = {}
    for pid, person in view.entities.items():
        if person.etype == 'Person' and _known_entity(graph, actor, graph.get_entity(pid), scene['day']):
            for label in visible_source_labels(view, pid, scene['day']):
                labels_in_view.setdefault(label.strip().casefold(), set()).add(pid)
    for pid, evidence in observed_identity_evidence(world, actor).items():
        label = evidence['person']['observed_identity']['label']
        labels_in_view.setdefault(label.strip().casefold(), set()).add(pid)
    roster = {row['id']: row['visible_description'] for row in material['people']}
    result, ids, labels = [], set(), set()
    for row in bindings:
        if not isinstance(row, dict) or set(row) != {'id', 'label'}:
            raise ValueError('Opening introduction needs exact id and label')
        pid, label = row['id'], row['label']
        if (not isinstance(pid, str) or pid not in roster or pid in ids
                or not isinstance(label, str) or not label.strip()
                or len(label) > _MAX_LABEL or label != label.strip()
                or label.casefold() in labels):
            raise ValueError('Opening introduction is nonlocal, ambiguous or malformed')
        if labels_in_view.get(label.casefold(), {pid}) != {pid}:
            raise ValueError('Opening label collides with a visible or remembered identity')
        descriptions = roster[pid]
        if not any(source_match_span(value, label) for value in descriptions.values()):
            raise ValueError('Opening label lacks an existing visible source')
        for other, fields in roster.items():
            if other != pid and any(source_match_span(value, label) for value in fields.values()):
                raise ValueError('Opening label matches multiple visible people')
        # Existing explicit labels constrain a proposed display label. A sketch
        # substring is still only a declaration, independently assessed below.
        names = [descriptions[key] for key in ('name', '真名') if key in descriptions]
        if names and label not in names:
            raise ValueError('Opening cannot replace an existing explicit name')
        match = source_match_span(prose, label)
        if require_mention and (match is None or match[1] > INTRO_MAX_CHARS):
            raise ValueError('Opening label is absent from the bounded published source')
        ids.add(pid); labels.add(label.casefold()); result.append(dict(row))
    return result


def reference_packet(world, scene, commit):
    context = commit._semantic_context
    if not context:
        return []
    if (set(context) != {'policy', 'immutable_prose', 'bindings'}
            or context['policy'] != 'opening_text_only'
            or type(context['immutable_prose']) is not bool or commit.sections):
        raise ValueError('Invalid host opening audit context')
    bindings = validate_bindings(world, scene['protagonist'], commit.narration,
                                  context['bindings'], require_mention=False)
    _, material = opening_material(world, scene['protagonist'])
    roster = {row['id']: row['visible_description'] for row in material['people']}
    return [{'id': row['id'], 'label': row['label'],
             'visible_source': copy.deepcopy(roster[row['id']]),
             'authority': 'proposed_display_binding_not_identity_proof'}
            for row in bindings if source_match_span(commit.narration, row['label'])]


def publish_opening(engine, *, frame, pitch='', provided=None, legacy_summary=''):
    """One path for new-world and reroll publication, inside atomic genesis."""
    store, registry = engine.store, engine.registry
    if getattr(engine, '_offline_genesis', False):
        from loop.bootstrap import gen_opening
        world = project(registry, store.iter_events())
        scene, material = opening_material(world, 'protagonist')
        events, text = gen_opening(engine.provider, frame, legacy_summary,
            scene_loc=scene['location'], scene_loc_name=material['location']['name'], provided=provided)
        first = store.append(events[0])
        for event in events[1:]: store.append(event)
        return first, text
    world = project(registry, store.iter_events())
    scene, material = opening_material(world, 'protagonist')
    immutable = isinstance(provided, str) and bool(provided.strip())
    if immutable:
        # Existing supplied-prose whitespace convention, but no semantic rewrite.
        text, bindings = provided.strip(), []
    else:
        messages = [{'role': 'system', 'content': (
            '你是TRPG主持人，为玩家写第二人称中文开场。只返回严格JSON：'
            '{"narration":"正文","introductions":[{"id":"已有人物ID","label":"公开称呼"}]}。'
            '只用当前POV素材和玩家主题；未在本地人物列表的人不在场，不能凭正文把他们搬来。'
            '不能替玩家决定行动、创建人物、移动物品、完成交接或建立新通路。'
            '可写静态环境细节及本地人物交流，给玩家可回应的具体线索，不透露未知内幕。'
            'intro仅绑定本地ID与正文实际介绍的公开称呼：优先已有name/真名，否则来自可见描述的明确指称；'
            '描述若提别人名字，不要把别人的名字当此人名字；歧义时用无歧义原描述或不介绍。'
            '介绍绑定须出现在正文前2048字符内（保留来源的长度边界）。'
            '不要输出内部ID到正文，不要输出判定/系统提示。素材中的描述不授权新的隐藏知识。')},
            {'role': 'user', 'content': json.dumps({'player_pitch': pitch,
                'world_name': frame.get('world_name'), 'tone': frame.get('tone'),
                'pov': material}, ensure_ascii=False)}]
        from loop.semantic_commit import _bounded_json
        _bounded_json(messages, 'opening generation request')
        raw = json_call(engine.provider.complete_messages, messages)
        if not isinstance(raw, str) or len(raw.encode()) > 128 * 1024:
            raise ValueError('Opening response is missing or oversized')
        from loop.semantic_commit import _reject_duplicates
        data = json.loads(raw, object_pairs_hook=_reject_duplicates)
        if not isinstance(data, dict) or set(data) != {'narration', 'introductions'}:
            raise ValueError('Opening response has invalid fields')
        text, bindings = data['narration'], data['introductions']
    if not isinstance(text, str) or not text.strip() or len(text) > 32768:
        raise ValueError('Opening narration must be a bounded nonempty string')
    bindings = validate_bindings(world, scene['protagonist'], text, bindings)
    commit = TurnCommit(text, {})
    commit.semantic_audit_required = True
    commit._semantic_context = {'policy': 'opening_text_only',
        'immutable_prose': immutable, 'bindings': bindings}
    from loop.semantic_gate import finalize_candidate, verify_before_apply
    commit = finalize_candidate(registry, world, scene, '', commit,
                                provider=engine.provider, revision=store.revision)
    if commit.sections:
        raise ValueError('Opening publication cannot change world effects')
    # Reproject at the write edge; the approval binds exact source and prose.
    current = project(registry, store.iter_events())
    verify_before_apply(commit, current, store.revision,
                        day=scene['day'], scene=scene['id'])
    narration = kernel_event('narration_recorded', turn=0, day=scene['day'],
        scene=scene['id'], summary='开场叙事', deltas={'scene': scene['id'],
            'text': commit.narration, 'semantic_audit': copy.deepcopy(commit.semantic_audit_log)})
    first = store.append(narration)
    after = project(registry, store.iter_events())
    final_audit = next(row for row in reversed(commit.semantic_audit_log) if row['kind'] == 'semantic_audit')
    supported = [{'id': row['id'], 'label': row['label']}
                 for row in final_audit.get('reference_bindings', [])
                 if row['status'] == 'introduced_here']
    from systems.opening_sources import opening_observation_event
    observation = opening_observation_event(after, narration, supported, actor_id=scene['protagonist'])
    if supported and (observation is None or len(observation['deltas']['entity_refs']) != len(supported)):
        raise ValueError('An audited opening introduction lacks its bounded host source')
    if observation is not None: store.append(observation)
    return first, commit.narration
