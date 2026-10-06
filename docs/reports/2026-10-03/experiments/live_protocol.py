"""Small real DeepSeek protocol experiment; credentials/reasoning never logged."""
import copy
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, '/root/rpg-engine-app')
from llm.provider import OpenAIProvider, _openai_parse, _openai_append_result

config = dict(line.split('=', 1) for line in Path('/root/.config/llm-rpg-engine/deepseek.env').read_text().splitlines() if '=' in line)
results = {'model': config['DEEPSEEK_MODEL'], 'calls': [], 'scope': 'synthetic read-only tool; real HTTP protocol, not full game acceptance'}

def save():
    (ROOT / 'evidence/live-protocol.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')

def post(body, label):
    if len(results['calls']) >= 8:
        raise RuntimeError('Protocol call budget reached')
    started = time.perf_counter()
    req = urllib.request.Request(config['DEEPSEEK_BASE_URL'].rstrip('/') + '/chat/completions',
        data=json.dumps(body).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + config['DEEPSEEK_API_KEY'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            obj = json.load(response)
        results['calls'].append({'label': label, 'http': 200, 'seconds': time.perf_counter()-started,
            'usage': obj.get('usage'), 'model_returned': obj.get('model'),
            'finish_reason': obj['choices'][0].get('finish_reason')})
        save()
        return obj
    except urllib.error.HTTPError as exc:
        results['calls'].append({'label': label, 'http': exc.code, 'seconds': time.perf_counter()-started,
            'error': exc.read().decode('utf-8', 'replace')[:1600]})
        save()
        return None

tools = [{'type':'function', 'function': {'name':'lookup_balance',
    'description':'Read the confirmed coin balance of hero.',
    'parameters': {'type':'object', 'properties':{}, 'additionalProperties':False}}}]
messages = [{'role':'user', 'content':'必须先调用 lookup_balance 查询余额；拿到工具结果后只回答金币数，不要重复调用。'}]
body = {'model':config['DEEPSEEK_MODEL'], 'messages':messages, 'tools':tools,
    'thinking':{'type':'enabled'}, 'reasoning_effort':'low', 'max_tokens':1024}
try:
    first = post(body, 'thinking_first_tool_request')
    if first is None:
        raise RuntimeError('Initial thinking request failed')
    msg = first['choices'][0]['message']
    _, calls = _openai_parse(first)
    results['first_tool_count'] = len(calls)
    results['reasoning_field_present'] = bool(msg.get('reasoning_content'))
    if not calls:
        raise RuntimeError('Model did not emit expected tool call')
    old_messages = copy.deepcopy(messages)
    for call in calls:
        _openai_append_result(old_messages, call, '{"coins":7}')
    old = post({**body, 'messages':old_messages}, 'legacy_adapter_followup')
    results['legacy_answer'] = old['choices'][0]['message'].get('content') if old else None

    full_messages = copy.deepcopy(messages)
    full_messages.append({k:v for k,v in msg.items() if k in {'role','content','reasoning_content','tool_calls'}})
    for call in calls:
        full_messages.append({'role':'tool','tool_call_id':call['id'],'content':'{"coins":7}'})
    repaired = post({**body, 'messages':full_messages}, 'full_assistant_message_followup')
    results['preserved_message_answer'] = repaired['choices'][0]['message'].get('content') if repaired else None
    results['preserved_message_ok'] = bool(repaired and '7' in results['preserved_message_answer'])

    class DisabledThinking(OpenAIProvider):
        def _post(self, url, headers, body, **kwargs):
            answer = post({**body,'thinking':{'type':'disabled'}}, 'legacy_adapter_thinking_disabled')
            if answer is None:
                raise RuntimeError('Disabled-thinking protocol failed')
            return answer
    provider = DisabledThinking(config['DEEPSEEK_MODEL'], config['DEEPSEEK_API_KEY'],
        base_url=config['DEEPSEEK_BASE_URL'], max_tokens=256)
    called = []
    def execute(name, arguments):
        assert name == 'lookup_balance'
        called.append(name)
        return '{"coins":7}'
    answer = provider.complete_with_tools(copy.deepcopy(messages), tools, execute, max_tool_rounds=2)
    results.update(disabled_thinking_answer=answer, disabled_thinking_tool_calls=len(called),
        disabled_thinking_ok=bool(called and '7' in answer), status='completed')
except Exception as exc:
    results.update(status='incomplete', error_type=type(exc).__name__, error=str(exc))
save()
print(json.dumps({k:v for k,v in results.items() if k != 'calls'}, ensure_ascii=False))
