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


def test_investigation_needs_a_post_acceptance_observation_of_the_right_feature():
    with game() as (engine, dash):
        goal = {'kind':'investigate','place':INN,'feature':'北墙梯子'}
        act(dash, '接下调查', propose(quests=[{'op':'open','id':'inspect','summary':'核实北墙梯子','objective':goal}]))
        resolution = {'op':'resolve','id':'inspect','summary':'已查验'}
        with pytest.raises(TurnRejected):
            act(dash, '还没有找到', propose(quests=[resolution]), 'unsupported')
        sources = {}
        for feature, sentence in [('北墙梯子','北墙有梯子'),('西墙水井','西墙有水井')]:
            observation = {'areas':[INN], 'level':1,'public_observation':True,'visibility':'public',
                           'observation_kind':'stable_layout','observation_range':'整个院子',
                           'feature':feature,'summary':sentence}
            if feature == '北墙梯子':
                observation['investigation'] = {'quest':'inspect', 'result':'confirmed'}
            act(dash, '核验' + feature, propose(world=[observation]), feature)
            event = next(e for e in engine.store.iter_events() if e['summary'] == sentence)
            sources[feature] = {'source_event_id':event['id'],'quote':sentence}
        assert {'北墙有梯子','西墙有水井'} <= {o['summary'] for o in dash.state()['observations']}
        with pytest.raises(TurnRejected):
            act(dash,'结案',propose(quests=[{**resolution,'evidence':sources['西墙水井']}]),'wrong-feature')
        act(dash,'结案',propose(quests=[{**resolution,'evidence':sources['北墙梯子']}]),'verified')
        resolved = engine.world['systems']['lore']['lines']['inspect']['resolved']
        assert resolved['evidence'] == sources['北墙梯子']
        dash.undo(expected_revision=engine.store.revision)
        assert engine.world['systems']['lore']['lines']['inspect']['state'] == '明'


def test_input_is_dated_before_wait_and_fact_versions_keep_the_turn():
    with game() as (engine, dash):
        act(dash, '我一直等到明天中午。', propose(clock=[{'advance':True,'days':1,'bands':1,'reason':'等待'}]))
        source = next(row for row in dash.recall('一直等到')['sources'] if row['kind']=='玩家输入')
        assert source['day'] == 1
        graph = engine.world['systems']['ontology']
        graph.assert_fact(HERO,'mood','镇定',day=2,turn=4,source_event='before')
        graph.assert_fact(HERO,'mood','焦虑',day=2,turn=6,source_event='after')
        assert graph.fact_at_turn(HERO,'mood',2,5).source_event == 'before'
        assert graph.fact_at_turn(HERO,'mood',2,6).value == '焦虑'
