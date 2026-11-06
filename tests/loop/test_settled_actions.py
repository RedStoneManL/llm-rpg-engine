"""Ordinary controller regressions against the RCV-130 counterexamples."""
import pytest

from docs.verification.rcv130.probe import game, seed, act, propose, HERO, INN, MARKET, LAMP
from loop.turn import TurnRejected


def transfer(to):
    return {'op':'transfer','item':LAMP,'to':to}


def test_custody_is_sequential_and_recoverable_after_replay():
    with game() as (engine,dash):
        act(dash,'放下灯',propose(items=[transfer(INN)]))
        assert engine.world['systems']['ontology'].relations_at(LAMP,'held_by',1)[0].attrs['dropped_by'] == HERO
        seed(engine,'character_created',id='guard',sketch='守卫',goal='巡查',tier='tracked')
        seed(engine,'entity_moved',who='guard',to=INN)
        before=engine.store.revision
        with pytest.raises(TurnRejected):
            act(dash,'捡起并给守卫两次',propose(items=[transfer(HERO),transfer('guard'),transfer('guard')]),'bad')
        assert engine.store.revision == before
        act(dash,'捡起灯',propose(items=[transfer(HERO)]),'pick')
        assert dash.state()['inventory'][0]['id'] == LAMP
        dash.undo(expected_revision=dash.state()['revision'])
        assert dash.state()['inventory'] == []


def test_single_journey_single_clock_and_explicit_wait():
    with game() as (engine,dash):
        with pytest.raises(TurnRejected):
            act(dash,'去集市',propose(moves=[{'who':HERO,'to':MARKET,'arrive_day':9}]))
        with pytest.raises(TurnRejected):
            act(dash,'先去市场再回来',propose(moves=[{'who':HERO,'to':MARKET},{'who':HERO,'to':INN}]),'two')
        with pytest.raises(TurnRejected):
            act(dash,'我一直等到明天中午。',propose(),'wait')
        result=act(dash,'我一直等到明天中午。',propose(clock=[{'advance':True,'days':1,'bands':1,'reason':'等到约定时刻'}]),'fixed')
        assert result['state']['scene']['time'] == {'day':2,'band':'中午'}


def test_purchase_is_an_atomic_debit_and_delivery_even_on_retry():
    with game() as (engine,dash):
        seed(engine,'resources_configured',subject=HERO,resources={'coins':{'initial':8,'type':'integer','min':0}})
        options=dict(prefix=[{'op':'buy','resource':'coins','amount':3,'item':'绳索'}])
        rev=engine.store.revision
        answer=act(dash,'我付三枚钱买绳索。',propose('你把绳索收入背包。'),**options)
        assert [i['label'] for i in answer['state']['inventory']].count('绳索') == 1
        assert engine.world['systems']['ontology'].value_at(HERO,'coins',1)==5
        assert dash.action(player_input='我付三枚钱买绳索。',action_id='t1',expected_revision=rev)['duplicate']
        assert sum(e['type']=='purchase_received' for e in engine.store.iter_events())==1
        dash.undo(expected_revision=dash.state()['revision'])
        assert engine.world['systems']['ontology'].value_at(HERO,'coins',1)==8
        assert all(i['label']!='绳索' for i in dash.state()['inventory'])


def test_quest_identity_cannot_be_reallocated_inside_an_action():
    with game() as (engine,dash):
        before=engine.store.revision
        with pytest.raises(TurnRejected):
            act(dash,'接两项调查',propose(quests=[{'op':'open','id':'a','summary':'寻狗'},{'op':'open','id':'a','summary':'寻书'}]))
        assert engine.store.revision==before


def test_unaffordable_purchase_does_not_create_goods():
    with game() as (engine,dash):
        seed(engine,'resources_configured',subject=HERO,resources={'coins':{'initial':2,'type':'integer','min':0}})
        result=act(dash,'我付三枚钱买绳索。',propose('钱不够，绳索仍在摊上。'),
                   prefix=[{'op':'buy','resource':'coins','amount':3,'item':'绳索'}])
        assert all(i['label'] != '绳索' for i in result['state']['inventory'])
        assert engine.world['systems']['ontology'].value_at(HERO,'coins',1)==2
