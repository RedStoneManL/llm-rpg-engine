"""RCV135: incremental native multi-day play in a temporary SQLite campaign."""
import copy
import json
from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.engine import build_engine
from app.gui import build_dashboard
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider
from loop.lore import create_lore_line

STATEMENT = '请记住我还会回来。'
ACTIONS = [
    '我留在柳溪镇广场，观察周围。',
    '我留在广场等到明天早晨。',
    '我观察通往枫桥的道路，暂不前往。',
    '我仍留在广场，等到明天早晨。',
    '我沿路去枫桥集市。',
    '我在集市观察周围。',
    '我沿路返回柳溪镇广场。',
    '我向广场守桥人阿岚说：“请记住我还会回来。”',
    '我继续留在广场，观察周围。',
]

class Pending(BaseException):
    pass


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


class Native(LLMProvider):
    def __init__(self, out):
        self.out = out; self.index = 0
        self.requests = self.load('requests.json'); self.responses = self.load('responses.json')

    def load(self, name):
        path = self.out / name
        return json.loads(path.read_text()) if path.exists() else []

    def complete_messages(self, messages, **kwargs):
        return self.capture({'method': 'complete_messages', 'messages': copy.deepcopy(messages), 'kwargs': kwargs})

    def complete(self, system, user, **kwargs):
        return self.capture({'method': 'complete', 'system': system, 'user': user, 'kwargs': kwargs})

    def capture(self, request):
        index = self.index; self.index += 1
        if index < len(self.requests):
            assert request == self.requests[index], f'production request mismatch {index}'
        else:
            self.requests.append(request); write(self.out / 'requests.json', self.requests)
        if index >= len(self.responses):
            write(self.out / 'pending.json', {'index': index, 'request': request})
            raise Pending()
        return self.responses[index]


def seed(engine):
    declarations = [
        ('character_created', {'id': 'protagonist', 'sketch': '留意镇上变化的旅人', 'goal': '了解两镇的情况', 'tier': 'tracked'}),
        ('place_created', {'id': 'a_square', 'level': 2, 'kind': 'settlement', 'seed': '柳溪镇广场', 'label': '柳溪镇广场', 'tier': 'tracked'}),
        ('place_created', {'id': 'b_market', 'level': 2, 'kind': 'settlement', 'seed': '枫桥集市', 'label': '枫桥集市', 'tier': 'tracked'}),
        ('place_created', {'id': 'c_hill', 'level': 2, 'kind': 'settlement', 'seed': '青山驿', 'label': '青山驿', 'tier': 'tracked'}),
        ('place_linked', {'a': 'a_square', 'b': 'b_market', 'travel_cost': 1}),
        ('place_linked', {'a': 'a_square', 'b': 'c_hill', 'travel_cost': 1}),
        ('entity_moved', {'who': 'protagonist', 'to': 'a_square'}),
        ('character_created', {'id': 'alice', 'sketch': '柳溪广场的守桥人阿岚', 'goal': '照看广场', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'alice', 'to': 'a_square'}),
        ('character_created', {'id': 'bob', 'sketch': '枫桥集市的卖菜人白禾', 'goal': '经营摊位', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'bob', 'to': 'b_market'}),
    ]
    for kind, delta in declarations:
        engine.store.append(kernel_event(kind, day=1, scene='a_square', turn=0, summary='合成开局', deltas=delta))
    lines = [
        {'id':'bridge', 'anchor':'a_square', 'l3_anchor':'a_square', 'about':'两镇道路水情', 'description':'两镇之间的道路水情',
         'stages':[{'hint':'道路被漫水暂时封闭', 'route_effect':{'a':'a_square','b':'b_market','passable':False}},
                   {'hint':'水退后道路恢复通行', 'route_effect':{'a':'a_square','b':'b_market','passable':True}},
                   {'hint':'路边仍留着水痕'}]},
        {'id':'market', 'anchor':'b_market', 'l3_anchor':'b_market', 'about':'枫桥摊位', 'description':'枫桥集市的摊位动静',
         'stages':[{'hint':'菜摊开始收拢篷布'}, {'hint':'集市上出现一排空菜筐'}, {'hint':'摊主重新摆出几篮青菜'}]},
        {'id':'hill', 'anchor':'c_hill', 'l3_anchor':'c_hill', 'about':'青山驿的信件', 'description':'青山驿传信动静',
         'stages':[{'hint':'驿站门边多了一只信筒'}, {'hint':'驿站里有人整理信件'}, {'hint':'驿站门口贴出收信时刻'}]},
    ]
    for line in lines:
        line.update({'complexity':'medium', 'cadence':'day', 'threshold':100, 'lifespan_days':30, 'trigger':'注意当地变化'})
        create_lore_line(engine.store, line, day=1, scene='a_square', turn=0)
    engine.world = project(engine.registry, engine.store.iter_events())


def snapshot(dash):
    world = dash.engine.world; graph = world['systems']['ontology']; day = world['meta']['day']
    from context.capabilities import scene_capabilities
    from app.play import _build_scene
    lines = world['systems']['lore']['lines']
    events = list(dash.engine.store.iter_events())
    return {'day': day, 'band': world['meta'].get('band'),
            'location': graph.neighbors('protagonist', 'located_in', day),
            'allowed_destinations': sorted(scene_capabilities(world, _build_scene(dash.engine)).allowed_move_ids),
            'speech_recipients': dash.state()['speech_recipients'],
            'lines': {key: {'stage_idx': lines[key]['stage_idx'], 'clues': lines[key]['clues_dropped'],
                            'last_advanced_day': lines[key].get('last_advanced_day')} for key in ('bridge','market','hill')},
            'stage_events': [{'id': e['deltas']['id'], 'stage': e['deltas']['stage_idx'], 'day': e['day']} for e in events if e['type']=='lore_advanced'],
            'route_events': [{'day': e['day'], 'deltas': e['deltas']} for e in events if e['type']=='route_changed'],
            'speech_events': [e['deltas'] for e in events if e['type']=='knowledge_set' and e['deltas'].get('source_kind')=='player_statement']}


def main():
    out=Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); provider=Native(out); transcript=[]
    with TemporaryDirectory(prefix='rcv135-') as tmp, ExitStack() as stack:
        for hook in ('digest_fleet','run_director','run_cascade','run_catchup','run_density','_run_demote_on_leave'):
            stack.enter_context(patch('loop.turn.'+hook, return_value=[]))
        engine=build_engine(tmp, provider=provider, embedder=None); seed(engine); engine.store.close()
        dash=build_dashboard(tmp, demo=False, provider=provider, max_repairs=0)
        try:
            assert type(dash.strategy).__name__ == 'GroundedAuthorStrategy'
            for i, action in enumerate(ACTIONS):
                if i==4:
                    before=snapshot(dash); dash.engine.store.close(); dash=build_dashboard(tmp, demo=False, provider=provider, max_repairs=0)
                    write(out/'reopen.json', {'before':before,'after':snapshot(dash)})
                extra={'recipient':'alice','statement':STATEMENT} if i==7 else {}
                before=snapshot(dash); start=provider.index
                try:
                    result=dash.action(player_input=action, action_id=f'world-{i}', expected_revision=dash.state()['revision'], **extra)
                except Pending:
                    write(out/'progress.json', {'transcript':transcript,'pending_action':action,'turn':i+1,'before':before})
                    raise
                except Exception as exc:
                    write(out/'failure.json', {'transcript':transcript,'action':action,'turn':i+1,'error_type':type(exc).__name__, 'error':str(exc),'before':before,'after':snapshot(dash),'request_start':start,'request_end':provider.index})
                    print('OBSERVED FAILURE',i+1,type(exc).__name__,str(exc)); return
                transcript.append({'turn':i+1,'action':action,'response':result,'snapshot':snapshot(dash),'request_start':start,'request_end':provider.index})
                write(out/'progress.json', {'transcript':transcript})
            write(out/'result.json',{'transcript':transcript,'request_count':provider.index,'model':'gpt-6.1-terra session generation','strategy':type(dash.strategy).__name__,'demo':False,'transport':'local exact-request replay, no HTTP', 'disabled_hooks':['digest_fleet','run_director','run_cascade','run_catchup','run_density','_run_demote_on_leave']})
            (out/'pending.json').unlink(missing_ok=True); print('COMPLETE',len(transcript),'actions',provider.index,'calls')
        except Pending:
            print('PENDING',provider.index-1)
        finally:
            dash.engine.store.close()

if __name__=='__main__':main()
