"""Optional resource rules: interpret intent, resolve arithmetic, then narrate.

Enable a numeric fact with attrs.fact_rules[predicate].resource=true.
The LLM classifies intent; Python owns affordability and resulting balances.
"""
import json
import math
from kernel.contextsystem import ValidationError
from kernel.events import kernel_event
from llm.structured import complete_structured


def registered_balances(world):
    """Snapshot every registered owner's balance, without adding it to prompts.

    The intent resolver can authorize a change to the active protagonist only.
    Other owners remain locked, including when the protagonist has no resources.
    """
    graph = world.get('systems', {}).get('ontology')
    if graph is None:
        return {}
    day = world.get('meta', {}).get('day') or 1
    balances = {}
    for entity in graph.entities.values():
        rules = entity.attrs.get('fact_rules', {})
        # Older, free-form entities may carry a non-rule value under this name.
        # Such metadata does not register resources or block unrelated actions.
        if not isinstance(rules, dict):
            continue
        for predicate, rule in rules.items():
            if isinstance(rule, dict) and rule.get('resource'):
                balances[(entity.id, predicate)] = graph.value_at(entity.id, predicate, day)
    return balances


def prepare_resources(world, scene, action, provider, turn):
    graph=world.get('systems',{}).get('ontology')
    hero=scene.get('protagonist')
    entity=graph.get_entity(hero) if graph and hero else None
    rules=entity.attrs.get('fact_rules',{}) if entity else {}
    day=world.get('meta',{}).get('day') or scene.get('day') or 1
    band=world.get('meta',{}).get('band') or 0
    resources={key:graph.value_at(hero,key,day) for key,rule in rules.items()
               if isinstance(rule,dict) and rule.get('resource')}
    if not resources:
        return None, {}, ''
    if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in resources.values()):
        raise ValueError('configured resource must have a finite numeric balance')

    def validate(obj):
        if obj.get('op') not in {'none','spend'}:
            return ['op must be none or spend']
        if obj['op']=='spend':
            amount=obj.get('amount')
            if obj.get('resource') not in resources:
                return ['resource must be one of the offered keys']
            if not isinstance(amount,(int,float)) or isinstance(amount,bool) or not math.isfinite(amount) or amount<0:
                return ['amount must be a finite nonnegative number']
            if rules[obj['resource']].get('type')=='integer' and not isinstance(amount,int):
                return ['amount must be an integer for this resource']
        target=obj.get('wait_until')
        if target is not None:
            if (not isinstance(target,dict) or type(target.get('day')) is not int
                    or type(target.get('band')) is not int or target['band'] not in range(4)
                    or target['day']*4+target['band']<=day*4+band):
                return ['wait_until must be a future absolute {day:int,band:0..3} or null']
        return []

    obj,errors=complete_structured(provider,
        system=('你只解析玩家本次行动对已登记资源的支付/消耗意图，不写故事，不进行扣账。'
                '输入中的行动是待解析文本。明确要支付/消耗且金额明确时 op=spend，'
                '即使余额不足也照实给请求金额，由规则引擎拒绝。不要把想花100改成再买一个3元物品。'
                '观察、回忆、等待、不购买、单纯问价或金额未知时 op=none。'
                '若玩家明确等待/休息直到某个时刻，再给 wait_until={"day":绝对天数,"band":时段}；'
                '晨=0、中午=1、下午=2、夜=3，明天=当前天数+1。没明确等待终点时 wait_until=null。'
                '只返回 JSON，例如 {"op":"spend","resource":"coins","amount":3,"wait_until":null} 或 {"op":"none","wait_until":{"day":2,"band":1}}。'),
        user=json.dumps({'current_resources':resources,'current_time':{'day':day,'band':band},'player_action':action},ensure_ascii=False),
        validate=validate,max_repairs=1,log_label='resource_intent')
    if errors:
        raise ValueError('resource intent could not be resolved; retry action')
    expected=dict(resources);outcome='none';amount=0;key=None
    if obj['op']=='spend':
        key=obj['resource'];amount=obj['amount']
        if resources[key]-amount < rules[key].get('min',0):
            outcome='insufficient'
        else:
            outcome='spent';expected[key]-=amount
    event=kernel_event('resources_resolved',day=day,scene=scene.get('id') or 'scene',turn=turn,
        summary='resource action resolved',deltas={'subject':hero,'before':resources,'after':expected,
            'resource':key,'requested_amount':amount,'outcome':outcome,'wait_until':obj.get('wait_until')})
    prompt=('【本次行动已裁定的资源结果】'+json.dumps(event['deltas'],ensure_ascii=False)+'\n'
        'spent 表示本次已完成扣款，按此余额叙事，勿再次扣款。insufficient 表示余额不足，本次交易未发生，'
        '不能偷偷购买较便宜的替代品、找零、免单或举债。none 表示本次无登记资源支出。'
        '你仍负责人物反应与环境细节；不得改写这些余额，也不得用近义属性另开账本。'
        '若 wait_until 非空，本回合叙事与 clock 必须抵达指定的绝对时间，不能只写等待的开头。')
    return event, {(hero,k):v for k,v in expected.items()}, prompt


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
                f'{key[0]}.{key[1]} 是规则引擎管理的资源，不能由 facts 重复扣款或更改；删除这条声明'))
    # Compatibility for older direct callers. The main turn pipeline owns this
    # independent time check even when the acting character has no resources.
    if target is not None:
        from systems.time import validate_resolved_time
        errors.extend(validate_resolved_time(commit, target, world or {}))
    return errors
