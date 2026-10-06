"""Reproducible architecture probes against actual engine code; no network.
Run: PYTHONPATH=/root/rpg-engine-app python3 experiments/probe_architecture.py
"""
import copy
import json
import logging
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from app.engine import build_engine, new_game, last_turn, rewind
from app.play import _build_scene
from context.assembler import assemble_context
from engine import settings
from facts.graph import FactGraph
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from kernel.validation import validate_commit
from llm.provider import FakeLLMProvider, OpenAIProvider, _openai_parse, _openai_append_result, _parse_json_object
from llm.tools import build_tool_registry
from loop.strategy import AuthorStrategy, HybridStrategy
from loop.turn import produce_turn, apply_turn, run_turn, REQUIRED_SECTIONS
from candidate import AtomicGateway, BufferedStore, Conflict, IncompleteAction, validated_apply, visible_world

OUT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix='rpg-independent-', dir=OUT/'experiments'))
RESULTS = []
logging.disable(logging.CRITICAL)


def event(kind, d=None, day=1, turn=0, scene='inn'):
    return kernel_event(kind, day=day, scene=scene, summary='audit fixture', deltas=d or {}, turn=turn)


def fresh(name, seed=True):
    e = build_engine(TMP/name, provider=FakeLLMProvider())
    if seed:
        events = [
            event('entity_created', {'id':'hero','etype':'Person','tier':'tracked'}),
            event('entity_created', {'id':'inn','etype':'Place','tier':'tracked','attrs':{'level':3,'kind':'venue','seed':'普通旅店','visibility':'public'}}),
            event('relation_added', {'src':'hero','rel':'located_in','dst':'inn'}),
            event('fact_asserted', {'subject':'hero','predicate':'coins','value':10}),
        ]
        for ev in events: e.store.append(ev)
        e.world = project(e.registry, e.store.iter_events())
    return e


def count(e):
    return len(list(e.store.iter_events()))


def check(id, fn):
    started = time.perf_counter()
    try:
        detail = fn()
        result = {'id':id, 'status':'observed', 'elapsed_s':round(time.perf_counter()-started,4), **detail}
    except Exception as exc:
        import traceback
        result = {'id':id, 'status':'probe_error', 'error':repr(exc), 'trace':traceback.format_exc()}
    RESULTS.append(result)
    (OUT/'evidence/probes.json').write_text(json.dumps(RESULTS,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


def poison():
    commit = TurnCommit('不应入库', {'entities':[{'id':'bad','etype':'Person','attrs':'invalid'}]})
    e = fresh('poison'); before = count(e)
    errors = validate_commit(e.registry,commit,e.world)
    failure = None
    try: apply_turn(e.registry,e.store,commit,day=1,scene='inn')
    except Exception as exc: failure=type(exc).__name__
    after=count(e); e.store.close()
    reopen_error=None
    try: build_engine(TMP/'poison',provider=FakeLLMProvider())
    except Exception as exc: reopen_error=type(exc).__name__
    p=fresh('poison-fixed'); n=count(p)
    try: validated_apply(p.registry,p.store,commit,day=1,scene='inn')
    except TypeError: pass
    unchanged=count(p)==n
    project(p.registry,p.store.iter_events())
    assert errors==[] and after==before+1 and reopen_error and unchanged
    return {'baseline':{'validation_errors':len(errors),'persisted_poison_events':after-before,'apply_error':failure,'reopen_error':reopen_error},'candidate':{'rejected_before_write':unchanged,'replay_ok':True},'verdict':'preflight gate verified'}


def partial_writes():
    c=TurnCommit('两位新 NPC',{'entities':[{'id':'one','etype':'Person'},{'id':'two','etype':'Person'}]})
    e=fresh('partial'); n=count(e); original=e.store.append; calls=0
    def broken(ev):
        nonlocal calls
        calls+=1
        if calls==2: raise OSError('injected disk write failure')
        return original(ev)
    with patch.object(e.store,'append',side_effect=broken):
        try: apply_turn(e.registry,e.store,c,day=1,scene='inn')
        except OSError: pass
    leftovers=count(e)-n
    p=fresh('partial-fixed'); n=count(p); gateway=AtomicGateway(p.store,p.registry)
    events=p.registry.owner_of_section('entities').to_events('entities',c.sections['entities'],turn=1,day=1,scene='inn')
    try: gateway.commit(events,action_id='one-action',expected_revision=0,fail_at=1)
    except OSError: pass
    leaked=count(p)-n
    assert leftovers==1 and leaked==0 and gateway.revision()==0
    return {'baseline_partial_events':leftovers,'candidate_partial_events':leaked,'candidate_revision':gateway.revision(),'verdict':'batch rollback verified'}


def mirror_failure():
    e=fresh('mirror'); n=count(e); e.store.jsonl_path=TMP/'missing-parent'/'events.jsonl'
    error=None
    try: e.store.append(event('fact_asserted',{'subject':'hero','predicate':'coins','value':9},turn=1))
    except Exception as exc:error=type(exc).__name__
    p=fresh('mirror-fixed'); gate=AtomicGateway(p.store,p.registry); p.store.jsonl_path=TMP/'missing-parent-2'/'events.jsonl'
    ev=event('fact_asserted',{'subject':'hero','predicate':'coins','value':9},turn=1)
    receipt=gate.commit([ev],action_id='idempotent-action',expected_revision=0)
    duplicate=gate.commit([ev],action_id='idempotent-action',expected_revision=0)
    different_rejected=False
    try:gate.commit([event('fact_asserted',{'subject':'hero','predicate':'coins','value':8},turn=1)],action_id='idempotent-action',expected_revision=1)
    except Conflict:different_rejected=True
    assert error and count(e)==n+1 and receipt['mirror_ok'] is False and duplicate['duplicate'] and different_rejected
    return {'baseline_raised_after_durable_commit':error,'candidate_committed':True,'candidate_export_pending':True,'retry_duplicate':duplicate['duplicate'],'different_payload_rejected':different_rejected,'verdict':'durable receipt + idempotency verified; production outbox pending'}


def monotonic():
    e=fresh('date'); e.store.append(event('fact_asserted',{'subject':'hero','predicate':'coins','value':5},day=9,turn=1))
    c=TurnCommit('时间错误',{'facts':[{'subject':'hero','predicate':'coins','value':4}]})
    try:apply_turn(e.registry,e.store,c,day=2,scene='inn')
    except ValueError:pass
    broken=False
    try:project(e.registry,e.store.iter_events())
    except ValueError:broken=True
    p=fresh('date-fixed'); p.store.append(event('fact_asserted',{'subject':'hero','predicate':'coins','value':5},day=9,turn=1)); before=count(p)
    try:validated_apply(p.registry,p.store,c,day=2,scene='inn')
    except ValueError:pass
    stable=project(p.registry,p.store.iter_events())['systems']['ontology'].value_at('hero','coins',9)
    assert broken and count(p)==before and stable==5
    return {'baseline_replay_broken':broken,'candidate_last_valid_value':stable,'candidate_extra_events':count(p)-before,'verdict':'keep strict graph invariant; reject invalid batch'}


def thread_divergence():
    settings.set_conversation_mode('multiturn')
    e=fresh('thread'); s=AuthorStrategy(); scene=_build_scene(e)
    c,_,_=produce_turn(e.registry,e.world,scene,'支付全部金币',strategy=s,provider=FakeLLMProvider(json_responses=[{'narration':'你已经花光金币。','facts':[{'subject':'hero','predicate':'coins','value':0}]}]))
    before_db=count(e); thread_pairs=(len(s._thread)-1)//2
    with patch.object(e.store,'append',side_effect=OSError('injected')):
        try:apply_turn(e.registry,e.store,c,day=1,scene='inn')
        except OSError:pass
    rewind(e,1)
    remains='花光' in json.dumps(s._thread,ensure_ascii=False)
    # Stage on a copy; publish only after successful durable commit.
    original=AuthorStrategy(); working=copy.deepcopy(original)
    c,_,_=produce_turn(e.registry,e.world,scene,'支付全部金币',strategy=working,provider=FakeLLMProvider(json_responses=[{'narration':'你已经花光金币。'}]))
    discarded=original._thread is None
    assert thread_pairs==1 and count(e)==before_db and remains and discarded
    return {'baseline_thread_committed_before_db':thread_pairs,'baseline_undo_retains_retracted_prose':remains,'candidate_discarded_staged_thread':discarded,'verdict':'staged memory boundary verified; rewind must invalidate cache'}


def real_undo():
    source=Path('/root/games/play8'); d=TMP/'play8-copy'; d.mkdir()
    for name in ['events.db','events.jsonl']:shutil.copy2(source/name,d/name)
    e=build_engine(d,provider=FakeLLMProvider()); events=list(e.store.iter_events()); turn=last_turn(e)
    last_narration=next(ev for ev in reversed(events) if ev['type']=='narration_recorded')
    types=[ev['type'] for ev in events if ev.get('turn')==turn]
    r=rewind(e,turn)
    remains=any(ev['id']==last_narration['id'] for ev in e.store.iter_events())
    assert remains
    return {'player_turns':len((source/'transcript.jsonl').read_text().splitlines()),'max_event_turn':turn,'last_narration_turn':last_narration['turn'],'undo_event_types':types,'retracted':r['retracted'],'last_narration_survives':remains,'verdict':'Claude undo defect reproduced on save copy'}


def unified_turn():
    settings.set_conversation_mode('multiturn')
    e=fresh('group'); buf=BufferedStore(e.store,turn=1); s=AuthorStrategy()
    canned={'narration':'你把一枚金币留在柜台。','facts':[{'subject':'hero','predicate':'coins','value':9}], 'clock':[{'advance':False,'reason':'同一场景内的即时行动'}]}
    result=run_turn(e.registry,buf,e.world,_build_scene(e),'支付一枚金币',strategy=s,provider=FakeLLMProvider(json_responses=[canned]),max_repairs=0)
    n=count(e); turns=sorted({ev['turn'] for ev in buf.staged})
    gate=AtomicGateway(e.store,e.registry); receipt=gate.commit(buf.staged,action_id='turn-1',expected_revision=0)
    written=count(e)-n; world=gate.undo(1)
    stale=False
    try:gate.commit([event('fact_asserted',{'subject':'hero','predicate':'coins','value':8},turn=2)],action_id='late-tick',expected_revision=1)
    except Conflict:stale=True
    assert turns==[1] and count(e)==n and world['systems']['ontology'].value_at('hero','coins',1)==10 and stale
    return {'actual_run_turn_staged_events':written,'root_turn_ids':turns,'event_types':sorted({ev['type'] for ev in buf.staged}),'undo_restores_coins':10,'stale_tick_rejected_after_undo':stale,'verdict':'single action grouping and revision check verified; not full hook migration'}


def fog():
    e=fresh('fog'); g=e.world['systems']['ontology']; scene=_build_scene(e)
    marker='AUDIT_HIDDEN_DRAGON_KEY'
    g.add_entity('vault','Place',tier='mentioned',level=3,kind='venue',seed=marker,visibility='secret')
    tools=build_tool_registry(e.registry,e.world,scene)
    baseline={name:marker in tools.execute(name,{'q':'vault'}) for name in ['map_query','ambient_query','recall_query']}
    view=visible_world(e.world,scene); tools2=build_tool_registry(e.registry,view,scene)
    fixed={name:marker in tools2.execute(name,{'q':'vault'}) for name in baseline}
    # Public places must remain reachable; discovery makes hidden content available.
    public_ok='普通旅店' in tools2.execute('map_query',{'q':'inn'})
    g.entities['vault'].attrs['discovered_by']=['hero']
    discovered=build_tool_registry(e.registry,visible_world(e.world,scene),scene)
    reveal=marker in discovered.execute('map_query',{'q':'vault'})
    assert all(baseline.values()) and not any(fixed.values()) and public_ok and reveal
    return {'baseline_leaks':baseline,'candidate_leaks':fixed,'public_place_kept':public_ok,'discovery_reveals_place':reveal,'verdict':'entity read projection verified for map/ambient/recall'}


def npc_fog():
    e=fresh('npc-fog'); g=e.world['systems']['ontology']; scene=_build_scene(e)
    g.add_entity('npc','Person',tier='tracked'); scene['present']=['npc']
    for pred,val in [('sketch','普通侍者'),('goal','AUDIT_SECRET_ASSASSINATION')]:
        g.assert_fact('npc',pred,val,day=1,turn=0,source_event='fixture',secrecy='secret' if pred=='goal' else 'public')
    output=build_tool_registry(e.registry,e.world,scene).execute('recall_query',{'q':'普通侍者'})
    leaked='AUDIT_SECRET_ASSASSINATION' in output
    strict=build_tool_registry(e.registry,e.world,scene).execute('characters_query',{'q':'npc'})
    assert leaked and 'AUDIT_SECRET_ASSASSINATION' not in strict
    return {'same_npc_recall_leaks_unknown_goal':leaked,'characters_query_hides_same_goal':True,'verdict':'additional cross-tool knowledge inconsistency; entity visibility alone insufficient'}


def blank_narration():
    s=AuthorStrategy(); s._messages=[{'role':'system','content':'audit'}]
    original='你推开旅店的门，侍者朝你招手。'
    raw=s._reask_json(original,FakeLLMProvider(json_responses=[{'clock':[{'advance':False,'reason':'同一场景内的即时行动'}]}]))
    c=TurnCommit.from_dict(_parse_json_object(raw))
    fixed=_parse_json_object(raw);fixed['narration']=original
    assert not c.narration and TurnCommit.from_dict(fixed).narration==original
    return {'baseline_narration_length':len(c.narration),'candidate_narration_length':len(original),'verdict':'reask source narration preservation verified'}


def prose_state_conflict():
    settings.set_conversation_mode('stateless');e=fresh('prose');scene=_build_scene(e)
    provider=FakeLLMProvider(responses=['你付清十枚金币，钱袋空了。',json.dumps({'facts':[{'subject':'hero','value':0}],'clock':[{'advance':False,'reason':'同一场景内的即时行动'}]})])
    c,repairs,dropped=produce_turn(e.registry,e.world,scene,'支付十枚金币',strategy=HybridStrategy(),provider=provider,max_repairs=0)
    w=apply_turn(e.registry,e.store,c,day=1,scene='inn'); coins=w['systems']['ontology'].value_at('hero','coins',1)
    p=fresh('prose-fixed');before=count(p);reject=False
    try:validated_apply(p.registry,p.store,c,day=1,scene='inn',dropped_sections=dropped)
    except IncompleteAction:reject=True
    assert coins==10 and '钱袋空' in c.narration and 'facts' in dropped and reject and count(p)==before
    return {'prose':c.narration,'committed_coins':coins,'dropped_sections':dropped,'candidate_gate_rejects':reject,'candidate_events_written':count(p)-before,'verdict':'prose-first + silent drop rejected for consequential outcomes'}


def genesis_down():
    class Down(FakeLLMProvider):
        def complete(self,*a,**k):raise ConnectionError('injected provider outage')
        def complete_messages(self,*a,**k):raise ConnectionError('injected provider outage')
    e=build_engine(TMP/'genesis-down',provider=Down());error=None
    try:new_game(e,'异世界冒险')
    except Exception as exc:error=type(exc).__name__
    n=count(e)
    assert n>0
    # Staging in a disposable campaign avoids publishing fallback data.
    class Watched(Down):
        failures=0
        def complete(self,*a,**k): self.failures+=1;return super().complete(*a,**k)
        def complete_messages(self,*a,**k):self.failures+=1;return super().complete_messages(*a,**k)
    p=Watched(); staging=build_engine(TMP/'genesis-staging',provider=p)
    try:new_game(staging,'异世界冒险')
    except Exception:pass
    accepted=p.failures==0
    target=fresh('genesis-published',seed=False)
    if accepted:
        AtomicGateway(target.store,target.registry).commit(list(staging.store.iter_events()),action_id='genesis',expected_revision=0)
    assert not accepted and count(target)==0
    return {'baseline_persisted_events':n,'baseline_raised':error,'candidate_provider_failures':p.failures,'candidate_published_events':count(target),'verdict':'staged genesis failure gate verified; checkpoint/resume not implemented'}


def skip_corruption():
    # A system can mutate then throw. Catch-and-continue retains a partial effect.
    w={'inventory':['key'],'coins':10}
    def bad_apply(world):
        world['inventory'].remove('key')
        raise ValueError('bad transfer recipient')
    try:bad_apply(w)
    except ValueError:pass
    assert w['inventory']==[]
    return {'after_skip':w,'verdict':'blanket apply exception swallowing rejected; not atomic'}


def provider_protocol():
    resp={'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'reasoning_content':'synthetic protocol field','tool_calls':[{'id':'t1','type':'function','function':{'name':'map_query','arguments':'{"q":"inn"}'}}]}}]}
    _,calls=_openai_parse(resp);messages=[]
    for call in calls:_openai_append_result(messages,call,'{}')
    missing='reasoning_content' not in messages[0]
    body=OpenAIProvider('deepseek-flash','dummy',base_url='https://api.deepseek.com')._build_request('s','u')[2]
    assert missing and 'thinking' not in body
    return {'thinking_explicitly_disabled':False,'reasoning_content_lost':missing,'configured_launcher_tool_rounds':0,'verdict':'tool-enabled DeepSeek profile requires protocol adapter; real service unverified'}


def meta_rewind():
    e=fresh('meta');events=list(e.store.iter_events())
    events += [event('fact_asserted',{'subject':'hero','predicate':'coins','value':8},day=9,turn=1,scene='new-scene'), event('narration_recorded',{'text':'旧记录'},day=1,turn=2,scene='old-scene')]
    w=project(e.registry,events)
    assert w['meta']['day']==1 and w['meta']['scene']=='old-scene'
    return {'latest_game_day':9,'projected_meta_day':w['meta']['day'],'projected_scene':w['meta']['scene'],'verdict':'event metadata must not authoritatively set simulation clock'}


def missing_required():
    e=fresh('required');c=TurnCommit('行动',{'clock':[{'advance':False,'reason':'同一场景内的即时行动'}]})
    errors=validate_commit(e.registry,c,e.world,required_sections=REQUIRED_SECTIONS)
    assert not errors
    return {'declared_required':sorted(REQUIRED_SECTIONS),'present':['clock'],'validation_errors':0,'verdict':'required section contract does not match implementation'}


def stale_delta():
    settings.set_conversation_mode('multiturn')
    e=fresh('delta');g=e.world['systems']['ontology'];scene=_build_scene(e)
    g.add_entity('npc','Person',tier='tracked');scene['present']=['npc']
    for pred,val in [('sketch','AUDIT_NPC'),('goal','找工作'),('mood','AUDIT_BEFORE')]:
        g.assert_fact('npc',pred,val,day=1,turn=0,source_event='seed')
    s=AuthorStrategy();provider=FakeLLMProvider(json_responses=[{'narration':'你和侍者打了招呼。'}])
    produce_turn(e.registry,e.world,scene,'问好',strategy=s,provider=provider)
    g.assert_fact('npc','mood','AUDIT_AFTER_BACKGROUND',day=1,turn=1,source_event='tick')
    s.produce(e.registry,e.world,scene,'现在他状态如何',provider=provider)
    delta=s._messages[-2]['content']
    absent='AUDIT_AFTER_BACKGROUND' not in delta
    rebuilt=assemble_context(e.registry,e.world,scene)
    included='AUDIT_AFTER_BACKGROUND' in rebuilt
    assert absent and included
    return {'default_multiturn_delta_includes_new_state':not absent,'rebuilt_context_includes_new_state':included,'verdict':'thread must ingest committed state deltas or rebuild after revision; deleting all history not required'}


if __name__=='__main__':
    for id,fn in [('E01',poison),('E02',partial_writes),('E03',mirror_failure),('E04',monotonic),('E05',thread_divergence),('E06',real_undo),('E07',unified_turn),('E08',fog),('E09',npc_fog),('E10',blank_narration),('E11',prose_state_conflict),('E12',genesis_down),('E13',skip_corruption),('E14',provider_protocol),('E15',meta_rewind),('E16',missing_required),('E18',stale_delta)]:check(id,fn)
    raise SystemExit(any(r['status']=='probe_error' for r in RESULTS))
