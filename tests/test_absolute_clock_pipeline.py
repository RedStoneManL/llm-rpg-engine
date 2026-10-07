"""Absolute end times share the same preview/publication clock, without extra AI calls."""
import copy
import pytest

from app.engine import build_engine, rewind
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from kernel.validation import validate_commit
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy
from loop.turn import run_turn, apply_turn, advanced_day, TurnRejected


@pytest.fixture
def game(tmp_path, monkeypatch):
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup',
                 'run_lore', 'run_density', '_run_demote_on_leave'):
        monkeypatch.setattr('loop.turn.' + name, lambda *args, **kwargs: [])
    monkeypatch.setattr('llm.provider._do_post', lambda *a, **k: pytest.fail('offline only'))
    game = build_engine(tmp_path / 'absolute-clock')
    data = [('entity_created', {'id': 'hero', 'etype': 'Person'}),
            ('entity_created', {'id': 'keeper', 'etype': 'Person'}),
            ('entity_created', {'id': 'bell', 'etype': 'Object'}),
            ('relation_added', {'src': 'bell', 'rel': 'held_by', 'dst': 'hero'}),
            ('clock_advanced', {'advance': True, 'days': 0, 'bands': 2, 'reason': 'setup'})]
    game.store.append_many([kernel_event(kind, day=1, scene='inn', turn=0,
        summary='fixture', deltas=data) for kind, data in data])
    game.world = project(game.registry, game.store.iter_events())
    yield game
    game.store.close()


def clock(target=None, **extra):
    return {'advance': True, 'target': target or {'day': 2, 'band': 0},
            'reason': '休息到次日清晨', **extra}


@pytest.mark.parametrize('resources', [False, True])
def test_next_morning_is_two_bands_with_or_without_resources(game, resources):
    if resources:
        game.store.append(kernel_event('resources_configured', day=1, scene='inn', turn=0,
            summary='fixture', deltas={'subject': 'hero', 'resources': {
                'coins': {'initial': 10, 'type': 'integer', 'min': 0}}}))
        game.world = project(game.registry, game.store.iter_events())
    responses = ([{'op': 'none', 'wait_until': {'day': 2, 'band': 0}}] if resources else [])
    responses += [{'narration': '你一觉睡到次日清晨。', 'clock': [clock()]}]
    provider = FakeLLMProvider(json_responses=responses)
    result = run_turn(game.registry, game.store, game.world,
        {**_build_scene(game), 'protagonist': 'hero'}, '休息到明天清晨',
        provider=provider, strategy=AuthorStrategy(), max_repairs=0)
    assert (result.world['meta']['day'], result.world['meta']['band']) == (2, 0)
    event = next(e for e in result.events if e['type'] == 'clock_advanced')
    assert event['day'] == 2
    assert event['deltas']['days'] == 0 and event['deltas']['bands'] == 2
    assert 'target' not in event['deltas']
    assert len(provider.calls) == (2 if resources else 1)
    assert result.commit.sections['clock'] == [clock()]  # proposal never mutated
    reopened = build_engine(game.store.db_path.parent)
    try:
        assert (reopened.world['meta']['day'], reopened.world['meta']['band']) == (2, 0)
    finally:
        reopened.store.close()
    game.world = result.world
    rewind(game, event['turn'])
    assert (game.world['meta']['day'], game.world['meta']['band']) == (1, 2)


def test_apply_turn_direct_and_item_preview_share_post_target_day(game):
    proposal = TurnCommit('清晨将铜铃交给掌柜。', {
        'clock': [clock()], 'items': [{'op': 'transfer', 'item': 'bell',
                                     'from': 'hero', 'to': 'keeper'}]})
    snapshot = copy.deepcopy(proposal.to_dict())
    assert validate_commit(game.registry, proposal, game.world) == []
    assert advanced_day(game.world, proposal) == 2
    # The caller's old day cannot corrupt target stamping.
    result = apply_turn(game.registry, game.store, proposal, day=1, scene='inn')
    assert (result['meta']['day'], result['meta']['band']) == (2, 0)
    graph = result['systems']['ontology']
    assert graph.neighbors('bell', 'held_by', 1) == ['hero']
    assert graph.neighbors('bell', 'held_by', 2) == ['keeper']
    assert proposal.to_dict() == snapshot
    assert all(e['day'] == 2 for e in game.store.iter_events() if e['turn'] == 1)


@pytest.mark.parametrize('bad_clock', [clock(days=0), clock(bands=0),
    clock({'day': 1, 'band': 1}), clock({'day': 2, 'band': True})])
def test_invalid_target_rejected_without_world_changes(game, bad_clock):
    before = list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world,
            {**_build_scene(game), 'protagonist': 'hero'}, '等待',
            provider=FakeLLMProvider(json_responses=[{
                'narration': '等待。', 'clock': [bad_clock]}]),
            strategy=AuthorStrategy(), max_repairs=0)
    assert list(game.store.iter_events()) == before


def test_host_resolved_wait_is_checked_without_resource_system_resolution(game):
    scene = {**_build_scene(game), 'protagonist': 'hero',
             '_resolved_clock': {'day': 2, 'band': 0}}
    provider = FakeLLMProvider(json_responses=[
        {'narration': '睡到清晨。', 'clock': [{'advance': True, 'days': 1,
            'bands': 2, 'reason': '错误重复进位'}]},
        {'clock': [clock()]}])
    result = run_turn(game.registry, game.store, game.world, scene, '等待次日清晨',
        provider=provider, strategy=AuthorStrategy(), max_repairs=1)
    assert result.repair_attempts == 1
    assert (result.world['meta']['day'], result.world['meta']['band']) == (2, 0)
    assert len(provider.calls) == 2


def test_resource_intent_uses_world_clock_when_scene_day_is_stale(game):
    import json
    from loop.resources import prepare_resources
    game.store.append(kernel_event('resources_configured', day=1, scene='inn', turn=0,
        summary='fixture', deltas={'subject': 'hero', 'resources': {
            'coins': {'initial': 10, 'type': 'integer', 'min': 0}}}))
    game.world = project(game.registry, game.store.iter_events())
    provider = FakeLLMProvider(json_responses=[{'op': 'none'}])
    prepare_resources(game.world, {'protagonist': 'hero', 'day': 99}, '观察', provider, 1)
    assert json.loads(provider.calls[0][1])['current_time'] == {'day': 1, 'band': 2}
