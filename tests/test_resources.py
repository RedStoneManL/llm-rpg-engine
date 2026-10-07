import pytest
from app.engine import build_engine, rewind
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.turn import run_turn, TurnRejected
from loop.strategy import AuthorStrategy


@pytest.fixture
def game(tmp_path,monkeypatch):
    e=build_engine(tmp_path/'resources')
    for name in ['digest_fleet','run_director','run_cascade','run_catchup','run_lore','run_density','_run_demote_on_leave']:
        monkeypatch.setattr('loop.turn.'+name,lambda *a,**kw:[])
    data=[('entity_created',{'id':'hero','etype':'Person','tier':'tracked'}),
          ('resources_configured',{'subject':'hero','resources':{'oxygen':{'initial':12,'type':'integer','min':0}}})]
    e.store.append_many([kernel_event(t,day=1,scene='room',turn=0,summary='setup',deltas=d) for t,d in data])
    e.world=project(e.registry,e.store.iter_events())
    yield e
    e.store.close()


@pytest.mark.parametrize('amount,expected,status',[(3,9,'spent'),(100,12,'insufficient')])
def test_resolve_before_narrating_and_undo_whole_action(game,amount,expected,status):
    p=FakeLLMProvider(json_responses=[{'op':'spend','resource':'oxygen','amount':amount}, {'narration':'阀门回到原位。'}])
    answer=run_turn(game.registry,game.store,game.world,_build_scene(game),'使用氧气',provider=p,strategy=AuthorStrategy())
    assert answer.world['systems']['ontology'].value_at('hero','oxygen',1)==expected
    assert answer.world['systems']['resources']['last_resolution']['outcome']==status
    assert len({e['turn'] for e in answer.events})==1
    game.world=answer.world
    rewind(game,answer.events[0]['turn'])
    assert game.world['systems']['ontology'].value_at('hero','oxygen',1)==12


def test_narrator_cannot_double_debit_or_debit_a_failed_purchase(game):
    p=FakeLLMProvider(json_responses=[{'op':'spend','resource':'oxygen','amount':100},
        {'narration':'我花掉三单位。','facts':[{'subject':'hero','predicate':'oxygen','value':9}]}])
    before=list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry,game.store,game.world,_build_scene(game),'使用100单位氧气',
                 provider=p,strategy=AuthorStrategy(),max_repairs=0)
    assert list(game.store.iter_events())==before


def test_prose_failure_does_not_charge_prepared_cost(game):
    class FailsAfterIntent(FakeLLMProvider):
        def complete_messages(self,*args,**kwargs):
            if self.calls: raise ConnectionError('narration failed')
            return super().complete_messages(*args,**kwargs)
    p=FailsAfterIntent(json_responses=[{'op':'spend','resource':'oxygen','amount':3}])
    before=list(game.store.iter_events())
    with pytest.raises(ConnectionError):
        run_turn(game.registry,game.store,game.world,_build_scene(game),'使用3单位氧气',provider=p,strategy=AuthorStrategy())
    assert list(game.store.iter_events())==before


def test_explicit_wait_is_repaired_to_absolute_target(game):
    p=FakeLLMProvider(json_responses=[{'op':'none','wait_until':{'day':2,'band':1}},
        {'narration':'你休息到约定的时间。','clock':[{'advance':False,'days':0,'bands':0,'reason':'wrong'}]},
        {'clock':[{'advance':True,'days':1,'bands':1,'reason':'次日中午'}]}])
    answer=run_turn(game.registry,game.store,game.world,_build_scene(game),'等到明天中午',
        provider=p,strategy=AuthorStrategy())
    assert (answer.world['meta']['day'],answer.world['meta']['band'])==(2,1)
    assert answer.repair_attempts==1


@pytest.fixture
def shared_resources(game):
    data = [
        ('entity_created', {'id': 'merchant', 'etype': 'Person'}),
        ('resources_configured', {'subject': 'merchant', 'resources': {
            'coins': {'initial': 37, 'type': 'integer', 'min': 0}}}),
        ('entity_created', {'id': 'observer', 'etype': 'Person'}),
    ]
    game.store.append_many([
        kernel_event(kind, day=1, scene='room', turn=0, summary='setup', deltas=deltas)
        for kind, deltas in data
    ])
    game.world = project(game.registry, game.store.iter_events())
    return game


@pytest.mark.parametrize('protagonist', ['hero', 'observer'])
def test_other_registered_owner_cannot_be_debited(shared_resources, protagonist):
    game = shared_resources
    scene = {**_build_scene(game), 'protagonist': protagonist}
    responses = ([{'op': 'none'}] if protagonist == 'hero' else []) + [{
        'narration': '商人整理柜台。',
        'facts': [{'subject': 'merchant', 'predicate': 'coins', 'value': 30}],
    }]
    strategy = AuthorStrategy()
    before = list(game.store.iter_events())
    revision = game.store.revision
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, scene, '观察柜台',
                 provider=FakeLLMProvider(json_responses=responses),
                 strategy=strategy, max_repairs=0)
    assert list(game.store.iter_events()) == before
    assert game.store.revision == revision
    assert getattr(strategy, '_thread', None) is None


def test_other_owner_repair_does_not_disclose_their_balance(shared_resources):
    game = shared_resources
    provider = FakeLLMProvider(json_responses=[
        {'op': 'spend', 'resource': 'oxygen', 'amount': 3},
        {'narration': '你调整呼吸，商人整理柜台。', 'facts': [
            {'subject': 'merchant', 'predicate': 'coins', 'value': 30}]},
        {'facts': []},
    ])
    result = run_turn(game.registry, game.store, game.world, _build_scene(game),
                      '使用3单位氧气', provider=provider, strategy=AuthorStrategy())
    assert result.repair_attempts == 1
    graph = result.world['systems']['ontology']
    assert graph.value_at('hero', 'oxygen', 1) == 9
    assert graph.value_at('merchant', 'coins', 1) == 37
    # Repair hints are not an extra read channel into another owner's balance.
    from kernel.turncommit import TurnCommit
    from loop.resources import validate_resources
    errors = validate_resources(TurnCommit('观察', {'facts': [
        {'subject': 'merchant', 'predicate': 'coins', 'value': 30}]}),
        {('merchant', 'coins'): 37})
    assert errors and '37' not in errors[0].hint
    game.world = result.world
    rewind(game, result.events[0]['turn'])
    assert game.world['systems']['ontology'].value_at('hero', 'oxygen', 1) == 12
    assert game.world['systems']['ontology'].value_at('merchant', 'coins', 1) == 37


@pytest.mark.parametrize('report_events', [False, True])
def test_backstage_cannot_change_other_owner_even_if_it_omits_event_report(
        shared_resources, monkeypatch, report_events):
    game = shared_resources
    def rewrite_balance(registry, store, *args, **kwargs):
        ev = kernel_event('fact_asserted', day=1, scene='room', turn=store.next_turn(),
                          summary='unresolved reward', deltas={
                              'subject': 'merchant', 'predicate': 'coins', 'value': 99})
        store.append(ev)
        return [ev] if report_events else []
    monkeypatch.setattr('loop.turn.run_density', rewrite_balance)
    before = list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '观察柜台',
                 provider=FakeLLMProvider(json_responses=[
                     {'op': 'none'}, {'narration': '商人整理柜台。'}]),
                 strategy=AuthorStrategy())
    assert list(game.store.iter_events()) == before


def test_unregistered_facts_and_unchanged_resources_remain_allowed(shared_resources, tmp_path):
    game = shared_resources
    scene = {**_build_scene(game), 'protagonist': 'observer'}
    provider = FakeLLMProvider(json_responses=[{
        'narration': '你记下商人的招牌。', 'facts': [
            {'subject': 'merchant', 'predicate': 'coins', 'value': 37},
            {'subject': 'merchant', 'predicate': 'sign', 'value': '杂货铺'},
        ]}])
    result = run_turn(game.registry, game.store, game.world, scene, '观察招牌',
                      provider=provider, strategy=AuthorStrategy())
    assert len(provider.calls) == 1  # No intent call for a resource-free actor.
    reopened = build_engine(tmp_path / 'resources', provider=FakeLLMProvider())
    try:
        graph = reopened.world['systems']['ontology']
        assert graph.value_at('merchant', 'coins', 1) == 37
        assert graph.value_at('merchant', 'sign', 1) == '杂货铺'
        assert graph.value_at('hero', 'oxygen', 1) == 12
        assert list(reopened.store.iter_events()) == list(game.store.iter_events())
    finally:
        reopened.store.close()
    game.world = result.world
    rewind(game, result.events[0]['turn'])
    assert game.world['systems']['ontology'].value_at('merchant', 'sign', 1) is None
    assert game.world['systems']['ontology'].value_at('merchant', 'coins', 1) == 37


@pytest.mark.parametrize('legacy_rules', [None, [], 'legacy metadata'])
def test_unrelated_legacy_rule_metadata_does_not_block_actions(game, legacy_rules):
    game.store.append(kernel_event('entity_created', day=1, scene='room', turn=0,
        summary='legacy entity', deltas={'id': 'legacy', 'etype': 'Person',
                                        'attrs': {'fact_rules': legacy_rules}}))
    game.world = project(game.registry, game.store.iter_events())
    result = run_turn(game.registry, game.store, game.world, _build_scene(game),
        '调整阀门', strategy=AuthorStrategy(), provider=FakeLLMProvider(json_responses=[
            {'op': 'none'}, {'narration': '阀门回到原位。'}]))
    assert result.world['systems']['ontology'].value_at('hero', 'oxygen', 1) == 12


def test_private_resource_snapshot_never_enters_provider_messages(shared_resources):
    import copy
    import json
    game = shared_resources
    sentinel = 846392751
    game.store.append(kernel_event('fact_asserted', day=1, scene='room', turn=0,
        summary='private balance', deltas={'subject': 'merchant', 'predicate': 'coins',
                                         'value': sentinel, 'secrecy': 'secret'}))
    game.world = project(game.registry, game.store.iter_events())
    class RecordingProvider(FakeLLMProvider):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.messages = []
        def complete_messages(self, messages, **kwargs):
            self.messages.append(copy.deepcopy(messages))
            return super().complete_messages(messages, **kwargs)
    provider = RecordingProvider(json_responses=[
        {'narration': '你看向远处的商店。', 'facts': [
            {'subject': 'merchant', 'predicate': 'coins', 'value': 30}]},
        {'facts': []},
    ])
    result = run_turn(game.registry, game.store, game.world,
        {**_build_scene(game), 'protagonist': 'observer'}, '观察商店',
        strategy=AuthorStrategy(), provider=provider)
    assert result.repair_attempts == 1
    assert str(sentinel) not in json.dumps(provider.messages, ensure_ascii=False)
    assert result.world['systems']['ontology'].value_at('merchant', 'coins', 1) == sentinel
