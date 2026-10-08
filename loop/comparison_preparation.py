"""Host-owned, unpublished resource/variation prefix shared by both narrators.

The serialized preparation is a trusted Python value, not a model credential.
It owns no live graph or mutable event dictionaries. Every restore checks the
source snapshot and reconstructs fresh values without publishing or appending
to the caller's batch.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from itertools import chain

from engine.store import EventBatch, RevisionConflict
from kernel.projection import project
from loop.resources import prepare_resources, registered_balances
from loop.semantic_gate import _hash, _source
from loop.strategy import _bound_actor_id
from loop.variation import prepare_variation, variation_fragment

_VERSION = 'comparison-preparation-v1'
_PROOF_SCENE_KEYS = frozenset({
    '_comparison_preparation', '_comparison_preparation_digest',
    '_comparison_required_prefix',
})
_GENERATED_SCENE_KEYS = _PROOF_SCENE_KEYS | frozenset({
    '_resolved_values', '_resolved_clock', '_resolution_prompt',
    '_variation_prompt',
})


def _reject(message):
    # Keep the module importable from turn.py without an import cycle.
    from loop.turn import TurnRejected
    raise TurnRejected(message)


def _base_scene(scene):
    return copy.deepcopy({key: value for key, value in scene.items()
                          if key not in _GENERATED_SCENE_KEYS})


def _input_context(scene, player_input):
    # Bind all supplied scene context, including id, location, day and present,
    # rather than collapsing id/location through the display-scene fallback.
    return _hash({'scene': _base_scene(scene), 'input': player_input})


def _world_fingerprint(world, actor):
    # _source retains exact ontology provenance (not merely current balances).
    # The extra state also binds variation history and other projected systems.
    return _hash({'source': _source(world, actor), 'meta': world.get('meta', {}),
                  'systems': {key: value for key, value in
                              world.get('systems', {}).items()
                              if key != 'ontology'}})


@dataclass(frozen=True)
class ComparisonPreparation:
    """Immutable host binding; decoded event access always returns new values.

    Do not pass this private payload to prompts, tools, or user-facing metadata:
    it includes all registered owners' validation balances. The digest alone is
    suitable for binding a candidate's host-only semantic approval.
    """

    _payload: str = field(repr=False)
    digest: str

    def _read(self):
        if (not isinstance(self._payload, str) or not isinstance(self.digest, str)
                or hashlib.sha256(self._payload.encode()).hexdigest() != self.digest):
            _reject('Comparison preparation was changed; prepare a new comparison')
        try:
            data = json.loads(self._payload)
        except (ValueError, TypeError):
            _reject('Comparison preparation has an invalid payload')
        if not isinstance(data, dict) or data.get('version') != _VERSION:
            _reject('Comparison preparation has an unsupported version')
        return data

    @property
    def revision(self):
        return self._read()['revision']

    @property
    def turn(self):
        return self._read()['turn']

    @property
    def actor(self):
        return self._read()['actor']

    @property
    def source(self):
        return self._read()['source']

    @property
    def preview_source(self):
        return self._read()['preview_source']

    @property
    def input_context(self):
        return self._read()['input_context']

    @property
    def prefix_events(self):
        """Exact prepared IDs/payloads, safe for the host to stage once."""
        return self._read()['prefix_events']


def _snapshot_batch(store_or_batch):
    batch = (store_or_batch if isinstance(store_or_batch, EventBatch)
             else EventBatch(store_or_batch))
    if batch.events or batch.retractions:
        _reject('Comparison preparation requires an unstaged action batch')
    events = list(batch.iter_events(include_retracted=True))
    actual_turn = max((event.get('turn') or 0 for event in events
                       if not event.get('retracted')), default=0) + 1
    if batch.turn != actual_turn:
        _reject('Comparison preparation requires the current action turn')
    return batch, events


def _check_source(registry, batch, events, world, scene):
    actor = _bound_actor_id(world, scene)
    version = world.get('_revision')
    if version is not None and version != batch.revision:
        raise RevisionConflict('Refresh the world before preparing a comparison')
    current = project(registry, events)
    if _source(current, actor) != _source(world, actor):
        _reject('Comparison source does not match the current event snapshot')
    if _world_fingerprint(current, actor) != _world_fingerprint(world, actor):
        _reject('Comparison world does not match the current event snapshot')
    if _bound_actor_id(current, scene) != actor:
        _reject('Comparison actor does not match the current event snapshot')
    return current, actor


def _preview_scene(scene, data):
    prepared = _base_scene(scene)
    prepared['_resolved_values'] = {
        (subject, predicate): value
        for subject, predicate, value in data['resolved_values']}
    prepared['_resolved_clock'] = copy.deepcopy(data['resolved_clock'])
    prepared['_resolution_prompt'] = data['resolution_prompt']
    prepared['_variation_prompt'] = data['variation_prompt']
    return prepared


def prepare_comparison(registry, store, world, scene, player_input, provider):
    """Resolve once against one snapshot; retain the entire unpublished prefix.

    No narrator runs here. Resource intent is resolved once, then variation is
    drawn once from that resource preview. Neither operation publishes events.
    """
    # Bind before even the resource intent model can observe the world.
    _bound_actor_id(world, scene)
    batch, events = _snapshot_batch(store)
    preview, actor = _check_source(registry, batch, events, world, scene)
    scene = _base_scene(scene)
    source = _source(preview, actor)
    source_world = _world_fingerprint(preview, actor)
    expected_balances = registered_balances(preview)
    resolution_prompt = ''
    resolved_clock = None
    if registry.owner_of_event('resources_resolved') is not None:
        resolution, expected, resolution_prompt = prepare_resources(
            preview, scene, player_input, provider, batch.turn)
        if resolution is not None:
            batch.append(resolution)
            preview = project(registry, batch.iter_events())
            expected_balances.update(expected)
            resolved_clock = copy.deepcopy(resolution['deltas'].get('wait_until'))
    data = {
        'version': _VERSION, 'revision': batch.revision, 'turn': batch.turn,
        'actor': actor, 'input_context': _input_context(scene, player_input),
        'source': source, 'source_world': source_world, 'snapshot': _hash(events),
        'resolved_values': [[subject, predicate, value]
                            for (subject, predicate), value in expected_balances.items()],
        'resolved_clock': resolved_clock, 'resolution_prompt': resolution_prompt,
        'variation_prompt': '',
    }
    preview_scene = _preview_scene(scene, data)
    variation = prepare_variation(registry, preview, preview_scene, batch.turn)
    if variation is not None:
        batch.append(variation)
        data['variation_prompt'] = variation_fragment(variation)
    # Project the complete prefix, including variation, exactly as selection
    # will. Facts retain the original resources_resolved event IDs.
    preview = project(registry, batch.iter_events())
    data['prefix_events'] = copy.deepcopy(batch.events)
    data['preview_source'] = _source(preview, actor)
    data['preview_world'] = _world_fingerprint(preview, actor)
    data['preview_scene'] = _hash(_preview_scene(scene, data))
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    return ComparisonPreparation(payload, hashlib.sha256(payload.encode()).hexdigest())


def restore_preparation(registry, store_or_batch, world, scene, player_input,
                        preparation):
    """Validate and replay privately, returning (preview_world, preview_scene).

    The caller's store/batch is unchanged. To publish a selected candidate,
    append preparation.prefix_events to the same validated empty EventBatch,
    project it, and call verify_staged_preparation before producing/applying.
    """
    if type(preparation) is not ComparisonPreparation:
        _reject('Comparison requires an intact host preparation')
    data = preparation._read()
    batch, events = _snapshot_batch(store_or_batch)
    if data['revision'] != batch.revision or data['turn'] != batch.turn:
        raise RevisionConflict('Comparison preparation is stale; prepare a new comparison')
    current, actor = _check_source(registry, batch, events, world, scene)
    if (data['actor'] != actor
            or data['input_context'] != _input_context(scene, player_input)
            or data['source'] != _source(current, actor)
            or data['source_world'] != _world_fingerprint(current, actor)
            or data['snapshot'] != _hash(events)):
        _reject('Comparison preparation no longer matches its source or input')
    preview = project(registry, chain(events, data['prefix_events']))
    preview['_revision'] = batch.revision
    preview['_action_turn'] = batch.turn
    preview_scene = _preview_scene(scene, data)
    verify_staged_preparation(preparation, preview, preview_scene)
    return preview, preview_scene


def verify_staged_preparation(preparation, world, scene):
    """Check the staged projection/context; the write edge also checks event IDs.

    A no-op resolution may project identically with a different event ID, so
    this fingerprint check does not replace exact-prefix validation on a batch.
    """
    if type(preparation) is not ComparisonPreparation:
        _reject('Comparison requires an intact host preparation')
    data = preparation._read()
    actor = data['actor']
    if (data['preview_source'] != _source(world, actor)
            or data['preview_world'] != _world_fingerprint(world, actor)
            or data['preview_scene'] != _hash({key: value for key, value in scene.items()
                                              if key not in _PROOF_SCENE_KEYS})
            or scene.get('protagonist') != data['actor']
            or world.get('_action_turn') != data['turn']
            or world.get('_revision') != data['revision']):
        _reject('Staged comparison prefix or private context was changed')
