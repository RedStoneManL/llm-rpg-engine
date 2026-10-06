import importlib.util,sys,json,time,statistics,tempfile,logging
from pathlib import Path
from unittest.mock import patch
OUT=Path(__file__).resolve().parents[1];sys.path.insert(0,'/root/rpg-engine-app')
from app.engine import build_engine
from kernel.projection import project
from kernel.events import kernel_event
from facts.graph import FactGraph
logging.disable(logging.CRITICAL)
spec=importlib.util.spec_from_file_location('before_fact_graph',OUT/'baseline-implementation/facts/graph.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
e=build_engine(tempfile.mkdtemp(prefix='shipped-bench-',dir=OUT/'experiments'))
rows=[]
for n in [1000,3000,6000,12000]:
    events=[kernel_event('fact_asserted',day=1+i//100,scene='bench',summary='bench',
        deltas={'subject':f'n{i%100}','predicate':f'p{i%17}','value':i},turn=i,id=f'b{i}') for i in range(n)]
    row={'events':n};states=[]
    for name,cls in [('baseline',module.FactGraph),('implemented',FactGraph)]:
        timings=[]
        for repeat in range(3):
            with patch('systems.ontology.FactGraph',cls):
                start=time.perf_counter();world=project(e.registry,events);timings.append((time.perf_counter()-start)*1000)
        row[name+'_ms']=statistics.median(timings);states.append(world['systems']['ontology'].facts)
    assert states[0]==states[1];row['speedup']=row['baseline_ms']/row['implemented_ms'];rows.append(row)
result={'rows':rows,'repeats':3,'statistic':'median','all_projections_equal':True,
    'scope':'synthetic fact projection only; no LLM calls, transaction cost or relation-heavy workloads'}
(OUT/'evidence/index-benchmark-implemented.json').write_text(json.dumps(result,indent=2));e.store.close();print(json.dumps(result))
