"""Optional resource rules: interpret intent, resolve arithmetic, then narrate.

Enable a numeric fact with attrs.fact_rules[predicate].resource=true.
The LLM classifies intent; Python owns affordability and resulting balances.
"""
import json
import math
import re
from kernel.clock import BANDS
from kernel.contextsystem import ValidationError
from kernel.events import kernel_event
from llm.structured import complete_structured


# A literal absolute wait is an engine command even on campaigns without
# numeric resources. Ambiguous dialogue/questions remain ordinary actions.
_WAIT = re.compile(r'(?:我(?:们)?\s*)?(?:一直|原地)?(?:等到|等候到|等待到|休息到|睡到|守到)\s*'
                   r'(今天|明天|次日|后天|第([1-9]\d*)天)(早晨|清晨|早上|晨|中午|下午|夜晚|晚上)[。.!！ ]*')
_PURCHASE = re.compile(r'(?:我(?:要|想)?|我要)?(?:付[^，。！？；]{1,24}[，,]?\s*)?(?:购买|买下|买)'
                       r'(?!不|不起|不到)([^，。！？；]{1,40}?)(?:[，,](?:支付|花|付).+)?[。.!！ ]*')


def explicit_wait(action, world):
    match = _WAIT.fullmatch(action.strip()) if isinstance(action, str) else None
    if not match:
        return None
    current = world.get('meta', {})
    day = current.get('day') or 1
    date, absolute, band = match.groups()
    end = int(absolute) if absolute else day + {'今天':0, '明天':1, '次日':1, '后天':2}[date]
    band = {'早晨':'晨','早上':'晨','清晨':'晨','晚上':'夜晚'}.get(band, band)
    target = {'day':end, 'band':BANDS.index(band)}
    if (end, target['band']) <= (day, current.get('band') or 0):
        return None
    return target


def purchase_label(action):
    match = _PURCHASE.fullmatch(action.strip()) if isinstance(action, str) else None
    return match.group(1).strip() if match else None


def resource_intent_errors(intent, resources, rules, *, day, band):
    """Validate structured resource intent independently of provider parsing."""
    if not isinstance(intent, dict) or intent.get('op') not in {'none', 'spend', 'buy'}:
        return ['op must be none, spend or buy']
    if intent['op'] in {'spend','buy'}:
        amount = intent.get('amount')
        if intent.get('resource') not in resources:
            return ['resource must be one of the offered keys']
        if (not isinstance(amount, (int, float)) or isinstance(amount, bool)
                or not math.isfinite(amount) or amount < 0):
            return ['amount must be a finite nonnegative number']
        if rules[intent['resource']].get('type') == 'integer' and not isinstance(amount, int):
            return ['amount must be an integer for this resource']
    if intent['op'] == 'buy' and intent['amount'] <= 0:
        return ['buy requires a positive price']
    if intent['op'] == 'buy' and not (isinstance(intent.get('item'), str) and 0 < len(intent['item'].strip()) <= 80):
        return ['buy requires the requested item label']
    target = intent.get('wait_until')
    if target is not None:
        if (not isinstance(target, dict) or type(target.get('day')) is not int
                or type(target.get('band')) is not int or target['band'] not in range(4)
                or target['day'] * 4 + target['band'] <= day * 4 + band):
            return ['wait_until must be a future absolute {day:int,band:0..3} or null']
    return []


def resolve_resource_intent(intent, resources, rules, *, subject, day, band, scene, turn):
    """Deterministically resolve a previously structured resource intent.

    Parsing free text remains the caller's responsibility.  This function owns
    only validation, affordability, the durable event and the author-facing
    constraint, making multi-resource arithmetic testable without any model.
    """
    errors = resource_intent_errors(intent, resources, rules, day=day, band=band)
    if errors:
        raise ValueError('resource intent could not be resolved; retry action')
    expected = dict(resources)
    outcome = 'none'
    amount = 0
    key = None
    if intent['op'] in {'spend','buy'}:
        key = intent['resource']
        amount = intent['amount']
        if resources[key] - amount < rules[key].get('min', 0):
            outcome = 'insufficient'
        else:
            outcome = 'spent'
            expected[key] -= amount
    event = kernel_event('resources_resolved', day=day, scene=scene or 'scene', turn=turn,
        summary='resource action resolved', deltas={'subject': subject, 'before': dict(resources),
            'after': expected, 'resource': key, 'requested_amount': amount,
            'outcome': outcome, 'wait_until': intent.get('wait_until')})
    if intent['op'] == 'buy' and outcome == 'spent':
        event['deltas']['purchase'] = {'id': f'purchase_{event["id"]}', 'label': intent['item'].strip(), 'to': subject}
    prompt = ('【本次行动已裁定的资源结果】' + json.dumps(event['deltas'], ensure_ascii=False) + '\n'
        'spent 表示本次已完成扣款，按此余额叙事，勿再次扣款。insufficient 表示余额不足，本次交易未发生，'
        '不能偷偷购买较便宜的替代品、找零、免单或举债。none 表示本次无登记资源支出。'
        '你仍负责人物反应与环境细节；不得改写这些余额，也不得用近义属性另开账本。'
        '若 wait_until 非空，本回合叙事与 clock 必须抵达指定的绝对时间，不能只写等待的开头。')
    return event, {(subject, key): value for key, value in expected.items()}, prompt


def prepare_resources(world, scene, action, provider, turn):
    graph=world.get('systems',{}).get('ontology')
    hero=scene.get('protagonist')
    entity=graph.get_entity(hero) if graph and hero else None
    rules=entity.attrs.get('fact_rules',{}) if entity else {}
    day=scene.get('day') or 1
    band=world.get('meta',{}).get('band') or 0
    resources={key:graph.value_at(hero,key,day) for key,rule in rules.items()
               if isinstance(rule,dict) and rule.get('resource')}
    if not resources:
        return None, {}, ''
    if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in resources.values()):
        raise ValueError('configured resource must have a finite numeric balance')

    obj,errors=complete_structured(provider,
        system=('你只解析玩家本次行动对已登记资源的支付/消耗意图，不写故事，不进行扣账。'
                '输入中的行动是待解析文本。明确要支付/消耗且金额明确时 op=spend，'
                '即使余额不足也照实给请求金额，由规则引擎拒绝。不要把想花100改成再买一个3元物品。'
                '按明确价格购买实物时 op=buy，item 为玩家指定的物品名称；普通付款或消耗为 spend。'
                '观察、回忆、等待、不购买、单纯问价或金额未知时 op=none。'
                '若玩家明确等待/休息直到某个时刻，再给 wait_until={"day":绝对天数,"band":时段}；'
                '晨=0、中午=1、下午=2、夜=3，明天=当前天数+1。没明确等待终点时 wait_until=null。'
                '只返回 JSON，例如 {"op":"spend","resource":"coins","amount":3,"wait_until":null} 或 {"op":"none","wait_until":{"day":2,"band":1}}。'),
        user=json.dumps({'current_resources':resources,'current_time':{'day':day,'band':band},'player_action':action},ensure_ascii=False),
        validate=lambda intent: resource_intent_errors(intent, resources, rules, day=day, band=band),
        max_repairs=1,log_label='resource_intent')
    if errors:
        raise ValueError('resource intent could not be resolved; retry action')
    requested = purchase_label(action)
    if requested and obj['op'] == 'spend':
        obj = {**obj, 'op':'buy', 'item':requested}
    if obj['op'] == 'buy' and obj['item'].strip() not in action:
        raise ValueError('purchase item must come from this action')
    wait = explicit_wait(action, world)
    if wait:
        obj = {**obj, 'wait_until': wait}
    return resolve_resource_intent(
        obj, resources, rules, subject=hero, day=day, band=band,
        scene=scene.get('id'), turn=turn,
    )


def validate_resources(commit, expected, target=None, world=None):
    errors=[]
    facts=commit.sections.get('facts') or []
    if not isinstance(facts,list): facts=[]
    for i,fact in enumerate(facts):
        if not isinstance(fact,dict): continue
        key=(fact.get('subject'),fact.get('predicate'))
        try: locked=key in expected
        except TypeError: continue
        if locked and fact.get('value')!=expected[key]:
            errors.append(ValidationError('facts',f'[{i}].value','resolved_resource',
                f'{key[0]}.{key[1]} 已由规则裁定为 {expected[key]}，不得重复扣款或更改'))
    if target:
        from kernel.clock import advance
        current=world.get('meta',{})
        day=current.get('day') or 1;band=current.get('band') or 0
        decl=commit.sections.get('clock') or []
        try:
            clock=decl[0]
            actual=advance(day,band,clock.get('days',0) if clock.get('advance') else 0,
                           clock.get('bands',0) if clock.get('advance') else 0)
        except (IndexError,KeyError,TypeError,AttributeError,ValueError):
            actual=None
        if actual!=(target['day'],target['band']):
            delta=(target['day']-day)*4+target['band']-band
            errors.append(ValidationError('clock','','resolved_time',
                f'明确等待必须抵达第{target["day"]}天时段{target["band"]}；advance=true,days={delta//4},bands={delta%4}'))
    return errors
