"""Synthetic 24-turn world-thread replay. Usage: python scripts/verify_world_threads_native.py next DIR.

Creates a fresh temporary SQLite campaign on every invocation. First 25 turns use
a deterministic provider; subsequent two turns use exact native responses supplied
in DIR/responses.json. Unrelated background hooks are suppressed, run_lore remains.
"""
from __future__ import annotations
import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.engine import build_engine
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider
from loop.strategy import AuthorStrategy
from loop.turn import run_turn

class Pending(BaseException):
    pass


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


class Provider(LLMProvider):
    def __init__(self, out, long=False):
        self.long = long
        self.out = out
        self.turn = 0
        self.i = 0
        self.foreground = []
        self.requests=json.loads((out/'requests.json').read_text()) if (out/'requests.json').exists() else []
        self.responses=json.loads((out/'responses.json').read_text()) if (out/'responses.json').exists() else []
    def complete_messages(self,messages,**kwargs):
        req={'method':'complete_messages','messages':copy.deepcopy(messages),'kwargs':kwargs}
        if self.turn < (81 if self.long else 25):
            self.foreground.append(req)
            moving=self.turn==(80 if self.long else 24)
            time_advance = (self.turn < 64 if self.long else self.turn < 24 and self.turn % 4 == 3)
            c={'narration':'你继续追查北塔的旧账。' if not moving else '你走进南桥镇。', 'moves':[{'who':'protagonist','to':'b_market'}] if moving else [], 'places':[],'cast':[],'facts':[], 'clock':[{'advance': time_advance,'days':0,'bands':1 if time_advance else 0,'reason':'等候并追查' if not moving else '短途返回'}]}
            return json.dumps(c,ensure_ascii=False)
        i=self.i; self.i+=1
        if i<len(self.requests):
            assert req==self.requests[i],f'production request mismatch {i}'
        else:
            self.requests.append(req); write(self.out/'requests.json',self.requests)
        if i>=len(self.responses):
            write(self.out/'pending.json',{'index':i,'request':req}); raise Pending()
        return self.responses[i]
    def complete(self,system,user,**kwargs):
        raise RuntimeError('unexpected prose/repair call')

def ev(kind,delta): return kernel_event(kind,day=1,scene='a_street',turn=0,summary='合成长局起点',deltas=delta)

def seed(e, neutral=False):
    events=[ev('character_created',{'id':'protagonist','sketch':'追查北塔旧账的旅人','goal':'查清北塔旧账','tier':'tracked'})]
    for id,level,parent,label in [('a_town',2,None,'北塔镇'),('a_street',3,'a_town','北塔镇街口'),('b_town',2,None,'南桥镇'),('b_market',3,'b_town','南桥集市')]:
        d={'id':id,'level':level,'kind':'settlement' if level==2 else 'venue','seed':label,'label':label,'tier':'tracked'}
        if parent:d['parent']=parent
        events.append(ev('place_created',d))
    events += [ev('place_linked',{'a':'a_street','b':'b_market','travel_cost':1}),ev('entity_moved',{'who':'protagonist','to':'a_street'}),ev('quest_opened',{'id':'main_a','summary':'查清北塔旧账的来历'})]
    for lid,complexity,about,word in [('b_mill','simple','南桥水磨坊故障','水磨坊'),('b_boats','medium','南桥渡船纠纷','渡船')]:
        events.append(ev('lore_created',{'id':lid,'complexity':complexity,'about':about,'anchor':'b_town','l3_anchor':'b_market','description':about,'trigger':'在南桥集市观察','stages':[{'hint':hint} for hint in ((['水磨坊的轮轴传来断续的敲响','水磨坊的木轮停在浅水里','水磨坊门前堆起待磨的麦袋','磨坊匠人正检查断裂的木销','磨坊旁多了一堆新削的木料'] if lid=='b_mill' else ['渡船旁有人争论摆渡次序','渡船的缆绳被重新系到木桩上','等船的人排到了集市口','船夫在木牌上写下新的摆渡时刻','码头边有人清点等候的货物']) + [f'{word}的变化继续演进第{i}阶段' for i in range(6,31)])],'threshold':100,'lifespan_days':40}))
    if neutral:
        for event in events:
            if event['type']=='lore_created':
                hints = (['水磨坊的木轮已经停转，门前堆着麦袋','磨坊匠人正在修理折断的木销','磨坊门前的麦袋排成了长列','水磨坊轮轴旁散落新削的木片','水磨坊的木轮缓缓转动起来'] if event['deltas']['id']=='b_mill' else ['渡口候船的人排到了集市口','船夫在木牌上写下新的渡船时刻','渡口旁堆着尚未装船的货包','候船的人正按木牌上的时刻列队','渡船载着第一批货物离岸'])
                for j,hint in enumerate(hints,15): event['deltas']['stages'][j]['hint']=hint
    e.store.append_many(events); e.world=project(e.registry,e.store.iter_events())

LIVE_ACTIONS = [
    '我在北塔镇街口看看旧账线索从哪里查起。',
    '我留在北塔镇等到明天早晨，再继续调查。',
    '我查看街口周围，寻找与旧账有关的线索。',
    '暂时没有头绪，我在镇上再等到明天早晨。',
    '我重新审视街口，看看有没有值得追问的细节。',
    '我整理思路，决定先记住旧账，暂时不替自己编造答案。',
    '我沿路去南桥集市。',
    '我在集市停下，看看周围有什么动静。',
    '我走近磨坊，观察正在发生什么，暂不触碰任何东西。',
    '我在原地稍等一会儿，留意周围的反应。',
    '我返回北塔镇街口。',
    '回到街口后，我看看接下来能怎样继续追查旧账。',
]


class LiveProvider(Provider):
    """Capture every native foreground request, with no deterministic answers."""

    def complete_messages(self, messages, **kwargs):
        request = {'method': 'complete_messages', 'messages': copy.deepcopy(messages),
                   'kwargs': kwargs}
        return self.capture(request)

    def complete(self, system, user, **kwargs):
        request = {'method': 'complete', 'system': system, 'user': user,
                   'kwargs': kwargs}
        return self.capture(request)

    def capture(self, request):
        index = self.i
        self.i += 1
        if index < len(self.requests):
            assert request == self.requests[index], f'production request mismatch {index}'
        else:
            self.requests.append(request)
            write(self.out / 'requests.json', self.requests)
        if index >= len(self.responses):
            write(self.out / 'pending.json', {'index': index, 'request': request})
            raise Pending()
        return self.responses[index]


def run_live(out):
    """Replay each raw response through real Author turns; pause before next call."""
    out.mkdir(parents=True, exist_ok=True)
    provider = LiveProvider(out)
    transcript = []
    with tempfile.TemporaryDirectory(prefix='world-live-') as tmp, ExitStack() as stack:
        for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density'):
            stack.enter_context(patch('loop.turn.' + name, return_value=[]))
        engine = build_engine(Path(tmp) / 'campaign', provider=provider, embedder=None)
        seed(engine)
        strategy = AuthorStrategy()
        try:
            for index, action in enumerate(LIVE_ACTIONS):
                scene = _build_scene(engine)
                before_call = provider.i
                try:
                    result = run_turn(engine.registry, engine.store, engine.world, scene,
                                      action, strategy=strategy, provider=provider,
                                      max_repairs=0)
                except Pending:
                    write(out / 'progress.json', {'completed': transcript, 'pending_action': action,
                                                  'pending_turn': index + 1})
                    raise
                except Exception as exc:
                    write(out / 'failure.json', {'completed': transcript, 'failed_turn': index + 1,
                          'action': action, 'error_type': type(exc).__name__, 'error': str(exc),
                          'request_start': before_call, 'request_end': provider.i,
                    'scene_present_before': list(scene.get('present', [])),
                    'created_person_exists': bool(world['systems']['ontology'].get_entity('a_paperkeeper')),
                    'created_person_location': world['systems']['ontology'].neighbors('a_paperkeeper', 'located_in', day)})
                    print('FAILED', index + 1, type(exc).__name__, str(exc))
                    return
                engine.world = result.world
                world = engine.world
                lines = world['systems']['lore']['lines']
                day = world['meta']['day']
                transcript.append({'turn': index + 1, 'action': action,
                    'narration': result.narration, 'day': day,
                    'band': world['meta'].get('band'),
                    'location': world['systems']['ontology'].neighbors('protagonist', 'located_in', day),
                    'main': copy.deepcopy(lines['main_a']),
                    'other_lines': {lid: copy.deepcopy(lines[lid]) for lid in ('b_mill', 'b_boats')},
                    'request_start': before_call, 'request_end': provider.i,
                    'scene_present_before': list(scene.get('present', [])),
                    'created_person_exists': bool(world['systems']['ontology'].get_entity('a_paperkeeper')),
                    'created_person_location': world['systems']['ontology'].neighbors('a_paperkeeper', 'located_in', day)})
                write(out / 'progress.json', {'completed': transcript})
            days = [row['day'] for row in transcript]
            write(out / 'result.json', {'mode': 'real AuthorStrategy / 12 consecutive native foreground actions',
                  'model': 'gpt-6.1-terra session generation', 'transport': 'local exact-request replay; no HTTP',
                  'hooks_enabled': ['run_lore', '_run_demote_on_leave'],
                  'hooks_disabled': ['digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density'],
                  'request_count': provider.i, 'elapsed_days': max(days) - min(days),
                  'transcript': transcript})
            (out / 'pending.json').unlink(missing_ok=True)
            print('COMPLETE', len(transcript), 'days', max(days) - min(days), 'requests', provider.i)
        except Pending:
            print('PENDING', provider.i - 1)
        finally:
            engine.store.close()


def main():
    out=Path(sys.argv[2])
    if sys.argv[1] == 'live':
        run_live(out)
        return
    out.mkdir(parents=True,exist_ok=True); neutral = sys.argv[1]=='neutral'; long = sys.argv[1] in ('long','neutral'); expected = sys.argv[3] if len(sys.argv)>3 else None; p=Provider(out,long=long)
    with tempfile.TemporaryDirectory(prefix='world-threads-') as tmp, ExitStack() as stack:
        for name in ('digest_fleet','run_director','run_cascade','run_catchup','run_density','_run_demote_on_leave'):
            if neutral and name=='_run_demote_on_leave': continue
            stack.enter_context(patch('loop.turn.'+name,return_value=[]))
        e=build_engine(Path(tmp)/'campaign',provider=p,embedder=None); seed(e,neutral=neutral)
        history=[]; strategy=AuthorStrategy()
        try:
            for i in range(83 if neutral else 81 if long else 27):
                p.turn=i
                if i==(80 if long else 24):
                    before=copy.deepcopy(e.world['systems']['lore']); e.store.close(); e=build_engine(Path(tmp)/'campaign',provider=p,embedder=None)
                    assert e.world['systems']['lore']==before
                scene=_build_scene(e); inputs=('我继续在北塔镇追查北塔旧账。' if i<(80 if long else 24) else '我沿路回到南桥集市。' if i==(80 if long else 24) else '我在南桥集市观察水磨坊和渡船的迹象，同时记得北塔旧账。' if i==25 else '我接着观察两处变化，并想想它们与北塔旧账是否有关。')
                if neutral and i>=81: inputs = '我在集市停下来观察周围。' if i==81 else '我继续留意眼前的动静。'
                res=run_turn(e.registry,e.store,e.world,scene,inputs,strategy=strategy,provider=p,max_repairs=0)
                e.world=res.world
                lines=e.world['systems']['lore']['lines']; history.append({'turn':i+1,'day':e.world['meta']['day'],'band':e.world['meta'].get('band'), 'location':e.world['systems']['ontology'].neighbors('protagonist','located_in',e.world['meta']['day']), 'stages':{x:lines[x]['stage_idx'] for x in ('b_mill','b_boats')},'main_state':lines['main_a']['state']})
                if i==(79 if long else 23):
                    assert all(h['main_state']=='明' for h in history)
                    assert not any(any(marker in json.dumps(r,ensure_ascii=False) for marker in ('b_mill','b_boats','水磨坊','渡船')) for r in p.foreground), 'remote clues disclosed before visiting B'
                    stages=history[-1]['stages']
                    if expected=='frozen': assert all(n==-1 for n in stages.values()),stages
                    if expected=='advance': assert all(n==(15 if long else 0) for n in stages.values()),stages
                    if long:
                        days={}
                        for event in e.store.iter_events():
                            if event.get('type')=='lore_advanced' and event.get('deltas',{}).get('id') in ('b_mill','b_boats'):
                                key=(event['deltas']['id'],event['day']); days[key]=days.get(key,0)+1
                        assert all(n==1 for n in days.values()),days
                        assert history[-1]['day']==17 and history[-1]['band']==0
                        if expected=='advance':
                            for lid in ('b_mill','b_boats'):
                                assert sorted(day for (name,day) in days if name==lid)==list(range(2,18)),(lid,days)
                        if expected=='frozen': assert not days,days
                    write(out/'before_return.json',{'history':history,'lines':{x:lines[x] for x in ('main_a','b_mill','b_boats')},'requests_a_count':len(p.foreground),'requests_a_sha256':hashlib.sha256(json.dumps(p.foreground,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'remote_clue_leak':False})
                if i==(80 if long else 24):
                    assert history[-1]['location']==['b_market']
                    if expected in ('frozen','advance'):
                        previous=history[-2]['stages']; assert all(stages_now==previous[lid]+1 for lid,stages_now in history[-1]['stages'].items())
                    write(out/'after_return.json',{'history':history,'lines':{x:lines[x] for x in ('main_a','b_mill','b_boats')},'return_request_sha256':hashlib.sha256(json.dumps(p.foreground[-1],ensure_ascii=False,sort_keys=True).encode()).hexdigest()})
            assert len(p.responses)==p.i, 'unused native responses'
            write(out/'result.json',{'advances':[{'id':v.get('deltas',{}).get('id'),'day':v.get('day'),'stage':v.get('deltas',{}).get('stage_idx')} for v in e.store.iter_events() if v.get('type')=='lore_advanced'], 'history':history,'request_count':p.i,'lines':{x:e.world['systems']['lore']['lines'][x] for x in ('main_a','b_mill','b_boats')},'limitations':('Synthetic first 80 turns; real run_lore and demote active; digest/director/cascade/catchup/density disabled; local prompt replay, not HTTP.' if neutral else 'Synthetic deterministic pre-return turns; run_lore active; digest/director/cascade/catchup/density/demote disabled; local prompt replay, not HTTP.')})
            (out/'pending.json').unlink(missing_ok=True); print('PASS',p.i)
        except Pending: print('PENDING',p.i-1)
        finally: e.store.close()
if __name__=='__main__':main()
