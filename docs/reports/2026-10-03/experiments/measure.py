"""Local performance, historical latency and knowledge-view experiments."""
import copy
import dataclasses
import json
import logging
import random
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from facts.graph import FactGraph
from kernel.events import kernel_event
from kernel.projection import project
from app.engine import build_engine
from llm.provider import FakeLLMProvider
from llm.tools import build_tool_registry
from candidate import IndexedFactGraph, pov_world

OUT=Path(__file__).resolve().parents[1]
logging.disable(logging.CRITICAL)
engine=build_engine(tempfile.mkdtemp(prefix='measure-',dir=OUT/'experiments'),provider=FakeLLMProvider())

# Fact-index differential check: normal updates, same-day updates, history,
# current ordering, non-monotonic rejection, plus real projection equivalence.
rng=random.Random(20261003); a=FactGraph(); b=IndexedFactGraph()
for i in range(1500):
    subject=f'npc_{rng.randrange(30)}'; predicate=f'property_{rng.randrange(8)}'
    kw=dict(day=1+i//100,turn=i,source_event=f'ev{i}',secrecy='public')
    for g in (a,b):g.assert_fact(subject,predicate,i,**kw)
queries=0
for eid in range(30):
    subject=f'npc_{eid}'
    assert a.current_facts(subject)==b.current_facts(subject)
    for pred in range(8):
        predicate=f'property_{pred}'
        assert a.fact_history(subject,predicate)==b.fact_history(subject,predicate)
        for day in range(0,18):
            assert a.value_at(subject,predicate,day)==b.value_at(subject,predicate,day);queries+=1
assert a.facts==b.facts

rows=[]
for n in [1000,3000,6000,12000]:
    events=[kernel_event('fact_asserted',day=1+i//100,scene='bench',summary='bench',deltas={'subject':f'n{i%100}','predicate':f'p{i%17}','value':i},turn=i,id=f'benchmark-{i}') for i in range(n)]
    timings={}
    outputs={}
    for label,cls in [('baseline',FactGraph),('indexed',IndexedFactGraph)]:
        samples=[]
        for repeat in range(3):
            with patch('systems.ontology.FactGraph',cls):
                start=time.perf_counter();w=project(engine.registry,events);samples.append((time.perf_counter()-start)*1000)
        timings[label]=round(statistics.median(samples),3)
        outputs[label]=w['systems']['ontology'].facts
    assert outputs['baseline']==outputs['indexed']
    rows.append({'events':n,'baseline_ms':timings['baseline'],'indexed_ms':timings['indexed'],'speedup':round(timings['baseline']/timings['indexed'],2)})
result={'repetitions':3,'statistic':'median','fixture':'synthetic fact events through real kernel.project; 100 subjects x 17 predicates; no LLM, no I/O','differential_queries':queries,'facts_compared':1500,'projection_equal_at_all_sizes':True,'rows':rows}
(OUT/'evidence/index-benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result),flush=True)

# Recompute historical GLM timings directly from trace, not from Claude's chart.
trace=[json.loads(s) for s in Path('/root/games/play8/trace.jsonl').read_text().splitlines()]
starts={r['seq']:r for r in trace if r['type']=='span_start'}
turns=[]
for item in trace:
    if item['type']!='span_end' or item['name']!='turn':continue
    start=starts[item['ref_seq']];prefix=start['path']+'▸'
    descendants=[r for r in trace if r.get('path','').startswith(prefix)]
    spans={r['name']:r['dur_ms']/1000 for r in descendants if r['type']=='span_end' and r.get('parent_seq')==start['seq']}
    gens=[r for r in descendants if r['type']=='gen']
    turns.append({'ordinal':len(turns)+1,'event_turn':start['attrs']['turn'],'total_s':item['dur_ms']/1000,'produce_s':spans.get('produce',0),'density_s':spans.get('density',0),'phases_s':spans,'llm_calls':len(gens),'models':dict(Counter(r.get('attrs',{}).get('model','?') for r in gens))})
historical={'source':'/root/games/play8/trace.jsonl','source_date':'2026-07-11','provider':'historical GLM, NOT DeepSeek','n':len(turns),'median_total_s':statistics.median(r['total_s'] for r in turns),'median_produce_s':statistics.median(r['produce_s'] for r in turns),'turns':turns}
(OUT/'evidence/historical-latency.json').write_text(json.dumps(historical,ensure_ascii=False,indent=2));print(json.dumps(historical),flush=True)

# Regression: co-presence must not authorize an unknown personal goal. When the
# hero holds a false belief, retrieval returns that belief, not ground truth.
w=engine.world;g=w['systems']['ontology']
g.add_entity('hero','Person',tier='tracked');g.add_entity('npc','Person',tier='tracked')
scene={'protagonist':'hero','present':['npc'],'day':1,'location':'inn'}
g.assert_fact('npc','sketch','普通侍者',day=1,turn=0,source_event='a',secrecy='public')
g.assert_fact('npc','goal','AUDIT_SECRET_ASSASSINATION',day=1,turn=0,source_event='b',secrecy='secret')
base=build_tool_registry(engine.registry,w,scene).execute('recall_query',{'q':'普通侍者'})
view=pov_world(w,scene);filtered=build_tool_registry(engine.registry,view,scene).execute('recall_query',{'q':'普通侍者'})
assert 'AUDIT_SECRET_ASSASSINATION' in base and 'AUDIT_SECRET_ASSASSINATION' not in filtered and '普通侍者' in filtered
g.assert_fact('hero','knows:npc.goal','寻找丢失的小猫',day=1,turn=0,source_event='c')
belief=build_tool_registry(engine.registry,pov_world(w,scene),scene).execute('recall_query',{'q':'普通侍者'})
assert '寻找丢失的小猫' in belief and 'AUDIT_SECRET_ASSASSINATION' not in belief
result={'id':'E17','baseline_unknown_goal_leaks':True,'candidate_unknown_goal_leaks':False,'public_sketch_preserved':True,'false_belief_preserved':True,'scope':'POV recall, explicit visibility fixture; not all DM prompt paths'}
(OUT/'evidence/knowledge-view.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False),flush=True)
