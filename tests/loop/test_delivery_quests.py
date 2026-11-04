"""Explicit delivery promises connect the task ledger to real item outcomes."""
import copy

import pytest

from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import _build_scene
from context.assembler import assemble_context
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy
from loop.turn import TurnRejected, run_turn


OBJECTIVE = {'kind': 'deliver', 'item': 'medicine', 'recipient': 'apothecary',
             'due': {'day': 1, 'band': 1}}


@pytest.fixture
def game(tmp_path, monkeypatch):
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup',
                 'run_density', '_run_demote_on_leave'):
        monkeypatch.setattr('loop.turn.' + name, lambda *a, **kw: [])
    engine = build_engine(tmp_path / 'campaign', embedder=None)
    _seed_demo_campaign(engine)
    location = _build_scene(engine)['location']
    for kind, delta in [
        ('character_created', {'id': 'apothecary', 'sketch': '正在等药的药师', 'goal': '救治病人', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'apothecary', 'to': location}),
        ('object_created', {'id': 'medicine', 'description': '封好的药包'}),
        ('item_transferred', {'item': 'medicine', 'to': 'protagonist'}),
    ]:
        engine.store.append(kernel_event(kind, day=1, scene=location, turn=0, summary='synthetic setup', deltas=delta))
    engine.world = project(engine.registry, engine.store.iter_events())
    yield engine
    engine.store.close()


def candidate(*, quests=None, items=None, bands=0):
    return {'narration': '你与药师交谈。', 'moves': [], 'places': [], 'cast': [], 'facts': [],
            'items': items or [], 'quests': quests or [],
            'clock': [{'advance': bool(bands), 'days': 0, 'bands': bands, 'reason': '本次行动耗时'}]}


def open_quest(game, objective=OBJECTIVE):
    game.provider = FakeLLMProvider(json_responses=[candidate(quests=[{
        'op': 'open', 'id': 'deliver_medicine', 'summary': '中午前把药包交给药师',
        'objective': copy.deepcopy(objective)}])])
    dashboard = PlayerDashboard(game, strategy=AuthorStrategy())
    dashboard.action(player_input='答应中午前把药包交给药师', action_id='accept',
                     expected_revision=dashboard.state()['revision'])
    return dashboard


def action(dashboard, proposal, *, text='递交药包', ident='deliver', prose='药师接过药包。'):
    dashboard.engine.provider = FakeLLMProvider(json_responses=[proposal], responses=[prose])
    return dashboard.action(player_input=text, action_id=ident,
                            expected_revision=dashboard.state()['revision'])


def test_open_delivery_objective_survives_storage_and_reopen(game):
    open_quest(game)
    assert game.world['systems']['lore']['lines']['deliver_medicine']['objective'] == OBJECTIVE
    game.store.close()
    reopened = build_engine(game.store.jsonl_path.parent, embedder=None)
    try:
        assert reopened.world['systems']['lore']['lines']['deliver_medicine']['objective'] == OBJECTIVE
        text = assemble_context(reopened.registry, reopened.world, _build_scene(reopened))
        assert 'medicine' in text and 'apothecary' in text and '第1天中午' in text
    finally:
        reopened.store.close()


def test_claiming_delivery_without_transferring_is_not_completion(game):
    open_quest(game)
    before = list(game.store.iter_events())
    p = FakeLLMProvider(json_responses=[candidate(quests=[{'op': 'resolve', 'id': 'deliver_medicine',
                                                        'summary': '药已送达'}])])
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '我说药已经送到了',
                 strategy=AuthorStrategy(), provider=p, max_repairs=0)
    assert list(game.store.iter_events()) == before


@pytest.mark.parametrize('bands,timing', [(0, 'on_time'), (1, 'on_time'), (2, 'late')])
def test_real_transfer_completes_task_even_when_author_omits_quests(game, bands, timing):
    dashboard = open_quest(game)
    response = action(dashboard, candidate(items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}], bands=bands))
    line = game.world['systems']['lore']['lines']['deliver_medicine']
    assert line['state'] == '了结'
    assert line['resolved']['resolution']['outcome'] == 'delivered'
    assert line['resolved']['resolution']['timing'] == timing
    assert response['state']['objectives']['active_quests'] == []
    assert len(response['state']['objectives']['completed_quests']) == 1
    assert game.world['systems']['ontology'].neighbors('medicine', 'held_by', 1) == ['apothecary']
    assert '【本回合已通过校验的结果】' in game.provider.calls[-1][1]
    assert timing in game.provider.calls[-1][1]


def test_late_pending_task_remains_actionable_and_visible(game):
    dashboard = open_quest(game)
    response = action(dashboard, candidate(bands=2), text='等到下午', prose='已经到了下午。')
    assert game.world['systems']['lore']['lines']['deliver_medicine']['state'] == '明'
    assert '逾期' in response['state']['objectives']['active_quests'][0]['summary']
    text = assemble_context(game.registry, game.world, _build_scene(game))
    assert '逾期' in text and '自动失败' in text


def test_abandonment_is_not_reported_as_success_and_requires_no_delivery(game):
    dashboard = open_quest(game)
    response = action(dashboard, candidate(quests=[{'op': 'resolve', 'id': 'deliver_medicine',
                        'outcome': 'abandoned', 'summary': '决定不接这趟送药的差事'}]), text='明确拒绝继续送药', prose='你说明不再送药，药师收回了托付。')
    assert game.world['systems']['lore']['lines']['deliver_medicine']['resolved']['resolution']['outcome'] == 'abandoned'
    assert '放弃' in response['state']['objectives']['completed_quests'][0]['summary']
    assert game.world['systems']['ontology'].neighbors('medicine', 'held_by', 1) == ['protagonist']


def test_undo_and_duplicate_do_not_duplicate_delivery_or_rewrite_prose(game):
    dashboard = open_quest(game)
    version = dashboard.state()['revision']
    result = action(dashboard, candidate(items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}]))
    game.provider = FakeLLMProvider(json_responses=[], responses=[])
    duplicate = dashboard.action(player_input='递交药包', action_id='deliver', expected_revision=version)
    assert duplicate['duplicate'] and duplicate['state']['narration'] == result['state']['narration']
    assert game.provider.calls == []
    dashboard.undo(expected_revision=dashboard.state()['revision'])
    assert game.world['systems']['lore']['lines']['deliver_medicine']['state'] == '明'
    assert game.world['systems']['ontology'].neighbors('medicine', 'held_by', 1) == ['protagonist']


def test_published_outcome_is_available_next_turn_without_raw_narration(game):
    dashboard = open_quest(game)
    action(dashboard, candidate(items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}]))
    world = copy.deepcopy(game.world)
    world['systems']['narrative'] = game.registry.owner_of_event('narration_recorded').empty_state()
    text = assemble_context(game.registry, world, _build_scene(game))
    assert '【任务·近期结局】' in text and '已交付' in text and 'deliver_medicine' in text


@pytest.mark.parametrize('objective', [
    {'kind': 'deliver', 'item': 'medicine'},
    {**OBJECTIVE, 'kind': 'invented'},
    {**OBJECTIVE, 'due': {'day': True, 'band': 1}},
    {**OBJECTIVE, 'due': {'day': 1, 'band': 4}},
    {**OBJECTIVE, 'item': 'missing_item'},
    {**OBJECTIVE, 'item': ['medicine']},
    {**OBJECTIVE, 'recipient': 'medicine'},
])
def test_invalid_objective_is_not_silently_discarded(game, objective):
    p = FakeLLMProvider(json_responses=[candidate(quests=[{'op': 'open', 'id': 'bad', 'summary': '送药', 'objective': objective}])])
    before = list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '答应送药',
                 strategy=AuthorStrategy(), provider=p, max_repairs=0)
    assert list(game.store.iter_events()) == before


def test_transferring_to_the_wrong_place_does_not_complete_task(game):
    dashboard = open_quest(game)
    action(dashboard, candidate(items=[{'op': 'transfer', 'item': 'medicine', 'to': _build_scene(game)['location']}]),
           text='把药包放在地上', prose='你把药包放在地上，还没有交给药师。')
    assert game.world['systems']['lore']['lines']['deliver_medicine']['state'] == '明'


@pytest.mark.parametrize('quests,items', [
    ([{'op': 'resolve', 'id': 'deliver_medicine', 'outcome': 'abandoned'}],
     [{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}]),
    ([{'op': 'resolve', 'id': 'deliver_medicine'}, {'op': 'resolve', 'id': 'deliver_medicine', 'outcome': 'abandoned'}],
     [{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}]),
    ([{'op': 'advance', 'id': 'deliver_medicine', 'objective': {**OBJECTIVE, 'recipient': 'protagonist'}}], []),
])
def test_conflicting_or_rewritten_delivery_conditions_are_rejected(game, quests, items):
    open_quest(game)
    before = list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '处理约定',
                 strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[candidate(quests=quests, items=items)]),
                 max_repairs=0)
    assert list(game.store.iter_events()) == before


def test_predating_possession_is_not_delivery_and_prior_actual_delivery_has_unknown_time(game):
    from kernel.quest_outcomes import bind_delivery_outcomes, delivery_errors
    from kernel.turncommit import TurnCommit
    open_quest(game)
    opened = game.world['systems']['lore']['lines']['deliver_medicine']['surfaced_turn']
    commit = TurnCommit.from_dict(candidate(quests=[{'op': 'resolve', 'id': 'deliver_medicine'}]))
    for turn, expected_error in [(opened - 1, True), (opened + 1, False)]:
        world = project(game.registry, [*game.store.iter_events(), kernel_event(
            'item_transferred', day=1, scene=_build_scene(game)['location'], turn=turn,
            summary='synthetic earlier transfer', deltas={'item': 'medicine', 'to': 'apothecary'})])
        assert bool(delivery_errors(commit, world)) is expected_error
        if not expected_error:
            settled = bind_delivery_outcomes(commit, world)
            assert settled.sections['quests'][0]['resolution']['timing'] == 'unknown'


def test_handoff_then_takeback_is_not_a_completed_delivery(game):
    from kernel.quest_outcomes import bind_delivery_outcomes
    from kernel.turncommit import TurnCommit
    open_quest(game)
    commit = TurnCommit.from_dict(candidate(items=[
        {'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'},
        {'op': 'transfer', 'item': 'medicine', 'to': 'protagonist'},
    ]))
    assert bind_delivery_outcomes(commit, game.world).sections['quests'] == []


def test_host_replaces_model_timing_before_final_prose(game):
    dashboard = open_quest(game)
    action(dashboard, candidate(bands=2, items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}],
           quests=[{'op': 'resolve', 'id': 'deliver_medicine', 'resolution': {'timing': 'on_time'}}]))
    result = game.world['systems']['lore']['lines']['deliver_medicine']['resolved']['resolution']
    assert result == {'outcome': 'delivered', 'timing': 'late', 'recorded_at': {'day': 1, 'band': 2}}
    assert '"timing": "late"' in game.provider.calls[-1][1]


@pytest.mark.parametrize('drop_section', ['items', 'quests'])
def test_final_event_materialization_cannot_lose_half_of_delivery(game, monkeypatch, drop_section):
    dashboard = open_quest(game)
    before = list(game.store.iter_events())
    owner = game.registry.owner_of_section(drop_section)
    monkeypatch.setattr(owner, 'to_events', lambda *a, **kw: [])
    with pytest.raises(TurnRejected):
        action(dashboard, candidate(items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}]))
    assert list(game.store.iter_events()) == before


def test_recent_outcomes_use_closure_order_and_do_not_disclose_unseen_world_endings(game):
    events = list(game.store.iter_events())
    for index in range(8):
        lid = f'quest-{7-index}'  # deliberately reverse lexical and closure order
        events.extend([
            kernel_event('quest_opened', day=1, scene='s', turn=1, summary='open',
                         deltas={'id': lid, 'summary': f'公开托付{index}'}),
            kernel_event('quest_resolved', day=1, scene='s', turn=2+index, summary='close',
                         deltas={'id': lid, 'by': 'player', 'summary': f'已办妥{index}'}),
        ])
    events.extend([
        kernel_event('quest_created', day=1, scene='s', turn=1, summary='hidden',
                     deltas={'id': 'hidden', 'state': '暗', 'summary': '秘密托付'}),
        kernel_event('quest_world_resolved', day=1, scene='s', turn=12, summary='hidden ending',
                     deltas={'id': 'hidden', 'summary': '秘密世界结局'}),
        kernel_event('quest_opened', day=1, scene='s', turn=13, summary='public start',
                     deltas={'id': 'demoted', 'summary': '旧的公开任务'}),
        kernel_event('quest_demoted', day=1, scene='s', turn=14, summary='offscreen', deltas={'id': 'demoted'}),
        kernel_event('quest_world_resolved', day=1, scene='s', turn=15, summary='unseen ending',
                     deltas={'id': 'demoted', 'summary': '玩家尚不知道的新结局'}),
    ])
    world = project(game.registry, events)
    text = game.registry.owner_of_section('quests').inject({}, world).text
    assert '已办妥0' not in text and '已办妥1' not in text
    assert text.index('已办妥7') < text.index('已办妥2')
    assert text.count('已办妥') == 6
    assert '秘密' not in text and '玩家尚不知道' not in text


def test_no_deadline_delivery_has_no_invented_lateness(game):
    dashboard = open_quest(game, {key: value for key, value in OBJECTIVE.items() if key != 'due'})
    action(dashboard, candidate(bands=6, items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}]))
    result = game.world['systems']['lore']['lines']['deliver_medicine']['resolved']['resolution']
    assert result['timing'] == 'unscheduled' and result['recorded_at'] == {'day': 2, 'band': 2}


def test_one_unique_delivery_can_satisfy_two_promises_with_different_deadlines(game):
    dashboard = open_quest(game)
    action(dashboard, candidate(quests=[{'op': 'open', 'id': 'second_promise', 'summary': '再次确认下午前交药',
           'objective': {**OBJECTIVE, 'due': {'day': 1, 'band': 2}}}]), ident='second', text='确认第二个约定')
    action(dashboard, candidate(bands=2, items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}]))
    lines = game.world['systems']['lore']['lines']
    assert lines['deliver_medicine']['resolved']['resolution']['timing'] == 'late'
    assert lines['second_promise']['resolved']['resolution']['timing'] == 'on_time'
    # One physical object changed hands once; this feature never grants rewards.
    assert sum(e['type'] == 'item_transferred' and e['deltas'].get('to') == 'apothecary'
               for e in game.store.iter_events()) == 1


def test_delivery_promise_is_accepted_before_the_delivering_turn(game):
    proposal = candidate(quests=[{'op': 'open', 'id': 'new_promise', 'summary': '送药', 'objective': OBJECTIVE}],
                         items=[{'op': 'transfer', 'item': 'medicine', 'to': 'apothecary'}])
    before = list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '接任务并交药',
                 strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[proposal]), max_repairs=0)
    assert list(game.store.iter_events()) == before
