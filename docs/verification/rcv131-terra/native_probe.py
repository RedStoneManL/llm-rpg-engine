"""RCV131 synthetic PlayerDashboard play; incremental native prompt capture.

Run `python docs/verification/rcv131-terra/native_probe.py DIR` repeatedly.
Append a raw native response to DIR/responses.json for each pending request.
A fresh temporary SQLite campaign is reconstructed on every invocation.
"""
import copy
import json
from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.engine import build_engine
from app.gui import _seed_demo_campaign, PlayerDashboard
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider
from loop.strategy import AuthorStrategy

QUERY = '朱橘纸鹤停在第三块石头'

class Pending(BaseException):
    pass


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


class Native(LLMProvider):
    def __init__(self, out):
        self.out = out
        self.index = 0
        self.requests = self.load('requests.json')
        self.responses = self.load('responses.json')

    def load(self, name):
        path = self.out / name
        return json.loads(path.read_text()) if path.exists() else []

    def complete(self, system, user, **kwargs):
        return self.capture({'method': 'complete', 'system': system, 'user': user, 'kwargs': kwargs})

    def complete_messages(self, messages, **kwargs):
        return self.capture({'method': 'complete_messages', 'messages': copy.deepcopy(messages), 'kwargs': kwargs})

    def capture(self, request):
        index = self.index
        self.index += 1
        if index < len(self.requests):
            assert self.requests[index] == request, f'production request changed at {index}'
        else:
            self.requests.append(request)
            write(self.out / 'requests.json', self.requests)
        if index >= len(self.responses):
            write(self.out / 'pending.json', {'index': index, 'request': request})
            raise Pending()
        return self.responses[index]


def seed(engine):
    _seed_demo_campaign(engine)
    records = ['路人说：“' + QUERY + '。”'] + [f'客栈里的第{i}段平静经过。' for i in range(9)]
    for i, text in enumerate(records):
        engine.store.append(kernel_event('narration_recorded', day=1, scene='stonebridge_inn',
                            turn=i+1, summary='合成公开旧叙事',
                            deltas={'scene': 'stonebridge_inn', 'text': text}))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world['_revision'] = engine.store.revision


def snapshot(engine, dash):
    graph = engine.world['systems']['ontology']
    day = engine.world['meta']['day']
    return {'state': dash.state(),
            'lamp_holder': graph.neighbors('old_brass_lantern', 'held_by', day),
            'scene': graph.neighbors('protagonist', 'located_in', day),
            'day': day, 'band': engine.world['meta'].get('band'),
            'old_quote_in_event_log': any(e['type'] == 'narration_recorded' and QUERY in (e.get('deltas') or {}).get('text', '') for e in engine.store.iter_events())}


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    provider = Native(out); transcript = []
    actions = ['我把手里的旧铜灯放在客栈脚边。',
               '我捡起刚刚放在脚边的旧铜灯。',
               '我走到石桥集市。',
               '我在集市一直等到明天中午。']
    with TemporaryDirectory(prefix='rcv131-native-') as tmp, ExitStack() as stack:
        for hook in ('digest_fleet', 'run_cascade', 'run_catchup', 'run_director', 'run_density', '_run_demote_on_leave'):
            stack.enter_context(patch('loop.turn.' + hook, return_value=[]))
        engine = build_engine(tmp, provider=provider, embedder=None)
        seed(engine)
        dash = PlayerDashboard(engine, strategy=AuthorStrategy(), max_repairs=0)
        try:
            for i, text in enumerate(actions):
                before = snapshot(engine, dash)
                start = provider.index
                try:
                    response = dash.action(player_input=text, action_id=f'native-{i}', expected_revision=before['state']['revision'])
                except Pending:
                    write(out / 'progress.json', {'transcript': transcript, 'pending_action': text, 'before': before})
                    raise
                except Exception as exc:
                    write(out / 'failure.json', {'transcript': transcript, 'failed_action': text,
                          'type': type(exc).__name__, 'error': str(exc), 'before': before,
                          'after': snapshot(engine, dash), 'request_start': start, 'request_end': provider.index})
                    print('OBSERVED FAILURE', i+1, type(exc).__name__, str(exc))
                    return
                transcript.append({'action': text, 'response': response, 'after': snapshot(engine, dash),
                                   'request_start': start, 'request_end': provider.index})
                write(out / 'progress.json', {'transcript': transcript})
            before_reopen = snapshot(engine, dash)
            engine.store.close()
            engine = build_engine(tmp, provider=provider, embedder=None)
            dash = PlayerDashboard(engine, strategy=AuthorStrategy(), max_repairs=0)
            after_reopen = snapshot(engine, dash)
            recall = dash.recall(QUERY)
            sources = recall.get('sources') or []
            opened = dash.open_source(sources[0]['ref']) if sources else None
            pre_undo = snapshot(engine, dash)
            undo_result = dash.undo(expected_revision=pre_undo['state']['revision'])
            after_undo = snapshot(engine, dash)
            recall_after_undo = dash.recall(QUERY)
            stale_open = None
            if sources:
                try:
                    stale_open = {'result': dash.open_source(sources[0]['ref'])}
                except Exception as exc:
                    stale_open = {'type': type(exc).__name__, 'error': str(exc)}
            write(out / 'result.json', {'transcript': transcript, 'recall': recall, 'opened': opened,
                  'before_reopen': before_reopen, 'after_reopen': after_reopen,
                  'undo_result': undo_result, 'after_undo': after_undo, 'recall_after_undo': recall_after_undo, 'old_ref_after_undo': stale_open,
                  'request_count': provider.index, 'transport': 'local replay of actual production requests, not HTTP',
                  'model': 'gpt-6.1-terra session responses',
                  'disabled_hooks': ['digest_fleet','run_cascade','run_catchup','run_director','run_density','_run_demote_on_leave'],
                  'enabled': ['ordinary PlayerDashboard', 'AuthorStrategy', 'run_lore', 'SQLite']})
            (out / 'pending.json').unlink(missing_ok=True)
            print('COMPLETE', len(transcript), 'actions', provider.index, 'requests')
        except Pending:
            print('PENDING', provider.index - 1)
        finally:
            engine.store.close()

if __name__ == '__main__':
    main()
