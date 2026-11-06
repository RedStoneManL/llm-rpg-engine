"""Reproduce review candidates on fresh synthetic saves; never opens a user campaign."""
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.engine import build_engine
from app.gui import _seed_demo_campaign, PlayerDashboard
from app.play import _build_scene
from app.player_recall import player_recall
from context.place_observations import public_place_observations
from engine.director import thread_due_scores
from engine.oracle import Oracle
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.fleet import digest_fleet
from loop.strategy import AuthorStrategy
from loop.time import run_catchup

INN, MARKET = 'stonebridge_inn', 'stonebridge_market'
HERO, LAMP = 'protagonist', 'old_brass_lantern'
OUTPUT = {}


@contextmanager
def game():
    with TemporaryDirectory() as folder, ExitStack() as stack:
        # Run real ordinary controller/statement/commit/publish logic; background
        # subsystems have their own isolated cases below.
        for name in ('digest_fleet','run_cascade','run_catchup','run_director','run_density','run_lore','_run_demote_on_leave'):
            stack.enter_context(patch('loop.turn.' + name, return_value=[]))
        engine = build_engine(folder, provider=FakeLLMProvider(), embedder=None)
        stack.callback(engine.store.close)
        _seed_demo_campaign(engine)
        yield engine, PlayerDashboard(engine, strategy=AuthorStrategy())


def seed(engine, event_type, **delta):
    engine.store.append(kernel_event(event_type, day=1, scene=INN, turn=0, summary='合成测试准备', deltas=delta))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world['_revision'] = engine.store.revision


def propose(narration='你停下来观察。', **sections):
    return {'narration':narration, 'moves':[], 'places':[], 'cast':[], 'facts':[],
            'clock':[{'advance':False,'reason':'短暂动作'}], **sections}


def act(dashboard, action, proposal, key='t1', prefix=()):
    dashboard.engine.provider = FakeLLMProvider(json_responses=[*prefix,proposal], responses=[proposal['narration']])
    return dashboard.action(player_input=action, action_id=key, expected_revision=dashboard.state()['revision'])


def time_probes():
    with game() as (engine, dash):
        result = act(dash,'我一直等到明天中午。',propose('你开始等候。'))
        OUTPUT['wait_without_resource'] = result['state']['scene']['time']
    with game() as (engine, dash):
        seed(engine,'resources_configured',subject=HERO,resources={'coins':{'initial':8,'type':'integer','min':0}})
        intent = {'op':'none','wait_until':{'day':2,'band':1}}
        result = act(dash,'去市场，明天中午准时到。',propose('你准时到了。',
                     moves=[{'who':HERO,'to':MARKET,'arrive_day':9}],
                     clock=[{'advance':True,'days':1,'bands':1,'reason':'次日中午'}]),prefix=[intent])
        OUTPUT['two_clocks'] = {'requested':intent['wait_until'],'actual':result['state']['scene']['time'],
                                'clock_event_day':[e['day'] for e in engine.store.iter_events() if e['type']=='clock_advanced']}
    with game() as (engine, dash):
        seed(engine,'place_created',id='quarry',level=2,seed='采石场',kind='settlement')
        seed(engine,'place_linked',a=INN,b='quarry',travel_cost=19)
        result = act(dash,'先去市场，再直接从市场去采石场。',propose('你依次到了市场和采石场。',
                      moves=[{'who':HERO,'to':MARKET},{'who':HERO,'to':'quarry'}]))
        OUTPUT['travel_chain'] = {'no_direct_edge': 'quarry' not in engine.world['systems']['ontology'].neighbors(MARKET,'adjacent_to',1),
            'from_market':[p for p,_ in engine.world['systems']['ontology'].relation_attrs_at(MARKET,'adjacent_to',1)],
            'recorded_destinations':[e['deltas']['to'] for e in engine.store.iter_events() if e['type']=='entity_moved' and e['turn']==1],
            'final':result['state']['scene']['location']['id'],'time':result['state']['scene']['time']}


def inventory_probes():
    with game() as (engine, dash):
        move = lambda to:[{'op':'transfer','item':LAMP,'to':to}]
        act(dash,'把铜灯放到脚边。',propose('你放下铜灯。',items=move(INN)))
        revision = engine.store.revision
        try:
            act(dash,'把刚才放下的自己的铜灯捡起来。',propose('你捡起铜灯。',items=move(HERO)),'t2')
            outcome='accepted'
        except Exception as exc:
            outcome=type(exc).__name__ + ': ' + str(exc)
        OUTPUT['drop_pickup']={'pickup':outcome,'owner':engine.world['systems']['ontology'].neighbors(LAMP,'held_by',1),
                               'pickup_changed_revision':revision!=engine.store.revision}
    with game() as (engine, dash):
        for pid in ('mara','lane'):
            seed(engine,'character_created',id=pid,sketch=pid,goal='等灯',tier='tracked')
            seed(engine,'entity_moved',who=pid,to=INN)
        result=act(dash,'我只有一盏灯，却试着分别给两个人。',propose('你把灯先后给了两个人。',items=[
            {'op':'transfer','item':LAMP,'to':'mara'},{'op':'transfer','item':LAMP,'to':'lane'}]))
        OUTPUT['double_gift']={'accepted':result['ok'],'final_holder':engine.world['systems']['ontology'].neighbors(LAMP,'held_by',1),
              'recorded_recipients':[e['deltas']['to'] for e in engine.store.iter_events() if e['type']=='item_transferred' and e['turn']==1]}
    with game() as (engine, dash):
        seed(engine,'resources_configured',subject=HERO,resources={'coins':{'initial':8,'type':'integer','min':0}})
        result=act(dash,'我付三枚钱买绳索。',propose('你付了三枚钱，把绳子放入背包。'),prefix=[{'op':'spend','resource':'coins','amount':3}])
        OUTPUT['buy_without_item']={'coins':engine.world['systems']['ontology'].value_at(HERO,'coins',1),
             'inventory':result['state']['inventory'],'narration':result['state']['narration'][-1]}


def quest_probes():
    with game() as (engine, dash):
        result=act(dash,'我同时答应两件不同的事。',propose('你答应了两件事。',quests=[
            {'op':'open','id':'promise','summary':'寻找走失的狗'}, {'op':'open','id':'promise','summary':'归还旧账簿'}]))
        OUTPUT['same_id_open']={'open_events':sum(e['type']=='quest_opened' for e in engine.store.iter_events()),
                              'ledger':result['state']['objectives']['active_quests']}
    with game() as (engine, dash):
        result=act(dash,'我看看附近是否有线索。',propose('你没有发现钥匙。', quests=[{'op':'open','id':'find_key','summary':'找出门钥匙'}]))
        result=act(dash,'我只看了一眼，尚未找到钥匙。',propose('你说这件事办妥了。',quests=[{'op':'resolve','id':'find_key','summary':'钥匙已找回'}]),'t2')
        OUTPUT['freeform_completion']={'ledger':result['state']['objectives']['completed_quests'], 'inventory':result['state']['inventory']}


def story_probes():
    with game() as (engine,dash):
        result=act(dash,'我只观察，不走动。',propose('你抵达石桥集市，把旧铜灯卖给摊主。'))
        OUTPUT['valid_structure_wrong_prose']={'scene':result['state']['scene']['location']['id'],'inventory':result['state']['inventory'],
                                             'text':result['state']['narration'][-1]}
        for k in (2,3):
            result=act(dash,'继续观察。',propose('你继续观察，暂时没有线索。'),str(k))
        OUTPUT['short_repeat']=result['state']['narration'][-2:]
        unique='柿红龙纹暗号'
        for n in range(9): seed(engine,'narration_recorded',scene='s'+str(n),text=unique if n==0 else '过场'+str(n))
        OUTPUT['recall_old_line']={'player_hits':player_recall(engine.registry,engine.world,_build_scene(engine),unique),
            'in_gui_last_eight':any(unique in s for s in dash.state()['narration']),
            'original_in_sqlite':any(e['type']=='narration_recorded' and e['deltas'].get('text')==unique for e in engine.store.iter_events())}


def background_probes():
    with game() as (engine,dash):
        seed(engine,'character_created',id='guard',sketch='守卫',goal='巡逻',tier='tracked')
        rows=[]
        for i in range(16):
            event=kernel_event('character_evolved',day=1,scene=INN,turn=1,summary='守卫换岗',deltas={'id':'guard','predicate':'mood','value':str(i)})
            engine.store.append(event); rows.append(event)
        world=project(engine.registry,engine.store.iter_events())
        bad='{"predicate":"knows:bridge.password","value":"朱砂"}'
        p=FakeLLMProvider(responses=[bad]); count=0
        for row in rows:
            count+=sum(e['type']=='character_evolved' for e in digest_fleet(engine.registry,engine.store,[row],world,provider=p,record_narration=False))
        batch=digest_fleet(engine.registry,engine.store,rows,world,provider=p,record_narration=False)
        world=project(engine.registry,engine.store.iter_events())
        OUTPUT['reflection']={'same_events_split_arc_count':count,'single_batch_predicates':[e['deltas'].get('predicate') for e in batch if e['type']=='character_evolved'],
                              'stored_belief':world['systems']['ontology'].value_at('guard','knows:bridge.password',1)}
    with game() as (engine,dash):
        ids=[f'smith{i}' for i in range(5)]
        for pid in ids: seed(engine,'character_created',id=pid,sketch=pid,goal='开店',tier='tracked')
        world=engine.world; world['meta']['day']=5
        scene={'present':ids,'day':5,'id':INN,'protagonist':HERO}
        p=FakeLLMProvider(json_responses=[{'changed':False}])
        first=run_catchup(engine.registry,engine.store,world,{'present':[]},scene,provider=p)
        world=project(engine.registry,engine.store.iter_events()); next_visit=run_catchup(engine.registry,engine.store,world,scene,scene,provider=p)
        OUTPUT['catchup_budget']={'processed':[e['deltas']['id'] for e in first], 'next_turn':len(next_visit),
             'unprocessed_last_update':world['systems']['ontology'].get_entity(ids[-1]).attrs['last_update']}
    threads={'clue':{'status':'活跃','speed':'快','last_advanced_scene':'A'}}
    def due(end):return thread_due_scores([{'scene':s} for s in ['A','B','C','D','E',end]],threads,Oracle(0))[0][1]
    OUTPUT['director_revisit']={'last_new_scene':due('F'),'revisit_old_scene_id':due('A')}


def other_probes():
    from kernel.turncommit import TurnCommit
    from loop.entity_resolve import augment_unresolved_refs
    from loop.fleet import backstop_quests
    from loop.lore import run_lore
    from loop.lore_disclosure import station_push_fragment
    from loop.import_sillytavern import convert_sillytavern
    with game() as (engine,dash):
        seed(engine,'character_created',id='town_speaker',op='create',name='林月',sketch='抱着陶罐的旅人',goal='回家',tier='tracked')
        seed(engine,'entity_moved',who='town_speaker',to=INN)
        world=engine.world
        candidate=TurnCommit.from_dict(propose(moves=[{'who':'林月','to':INN}]))
        new_ids=augment_unresolved_refs(candidate,world,scene=INN,day=1)
        OUTPUT['name_resolution']={'stored_name':world['systems']['ontology'].value_at('town_speaker','name',1),
                                   'resolved_moves':candidate.sections['moves'],'new_ids':new_ids}
        seed(engine,'character_evolved',id='town_speaker',predicate='state',value='已死亡')
        state=dash.state()
        OUTPUT['dead_speaker']={'present':state['present_people'],'recipients':state['speech_recipients']}
    with game() as (engine,dash):
        seed(engine,'character_created',id='mara',op='create',name='玛拉',sketch='看守',goal='巡查',tier='tracked')
        seed(engine,'entity_moved',who='mara',to=INN)
        seed(engine,'fact_asserted',subject=MARKET,predicate='gate_status',value='实际封闭',secrecy='public')
        seed(engine,'knowledge_set',knower='mara',fact_key=MARKET+'.gate_status',value='只知开放')
        key=MARKET+'.gate_status'
        results={}
        for via in ('mara','玛拉亲口'):
            try:
                act(dash,'问玛拉是否通行。',propose('玛拉说已经封闭。',knowledge=[{'op':'told','knower':HERO,'fact_key':key,'value':'实际封闭','via':via}]),'via-'+via)
                results[via]='accepted'
            except Exception as exc:
                results[via]=type(exc).__name__
        graph=engine.world['systems']['ontology']
        OUTPUT['speaker_alias']={'by_via':results,'mara_belief':graph.value_at('mara','knows:'+key,1),
                               'protagonist_belief':graph.value_at(HERO,'knows:'+key,1)}
    with game() as (engine,dash):
        for i,line in enumerate(('北墙有一架梯子','西墙有一口井')):
            act(dash,'观察整个院子。',propose(line,world=[{'level':1,'areas':[INN],'public_observation':True,'visibility':'public',
                     'observation_kind':'stable_layout','observation_range':'整个院子','summary':line}]),'obs-'+str(i))
        OUTPUT['layout_compaction']={'at_site':[i['summary'] for i in public_place_observations(engine.world,_build_scene(engine))],
                                  'history_count':sum(e['type']=='world_change' and e['deltas'].get('public_observation') is True for e in engine.store.iter_events())}
    with game() as (engine,dash):
        event=backstop_quests(engine.world,[kernel_event('character_evolved',day=1,turn=1,scene=INN,summary='守卫欠一份人情',deltas={'id':'guard'})])
        engine.store.append(event); world=project(engine.registry,engine.store.iter_events()); world['meta']['day']=20
        change=run_lore(engine.registry,engine.store,world,protagonist=HERO,previous_day=1)
        row=world['systems']['lore']['lines'][event['deltas']['id']]
        OUTPUT['backstop_flag']={'state':row['state'],'anchor':row.get('anchor'),'stages':row['stages'],'lifespan':row['lifespan_days'],
                         'local_clue':station_push_fragment(engine.registry,world,{'protagonist':HERO}), 'changes_day20':len(change)}
    p=FakeLLMProvider(json_responses=[{'world_premise':{'tone':'朴素'}}])
    convert_sillytavern(p, world_book={'entries':{'a':{'disable':True,'key':['见到铃铛时'],'content':'橙色测试禁用条目'}}})
    OUTPUT['world_book_switch']={'disabled_content_in_translation_request':any('橙色测试禁用条目' in user for _,user in p.calls),
                             'activation_key_in_request':any('见到铃铛时' in user for _,user in p.calls)}


def normal_reflection_probe():
    with game() as (engine, dash), patch('loop.turn.digest_fleet', digest_fleet):
        facts = [{'subject':HERO,'predicate':'observed_'+str(i),'value':str(i),'secrecy':'public'} for i in range(16)]
        before = engine.store.revision
        candidate = propose('你做完了记录。', facts=facts)
        p = FakeLLMProvider(json_responses=[candidate],responses=['{"predicate":"arc","value":"主角越来越细心"}'])
        engine.provider = p
        try:
            dash.action(player_input='记录16个观察结果',action_id='reflection',expected_revision=before)
            result = 'accepted'
        except Exception as exc:
            result = type(exc).__name__ + ': ' + str(exc)
        OUTPUT['normal_reflection']={'result':result,'called_reflection':any('叙事弧' in sys for sys,_ in p.calls),
                                     'revision_unchanged':before == engine.store.revision}


def terra_controls():
    from llm.tools import build_tool_registry
    from loop.lore import create_lore_line, run_lore
    from loop.bootstrap import _build_world_summary
    with game() as (engine,dash):
        seed(engine,'character_created', id='potter', op='create', name='青禾', sketch='正在捏碗的陶工', goal='开窑', tier='mentioned')
        seed(engine,'entity_moved', who='potter', to=INN)
        scene = _build_scene(engine)
        tools = build_tool_registry(engine.registry,engine.world,scene,dm=True)
        OUTPUT['walkon_viewpoint']={'ui':dash.state()['present_people'],
                     'tool':tools.execute('characters_query',{'q':'potter'}),
                     'dm_shift':tools.execute('characters_query',{'q':'potter','pov':'potter'})}
        for lid,anchor in [('pthread_audit',HERO),('town_audit',INN)]:
            create_lore_line(engine.store,{'id':lid,'complexity':'medium','about':lid,'description':lid,'trigger':'靠近',
                             'secret':'仅供合成测试','anchor':anchor,'l3_anchor':INN,'threshold':100,
                             'stages':[{'hint':'第一处迹象'}]},day=1,scene=INN,turn=0)
        world=project(engine.registry,engine.store.iter_events())
        changed=run_lore(engine.registry,engine.store,world,protagonist=HERO,previous_day=1)
        OUTPUT['person_anchored']={'same_day_advanced':[e['deltas']['id'] for e in changed if e['type']=='lore_advanced']}
    OUTPUT['opening_brief']=_build_world_summary({'world_name':'双镇','tone':'调查','central_conflict':'渡船迟滞'},
        {'regions':[{'name':'河谷'}]}, {'start_town':'t','l2':[{'id':'t','name':'小镇'}],
        'venues':['x','y'],'venue_names':{'x':'客栈','y':'磨坊'}},
        {'npcs':[{'sketch':'客栈老人','location':'x'},{'sketch':'磨坊木匠','location':'y'}]}, {'threads':[]})


def status_cost_probe():
    from statistics import median
    from time import perf_counter
    rows=[]
    for count in (100,1000,5000):
        with game() as (engine,dash):
            events=[kernel_event('narration_recorded',day=1,scene=INN,turn=i+1,summary='合成记录',
                                deltas={'scene':INN,'text':'合成旁白'+str(i)}) for i in range(count)]
            engine.store.append_many(events)
            engine.world=project(engine.registry,engine.store.iter_events()); engine.world['_revision']=engine.store.revision
            dash.state()
            timings=[]
            for _ in range(5):
                start=perf_counter(); state=dash.state(); timings.append((perf_counter()-start)*1000)
            rows.append({'extra_narration_events':count,'narrations_returned':len(state['narration']),
                         'median_ms_5_warm_reads':round(median(timings),2)})
    OUTPUT['dashboard_read']=rows


if __name__ == '__main__':
    time_probes(); inventory_probes(); quest_probes(); story_probes(); background_probes(); other_probes(); normal_reflection_probe(); terra_controls(); status_cost_probe()
    print(json.dumps(OUTPUT,ensure_ascii=False,indent=2))
