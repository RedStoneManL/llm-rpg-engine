"""Public, bounded provenance for authored narration and its compressed recaps.

This sidecar is not canonical truth or a private input/identity ledger. Actor
identity is used transiently to check historical visibility; it is never copied
into the packet. Readers do not repair prose or resolve history from current
world state. All serialization goes through an explicit field allowlist.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from context.summary_identity import build_summary_identity
from kernel.projection import project
from facts.visibility import held_by_visibility
from systems.player_sources import (
    LABEL_FIELDS, published_identity_bindings, source_match_span, source_mentions,
    valid_narration_source, visible_source_entities,
)


_CATEGORY = "historical_narration"
_MAX_CHARS = 12000
_MAX_GROUPS = 24
_MAX_SOURCES = 32
_MAX_RELATIONS = 8
_MAX_ID = 256
_MAX_LABEL = 128
_SCOPES = {"scene", "previous_super_summary", "new_summary"}
_ERRORS = (KeyError, TypeError, ValueError, AttributeError, IndexError)


def _identifier(value):
    return (isinstance(value, str) and bool(value.strip())
            and len(value) <= _MAX_ID)


def _number(value):
    return value if type(value) is int and 0 <= value < 10**15 else None


def _count(value, fallback=0):
    number = _number(value)
    return fallback if number is None else number


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, (list, tuple)) else []


def _ends(values, limit):
    if len(values) <= limit:
        return values
    left = (limit + 1) // 2
    return values[:left] + values[-(limit - left):] if limit > 1 else values[:1]


def _range(value):
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(_number(item) is not None for item in value)
            and value[0] <= value[1]):
        return list(value)
    return None


def _union(ranges):
    ranges = [value for raw in ranges if (value := _range(raw)) is not None]
    return [min(value[0] for value in ranges), max(value[1] for value in ranges)] if ranges else None


def _source_range(sources, key):
    return _union([[source[key], source[key]] for source in sources
                   if _number(source.get(key)) is not None])


def _endpoint(value, etype):
    value = _dict(value)
    if not _identifier(value.get("id")) or value.get("type") != etype:
        return None
    labels = [label for label in _list(value.get("labels"))
              if isinstance(label, str) and label.strip() and len(label) <= _MAX_LABEL]
    result = {"id": value["id"], "type": etype, "labels": sorted(set(labels))[:4]}
    published = {row.get("label") for raw in _list(value.get("label_sources"))
                 if (row := _dict(raw)).get("category") == "published_display_binding"
                 and isinstance(row.get("label"), str)}
    if published:
        sources = [{"label": label, "category": "published_display_binding"}
                   for label in result["labels"] if label in published]
        if sources:
            result["label_sources"] = sources
    return result


def _relation(value):
    value = _dict(value)
    if (value.get("relation") != "held_by" or value.get("visibility") != "public"
            or value.get("basis") != "canonical_at_narration_recorded"
            or value.get("visibility_basis") not in {"explicit_public", "legacy_public_default"}):
        return None
    subject, holder = _endpoint(value.get("subject"), "Object"), _endpoint(value.get("holder"), "Person")
    if subject is None or holder is None:
        return None
    return {"relation": "held_by", "subject": subject, "holder": holder,
            "visibility": "public", "basis": "canonical_at_narration_recorded",
            "visibility_basis": value["visibility_basis"]}


def _source(value, index=0, *, binding=False):
    """No private/source identity fields survive this allowlist."""
    value = _dict(value)
    ref = value.get("narration_ref", value.get("id"))
    bound = (binding and value.get("actor_binding") == "bound" and _identifier(ref)
             and _number(value.get("turn")) is not None and _number(value.get("day")) is not None
             and _identifier(value.get("scene")))
    result = {"raw_index": _count(value.get("raw_index"), index),
              "narration_ref": ref if _identifier(ref) else None,
              "turn": _number(value.get("turn")), "day": _number(value.get("day")),
              "scene": value.get("scene") if _identifier(value.get("scene")) else None,
              "actor_binding": "bound" if bound else "unknown"}
    relations = [relation for raw in _list(value.get("historical_relations"))
                 if bound and (relation := _relation(raw)) is not None]
    if relations:
        result["historical_relations"] = relations
    return result


def _group(value):
    value = _dict(value)
    sources = [_source(source, index, binding=True)
               for index, source in enumerate(_list(value.get("sources")))]
    old = _dict(value.get("coverage"))
    count = max(len(sources), _count(old.get("source_count")))
    unknown = min(count, max(sum(source["actor_binding"] == "unknown" for source in sources),
                            _count(old.get("unknown_source_count"))))
    result = {"bucket_id": value.get("bucket_id") if _identifier(value.get("bucket_id")) else None,
              "scene": value.get("scene") if _identifier(value.get("scene")) else None,
              "sources": sources,
              "source_turn_range": _union([value.get("source_turn_range"), _source_range(sources, "turn")]),
              "source_day_range": _union([value.get("source_day_range"), _source_range(sources, "day")]),
              "coverage": {"source_count": count, "unknown_source_count": unknown,
                           "omitted_relation_count": _count(old.get("omitted_relation_count")),
                           "partial": old.get("partial") is True,
                           "truncated": old.get("truncated") is True}}
    if isinstance(value.get("scope"), str) and value["scope"] in _SCOPES:
        result["scope"] = value["scope"]
    return result


def _packet(groups, *, inherited=None):
    """Bound complete records, retaining original range/coverage envelopes."""
    inherited = _dict(inherited)
    groups = [_group(group) for group in groups]
    count = max(sum(group["coverage"]["source_count"] for group in groups),
                _count(inherited.get("source_count")))
    group_count = max(len(groups), _count(inherited.get("group_count")))
    unknown = min(count, max(sum(group["coverage"]["unknown_source_count"] for group in groups),
                            _count(inherited.get("unknown_source_count"))))
    omitted_relations = max(sum(group["coverage"]["omitted_relation_count"] for group in groups),
                            _count(inherited.get("omitted_relation_count")))
    partial = inherited.get("partial") is True or any(group["coverage"]["partial"] for group in groups)
    truncated = inherited.get("truncated") is True or any(group["coverage"]["truncated"] for group in groups)
    packet = {"version": 1, "category": _CATEGORY,
              "authority": "authored_record_not_current_canonical_state",
              "source_turn_range": _union([inherited.get("source_turn_range")]
                                           + [group["source_turn_range"] for group in groups]),
              "source_day_range": _union([inherited.get("source_day_range")]
                                          + [group["source_day_range"] for group in groups]),
              "groups": _ends(groups, _MAX_GROUPS), "coverage": {}}
    selected = _ends([(gi, si) for gi, group in enumerate(packet["groups"])
                      for si in range(len(group["sources"]))], _MAX_SOURCES)
    selected = set(selected)
    for gi, group in enumerate(packet["groups"]):
        group["sources"] = [source for si, source in enumerate(group["sources"]) if (gi, si) in selected]
        for source in group["sources"]:
            relations = source.get("historical_relations", [])
            omitted = max(0, len(relations) - _MAX_RELATIONS)
            if omitted:
                source["historical_relations"] = _ends(relations, _MAX_RELATIONS)
                group["coverage"]["omitted_relation_count"] += omitted
                omitted_relations += omitted

    def refresh():
        for group in packet["groups"]:
            c = group["coverage"]
            c["returned_source_count"] = len(group["sources"])
            c["omitted_source_count"] = c["source_count"] - len(group["sources"])
            c["truncated"] |= bool(c["omitted_source_count"] or c["omitted_relation_count"])
            c["partial"] |= c["truncated"] or bool(c["unknown_source_count"]) or not c["source_count"]
        sources = [source for group in packet["groups"] for source in group["sources"]]
        cut = truncated or group_count > len(packet["groups"]) or count > len(sources) or bool(omitted_relations)
        packet["coverage"] = {"group_count": group_count, "returned_group_count": len(packet["groups"]),
            "omitted_group_count": group_count - len(packet["groups"]),
            "source_count": count, "returned_source_count": len(sources), "omitted_source_count": count - len(sources),
            "unknown_source_count": unknown,
            "returned_bound_source_count": sum(source["actor_binding"] == "bound" for source in sources),
            "omitted_relation_count": omitted_relations, "truncated": cut,
            "partial": partial or cut or bool(unknown) or not count,
            "limits": {"serialized_chars": _MAX_CHARS, "groups": _MAX_GROUPS,
                       "sources": _MAX_SOURCES, "relations_per_source": _MAX_RELATIONS}}

    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    refresh()
    # Leave room for the separately added, bounded summary-created timestamp.
    while len(encode(packet)) > _MAX_CHARS - 512:
        rows = [(len(encode(source)), group, source) for group in packet["groups"] for source in group["sources"]]
        if rows:
            _, group, source = max(rows, key=lambda row: row[0])
            relations = source.get("historical_relations")
            if relations:
                omitted_relations += len(relations)
                group["coverage"]["omitted_relation_count"] += len(relations)
                del source["historical_relations"]
            else:
                group["sources"].remove(source)
        elif packet["groups"]:
            packet["groups"].pop()
        else:
            break
        refresh()
    return packet


def _bucket_group(bucket):
    """Legacy metadata remains useful without asserting an actor binding."""
    bucket = _dict(bucket)
    raw, metadata = _list(bucket.get("raw")), _list(bucket.get("narration_sources"))
    count = max(len(raw), len(metadata), int(bool(bucket.get("summary")) and not raw and not metadata))
    sources = [_source(metadata[index] if index < len(metadata) else {}, index) for index in range(count)]
    bucket_id = bucket.get("bucket_id")
    if not _identifier(bucket_id) and metadata:
        bucket_id = _dict(metadata[0]).get("id")
    group = {"bucket_id": bucket_id, "scene": bucket.get("scene"), "sources": sources,
             "coverage": {"source_count": count, "unknown_source_count": count}}
    if isinstance(bucket.get("scope"), str) and bucket["scope"] in _SCOPES:
        group["scope"] = bucket["scope"]
    return _group(group)


def _published_relations(world, actor, text, day, bindings):
    """Use the existing visibility contract plus unambiguous published endpoints."""
    canonical = world["systems"]["ontology"]
    view = visible_source_entities(world, actor)
    if view is None:
        return []
    names, owners, published = {}, defaultdict(set), {}
    for eid, entity in view.entities.items():
        if not _identifier(eid) or entity.etype not in {"Object", "Person"}:
            continue
        public = set()
        for fact in canonical.current_facts(eid):
            if fact.predicate in LABEL_FIELDS and fact.secrecy == "public" and fact.valid_at(day):
                for label in fact.value if isinstance(fact.value, list) else [fact.value]:
                    if isinstance(label, str):
                        public.add(label)
        labels = set()
        for fact in view.current_facts(eid):
            if fact.predicate not in LABEL_FIELDS or not fact.valid_at(day):
                continue
            for label in fact.value if isinstance(fact.value, list) else [fact.value]:
                if (isinstance(label, str) and label.strip() and len(label) <= _MAX_LABEL
                        and label in public and source_mentions(text, label)):
                    labels.add(label)
                    owners[label.casefold()].add(eid)
        # Binding evidence is replayed through the later source event, while
        # typed endpoints and held_by facts remain at narration publication.
        binding = bindings.get(eid, {}) if entity.etype == "Person" else {}
        label = binding.get("label")
        match = source_match_span(text, label)
        if (binding.get("category") == "published_display_binding" and match is not None
                and len(label) <= _MAX_LABEL and text[match[0]:match[1]] == label):
            labels.add(label)
            owners[label.casefold()].add(eid)
            published[eid] = label
        names[eid] = labels
    endpoints = {}
    for eid, labels in names.items():
        unique = sorted(label for label in labels if owners[label.casefold()] == {eid})
        if source_mentions(text, eid) or unique:
            endpoints[eid] = {"id": eid, "type": view.get_entity(eid).etype, "labels": unique[:4]}
            if published.get(eid) in unique[:4]:
                endpoints[eid]["label_sources"] = [{
                    "label": published[eid], "category": "published_display_binding"}]
    rows = []
    for relation in canonical.relations:
        if (relation.rel != "held_by" or not isinstance(relation.attrs, dict)
                or held_by_visibility({"attrs": relation.attrs}).get("visibility", "public") != "public"
                or not relation.valid_at(day)
                or relation.src not in endpoints or relation.dst not in endpoints
                or endpoints[relation.src]["type"] != "Object"
                or endpoints[relation.dst]["type"] != "Person"):
            continue
        canonical_edges = canonical.relations_at(relation.src, "held_by", day)
        visible_edges = view.relations_at(relation.src, "held_by", day)
        if (len(canonical_edges) != 1 or len(visible_edges) != 1
                or visible_edges[0].dst != relation.dst
                or visible_edges[0].source_event != relation.source_event
                or visible_edges[0].event_time_start != relation.event_time_start):
            continue
        rows.append({"relation": "held_by", "subject": endpoints[relation.src],
                     "holder": endpoints[relation.dst], "visibility": "public",
                     "basis": "canonical_at_narration_recorded",
                     "visibility_basis": ("explicit_public" if "visibility" in relation.attrs
                                          else "legacy_public_default")})
    return rows


def build_narrative_evidence(registry, events, buckets, *, identity=None):
    """Build source metadata; independently verify any optional actor binding.

    A historical graph is replayed through the narration's append position,
    before later backstage effects. Identity data is transient and never output.
    An absent/malformed/ambiguous source cannot borrow the current protagonist.
    """
    try:
        events = [event for event in events if isinstance(event, dict) and not event.get("retracted")]
    except TypeError:
        events = []
    buckets = [_dict(bucket) for bucket in _list(buckets)]
    if identity is None:
        try:
            identity = build_summary_identity(registry, events, buckets)
        except _ERRORS:
            identity = {}
    identities = defaultdict(list)
    for group in _list(_dict(identity).get("groups")):
        group = _dict(group)
        index = _number(group.get("bucket_index"))
        for passage in _list(group.get("passages")):
            passage = _dict(passage)
            raw_index = _number(passage.get("raw_index"))
            if index is not None and raw_index is not None:
                identities[(index, raw_index)].append(passage)
    ids = Counter(event.get("id") for event in events if _identifier(event.get("id")))
    indexed = {event["id"]: (index, event) for index, event in enumerate(events)
               if _identifier(event.get("id")) and ids[event["id"]] == 1}
    narrations = defaultdict(list)
    linked = defaultdict(list)
    for index, event in enumerate(events):
        data = _dict(event.get("deltas"))
        if (event.get("type") == "narration_recorded" and _identifier(data.get("scene"))
                and isinstance(data.get("text"), str)):
            narrations[(data["scene"], data["text"])].append((index, event))
        if (event.get("type") in {"player_input_recorded", "opening_observed"}
                and _identifier(data.get("narration_ref"))):
            linked[data["narration_ref"]].append((index, event))
    historical, groups = {}, []
    for index, bucket in enumerate(buckets):
        group = _bucket_group(bucket)
        raw = _list(bucket.get("raw"))
        metadata = _list(bucket.get("narration_sources"))
        bucket_index = _number(bucket.get("bucket_index"))
        bucket_index = index if bucket_index is None else bucket_index
        for raw_index, source in enumerate(group["sources"]):
            text = raw[raw_index] if raw_index < len(raw) and isinstance(raw[raw_index], str) else None
            match = indexed.get(source["narration_ref"])
            # Old raw buckets can still recover a unique narration reference;
            # actor binding is a separate, stricter check below.
            explicit = _dict(metadata[raw_index]) if raw_index < len(metadata) else {}
            has_ref = "id" in explicit or "narration_ref" in explicit
            if match is None and source["narration_ref"] is None and text is not None and not has_ref:
                candidates = narrations.get((group["scene"], text), [])
                match = candidates[0] if len(candidates) == 1 else None
            if match is None:
                continue
            narration_index, narration = match
            data = _dict(narration.get("deltas"))
            if (narration.get("type") != "narration_recorded" or not _identifier(narration.get("id"))
                    or ids[narration["id"]] != 1
                    or text is None or data.get("text") != text or data.get("scene") != group["scene"]):
                continue
            source.update(narration_ref=narration["id"], turn=_number(narration.get("turn")),
                          day=_number(narration.get("day")), scene=data["scene"])
            hints = identities.get((bucket_index, raw_index), [])
            if len(hints) != 1 or hints[0].get("status") != "bound":
                continue
            hint = hints[0]
            actor = hint.get("original_actor")
            candidates = linked.get(narration["id"], [])
            if len(candidates) != 1 or not _identifier(actor) or source["day"] is None:
                continue
            source_index, original = candidates[0]
            try:
                if (not valid_narration_source(original) or source_index <= narration_index
                        or ids[original["id"]] != 1 or original["turn"] != narration.get("turn")
                        or hint.get("turn") != original["turn"]
                        or hint.get("narration_ref") != narration["id"]
                        or hint.get("source_event_id") != original["id"]
                        or _dict(hint.get("source_time")).get("day") != original["deltas"]["committed_at"]["day"]
                        or original["deltas"]["actor_id"] != actor):
                    continue
                # Replay the source too: a turn-0 observation must validate
                # against its earlier exact narration, context and actor.
                if source_index not in historical:
                    historical[source_index] = project(registry, events[:source_index + 1])
                bindings = published_identity_bindings(historical[source_index], actor)
                if narration_index not in historical:
                    historical[narration_index] = project(registry, events[:narration_index + 1])
                world = historical[narration_index]
                visible = visible_source_entities(world, actor)
                if visible is None:
                    continue
                source["actor_binding"] = "bound"
                # A non-monotonic legacy envelope cannot mix a later POV day
                # with earlier relation validity. Linkage is still independently bound.
                relations = (_published_relations(world, actor, text, source["day"], bindings)
                             if world.get("meta", {}).get("day") == source["day"] else [])
                if relations:
                    source["historical_relations"] = relations
            except _ERRORS:
                # Never fall back to current world state or raw entity attrs.
                source["actor_binding"] = "unknown"
                source.pop("historical_relations", None)
        if group["bucket_id"] is None and group["sources"]:
            group["bucket_id"] = group["sources"][0]["narration_ref"]
        group["coverage"]["unknown_source_count"] = sum(source["actor_binding"] == "unknown" for source in group["sources"])
        group["source_turn_range"] = _source_range(group["sources"], "turn")
        group["source_day_range"] = _source_range(group["sources"], "day")
        groups.append(group)
    return _packet(groups)


def combine_narrative_evidence(packets):
    """Union recap dependencies without upgrading unknown or lost coverage.

    Stable bucket IDs merge repeated dependencies. Unknown legacy groups remain
    separate. Bounds describe original source extrema, never summary creation.
    """
    groups, positions = [], {}
    inherited = {"source_count": 0, "group_count": 0, "unknown_source_count": 0,
                 "omitted_relation_count": 0, "partial": False, "truncated": False}
    turns, days = [], []
    for packet in _list(packets):
        packet = _dict(packet)
        if packet.get("category") != _CATEGORY or packet.get("version") != 1:
            inherited["partial"] = True
            continue
        coverage = _dict(packet.get("coverage"))
        inherited["partial"] |= coverage.get("partial") is True
        inherited["truncated"] |= coverage.get("truncated") is True
        # Counts for completely omitted groups cannot be reconstructed from rows.
        input_groups = [_group(group) for group in _list(packet.get("groups"))]
        for key in ("source_count", "unknown_source_count", "omitted_relation_count"):
            inherited[key] += max(0, _count(coverage.get(key)) - sum(group["coverage"][key] for group in input_groups))
        inherited["group_count"] += max(0, _count(coverage.get("group_count")) - len(input_groups))
        turns.append(packet.get("source_turn_range"))
        days.append(packet.get("source_day_range"))
        for group in input_groups:
            key = group["bucket_id"]
            if key is None or key not in positions:
                if key is not None:
                    positions[key] = len(groups)
                groups.append(group)
                continue
            old = groups[positions[key]]
            sources = {json.dumps([source["narration_ref"], source["raw_index"], source["turn"], source["day"]]): source
                       for source in old["sources"]}
            for source in group["sources"]:
                source_key = json.dumps([source["narration_ref"], source["raw_index"], source["turn"], source["day"]])
                if source_key not in sources:
                    sources[source_key] = source
                elif source["actor_binding"] == "unknown":
                    sources[source_key]["actor_binding"] = "unknown"
                    sources[source_key].pop("historical_relations", None)
            old["sources"] = list(sources.values())
            for field in ("source_turn_range", "source_day_range"):
                old[field] = _union([old[field], group[field]])
            for field in ("source_count", "unknown_source_count", "omitted_relation_count"):
                old["coverage"][field] = max(old["coverage"][field], group["coverage"][field])
            for field in ("partial", "truncated"):
                old["coverage"][field] |= group["coverage"][field]
    for key in ("source_count", "unknown_source_count", "omitted_relation_count"):
        inherited[key] += sum(group["coverage"][key] for group in groups)
    inherited["group_count"] += len(groups)
    inherited["source_turn_range"], inherited["source_day_range"] = _union(turns), _union(days)
    return _packet(groups, inherited=inherited)


def read_narrative_evidence(bucket, *, summary=False):
    """Read a safe sidecar, or unknown legacy provenance, without changing prose."""
    bucket = _dict(bucket)
    stored = bucket.get("summary_evidence") if summary else bucket.get("narrative_evidence")
    if isinstance(stored, dict) and stored.get("category") == _CATEGORY and stored.get("version") == 1:
        packet = combine_narrative_evidence([stored])
    else:
        packet = _packet([_bucket_group(bucket)])
    if summary:
        stamp = _dict(bucket.get("summary_created"))
        packet["summary_created"] = {"id": stamp.get("id") if _identifier(stamp.get("id")) else None,
                                     "turn": _number(stamp.get("turn")), "day": _number(stamp.get("day"))}
    return packet


def format_narrative_evidence(packet):
    """Metadata prefix only; caller appends the unchanged raw/summary prose."""
    original = _dict(packet)
    safe = combine_narrative_evidence([original])
    if "summary_created" in original:
        stamp = _dict(original["summary_created"])
        safe["summary_created"] = {"id": stamp.get("id") if _identifier(stamp.get("id")) else None,
                                   "turn": _number(stamp.get("turn")), "day": _number(stamp.get("day"))}
    return ("【历史叙述来源 / historical authored narration record】\n"
            "以下 JSON 仅标注已发布叙述及摘要依赖的历史来源，不是当前规范状态、事实写入或 NPC 知识。"
            "category=historical_narration；叙述仍可能遗漏或出错，不能覆盖已验证的 canonical current state。"
            "source_turn_range/source_day_range 是已知来源的最早/最晚边界，不表示中间连续覆盖；partial时也不能当作全部历史边界。"
            "summary_created 仅是摘要生成时间，绝不是剧情发生时间。bucket_id 与 narration_ref 标识原始来源。"
            "actor_binding=bound 仅表示原始参与者绑定经核验，unknown 表示无可核验绑定；"
            "不得用当前主角或当前持有人补全历史身份。historical_relations 只表示 narration_recorded "
            "时点的公开、原文已点名的 typed held_by(Object→Person) 端点；不是当前持有、转交或动作成功证明。"
            "端点 label_sources 中 published_display_binding 仅为原文已发布的显示称呼绑定，不是规范姓名或真名事实。"
            "未列出关系不表示不存在关系。coverage.partial/truncated 或 unknown 表示证据不足；"
            "不得把元数据、内部 ID 或来源标记编入剧情正文。\n"
            + json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
