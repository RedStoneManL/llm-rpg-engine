"""Explicit delivery promises: pure checks, settlement and public ledger text.

Free-form legacy quests keep their existing semantics. A delivery is one unique
Object, not a stack quantity. Deadlines mark lateness, never silently fail a quest.
"""
from __future__ import annotations

import copy

from kernel.clock import advance, band_name
from kernel.contextsystem import ValidationError
from kernel.turncommit import TurnCommit


def objective_problems(value, world):
    if (not isinstance(value, dict) or value.get('kind') != 'deliver'
            or set(value) - {'kind', 'item', 'recipient', 'due'}):
        return ['objective 必须为 deliver 交付约定，字段为 kind/item/recipient/可选 due']
    graph = world.get('systems', {}).get('ontology')
    for field, types in (('item', {'Object'}), ('recipient', {'Person', 'Place'})):
        ref = value.get(field)
        entity = graph.get_entity(ref) if graph and isinstance(ref, str) else None
        if not entity or entity.etype not in types:
            return [f'objective.{field} 必须引用已存在的正确类型实体']
    if 'due' in value:
        due = value['due']
        if (not isinstance(due, dict) or set(due) != {'day', 'band'}
                or type(due.get('day')) is not int or due['day'] < 1
                or type(due.get('band')) is not int or due['band'] not in range(4)):
            return ['objective.due 必须是 {day:正整数, band:0..3}，以该时段为含边界的截止时刻']
    return []


def _rows(commit, key):
    rows = commit.sections.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _time(world, commit=None):
    meta = world.get('meta', {})
    day, band = meta.get('day') or 1, meta.get('band') or 0
    if commit is not None:
        for row in _rows(commit, 'clock')[:1]:
            if row.get('advance'):
                day, band = advance(day, band, row.get('days', 0), row.get('bands', 0))
    return {'day': day, 'band': band}


def _delivery_basis(commit, line, world):
    objective = line['objective']
    transfers = [row for row in _rows(commit, 'items')
                 if row.get('op') == 'transfer' and row.get('item') == objective['item']]
    if transfers:
        return 'current_turn' if transfers[-1].get('to') == objective['recipient'] else None
    graph = world.get('systems', {}).get('ontology')
    opened = line.get('surfaced_turn')
    if graph and type(opened) is int:
        for relation in graph.relations_at(objective['item'], 'held_by', _time(world)['day']):
            # Possession predating this promise is not evidence of fulfilling it.
            if relation.dst == objective['recipient'] and relation.ingest_turn > opened:
                return 'earlier_turn'
    return None


def delivery_errors(commit, world):
    lines = world.get('systems', {}).get('lore', {}).get('lines', {})
    errors = []
    seen = set()
    for i, row in enumerate(_rows(commit, 'quests')):
        lid, op = row.get('id'), row.get('op')
        if not isinstance(lid, str):
            continue  # The owning system reports malformed ids.
        line = lines.get(lid) if isinstance(lid, str) else None
        objective = row.get('objective') if op == 'open' else (line or {}).get('objective')
        if not objective or objective_problems(objective, world):
            continue  # LoreSystem diagnoses malformed declarations.
        hint = None
        if lid in seen:
            hint = '同一交付任务每回合只能声明一次变更，不能同时完成和放弃'
        seen.add(lid)
        if op != 'open' and 'objective' in row:
            hint = '已接取的交付条件不能在推进或结算时改写'
        if op == 'open' and any(r.get('item') == objective['item'] for r in _rows(commit, 'items')):
            hint = '交付约定须先接取再执行；本回合不要同时新建约定并转移该物品'
        if op == 'resolve' and line:
            basis = _delivery_basis(commit, line, world)
            if row.get('outcome') == 'abandoned':
                if basis == 'current_turn':
                    hint = '本回合实际交付与放弃声明矛盾，请选择与行动一致的结局'
            elif not basis:
                hint = ('约定物品尚未实际交到指定接收者手中，口头声称不算交付。'
                        '任务仍在进行时用 advance 或不改任务；明确拒绝继续时才用 resolve + outcome:abandoned')
            elif row.get('outcome') not in (None, 'delivered'):
                hint = '交付任务的 outcome 只能为 delivered 或明确放弃时的 abandoned'
        if hint:
            errors.append(ValidationError('quests', f'[{i}]', 'delivery_outcome', hint))
    return errors


def bind_delivery_outcomes(commit, world):
    """Called only after candidate validation. Add actual deliveries and host timing."""
    lines = world.get('systems', {}).get('lore', {}).get('lines', {})
    sections = copy.deepcopy(commit.sections)
    rows = list(sections.get('quests') or [])
    now = _time(world, commit)
    changed = False
    for lid, line in lines.items():
        objective = line.get('objective')
        if line.get('state') != '明' or not objective or objective_problems(objective, world):
            continue
        row = next((r for r in rows if r.get('id') == lid), None)
        basis = _delivery_basis(commit, line, world)
        if basis == 'current_turn' and (row is None or row.get('op') == 'advance'):
            if row is not None:
                rows.remove(row)
            row = {'op': 'resolve', 'id': lid, 'summary': line.get('summary') or '交付约定已履行'}
            rows.append(row)
        if row is None or row.get('op') != 'resolve':
            continue
        outcome = 'abandoned' if row.get('outcome') == 'abandoned' else 'delivered'
        due = objective.get('due')
        timing = 'unscheduled'
        if outcome == 'delivered' and due:
            timing = ('unknown' if basis == 'earlier_turn' else
                      'late' if (now['day'], now['band']) > (due['day'], due['band']) else 'on_time')
        row['outcome'] = outcome
        row['resolution'] = {'outcome': outcome, 'timing': timing, 'recorded_at': now}
        changed = True
    if not changed:
        return commit
    sections['quests'] = rows
    return TurnCommit(narration=commit.narration, sections=sections)


def validate_delivery_events(events, world):
    """Recompute from final event payload, so dropped/changed transfers cannot win."""
    sections = {'items': [], 'quests': [], 'clock': []}
    for event in events:
        if event.get('retracted'):
            continue
        kind, delta = event.get('type'), copy.deepcopy(event.get('deltas', {}))
        if kind == 'item_transferred':
            sections['items'].append({'op': 'transfer', **delta})
        elif kind == 'clock_advanced':
            sections['clock'].append(delta)
        elif kind in {'quest_opened', 'quest_advanced', 'quest_resolved'}:
            sections['quests'].append({'op': {'quest_opened': 'open', 'quest_advanced': 'advance',
                                             'quest_resolved': 'resolve'}[kind], **delta})
    candidate = TurnCommit(narration='', sections=sections)
    errors = delivery_errors(candidate, world)
    for row in sections['quests']:
        if row['op'] == 'open' and 'objective' in row:
            errors.extend(objective_problems(row['objective'], world))
    if errors:
        raise ValueError('final delivery outcome lacks a matching item transfer')
    expected = bind_delivery_outcomes(candidate, world)
    if expected.sections.get('quests') != candidate.sections.get('quests'):
        raise ValueError('final delivery result differs from the settled outcome')


def quest_summary(line, world, *, closed=False):
    """Small player-readable status, reused by author cards and CLI/GUI ledger."""
    resolved = line.get('resolved') or {}
    summary = (resolved.get('summary') if closed else None) or line.get('summary') or '（暂无摘要）'
    objective = line.get('objective')
    if not isinstance(objective, dict):
        return summary
    if closed:
        result = resolved.get('resolution') or {}
        if result.get('outcome') == 'abandoned':
            return f'【已放弃】{summary}'
        timing = {'on_time': '按时', 'late': '逾期', 'unknown': '时间未核实', 'unscheduled': ''}.get(result.get('timing'), '')
        return f'【已交付{("·" + timing) if timing else ""}】{summary}' if result.get('outcome') == 'delivered' else summary
    due = objective.get('due')
    if not isinstance(due, dict):
        return summary
    now = _time(world)
    overdue = (now['day'], now['band']) > (due['day'], due['band'])
    return f'【{"已逾期，仍可交付；" if overdue else ""}期限：第{due["day"]}天{band_name(due["band"])}（含）】{summary}'
