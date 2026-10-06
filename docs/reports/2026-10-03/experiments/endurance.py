"""Offline long-sequence persistence probe and actual concurrent writer race."""
import json
import logging
import tempfile
import threading
from pathlib import Path
from app.engine import build_engine
from llm.provider import FakeLLMProvider
from kernel.events import kernel_event
from kernel.projection import project
from candidate import AtomicGateway, Conflict

OUT=Path(__file__).resolve().parents[1]
logging.disable(logging.CRITICAL)
folder=Path(tempfile.mkdtemp(prefix='endurance-',dir=OUT/'experiments'))
engine=build_engine(folder,provider=FakeLLMProvider());gate=AtomicGateway(engine.store,engine.registry)
expected=1000
counts={'actions':0,'undo':0,'reopen':0,'duplicates':0,'write_failures':0,'stale_rejected':0}
seed=kernel_event('entity_created',day=1,scene='town',summary='hero',deltas={'id':'hero','etype':'Person','tier':'tracked'},turn=0)
engine.store.append(seed)
for i in range(1,121):
    revision=gate.revision();old_expected=expected;expected-=1
    ev=kernel_event('fact_asserted',day=1,scene='town',summary='paid one coin',deltas={'subject':'hero','predicate':'coins','value':expected},turn=i)
    if i%15==0:
        try:gate.commit([ev],action_id=f'action-{i}',expected_revision=revision,fail_at=0)
        except OSError:counts['write_failures']+=1
        assert gate.revision()==revision
    result=gate.commit([ev],action_id=f'action-{i}',expected_revision=revision);counts['actions']+=1
    if i%7==0:
        assert gate.commit([ev],action_id=f'action-{i}',expected_revision=revision)['duplicate'];counts['duplicates']+=1
    if i%10==0:
        stale_revision=gate.revision();gate.undo(i);expected=old_expected;counts['undo']+=1
        try:gate.commit([ev],action_id=f'late-{i}',expected_revision=stale_revision)
        except Conflict:counts['stale_rejected']+=1
        engine.store.close();engine=build_engine(folder,provider=FakeLLMProvider());gate=AtomicGateway(engine.store,engine.registry);counts['reopen']+=1
    world=project(engine.registry,engine.store.iter_events())
    assert world['systems']['ontology'].value_at('hero','coins',1)==expected

# Both threads really read revision 0 on separate SQLite connections, then race.
race_folder=Path(tempfile.mkdtemp(prefix='writer-race-',dir=OUT/'experiments'))
barrier=threading.Barrier(2);race=[]
def writer(index):
    e=build_engine(race_folder,provider=FakeLLMProvider());g=AtomicGateway(e.store,e.registry)
    revision=g.revision();barrier.wait(timeout=10)
    try:
        ev=kernel_event('entity_created',day=1,scene='town',summary='race',deltas={'id':f'actor{index}','etype':'Person'},turn=1)
        g.commit([ev],action_id=f'writer-{index}',expected_revision=revision)
        race.append('committed')
    except Conflict:race.append('conflict')
    finally:e.store.close()
threads=[threading.Thread(target=writer,args=(i,)) for i in range(2)]
for t in threads:t.start()
for t in threads:t.join(timeout=15)
assert sorted(race)==['committed','conflict']
result={'scope':'real SQLite and kernel projection with deterministic scripted actions; no model quality claims','status':'passed','counts':counts,'final_coins':expected,'revision':gate.revision(),'concurrent_writers':race}
(OUT/'evidence/endurance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result))
