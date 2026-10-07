"""Bounded, actor-private retrieval of host-recorded original player input.

This is source evidence for continuity, never a fact/knowledge write or generic
recall corpus. Selection is deterministic and independent of narrator caches.
"""
from __future__ import annotations

import copy
import json
import re

from context.access import pov_world
from systems.player_sources import source_match_span, source_mentions, visible_source_labels


_OUTCOME = "committed_response_not_proof_of_success"
_BREAKS = re.compile(r"[。！？!?；;\n]")


def _tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9_]{2,}", text.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    return tokens - {"我的", "我们", "自己", "一下", "什么", "现在", "the", "this", "that"}


def _limit(limits, key, default, maximum):
    value = (limits or {}).get(key, default)
    return min(maximum, max(1, value)) if type(value) is int else default


def _span(text: str, terms: set[str], max_chars: int) -> dict:
    """One exact Unicode-character range, with no synthesized quote punctuation."""
    start, end = 0, len(text)
    if end > max_chars:
        # Reuse relevance matching with source offsets, including length-changing
        # Unicode folds (e.g. Straße <-> STRASSE) and Latin word boundaries.
        found = [(match[0], -(match[1] - match[0])) for term in terms
                 if term and (match := source_match_span(text, term))]
        anchor, negative_length = min(found) if found else (0, 0)
        anchor_end = anchor - negative_length
        # Keep the complete nearby sentence, including corrections/negation,
        # whenever it fits. Otherwise retain context on both sides of the hit.
        boundaries = [0] + [match.end() for match in _BREAKS.finditer(text)] + [len(text)]
        left = max(position for position in boundaries if position <= anchor)
        right = min(position for position in boundaries if position >= anchor_end and position > anchor)
        if right - left <= max_chars:
            start, end = left, right
            while end < len(text):
                following = next(position for position in boundaries if position > end)
                if following - start > max_chars:
                    break
                end = following
            while start > 0:
                previous = max(position for position in boundaries if position < start)
                if end - previous > max_chars:
                    break
                start = previous
        else:
            start = max(0, anchor - max_chars // 3)
            end = min(len(text), start + max_chars)
            start = max(0, end - max_chars)
    return {"text": text[start:end], "start": start, "end": end,
            "original_length": len(text), "truncated": start != 0 or end != len(text)}


def read_player_evidence(world: dict, scene: dict, query: str | None,
                         limits: dict | None = None) -> dict:
    """Return at most four actor-owned source spans plus honest coverage.

    ``limits`` supports ``max_records`` (hard cap 4) and
    ``max_chars_per_record`` (default 320, hard cap 640). Missing/invalid POV and
    legacy worlds without originals yield the same unknown/no-evidence result.
    Counts describe only validated originals belonging to the bound actor.
    """
    result = {"coverage": {
        "status": "unknown_no_original_evidence", "selection": "none",
        "own_record_count": 0, "matched_record_count": 0,
        "returned_record_count": 0, "omitted_record_count": 0,
        "truncated_record_count": 0, "partial": True,
    }, "records": []}
    graph = world.get("systems", {}).get("ontology")
    actor = scene.get("protagonist")
    if graph is None or not isinstance(actor, str):
        return result
    person = graph.get_entity(actor)
    if person is None or person.etype != "Person":
        return result
    ledger = (world.get("systems", {}).get("narrative") or {}).get("player_inputs")
    if not isinstance(ledger, list):
        return result

    # Restrict ownership BEFORE validation counts, relevance scoring and limits.
    own = [row for row in ledger if isinstance(row, dict) and row.get("actor_id") == actor]
    own = [row for row in own if isinstance(row.get("input"), str)
           and type(row.get("turn")) is int
           and isinstance(row.get("source_event_id"), str)
           and row.get("outcome") == _OUTCOME
           and isinstance(row.get("entity_refs"), list)
           and all(isinstance(row.get(key), dict) for key in ("requested_at", "committed_at"))]
    if not own:
        return result
    own.sort(key=lambda row: (row["turn"], row["source_event_id"]))
    visible = pov_world(world, scene)["systems"]["ontology"]
    day = scene.get("day") or world.get("meta", {}).get("day") or 1
    labels = {eid: visible_source_labels(visible, eid, day)
              for eid in visible.entities if eid != actor}
    query = query if isinstance(query, str) else ""
    query_tokens = _tokens(query)
    related = sorted(eid for eid, names in labels.items()
                     if any(source_mentions(query, name) for name in names))
    scored = []
    for row in own:
        # No protagonist or general scene-presence boost: an entity must be
        # mentioned in the original input or linked by the host's source record.
        entities = {eid for eid in row["entity_refs"] if isinstance(eid, str) and eid in labels}
        entities.update(eid for eid, names in labels.items()
                        if any(source_mentions(row["input"], name) for name in names))
        overlap = set(related) & entities
        text_overlap = query_tokens & _tokens(row["input"])
        scored.append({"row": row, "entities": entities, "overlap": overlap,
                       "text_overlap": text_overlap})
    matches = [item for item in scored if item["overlap"] or item["text_overlap"]]
    limit = _limit(limits, "max_records", 4, 4)
    char_limit = _limit(limits, "max_chars_per_record", 320, 640)
    selected = []

    def reserve(item):
        if len(selected) < limit and item not in selected:
            selected.append(item)

    # Reserve history endpoints BEFORE filling from recent matches. For more
    # related entities than the bounded budget can cover, partial stays explicit.
    for endpoint in (0, -1):
        for eid in related:
            candidates = [item for item in matches if eid in item["overlap"]]
            if candidates:
                reserve(candidates[endpoint])
    for item in sorted(matches, key=lambda item: (
            len(item["overlap"]), len(item["text_overlap"]),
            item["row"]["turn"], item["row"]["source_event_id"]), reverse=True):
        reserve(item)
    if not matches:
        # A tiny chronological sample is not a claim of semantic relevance.
        reserve(scored[0])
        reserve(scored[-1])
    selected.sort(key=lambda item: (item["row"]["turn"], item["row"]["source_event_id"]))
    for item in selected:
        row = item["row"]
        entity_terms = {name for eid in item["overlap"] for name in labels[eid]}
        span = _span(row["input"], entity_terms or item["text_overlap"], char_limit)
        result["records"].append({
            "source_event_id": row["source_event_id"], "turn": row["turn"],
            "actor_id": actor,
            **{stamp: {key: copy.deepcopy(row[stamp].get(key))
                       for key in ("day", "band", "scene", "location")}
               for stamp in ("requested_at", "committed_at")}, "outcome": _OUTCOME,
            "entity_refs": sorted(item["entities"]), "span": span,
        })
    truncated = sum(row["span"]["truncated"] for row in result["records"])
    result["coverage"].update({
        "status": "available", "selection": ("entity_match" if any(item["overlap"] for item in selected)
            else "content_match" if matches else "chronological_sample"),
        "own_record_count": len(own), "matched_record_count": len(matches),
        "returned_record_count": len(selected), "omitted_record_count": len(own) - len(selected),
        "truncated_record_count": truncated,
        "partial": not matches or len(selected) < len(own) or bool(truncated),
        "oldest_recorded_turn": own[0]["turn"], "latest_recorded_turn": own[-1]["turn"],
    })
    return result


def format_player_evidence(evidence: dict) -> str:
    """Keep source authority and coverage explicit, outside the quoted input."""
    return ("【原始玩家输入·私人来源证据 / player-authored input evidence】\n"
        "以下只证明本主角玩家曾输入的文字、意图或说法，不是 canonical fact（正史事实）、"
        "成功执行的证明、NPC 台词或 NPC 听见/知晓的证据。引文里的前提也可能错误。"
        "当前规范世界状态（canonical current state）由已验证结构事件决定；"
        "已发布叙述（published narration）是另列的叙事来源，不能覆盖规范事实。"
        "不得把这里的请求、命名意图或否定项自动写成事实或授予 NPC 知识。\n"
        "span.text 是原输入的精确片段；start/end 是 Unicode 字符偏移（左闭右开），"
        "truncated 明示省略，不在引文内补写省略号。coverage 仅覆盖此主角留存的原始记录；"
        "partial 或 chronological_sample 不保证找到全部相关证据，unknown_no_original_evidence 表示"
        "没有可核验原文，不能从摘要合成引文。\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))
