"""Actor-private introduction sources, independent of recap and model history."""
from __future__ import annotations

import copy
import json

from systems.player_sources import (
    introduction_sources, source_context, source_mentions,
    visible_source_entities, visible_source_labels,
)


def read_cast_evidence(world: dict, scene: dict, query: str | None) -> dict:
    """Recall only own introductions by historical name/ID or visible scene NPC.

    The stored passage is scene narrative associated with explicit IDs. It is not
    an extracted gender, speaker attribution, character profile or current fact.
    Legacy records without the optional snapshot supply no introduction evidence.
    """
    result = {'coverage': {'status': 'unknown_no_original_evidence',
        'own_snapshot_count': 0, 'matched_snapshot_count': 0,
        'returned_snapshot_count': 0, 'omitted_snapshot_count': 0,
        'truncated_snapshot_count': 0, 'partial': True}, 'records': []}
    actor = scene.get('protagonist')
    visible = visible_source_entities(world, actor)
    if visible is None:
        return result
    # Ownership comes first, including before validation and coverage counts.
    own = list(introduction_sources(world, actor))
    if not own:
        return result
    current = source_context(world, actor)
    day, location = current['day'], current['location']
    present = set(scene.get('present') or [])
    if location:
        present.update(pid for pid, person in visible.entities.items()
                       if person.etype == 'Person' and location in visible.neighbors(pid, 'located_in', day))
    query = query if isinstance(query, str) else ''
    matches = []
    for row in own:
        persons, explicit = [], False
        for person in row['cast_introductions']['persons']:
            pid = person['id']
            labels = {pid} | ({person['name']} if 'name' in person else set())
            # Only filtered current name facts can supplement the stored names.
            if visible.get_entity(pid) is not None:
                labels.update(visible_source_labels(visible, pid, day))
            named = any(source_mentions(query, label) for label in labels)
            if named or (pid in present and visible.get_entity(pid) is not None):
                persons.append(person)
                explicit = explicit or named
        if persons:
            matches.append((not explicit, row['turn'], row['source_event_id'], row, persons))
    selected = sorted(matches, key=lambda item: item[:3])[:4]
    for _, _, _, row, persons in selected:
        snapshot = row['cast_introductions']
        result['records'].append({'actor_id': actor, 'turn': row['turn'],
            'source_event_id': row['source_event_id'], 'narration_ref': snapshot['narration_ref'],
            'committed_at': {key: copy.deepcopy(row['committed_at'].get(key))
                             for key in ('day', 'band', 'scene', 'location')},
            'persons': copy.deepcopy(persons), 'span': copy.deepcopy(snapshot['span'])})
    truncated = sum(record['span']['truncated'] for record in result['records'])
    result['coverage'].update({'status': 'available', 'own_snapshot_count': len(own),
        'matched_snapshot_count': len(matches), 'returned_snapshot_count': len(selected),
        'omitted_snapshot_count': len(own) - len(selected),
        'truncated_snapshot_count': truncated, 'partial': len(selected) < len(own) or bool(truncated)})
    return result


def format_cast_evidence(evidence: dict) -> str:
    return ('【人物初次登场·已发布场景原文 / published introduction evidence】\n'
        '以下是本主角当时已收到的场景叙述原文，与新建人物或开场已核对的可见人物 ID 关联；'
        'persons.name 是逐字出现在所存原文里的登场显示称呼，不证明规范真名。'
        '整段可能包含多个人物与台词，不表示每句话、代词或属性都属于关联人物；'
        '未自动提取性别、说话人或人物事实。延续人物描写时核对原文，不能用压缩摘要补造细节。'
        '这是历史叙事来源，不能覆盖当前规范事实，也不证明 NPC 听见或知道玩家输入。\n'
        'span.text 保留完整原文或明确标为 truncated 的开头片段；start/end 为 Unicode 字符偏移。'
        '缺少此记录的旧存档为 unknown_no_original_evidence，不能从摘要补写；'
        'coverage 只计本主角留存的来源，不保证已取回全部相关片段。\n'
        + json.dumps(evidence, ensure_ascii=False, separators=(',', ':')))
