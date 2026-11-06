import copy,json,sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from app.engine import build_engine
from app.gui import _seed_demo_campaign,build_dashboard
from kernel.events import kernel_event
from llm.provider import LLMProvider

OUT=Path(__file__).parent
DB=Path('/tmp/rcv138-terra-investigation-save')
HOOKS=('digest_fleet','run_director','run_cascade','run_catchup','run_density','_run_demote_on_leave','run_lore')
ACTIONS=['查证 books 1','前往石桥集市。','查证 books 2','我整理证据，按寄存簿原文结案。']
def read(name,default):
 p=OUT/name
 return json.loads(p.read_text()) if p.exists() else default
def write(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
class Pending(BaseException): pass
class Provider(LLMProvider):
 def __init__(self,turn): self.turn=turn;self.i=0;self.requests=read('requests.json',[]);self.responses=read('responses.json',[])
 def complete_messages(self,messages,**kwargs): return self.capture({'method':'complete_messages','messages':copy.deepcopy(messages),'kwargs':kwargs})
 def complete(self,system,user,**kwargs): return self.capture({'method':'complete','system':system,'user':user,'kwargs':kwargs})
 def capture(self,r):
  key=f'{self.turn}:{self.i}'; self.i+=1
  found=next((x for x in self.requests if x['key']==key),None)
  if found: assert found['request']==r, 'request mismatch '+key
  else: self.requests.append({'key':key,'request':r});write('requests.json',self.requests)
  response=next((x['response'] for x in self.responses if x['key']==key),None)
  if response is None: write('pending.json',{'key':key,'request':r});raise Pending()
  return response

def main():
 progress=read('progress.json',{'turn':0,'results':[]}); n=progress['turn']; assert n<len(ACTIONS)
 p=Provider(n)
 with ExitStack() as stack:
  for name in HOOKS: stack.enter_context(patch('loop.turn.'+name,return_value=[]))
  if not DB.exists():
   DB.mkdir(parents=True)
   e=build_engine(DB,provider=p,embedder=None);_seed_demo_campaign(e)
   e.store.append(kernel_event('quest_opened',day=1,scene='stonebridge_inn',turn=0,summary='已接下寻找账簿',deltas={'id':'books','summary':'找到账簿的存放处','objective':{'kind':'investigate','place':'stonebridge_inn','feature':'旧柜'}}));e.store.close()
  d=build_dashboard(DB,demo=False,provider=p,max_repairs=0)
  try:
   before=d.state(); assert type(d.strategy).__name__=='GroundedAuthorStrategy'
   if n==1:
    leads=before['objectives']['active_quests'][0]['leads'];progress['cold_reopen_leads']=leads
    for lead in leads:
     if lead.get('ref'): progress.setdefault('sources',[]).append(d.open_source(lead['ref']))
   if n==3:
    leads=before['objectives']['active_quests'][0]['leads']
    for lead in leads:
     if lead.get('ref'): progress.setdefault('sources',[]).append(d.open_source(lead['ref']))
   result=d.action(player_input=ACTIONS[n],action_id=f'rcv138-{n}',expected_revision=before['revision'])
   progress['results'].append({'input':ACTIONS[n],'result':result});progress['turn']=n+1;write('progress.json',progress)
   (OUT/'pending.json').unlink(missing_ok=True)
   print('PUBLISHED',n,'state',json.dumps(result['state']['objectives'],ensure_ascii=False))
  except Pending: print('PENDING',n,p.i-1)
  except Exception as e: write('failure.json',{'turn':n,'type':type(e).__name__,'error':str(e)});raise
  finally: d.engine.store.close()
if __name__=='__main__':main()
