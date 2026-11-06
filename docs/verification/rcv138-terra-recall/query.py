import json,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from app.gui import build_dashboard
from llm.provider import LLMProvider
class Counter(LLMProvider):
 def __init__(self): self.calls=[]
 def complete_messages(self,messages,**kwargs): self.calls.append('messages');raise AssertionError('unexpected model call')
 def complete(self,system,user,**kwargs): self.calls.append('complete');raise AssertionError('unexpected model call')
p=Counter();d=build_dashboard('/tmp/rcv138-terra-recall-save',demo=False,provider=p,max_repairs=0)
try:
 before=d.state();rev=before['revision']; events=list(d.engine.store.iter_events());count=len(events)
 questions=['第一天我对阿岚说了什么？','我是哪天对阿岚说“紫檀风铃今晚响三次。”？','我是哪天对阿岚说“白瓷茶盏放在西窗。”？','我是哪天对阿岚说“金色雨滴落在北门。”？']
 replies=[]
 for i,q in enumerate(questions):
  r=d.action(player_input=q,action_id='lookup-'+str(i),expected_revision=rev);replies.append(r)
  assert r.get('read_only') and r['revision']==rev and not p.calls
 sources=replies[0]['recall'].get('sources',[]); opened=[d.open_source(x['ref']) for x in sources]
 out={'revision_before':rev,'revision_after':d.state()['revision'],'events_before':count,'events_after':len(list(d.engine.store.iter_events())),'model_calls':len(p.calls),'questions':questions,'replies':replies,'opened':opened,'location':before['scene']['location']}
 Path('docs/verification/rcv138-terra-recall/queries.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'revision':rev,'model_calls':len(p.calls),'replies':[r['recall'] for r in replies],'opened':opened},ensure_ascii=False,indent=2))
finally:d.engine.store.close()
src=sqlite3.connect('/tmp/rcv138-terra-recall-save/events.db');dst=sqlite3.connect('docs/verification/rcv138-terra-recall/synthetic-events.db');src.backup(dst);dst.close();src.close()
