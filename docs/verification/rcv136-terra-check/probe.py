import copy, json, sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.engine import build_engine
from app.gui import _seed_demo_campaign, build_dashboard
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider
from systems.challenge import resolve_check

ACTION='检定 潜行 困难 绕过守卫'
OUT=Path(__file__).parent
HOOKS=('digest_fleet','run_director','run_cascade','run_catchup','run_density','_run_demote_on_leave','run_lore')
def write(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
class Pending(BaseException): pass
class Provider(LLMProvider):
 def __init__(self):
  self.index=0
  self.requests=json.loads((OUT/'requests.json').read_text()) if (OUT/'requests.json').exists() else []
  self.responses=json.loads((OUT/'responses.json').read_text()) if (OUT/'responses.json').exists() else []
 def complete_messages(self,messages,**kwargs): return self.capture({'method':'complete_messages','messages':copy.deepcopy(messages),'kwargs':kwargs})
 def complete(self,system,user,**kwargs): return self.capture({'method':'complete','system':system,'user':user,'kwargs':kwargs})
 def capture(self,request):
  i=self.index; self.index+=1
  if i<len(self.requests): assert self.requests[i]==request, f'request mismatch {i}'
  else: self.requests.append(request); write('requests.json',self.requests)
  if i>=len(self.responses): write('pending.json',{'index':i,'request':request}); raise Pending()
  return self.responses[i]
def main():
 p=Provider()
 with TemporaryDirectory(prefix='rcv136-') as tmp, ExitStack() as stack:
  for name in HOOKS: stack.enter_context(patch('loop.turn.'+name,return_value=[]))
  e=build_engine(tmp,provider=p,embedder=None); _seed_demo_campaign(e)
  def seed(t,**d):
   e.store.append(kernel_event(t,day=1,scene='stonebridge_inn',turn=0,summary='合成测试准备',deltas=d))
   e.world=project(e.registry,e.store.iter_events()); e.world['_revision']=e.store.revision
  seed('character_created',id='guard',op='create',name='守卫',sketch='守着客栈出口的守卫',goal='看守出口',tier='tracked')
  seed('entity_moved',who='guard',to='stonebridge_inn')
  for s in range(1,100):
   check=resolve_check(ACTION,{**e.world,'meta':{**e.world['meta'],'campaign_seed':s}},_build_scene(e),e.store.next_turn())
   if check['deltas']['outcome']=='受挫': break
  assert check['deltas']['outcome']=='受挫'
  seed('campaign_seeded',campaign_seed=s)
  write('expected.json',{'seed':s,'check':check})
  e.store.close()
  d=build_dashboard(tmp,demo=False,provider=p,max_repairs=0)
  try:
   assert type(d.strategy).__name__=='GroundedAuthorStrategy'
   initial=d.state(); revision=initial['revision']
   response=d.action(player_input=ACTION,action_id='risk-native',expected_revision=revision)
   events=list(d.engine.store.iter_events()); checks=[x for x in events if x['type']=='action_checked']; assert len(checks)==1
   assert checks[0]['deltas']==check['deltas']
   state=d.state(); world=d.engine.world; graph=world['systems']['ontology']; day=world['meta']['day']
   location=graph.neighbors('protagonist','located_in',day); holder=graph.neighbors('old_brass_lantern','held_by',day)
   assert location==['stonebridge_inn'] and holder==['protagonist']
   assert '受挫' in state['latest_check'] and str(check['deltas']['roll']) in state['latest_check']
   d.engine.store.close()
   d=build_dashboard(tmp,demo=False,provider=p,max_repairs=0)
   reopened=d.state(); duplicate=d.action(player_input=ACTION,action_id='risk-native',expected_revision=revision)
   assert duplicate['duplicate']; checks2=[x for x in d.engine.store.iter_events() if x['type']=='action_checked']; assert len(checks2)==1 and checks2==checks
   assert reopened['latest_check']==state['latest_check']
   write('result.json',{'strategy':type(d.strategy).__name__,'demo':d.demo,'action':ACTION,'seed':s,'initial':initial,'response':response,'location':location,'lamp_holder':holder,'check_events':checks,'reopened_latest_check':reopened['latest_check'],'duplicate':duplicate,'native_calls':p.index,'disabled_hooks':HOOKS,'transport':'local capture and native responses, no HTTP'})
   (OUT/'pending.json').unlink(missing_ok=True); print('COMPLETE',p.index)
  except Pending: print('PENDING',p.index-1)
  except Exception as exc: write('failure.json',{'type':type(exc).__name__,'error':str(exc)}); print('FAILURE',type(exc).__name__,str(exc)); raise
  finally: d.engine.store.close()
if __name__=='__main__': main()
