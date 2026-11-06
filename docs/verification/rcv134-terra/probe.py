"""Actual non-demo default GUI author/reviewer capture; injected draft is labeled.

Usage: python docs/verification/rcv134-terra/probe.py DIR [natural|injected]
Append each freshly generated raw response to DIR/responses.json and rerun.
"""
import copy
import json
from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.engine import build_engine
from app.gui import _seed_demo_campaign, build_dashboard
from llm.provider import LLMProvider

ACTION = '我只观察，不去集市，不卖灯。'
INJECTED = {'narration': '你抵达集市，把铜灯卖了。', 'moves': [], 'places': [], 'cast': [], 'facts': [],
            'clock': [{'advance': False, 'days': 0, 'bands': 0, 'reason': '短暂观察'}]}

class Pending(BaseException):
    pass


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class Probe(LLMProvider):
    def __init__(self, path, mode):
        self.path, self.mode, self.index, self.calls = path, mode, 0, 0
        self.requests = self.load('requests.json')
        self.responses = self.load('responses.json')

    def load(self, name):
        file = self.path / name
        return json.loads(file.read_text()) if file.exists() else []

    def complete_messages(self, messages, **kwargs):
        return self.capture({'method': 'complete_messages', 'messages': copy.deepcopy(messages), 'kwargs': kwargs})

    def complete(self, system, user, **kwargs):
        return self.capture({'method': 'complete', 'system': system, 'user': user, 'kwargs': kwargs})

    def capture(self, request):
        call = self.calls
        self.calls += 1
        if self.mode == 'injected' and call == 0:
            write(self.path / 'injected_author_request.json', request)
            write(self.path / 'injected_draft.json', {'kind': 'deliberately injected, not native generation', 'proposal': INJECTED})
            return json.dumps(INJECTED, ensure_ascii=False)
        index = self.index
        self.index += 1
        if index < len(self.requests):
            assert request == self.requests[index], f'production request mismatch at {index}'
        else:
            self.requests.append(request); write(self.path / 'requests.json', self.requests)
        if index >= len(self.responses):
            write(self.path / 'pending.json', {'index': index, 'request': request})
            raise Pending()
        return self.responses[index]


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[2] if len(sys.argv) > 2 else 'natural'
    assert mode in ('natural', 'injected')
    provider = Probe(out, mode)
    with TemporaryDirectory(prefix='rcv134-') as tmp, ExitStack() as stack:
        for hook in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density', '_run_demote_on_leave'):
            stack.enter_context(patch('loop.turn.' + hook, return_value=[]))
        e = build_engine(tmp, provider=provider, embedder=None)
        _seed_demo_campaign(e); e.store.close()
        dashboard = build_dashboard(tmp, demo=False, provider=provider, max_repairs=0)
        try:
            assert type(dashboard.strategy).__name__ == 'GroundedAuthorStrategy'
            response = dashboard.action(player_input=ACTION, action_id='observe', expected_revision=dashboard.state()['revision'])
            world = dashboard.engine.world; graph = world['systems']['ontology']; day = world['meta']['day']
            narrations = [event['deltas']['text'] for event in dashboard.engine.store.iter_events()
                          if event['type'] == 'narration_recorded']
            write(out / 'result.json', {'mode': mode, 'author_is_injected': mode == 'injected',
                'strategy': type(dashboard.strategy).__name__, 'demo': dashboard.demo,
                'action': ACTION, 'response': response, 'published_narration': narrations[-1],
                'location': graph.neighbors('protagonist', 'located_in', day),
                'lamp_holder': graph.neighbors('old_brass_lantern', 'held_by', day),
                'native_call_count': provider.index,
                'transport': 'local exact production request replay, not HTTP',
                'disabled_hooks': ['digest_fleet','run_director','run_cascade','run_catchup','run_density','_run_demote_on_leave']})
            (out / 'pending.json').unlink(missing_ok=True)
            print('COMPLETE', mode, provider.index)
        except Pending:
            print('PENDING', mode, provider.index - 1)
        except Exception as exc:
            write(out / 'failure.json', {'type': type(exc).__name__, 'error': str(exc), 'mode': mode})
            print('FAILURE', type(exc).__name__, str(exc))
        finally:
            dashboard.engine.store.close()

if __name__ == '__main__':
    main()
