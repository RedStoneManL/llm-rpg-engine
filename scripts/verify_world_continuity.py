"""120 actions through the shipped gateway, rule engine, memory and undo path."""
import argparse,json,sys,tempfile,time,logging
from pathlib import Path
from unittest.mock import patch
REPO=Path(__file__).resolve().parents[1];sys.path.insert(0,str(REPO))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir',required=True,type=Path,help='A new directory for the campaign and result JSON')
options=parser.parse_args()
ROOT=options.output_dir.resolve()
if ROOT.exists(): parser.error('output directory already exists; use a fresh directory')
ROOT.mkdir(parents=True)
(ROOT/'experiments').mkdir()
(ROOT/'evidence').mkdir()
from app.engine import build_engine,rewind
from app.play import _build_scene
from engine import settings
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.turn import run_turn,REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy
logging.disable(logging.CRITICAL)

class HarnessFake(FakeLLMProvider):
    def complete_messages(self,messages,**kwargs):
        system=messages[0]['content']
        if '你只解析' in system:return json.dumps({'op':'spend','resource':'fuel','amount':1})
        if '你是主持人' in system:
            return json.dumps({'narration':'你消耗一单位燃料，设备继续平稳运作。','moves':[],'places':[],'cast':[],'facts':[],
                'clock':[{'advance':False,'days':0,'bands':0,'reason':'片刻'}]})
        return json.dumps({'summary':'设备一直平稳工作；燃料以事实账本为准。'})

folder=Path(tempfile.mkdtemp(prefix='shipped-endurance-',dir=ROOT/'experiments'))
provider=HarnessFake();e=build_engine(folder,provider=provider)
data=[('entity_created',{'id':'protagonist','etype':'Person','tier':'tracked'}),
      ('campaign_seeded',{'campaign_seed':20261003,'flavor':'classic'}),
      ('resources_configured',{'subject':'protagonist','resources':{'fuel':{'initial':1000,'min':0,'type':'integer'}}})]
e.store.append_many([kernel_event(t,day=1,scene='room',summary='fixture',deltas=d,turn=0) for t,d in data])
e.world=project(e.registry,e.store.iter_events())
settings.set_conversation_mode('multiturn');strategy=AuthorStrategy()
result={'actions':0,'injected_write_failures':0,'whole_action_undos':0,'reopens':0,'salt_replays':0,'failures':[]}
start=time.perf_counter()
def act():
    return run_turn(e.registry,e.store,e.world,_build_scene(e),'消耗1单位燃料维持设备',
                    strategy=strategy,provider=provider,required_sections=REQUIRED_SECTIONS)
for i in range(1,121):
    if i%10==0:
        before=list(e.store.iter_events());revision=e.store.revision;original=e.store._insert;count=[0]
        def fail_second(ev):
            count[0]+=1
            if count[0]==2:raise OSError('fault injection')
            return original(ev)
        with patch.object(e.store,'_insert',fail_second):
            try:act();raise AssertionError('write failure not raised')
            except OSError:pass
        assert list(e.store.iter_events())==before and e.store.revision==revision
        result['injected_write_failures']+=1
    answer=act();e.world=answer.world
    assert e.world['systems']['ontology'].value_at('protagonist','fuel',1)==1000-i
    assert len({x['turn'] for x in answer.events})==1
    assert len(strategy._thread)<=17
    if i%12==0:
        old_salt=[x['deltas'] for x in answer.events if x['type']=='variation_sampled']
        rewind(e,answer.events[0]['turn']);answer=act();e.world=answer.world
        assert old_salt==[x['deltas'] for x in answer.events if x['type']=='variation_sampled']
        result['whole_action_undos']+=1;result['salt_replays']+=1
    if i%15==0:
        e.store.close();e=build_engine(folder,provider=provider);strategy=AuthorStrategy()
        assert e.world['systems']['ontology'].value_at('protagonist','fuel',1)==1000-i
        result['reopens']+=1
    result['actions']=i
result.update(status='passed',seconds=time.perf_counter()-start,final_fuel=880,
    active_events=len(list(e.store.iter_events())),campaign=str(folder),
    scope='deterministic provider, actual run_turn and SQLite; not model narrative quality')
e.store.close();(ROOT/'evidence/implemented-endurance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result))
