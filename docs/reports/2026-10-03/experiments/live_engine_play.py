"""Bounded, real DeepSeek genesis and continuous play in fresh campaigns only."""
import json, logging, sys, time, urllib.request, hashlib, os
from pathlib import Path

ROOT=Path('/root/rpg-engine-app'); OUT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.engine import build_engine, rewind
from app.play import _build_scene
from engine import settings
from engine.oracle import load_pack_manifest
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import DeepSeekProvider
from loop.bootstrap import bootstrap_world
from loop.strategy import AuthorStrategy
from loop.turn import run_turn, REQUIRED_SECTIONS

logging.basicConfig(level=logging.ERROR)
cfg=dict(line.split('=',1) for line in Path('/root/.config/llm-rpg-engine/deepseek.env').read_text().splitlines() if '=' in line)
result={'status':'running','started_at':time.strftime('%Y-%m-%d %H:%M:%S'),
        'model':cfg['DEEPSEEK_MODEL'],'call_budget':160,'api_calls':[],'campaigns':[]}
phase='setup'
dest=OUT/'evidence/implemented-live-play.json'
def save(): dest.write_text(json.dumps(result,ensure_ascii=False,indent=2))

class Measured(DeepSeekProvider):
    def _post(self,url,headers,body,**kw):
        if len(result['api_calls'])>=result['call_budget']:
            raise RuntimeError('experiment API call budget exhausted')
        body=self._prepare_body(body)
        record={'phase':phase,'n_tools_offered':len(body.get('tools') or []),'json_mode':body.get('response_format'),'ok':False}
        result['api_calls'].append(record);save();started=time.perf_counter()
        try:
            req=urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode(),headers=headers,method='POST')
            with urllib.request.urlopen(req,timeout=90) as response: obj=json.load(response)
            usage=obj.get('usage',{})
            self.last_usage={'input':usage.get('prompt_tokens'),'output':usage.get('completion_tokens'),'total':usage.get('total_tokens')}
            message=obj['choices'][0]['message']
            record.update(ok=True,model_returned=obj.get('model'),usage=usage,
                          tool_calls=[t.get('function',{}).get('name') for t in message.get('tool_calls',[])],
                          finish_reason=obj['choices'][0].get('finish_reason'))
            return obj
        except Exception as exc:
            record['error_type']=type(exc).__name__;raise
        finally:
            record['seconds']=time.perf_counter()-started;save()

provider=Measured(cfg['DEEPSEEK_MODEL'],cfg['DEEPSEEK_API_KEY'],base_url=cfg['DEEPSEEK_BASE_URL'],max_tokens=10000)
os.environ['RPG_RESOURCE_RULES']='1'
settings.set_conversation_mode('multiturn');settings.set_max_tool_rounds(2);settings.set_verbosity('concise')

def snapshot(world):
    graph=world['systems']['ontology']; day=world['meta'].get('day') or 1
    data={'meta':world['meta'],'systems':{k:v for k,v in world['systems'].items() if k!='ontology'},
          'entities':{k:vars(v) for k,v in graph.entities.items()},
          'facts':[vars(f) for f in graph.facts], 'relations':[vars(r) for r in graph.relations]}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

for flavor,pitch in [('classic','东方武侠，河港小镇，重日常生活与自由探索，不替主角做决定'),
                     ('isekai','明亮轻快的异世界日常冒险；以普通旅人视角探索，不是救世主')]:
    settings.set_style(load_pack_manifest(flavor).get('voice',''))
    folder=OUT/'experiments'/'implemented-play-v4'/flavor
    if folder.exists(): raise RuntimeError('Use a fresh experiment folder; never overwrite a prior live campaign')
    engine=build_engine(folder,provider=provider)
    sample={'flavor':flavor,'pitch':pitch,'turns':[],'status':'generating','campaign_directory':str(folder)}
    result['campaigns'].append(sample);save()
    try:
        phase=flavor+'/genesis';start=time.perf_counter();before_calls=len(result['api_calls'])
        genesis=bootstrap_world(engine,pitch,flavor=flavor)
        sample['genesis']={'seconds':time.perf_counter()-start,'calls':len(result['api_calls'])-before_calls,
                           'summary':genesis['summary'],'events':len(list(engine.store.iter_events()))}
        scene=_build_scene(engine);origin=scene['location'];hero=scene['protagonist']
        # Host-authored, explicit acceptance fixture added after genuine genesis.
        # These controlled resources enable exact checks, alongside free exploration.
        data=[('entity_created',{'id':'audit_supplies','etype':'Object','attrs':{'fact_rules':{'unit_price':{'type':'integer','min':0,'immutable':True}}}}),
              ('fact_asserted',{'subject':'audit_supplies','predicate':'sketch','value':'此处摊位出售的一份干粮，标价3枚通用钱币','secrecy':'public'}),
              ('fact_asserted',{'subject':'audit_supplies','predicate':'unit_price','value':3,'secrecy':'public'}),
              ('fact_asserted',{'subject':hero,'predicate':'coins','value':12,'secrecy':'public'}),
              ('knowledge_set',{'knower':hero,'fact_key':hero+'.coins','value':12}),
              ('fact_asserted',{'subject':hero,'predicate':'promise','value':'答应在明日中午回到这个摊位归还借来的蓝伞','secrecy':'public'}),
              ('knowledge_set',{'knower':hero,'fact_key':hero+'.promise','value':'答应在明日中午回到这个摊位归还借来的蓝伞'}),
              ('entity_created',{'id':'unseen_cellar','etype':'Place','attrs':{'visibility':'hidden','level':3,'kind':'venue','seed':'CANARY_UNSEEN_CELLAR_4937'}})]
        engine.store.append_many([kernel_event(t,day=scene['day'],scene=scene['id'],summary='acceptance fixture',deltas=d,turn=0) for t,d in data])
        engine.world=project(engine.registry,engine.store.iter_events());engine.world['_revision']=engine.store.revision
        strategy=AuthorStrategy();sample['status']='playing'
        actions=[
            ('pay','我按摊位标价买一份3枚钱币的干粮，现在付款。',9),
            ('insufficient','我还想花100枚钱币买下全部货物，但不借债，也没有别的财物可换。',9),
            ('quiet','我不买东西，也不接任务，只静静看一会儿这里的人各自忙什么。',9),
            ('recall','我检查自己的行程，回想之前答应归还的东西是什么、什么时候在哪里归还。',9),
            ('explore','我走出当前场所，在门口附近观察来往的人和道路，暂时不接新任务。',None),
            ('return',f'我回到刚才买干粮的地方（{origin}），看看之前遇见的人是否还在。',None),
            ('wait','我在此休息到明天中午，不购买任何东西。留意周围的人和先前的事情有没有变化。',None),
            ('promise','到了约好的时间，我在约好的摊位试着归还借来的蓝伞；先寻找出借人，不假定一定能见到。',None),
        ]
        for i,(label,action,expected) in enumerate(actions,1):
            if i==4:
                before=snapshot(engine.world);engine.store.close()
                engine=build_engine(folder,provider=provider);strategy=AuthorStrategy()
                sample['reopen_equal']=snapshot(engine.world)==before
            phase=flavor+'/'+label;before_calls=len(result['api_calls']);start=time.perf_counter()
            row={'label':label,'action':action,'expected_coins':expected}
            try:
                answer=run_turn(engine.registry,engine.store,engine.world,_build_scene(engine),action,
                                strategy=strategy,provider=provider,cascade_provider=provider,
                                max_repairs=3,required_sections=REQUIRED_SECTIONS)
                engine.world=answer.world
                graph=engine.world['systems']['ontology'];day=engine.world['meta'].get('day') or 1
                row.update(accepted=True,narration=answer.narration,sections=answer.commit.sections,
                    repairs=answer.repair_attempts,events=len(answer.events),receipt=answer.receipt,
                    coins=graph.value_at(hero,'coins',day),day=day,band=engine.world['meta'].get('band',0),
                    turn_groups=sorted({e.get('turn') for e in answer.events}),
                    replay_equal=snapshot(engine.world)==snapshot(project(engine.registry,engine.store.iter_events())),
                    variation=[e['deltas'] for e in answer.events if e['type']=='variation_sampled'],
                    event_types=sorted({e['type'] for e in answer.events}),
                    canary_leaked='CANARY_UNSEEN_CELLAR_4937' in answer.narration,
                    resource_resolution=engine.world['systems']['resources'].get('last_resolution'))
                if expected is not None: row['accounting_ok']=row['coins']==expected
                if i==3:
                    # Undo the quiet action and retry after a real store reload.
                    turn=answer.events[0]['turn']; old_revision=engine.store.revision
                    rewind(engine,turn)
                    sample['undo']={'removed_turn':turn,'revision_increased':engine.store.revision>old_revision,
                        'remaining_turns':sorted({x.get('turn') for x in engine.store.iter_events()}),
                        'quiet_narration_removed':all(x.get('deltas',{}).get('text')!=answer.narration for x in engine.store.iter_events())}
            except Exception as exc:
                row.update(accepted=False,error_type=type(exc).__name__,error=str(exc))
            row.update(seconds=time.perf_counter()-start,calls=len(result['api_calls'])-before_calls)
            sample['turns'].append(row);save()
            print(json.dumps({'flavor':flavor,'step':label,'accepted':row['accepted'],'calls':row['calls']},ensure_ascii=False),flush=True)
        sample['status']='completed'
        sample['final_event_count']=len(list(engine.store.iter_events()))
    except Exception as exc:
        sample.update(status='failed',error_type=type(exc).__name__,error=str(exc))
    finally:
        engine.store.close();save()
result['status']='completed_requires_review';save()
print(json.dumps({'status':result['status'],'calls':len(result['api_calls'])}),flush=True)
