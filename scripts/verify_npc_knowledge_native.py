"""RCV129: separate NPC beliefs, private thoughts and explicit speech.

``projection DIR`` captures deterministic current/arrival evidence without a
model. ``native DIR`` incrementally replays all native calls through a normal
PlayerDashboard; add each pending raw response to DIR/responses.json.
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
from app.play import _build_scene
from context.native_authoring import native_authoring_context
from context.assembler import safe_authoring_handoff
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider
from loop.strategy import AuthorStrategy

PUBLIC = '公告说渡船停航'
ALICE = '旧告示说渡船全天通航'
BOB = '听说渡船仅在上午通航'
SECRET = 'HOST_SECRET_紫晶钥匙在井底'
THOUGHT = 'UNSPOKEN_我猜铃铛藏在柳树下'
STATEMENT = '请记住：橘色纸鹤停在第三块石头上。'
KEY = 'a_square.ferry_status'


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

    def read(self, name):
        path = self.directory / name
        return json.loads(path.read_text()) if path.exists() else []

    def complete_messages(self, messages, **kwargs):
        return self.capture({'method': 'complete_messages', 'messages': copy.deepcopy(messages), 'kwargs': kwargs})

    def complete(self, system, user, **kwargs):
        return self.capture({'method': 'complete', 'system': system, 'user': user, 'kwargs': kwargs})

    def capture(self, request):
        index = self.index
        self.index += 1
        if index < len(self.requests):
            assert request == self.requests[index], f'production request mismatch {index}'
        else:
            self.requests.append(request)
            write(self.directory / 'requests.json', self.requests)
        if index >= len(self.responses):
            write(self.directory / 'pending.json', {'index': index, 'request': request})
            raise Pending()
        return self.responses[index]


def seed(engine):
    declarations = [
        ('character_created', {'id': 'protagonist', 'sketch': '一位过路的旅人', 'goal': '了解渡船情况', 'tier': 'tracked'}),
        ('place_created', {'id': 'a_square', 'level': 2, 'kind': 'settlement', 'seed': '柳溪镇街口', 'label': '柳溪镇街口', 'tier': 'tracked'}),
        ('place_created', {'id': 'b_square', 'level': 2, 'kind': 'settlement', 'seed': '枫桥镇集市', 'label': '枫桥镇集市', 'tier': 'tracked'}),
        ('place_linked', {'a': 'a_square', 'b': 'b_square', 'travel_cost': 1}),
        ('entity_moved', {'who': 'protagonist', 'to': 'a_square'}),
        ('character_created', {'id': 'alice', 'name': '阿岚', 'sketch': '柳溪的杂货商阿岚', 'goal': '照料杂货', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'alice', 'to': 'a_square'}),
        ('character_created', {'id': 'bob', 'name': '白禾', 'sketch': '枫桥的船工白禾', 'goal': '照料船只', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'bob', 'to': 'b_square'}),
        ('fact_asserted', {'subject': 'a_square', 'predicate': 'ferry_status', 'value': PUBLIC, 'secrecy': 'public'}),
        ('fact_asserted', {'subject': 'a_square', 'predicate': 'private_marker', 'value': SECRET, 'secrecy': 'secret'}),
        ('knowledge_set', {'knower': 'alice', 'fact_key': KEY, 'value': ALICE, 'via': '旧告示'}),
        ('knowledge_set', {'knower': 'bob', 'fact_key': KEY, 'value': BOB, 'via': '传闻'}),
    ]
    events = [kernel_event(kind, day=1, scene='a_square', turn=0,
                           summary='合成设定', deltas=deltas) for kind, deltas in declarations]
    engine.store.append_many(events)
    engine.world = project(engine.registry, engine.store.iter_events())


def snapshot(engine):
    graph = engine.world['systems']['ontology']
    day = engine.world['meta']['day']
    return {
        'day': day,
        'author_handoff': safe_authoring_handoff(engine.world, _build_scene(engine), trusted_events=list(engine.store.iter_events())),
        'location': graph.neighbors('protagonist', 'located_in', day),
        'world_value': graph.value_at('a_square', 'ferry_status', day),
        'beliefs': {who: graph.value_at(who, 'knows:' + KEY, day) for who in ('alice', 'bob')},
        'knowledge_events': [copy.deepcopy(e['deltas']) for e in engine.store.iter_events()
                             if e['type'] == 'knowledge_set'],
    }


def projection(out):
    with tempfile.TemporaryDirectory(prefix='npc-projection-') as tmp:
        engine = build_engine(Path(tmp) / 'campaign', provider=Native(out), embedder=None)
        try:
            seed(engine)
            scene = _build_scene(engine)
            context = native_authoring_context(engine.world, scene, trusted_events=list(engine.store.iter_events()))
            observations = {'world_and_beliefs': snapshot(engine), 'scene': scene,
                            'context': context, 'rendered_handoff': safe_authoring_handoff(engine.world, scene, trusted_events=list(engine.store.iter_events()))}
            write(out / 'projection.json', observations)
            print('PROJECTION', json.dumps({'present': context['present_people'],
                  'arrival': context['conditional_arrivals']}, ensure_ascii=False))
        finally:
            engine.store.close()


def native(out):
    provider = Native(out)
    actions = [
        {'text': f'我只向阿岚说：“{STATEMENT}”我心里想：{THOUGHT}，没有说出口。',
         'recipient': 'alice', 'statement': STATEMENT},
        {'text': '我问阿岚，她对渡船现在的情况了解多少？'},
        {'text': '我去枫桥镇集市。'},
        {'text': '我问白禾，他对渡船现在的情况了解多少？'},
        {'text': '我返回柳溪镇街口。'},
    ]
    transcript = []
    with tempfile.TemporaryDirectory(prefix='npc-native-') as tmp, ExitStack() as stack:
        for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density', '_run_demote_on_leave'):
            stack.enter_context(patch('loop.turn.' + name, return_value=[]))
        engine = build_engine(Path(tmp) / 'campaign', provider=provider, embedder=None)
        seed(engine)
        dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), max_repairs=0)
        try:
            for index, action in enumerate(actions):
                if index == 2:
                    before = snapshot(engine)
                    engine.store.close()
                    engine = build_engine(Path(tmp) / 'campaign', provider=provider, embedder=None)
                    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), max_repairs=0)
                    write(out / 'reopen.json', {'before': before, 'after': snapshot(engine)})
                kwargs = {key: value for key, value in action.items() if key != 'text'}
                try:
                    response = dashboard.action(player_input=action['text'], action_id=f'knowledge-{index}',
                          expected_revision=dashboard.state()['revision'], **kwargs)
                except Pending:
                    write(out / 'progress.json', {'completed': transcript, 'pending_turn': index + 1,
                                                 'pending_action': action})
                    raise
                except Exception as exc:
                    write(out / 'failure.json', {'completed': transcript, 'failed_turn': index + 1,
                          'action': action, 'type': type(exc).__name__, 'error': str(exc),
                          'snapshot': snapshot(engine)})
                    print('OBSERVED FAILURE', index + 1, type(exc).__name__, str(exc))
                    return
                transcript.append({'turn': index + 1, 'action': action, 'response': response,
                                   'snapshot': snapshot(engine)})
                write(out / 'progress.json', {'completed': transcript})
            last = transcript[-1]['snapshot']
            knowledge = last['knowledge_events']
            checks = {
                'world_fact_unchanged': last['world_value'] == PUBLIC,
                'both_beliefs_unchanged': last['beliefs'] == {'alice': ALICE, 'bob': BOB},
                'only_alice_received_statement': any(e.get('knower') == 'alice' and e.get('value') == STATEMENT and e.get('source_kind') == 'player_statement' for e in knowledge) and not any(e.get('knower') == 'bob' and e.get('value') == STATEMENT for e in knowledge),
                'neither_heard_thought': not any(e.get('knower') in ('alice', 'bob') and THOUGHT in str(e.get('value')) for e in knowledge),
                'secret_not_in_requests': not any(SECRET in json.dumps(req, ensure_ascii=False) for req in provider.requests),
                'reopen_identical': json.loads((out / 'reopen.json').read_text())['before'] == json.loads((out / 'reopen.json').read_text())['after'],
                'b_handoffs_exclude_alice_statement': all(STATEMENT not in t['snapshot']['author_handoff'] for t in transcript if t['snapshot']['location'] == ['b_square']),
                'alice_statement_after_return': STATEMENT in transcript[-1]['snapshot']['author_handoff'],
            }
            write(out / 'result.json', {'transcript': transcript, 'request_count': provider.index, 'checks': checks,
                  'model': 'gpt-6.1-terra session generation', 'transport': 'local exact-request replay, no HTTP',
                  'disabled_hooks': ['digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density', '_run_demote_on_leave'],
                  'enabled': ['ordinary PlayerDashboard', 'AuthorStrategy', 'run_lore', 'SQLite', 'host speech receipt']})
            (out / 'pending.json').unlink(missing_ok=True)
            print('COMPLETE', len(transcript), 'requests', provider.index)
        except Pending:
            print('PENDING', provider.index - 1)
        finally:
            engine.store.close()


def main():
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == 'projection':
        projection(out)
    elif sys.argv[1] == 'native':
        native(out)
    else:
        raise ValueError('mode must be projection or native')


if __name__ == '__main__':
    main()
