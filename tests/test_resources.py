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
