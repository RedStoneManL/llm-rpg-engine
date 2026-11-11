"""Live native-model exchange through the normal dashboard, without patched hooks.

The selected gpt-6.1-terra agent writes one response envelope for each production
request. No prewritten DM answers, external API credentials, or HTTP inference.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from app.engine import build_engine
from app.gui import build_dashboard
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider, _run_tool_loop, _openai_parse, _openai_append_result
from loop.lore import create_lore_line


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


class NativeProvider(LLMProvider):
    model = 'gpt-6.1-terra'

    def __init__(self, out):
        self.out = out
        self.index = 0
        self.action = None
        self.calls = []

    def exchange(self, messages, tools=None, **kwargs):
        index = self.index
        self.index += 1
        if index >= 70:
            raise RuntimeError('Bounded live experiment reached 70 model requests')
        request = {'messages': copy.deepcopy(messages), 'tools': copy.deepcopy(tools), 'kwargs': kwargs}
        digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        frame_names = [f.function for f in inspect.stack()[1:14]]
        record = {'index': index, 'model': self.model, 'action': self.action,
                  'request_sha256': digest, 'call_path': frame_names, 'request': request}
        write(self.out / 'requests' / f'{index:03}.json', record)
        write(self.out / 'pending.json', record)
        print('MODEL_PENDING', index, self.action, '/'.join(frame_names[:5]), flush=True)
        response_path = self.out / 'responses' / f'{index:03}.json'
        started = time.monotonic()
        while not response_path.exists():
            if time.monotonic() - started > 1500:
                raise TimeoutError(f'Native model response timeout at {index}')
            time.sleep(0.25)
        response = json.loads(response_path.read_text())
        if response.get('request_sha256') != digest:
            raise ValueError(f'Native response does not match request {index}')
        message = response['message']
        if not isinstance(message, dict) or not ('content' in message or message.get('tool_calls')):
            raise ValueError('Expected assistant content or tool_calls')
        if message.get('tool_calls') and not tools:
            raise ValueError('Tools unavailable for this request')
        self.calls.append({'index': index, 'action': self.action, 'call_path': frame_names,
                           'request_sha256': digest, 'model_selection': self.model,
                           'request_utf8_bytes': len(json.dumps(request, ensure_ascii=False).encode()),
                           'response_utf8_bytes': len(json.dumps(message, ensure_ascii=False).encode()),
                           'exchange_elapsed_seconds': round(time.monotonic() - started, 3),
                           'tool_names': [t['function']['name'] for t in message.get('tool_calls', [])]})
        write(self.out / 'calls.json', self.calls)
        (self.out / 'pending.json').unlink(missing_ok=True)
        return {'choices': [{'message': message}]}

    def complete(self, system, user, **kwargs):
        return self.complete_messages([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], **kwargs)

    def complete_messages(self, messages, **kwargs):
        response = self.exchange(messages, **kwargs)
        return response['choices'][0]['message'].get('content') or ''

    def supports_tools(self):
        return True

    def complete_with_tools(self, messages, tools, tool_executor, *, max_tool_rounds=3, **kwargs):
        return _run_tool_loop(messages, tools, tool_executor,
                              post=lambda m, t: self.exchange(m, t, **kwargs),
                              parse=_openai_parse, append_result=_openai_append_result,
                              max_tool_rounds=max_tool_rounds)


STATEMENT = '青竹铃铛明晨响四次。'
FUTURE = '明晚在西钟楼见面，带上紫铜罗盘。'
SECRET = '紫晶钥匙藏在井底的双层陶罐里。'


def seed(engine):
    declarations = [
        ('character_created', {'id': 'protagonist', 'name': '旅人', 'sketch': '来柳溪探路的旅人', 'goal': '弄清渡船消息并继续旅行', 'tier': 'tracked'}),
    ]
    for pid, label in [('a_square', '柳溪镇街口'), ('b_market', '枫桥镇集市'), ('c_hill', '青山驿')]:
        declarations.append(('place_created', {'id': pid, 'level': 2, 'kind': 'settlement', 'seed': label, 'label': label, 'tier': 'tracked'}))
    declarations += [
        ('place_linked', {'a': 'a_square', 'b': 'b_market', 'travel_cost': 1}),
        ('place_linked', {'a': 'a_square', 'b': 'c_hill', 'travel_cost': 1}),
        ('entity_moved', {'who': 'protagonist', 'to': 'a_square'}),
        ('character_created', {'id': 'alice', 'name': '阿岚', 'sketch': '柳溪杂货商，做事稳妥，说话简短', 'goal': '照料杂货铺', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'alice', 'to': 'a_square'}),
        ('character_created', {'id': 'bob', 'name': '白禾', 'sketch': '枫桥船工，对不确定的消息会留余地', 'goal': '照料渡船', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'bob', 'to': 'b_market'}),
        ('fact_asserted', {'subject': 'a_square', 'predicate': 'ferry_status', 'value': '今日公告说渡船停航', 'secrecy': 'public'}),
        ('fact_asserted', {'subject': 'a_square', 'predicate': 'hidden_cache', 'value': SECRET, 'secrecy': 'secret'}),
        ('knowledge_set', {'knower': 'alice', 'fact_key': 'a_square.ferry_status', 'value': '旧告示说渡船全天通航', 'via': '旧告示'}),
        ('knowledge_set', {'knower': 'bob', 'fact_key': 'a_square.ferry_status', 'value': '听说渡船只在上午通航', 'via': '传闻'}),
    ]
    engine.store.append_many([kernel_event(kind, day=1, scene='a_square', turn=0, summary='合成开局', deltas=delta) for kind, delta in declarations])
    for key, anchor, topic, stages in [
        ('market', 'b_market', '枫桥菜摊搬迁', ['菜贩把竹筐移到棚檐下', '旧摊位留下空木架，新摊移到井边', '井边新摊摆满青菜，旧棚开始拆卸']),
        ('mail', 'c_hill', '青山驿邮路调整', ['驿站门边多了封口的信筒', '驿站门前贴出临时收信时刻', '驿站有人装好邮袋准备启程']),
    ]:
        create_lore_line(engine.store, {'id': key, 'anchor': anchor, 'l3_anchor': anchor, 'about': topic, 'description': topic,
                                      'stages': [{'hint': h} for h in stages], 'complexity': 'medium', 'cadence': 'day',
                                      'threshold': 100, 'lifespan_days': 30, 'trigger': '注意当地变化'}, day=1, scene='a_square', turn=0)
    engine.world = project(engine.registry, engine.store.iter_events())


def snapshot(dash):
    world = dash.engine.world
    graph = world['systems']['ontology']
    day = world['meta']['day']
    events = list(dash.engine.store.iter_events())
    return {'meta': copy.deepcopy(world['meta']), 'revision': dash.engine.store.revision,
            'location': graph.neighbors('protagonist', 'located_in', day),
            'beliefs': {who: graph.value_at(who, 'knows:a_square.ferry_status', day) for who in ['alice', 'bob']},
            'canonical_ferry': graph.value_at('a_square', 'ferry_status', day),
            'lines': {k: {'stage_idx': v.get('stage_idx'), 'status': v.get('status'), 'last_advanced_day': v.get('last_advanced_day')}
                      for k, v in world['systems']['lore']['lines'].items()},
            'knowledge_events': [e for e in events if e['type'] == 'knowledge_set'],
            'event_types': {kind: sum(e['type'] == kind for e in events) for kind in sorted({e['type'] for e in events})}}


ACTIONS = [
    {'text': '我问阿岚：“你对渡船的情况了解多少？这个消息是从哪儿来的？”'},
    {'text': '我只把一句话告诉阿岚。我心里猜铃铛藏在柳树下，但这句话没有说出口。', 'recipient': 'alice', 'statement': STATEMENT},
    {'text': '我沿路前往枫桥镇集市。'},
    {'text': '我问白禾：“阿岚有没有托你带什么话？还有，你听说渡船什么时候开？”'},
    {'text': '我留在枫桥镇等到明天早晨，再看看集市有什么变化。'},
    {'text': '我走近菜摊，问摊主搬摊会不会影响今天买菜，看看接下来能做些什么。'},
    {'text': '我沿路返回柳溪镇街口。'},
    {'text': '我问阿岚：“我出发前让你记住的那句话，你还记得吗？你有没有告诉别人？”'},
    {'text': '我在柳溪镇休息到明天早晨，留意街口和通往青山驿的方向。'},
    {'text': '我把一个见面约定只告诉阿岚。', 'recipient': 'alice', 'statement': FUTURE},
    {'control': 'undo_and_reopen'},
    {'text': '我问阿岚：“我们之前定过见面的时间和地点吗？没有的话就先不约。”'},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--campaign', type=Path, required=True)
    ap.add_argument('--resume-from', type=Path, help='Prior failed run; retain its files and resume the unchanged saved campaign')
    ap.add_argument('--start-action', type=int, default=1)
    args = ap.parse_args()
    out, campaign = args.out.resolve(), args.campaign.resolve()
    if (campaign.exists() and not args.resume_from) or (out / 'manifest.json').exists():
        raise RuntimeError('Use a fresh output and campaign; never overwrite a previous live run')
    if args.resume_from and not (args.resume_from / 'failure.json').exists():
        raise RuntimeError('Resume must identify a retained failed run')
    out.mkdir(parents=True, exist_ok=True)
    provider = NativeProvider(out)
    write(out / 'manifest.json', {'model': provider.model, 'transport': 'native selected-model agent via request-hashed local mailbox',
                                'external_http_model_calls': 0, 'disabled_hooks': [], 'demo': False,
                                'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
                                'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                'changed_source_sha256': {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                                                         for name in ['context/capabilities.py', 'context/native_authoring.py']},
                                'resume_from': str(args.resume_from.resolve()) if args.resume_from else None,
                                'start_action': args.start_action,
                                'actions': ACTIONS, 'max_requests': 70,
                                'limits': ['synthetic campaign', 'one sample', 'native exchange time is not API latency', 'no provider-reported token usage', 'DSH host not booted']})
    if not args.resume_from:
        engine = build_engine(campaign, provider=provider, embedder=None)
        seed(engine)
        engine.store.close()
    dash = build_dashboard(campaign, demo=False, provider=provider)
    transcript = []
    write(out / 'initial.json', {'state': dash.state(), 'snapshot': snapshot(dash)})
    try:
        for n, action in enumerate(ACTIONS, 1):
            if n < args.start_action:
                continue
            provider.action = n
            if action.get('control'):
                before = snapshot(dash)
                result = dash.undo(expected_revision=dash.state()['revision'])
                after_undo = snapshot(dash)
                dash.engine.store.close()
                dash = build_dashboard(campaign, demo=False, provider=provider)
                write(out / 'undo.json', {'before': before, 'result': result, 'after_undo': after_undo,
                                         'after_reopen': snapshot(dash)})
                continue
            if n == 7:
                before = snapshot(dash)
                dash.engine.store.close()
                dash = build_dashboard(campaign, demo=False, provider=provider)
                write(out / 'cold-reopen.json', {'before': before, 'after': snapshot(dash)})
            start = provider.index
            before = snapshot(dash)
            extra = {k: v for k, v in action.items() if k != 'text'}
            result = dash.action(player_input=action['text'], action_id=f'terra-live-{n}',
                                 expected_revision=dash.state()['revision'], **extra)
            transcript.append({'action_number': n, 'input': action, 'result': result,
                               'before': before, 'after': snapshot(dash), 'request_start': start, 'request_end': provider.index})
            write(out / 'transcript.json', transcript)
            print('TURN_DONE', n, 'model_requests', provider.index - start, 'day', snapshot(dash)['meta']['day'], flush=True)
        write(out / 'recall.json', {'past': dash.recall('青竹铃铛'), 'retracted': dash.recall('紫铜罗盘')})
        write(out / 'events.json', list(dash.engine.store.iter_events(include_retracted=True)))
        write(out / 'result.json', {'status': 'complete', 'actions_completed': len(transcript), 'model_requests': provider.index,
                                  'snapshot': snapshot(dash), 'state': dash.state(), 'disabled_hooks': []})
    except BaseException as exc:
        write(out / 'failure.json', {'type': type(exc).__name__, 'error': str(exc), 'traceback': traceback.format_exc(),
                                   'actions_completed': len(transcript), 'model_requests': provider.index,
                                   'snapshot': snapshot(dash)})
        raise
    finally:
        dash.engine.store.close()
        write(out / 'done.json', {'finished': True, 'failed': (out / 'failure.json').exists()})


if __name__ == '__main__':
    main()
