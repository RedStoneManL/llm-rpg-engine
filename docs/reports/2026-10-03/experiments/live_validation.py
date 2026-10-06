"""Bounded DeepSeek validation, ready for an environment with API network access.

Real model calls only; no simulated fallback. Writes a blocked result if the
service cannot be reached. Stores no headers, keys, or provider reasoning.
Run from any directory: python3 live_validation.py --max-calls 32
"""
import argparse
import copy
import json
import logging
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT=Path('/root/rpg-engine-app');OUT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.engine import build_engine
from app.play import _build_scene
from engine import settings
from kernel.events import kernel_event
from kernel.projection import project
from kernel.validation import validate_commit
from llm.provider import OpenAIProvider
from loop.turn import produce_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy, HybridStrategy
from candidate import validated_apply

logging.disable(logging.CRITICAL)
parser=argparse.ArgumentParser();parser.add_argument('--max-calls',type=int,default=32);args=parser.parse_args()
config=dict(line.split('=',1) for line in Path('/root/.config/llm-rpg-engine/deepseek.env').read_text().splitlines() if '=' in line)
base=config['DEEPSEEK_BASE_URL'].rstrip('/');model=config['DEEPSEEK_MODEL'];key=config['DEEPSEEK_API_KEY']
result={'model_requested':model,'endpoint':base,'status':'running','real_calls':0,'samples':[],'api_calls':[],'scope':'paired one-turn synthetic scenes, not a long-session or streaming acceptance'}
def save(): (OUT/'evidence/live-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
try:
    req=urllib.request.Request(base+'/models',headers={'Authorization':'Bearer '+key})
    with urllib.request.urlopen(req,timeout=20) as response:available={m['id'] for m in json.load(response).get('data',[])}
    if model not in available:raise ValueError('requested model not available')
except Exception as exc:
    result.update(status='blocked',error_type=type(exc).__name__,error=str(exc));save()
    print('Real validation blocked before any model completion: '+type(exc).__name__);raise SystemExit(2)

class Measured(OpenAIProvider):
    def _post(self,url,headers,body,**kw):
        if result['real_calls']>=args.max_calls:raise RuntimeError('configured API call budget reached')
        body={**body,'thinking':{'type':'disabled'}}
        result['real_calls']+=1
        start=time.perf_counter()
        try:
            req=urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode(),headers=headers,method='POST')
            with urllib.request.urlopen(req,timeout=90) as response:obj=json.load(response)
        except Exception as exc:
            result['api_calls'].append({'ok':False,'error_type':type(exc).__name__,'seconds':time.perf_counter()-start});save();raise
        usage=obj.get('usage',{});self.last_usage={'input':usage.get('prompt_tokens'),'output':usage.get('completion_tokens'),'total':usage.get('total_tokens')}
        result['api_calls'].append({'ok':True,'model_returned':obj.get('model'),'seconds':time.perf_counter()-start,'usage':usage,'finish_reason':obj['choices'][0].get('finish_reason')});save()
        return obj

settings.set_conversation_mode('stateless');settings.set_max_tool_rounds(0);settings.set_verbosity('concise')
provider=Measured(model,key,base_url=base,max_tokens=4096)
scenarios=[
 ('payment','我购买这杯标价 3 枚金币的酒，用金币付款。',7),
 ('insufficient_funds','我想花 100 枚金币买下旅店，但是没有钱可以借；看看老板怎么回应。',10),
 ('conversation','我不买任何东西，只问侍者附近有什么值得探索的地方。',10),
]
try:
    for name,action,expected in scenarios:
        for repeat in range(2):
            # Counterbalance pair order to reduce systematic cache/order effects.
            strategies=[('author',AuthorStrategy),('hybrid',HybridStrategy)]
            if repeat%2:strategies.reverse()
            for label,cls in strategies:
                folder=Path(tempfile.mkdtemp(prefix='live-',dir=OUT/'experiments'))
                engine=build_engine(folder,provider=provider)
                seeds=[('entity_created',{'id':'hero','etype':'Person','tier':'tracked'}),('entity_created',{'id':'inn','etype':'Place','tier':'tracked','attrs':{'level':3,'kind':'venue','seed':'酒一杯3金币；旅店售价100金币，店主不借钱。'}}),('relation_added',{'src':'hero','rel':'located_in','dst':'inn'}),('fact_asserted',{'subject':'hero','predicate':'coins','value':10,'secrecy':'public'}),('knowledge_set',{'knower':'hero','fact_key':'hero.coins','value':10})]
                for typ,data in seeds:engine.store.append(kernel_event(typ,day=1,scene='inn',summary='synthetic live fixture',deltas=data,turn=0))
                engine.world=project(engine.registry,engine.store.iter_events());scene=_build_scene(engine)
                # Explicit state in the input prevents retrieval differences from
                # being confused with accounting differences in this small test.
                action_full='【当前确认状态】我有10枚金币，钱记作hero.coins。'+action
                start=time.perf_counter();call_start=result['real_calls']
                commit,repairs,dropped=produce_turn(engine.registry,engine.world,scene,action_full,strategy=cls(),provider=provider,max_repairs=1,required_sections=REQUIRED_SECTIONS)
                errors=validate_commit(engine.registry,commit,engine.world,required_sections=REQUIRED_SECTIONS)
                sample={'scenario':name,'repeat':repeat,'strategy':label,'seconds':time.perf_counter()-start,'calls':result['real_calls']-call_start,'repairs':repairs,'dropped':dropped,'validation_errors':[{'section':e.section,'code':e.code} for e in errors],'narration':commit.narration,'sections':commit.sections,'expected_coins':expected,'persisted':False}
                if not dropped and not errors:
                    try:
                        receipt=validated_apply(engine.registry,engine.store,commit,day=1,scene='inn')
                        observed=receipt['world']['systems']['ontology'].value_at('hero','coins',1)
                        sample.update(persisted=True,observed_coins=observed,accounting_ok=observed==expected)
                    except Exception as exc:sample['apply_error']=type(exc).__name__
                result['samples'].append(sample);save()
    result['status']='completed_requires_review'
except Exception as exc:
    result.update(status='incomplete',error_type=type(exc).__name__,error=str(exc))
save();print(json.dumps({k:result[k] for k in ['status','real_calls','model_requested']}))
