"""RCV128: replay native Author responses through a normal PlayerDashboard.

Usage: python scripts/verify_encounter_native.py DIR
An empty directory pauses at pending.json. Append the native response string to
responses.json and rerun; prior production requests must match exactly.
"""
from __future__ import annotations

import copy
import json
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.engine import build_engine
from app.gui import PlayerDashboard
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider
from loop.strategy import AuthorStrategy


class Pending(BaseException):
    pass


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class Native(LLMProvider):
    def __init__(self, directory):
        self.directory = directory
        self.index = 0
        self.requests = self.read('requests.json')
        self.responses = self.read('responses.json')

    def read(self, filename):
        path = self.directory / filename
        return json.loads(path.read_text()) if path.exists() else []

    def complete_messages(self, messages, **kwargs):
        return self.capture({'method': 'complete_messages', 'messages': copy.deepcopy(messages), 'kwargs': kwargs})

    def complete(self, system, user, **kwargs):
        return self.capture({'method': 'complete', 'system': system, 'user': user, 'kwargs': kwargs})

    def capture(self, request):
        index = self.index
        self.index += 1
        if index < len(self.requests):
            assert self.requests[index] == request, f'production request mismatch at {index}'
        else:
            self.requests.append(request)
            write(self.directory / 'requests.json', self.requests)
        if index >= len(self.responses):
            write(self.directory / 'pending.json', {'index': index, 'request': request})
            raise Pending()
        return self.responses[index]


def seed(engine):
    events = []
    for kind, deltas in [
        ('character_created', {'id': 'protagonist', 'sketch': '一位初到镇上的旅人', 'goal': '认识两镇的居民', 'tier': 'tracked'}),
        ('place_created', {'id': 'a_square', 'level': 2, 'kind': 'settlement', 'seed': '柳溪镇街口', 'label': '柳溪镇街口', 'tier': 'tracked'}),
        ('place_created', {'id': 'b_square', 'level': 2, 'kind': 'settlement', 'seed': '枫桥镇集市', 'label': '枫桥镇集市', 'tier': 'tracked'}),
        ('place_linked', {'a': 'a_square', 'b': 'b_square', 'travel_cost': 1}),
        ('entity_moved', {'who': 'protagonist', 'to': 'a_square'}),
    ]:
        events.append(kernel_event(kind, day=1, scene='a_square', turn=0,
                                   summary='合成场景', deltas=deltas))
    engine.store.append_many(events)
    engine.world = project(engine.registry, engine.store.iter_events())


def snapshot(dashboard):
    world = dashboard.engine.world
    day = world['meta']['day']
    graph = world['systems']['ontology']
    people = {key: {'attrs': copy.deepcopy(value.attrs),
                    'location': graph.neighbors(key, 'located_in', day)}
              for key, value in graph.entities.items() if value.etype == 'Person'}
    receipts = [{'type': event['type'], 'deltas': event.get('deltas')}
                for event in dashboard.engine.store.iter_events()
                if 'receipt' in event['type'] or event['type'] == 'knowledge_set']
    return {'state': dashboard.state(), 'people': people, 'receipt_events': receipts}


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    provider = Native(directory)
    transcript = []
    with tempfile.TemporaryDirectory(prefix='encounter-native-') as tmp, ExitStack() as stack:
        for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density', '_run_demote_on_leave'):
            stack.enter_context(patch('loop.turn.' + name, return_value=[]))
        engine = build_engine(Path(tmp) / 'campaign', provider=provider, embedder=None)
        seed(engine)
        dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), max_repairs=0)
        actions = [
            '在柳溪镇街口，我当面遇见木匠林月，向她点头问好。',
            '我问林月：“你听说过枫桥镇的画师沈砚吗？”我只是在问，沈砚不在这里。',
            '我沿路去枫桥镇集市。',
            '我沿原路返回柳溪镇街口。',
        ]
        try:
            person_id = None
            for index, text in enumerate(actions):
                if index == 2:
                    before = snapshot(dashboard)
                    engine.store.close()
                    engine = build_engine(Path(tmp) / 'campaign', provider=provider, embedder=None)
                    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), max_repairs=0)
                    after = snapshot(dashboard)
                    write(directory / 'reopen.json', {'before': before, 'after': after})
                state = dashboard.state()
                extra = {}
                if index == 1:
                    matches = list(state['speech_recipients'])
                    if len(matches) != 1:
                        write(directory / 'failure.json', {'turn': 2, 'reason': '林月没有作为唯一可选同场人物出现',
                                                          'matches': matches, 'snapshot': snapshot(dashboard), 'transcript': transcript})
                        print('OBSERVED FAILURE: person not available as recipient')
                        return
                    person_id = matches[0]['id']
                    extra = {'recipient': person_id, 'statement': '你听说过枫桥镇的画师沈砚吗？'}
                try:
                    result = dashboard.action(player_input=text, action_id=f'encounter-{index}',
                                              expected_revision=state['revision'], **extra)
                except Pending:
                    write(directory / 'progress.json', {'transcript': transcript, 'pending_action': text,
                                                        'pending_turn': index + 1, 'recipient': extra})
                    raise
                except Exception as exc:
                    write(directory / 'failure.json', {'turn': index + 1, 'type': type(exc).__name__,
                          'error': str(exc), 'transcript': transcript, 'snapshot': snapshot(dashboard)})
                    print('OBSERVED FAILURE:', type(exc).__name__, str(exc))
                    return
                transcript.append({'turn': index + 1, 'input': text, 'response': result,
                                   'snapshot': snapshot(dashboard)})
                write(directory / 'progress.json', {'transcript': transcript})
            write(directory / 'result.json', {'transcript': transcript, 'person_id': person_id,
                  'observations': {'name_visible_first': any('林月' in v['label'] for v in transcript[0]['snapshot']['state']['speech_recipients']),
                    'selected_conversation_committed': bool(person_id),
                    'returned_recipient': any(v['id']==person_id for v in transcript[3]['snapshot']['state']['speech_recipients']),
                    'remote_person_not_materialized': len(transcript[1]['snapshot']['people'])==2,
                    'away_recipients_empty': not transcript[2]['snapshot']['state']['speech_recipients']},
                  'request_count': provider.index, 'generation': 'gpt-6.1-terra local native exact-request replay',
                  'disabled_hooks': ['digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density', '_run_demote_on_leave'],
                  'enabled': ['normal PlayerDashboard', 'AuthorStrategy', 'run_lore', 'SQLite', 'host speech receipt']})
            (directory / 'pending.json').unlink(missing_ok=True)
            print('COMPLETE', provider.index)
        except Pending:
            print('PENDING', provider.index - 1)
        finally:
            engine.store.close()


if __name__ == '__main__':
    main()
