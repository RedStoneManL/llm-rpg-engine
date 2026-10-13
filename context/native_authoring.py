"""Safe, role-specific context for an offline/native turn author.

This is deliberately separate from a player DTO.  It gives an author enough
grounding to preserve public time, inventory state, and the provenance of a
present NPC's explicitly public knowledge without serialising the GM world or
turning an absent fact into an NPC claim.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from context.access import pov_world
from context.capabilities import scene_capabilities
from context.place_observations import public_place_observations
from kernel.contextsystem import ValidationError
from systems.knowledge import player_statement_fact_key
from systems.time import band_name


def _safe_value(value: Any) -> str:
    """Keep the author brief scalar and bounded even for a public fact value."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return "[非标量公开事实；请由宿主提供可叙述摘要]"


def _current_public_fact(graph, fact_key: str, day: int):
    """Return a fact only when both the fact and its subject are visible.

    A public attribute on a hidden, secret, or undiscovered entity is not safe
    author material: exposing its fact key alone would leak the entity id.
    """
    if "." not in fact_key:
        return None
    subject, predicate = fact_key.split(".", 1)
    for fact in graph.current_facts(subject):
        if fact.predicate == predicate and fact.secrecy == "public":
            return fact
    return None


def _is_public_subject_in_view(canonical, visible, subject: str) -> bool:
    """Return whether a referenced subject is safe author-facing material.

    ``pov_world`` deliberately keeps co-located people so ordinary scene
    renderers can describe them.  That is not enough for a native hand-off:
    an explicitly hidden/secret/undiscovered canonical person remains hidden
    even when its co-location relation survives the POV projection.  Both
    current-scene and conditional-arrival evidence must use this same stricter
    judgment before exposing a fact key or its value.
    """
    if visible.get_entity(subject) is None:
        return False
    canonical_subject = canonical.get_entity(subject)
    return canonical_subject is not None and canonical_subject.attrs.get("visibility") not in {
        "hidden", "secret", "undiscovered",
    }


def native_authoring_context(world: dict, scene: dict, *, player_input: str | None = None,
                             host_authorized_routes: list[dict[str, str]] | None = None,
                             player_statement_receipts: list[dict[str, object]] | None = None,
                             trusted_events: Iterable[dict[str, object]] = ()) -> dict:
    """Build a bounded author-only supplement from safe, attributable material.

    ``player_context`` remains the public UI view.  This supplement does not
    pass raw events, hidden entities, secret facts, or arbitrary NPC beliefs to
    the author.  A present NPC can receive a `speakable_evidence` item only if
    the NPC has an explicit knowledge fact *and* the referenced world fact is
    explicitly ``secrecy='public'``.  An empty list means material was not
    supplied; it never means the NPC does not know something.
    """
    actor = scene.get("protagonist")
    day = int(scene.get("day") or world.get("meta", {}).get("day") or 1)
    band = int(world.get("meta", {}).get("band") or 0)
    canonical = world.get("systems", {}).get("ontology")
    view = pov_world(world, scene, pov=actor, redact_facts=True)
    visible = view.get("systems", {}).get("ontology")

    trusted_event_index = _trusted_active_event_index(trusted_events)
    inventory: list[dict[str, str]] = []
    people: list[dict[str, object]] = []
    if visible is not None and actor:
        for entity_id, entity in sorted(visible.entities.items()):
            if entity.etype == "Object" and actor in visible.neighbors(entity_id, "held_by", day):
                inventory.append({"id": str(entity_id), "canonical_holder": str(actor)})
        # ``_build_scene`` deliberately treats only tracked persons as its
        # ordinary ``present`` cast.  A native author, however, may have just
        # introduced a public, currently co-located source with a durable
        # ``reported:*`` statement.  Losing that source on the next brief
        # turns a provenance-preserving report into unusable history.  Include
        # exactly those report-owning people (not every untracked canonical
        # person) when their public projection says they share this scene.
        present_ids = set(scene.get("present") or [])
        location = scene.get("location")
        if canonical is not None and location:
            for person_id, entity in visible.entities.items():
                if entity.etype != "Person" or person_id == actor:
                    continue
                if location not in visible.neighbors(person_id, "located_in", day):
                    continue
                has_public_report = any(
                    fact.predicate.startswith("reported:") and fact.secrecy == "public"
                    for fact in canonical.current_facts(person_id)
                )
                if has_public_report:
                    present_ids.add(person_id)
        for person_id in sorted(present_ids):
            entity = visible.get_entity(person_id)
            if entity is None or entity.etype != "Person":
                continue
            canonical_person = canonical.get_entity(person_id) if canonical is not None else None
            if canonical_person is not None and canonical_person.attrs.get("visibility") in {
                "hidden", "secret", "undiscovered",
            }:
                continue
            observable = {}
            for predicate in ("sketch", "mood", "state"):
                value = visible.value_at(person_id, predicate, day)
                if value is not None:
                    observable[predicate] = _safe_value(value)
            evidence = []
            if canonical is not None:
                seen_evidence: set[str] = set()
                for fact in canonical.current_facts(person_id):
                    if not fact.predicate.startswith("knows:"):
                        continue
                    fact_key = fact.predicate[len("knows:"):]
                    public_fact = _current_public_fact(canonical, fact_key, day)
                    subject = fact_key.split(".", 1)[0] if "." in fact_key else None
                    # Reuse the same POV projection as player state.  A fact
                    # must not reveal a canonical subject absent from that
                    # projection, even if the fact itself says public.
                    if (public_fact is None or subject is None
                            or not _is_public_subject_in_view(canonical, visible, subject)):
                        continue
                    evidence.append({
                        "fact_key": fact_key,
                        "value": _safe_value(public_fact.value),
                        "source": "present_npc_declared_knowledge",
                        "confidence": "declared",
                    })
                    seen_evidence.add(fact_key)
                evidence.extend(_recipient_player_statement_evidence(
                    canonical, visible, person_id, actor, trusted_event_index,
                ))
                # A ``reported:*`` fact is intentionally a fact *about this
                # visible source's statement*, not proof of the reported
                # proposition.  It can therefore be replayed as the exact
                # source-bound evidence the next native author needs, without
                # authorizing any new claim about the ferry/world itself.
                for fact in canonical.current_facts(person_id):
                    if (not fact.predicate.startswith("reported:")
                            or fact.secrecy != "public"):
                        continue
                    fact_key = f"{person_id}.{fact.predicate}"
                    if fact_key in seen_evidence:
                        continue
                    evidence.append({
                        "fact_key": fact_key,
                        "value": _safe_value(fact.value),
                        "source": "present_npc_persisted_report",
                        "confidence": "self_reported",
                    })
                    seen_evidence.add(fact_key)
            people.append({
                "id": str(person_id),
                "observable": observable,
                "speakable_evidence": evidence,
                "absence_meaning": "not_supplied_not_npc_unknown",
            })

    arrivals = _conditional_arrivals(world, scene, trusted_event_index=trusted_event_index)
    return {
        "scene_time": {"day": day, "band": band_name(band)},
        "stateful_inventory": inventory,
        "present_people": people,
        "conditional_arrivals": arrivals,
        # Empty by default.  A trusted host must explicitly select a direct,
        # public player statement and one recipient; raw player input often
        # mixes actions, thoughts, and speech and is never auto-promoted.
        "communication_receipts": list(player_statement_receipts or []),
        # Empty by default.  Only a host-side validator may put an existing,
        # undiscovered direct route here; proposal text never creates one.
        "host_authorized_routes": list(host_authorized_routes or []),
        "public_place_observations": public_place_observations(world, scene),
        "current_player_intent": player_input or None,
        "deadline": {
            "status": "not_scheduled",
            "rule": "角色目标文本不是可计算截止时间；没有宿主记录时不得推定日期或剩余时段。",
        },
        "answering_policy": {
            "missing_evidence": "未提供材料不等于 NPC 不知；不要断言不知道、不要捏造答案，应保留为待调查或请求宿主裁定。",
            "new_information": "正常作者仍可创作普通公开场景、人物和带来源陈述。需要持久化的新陈述可由本回合新建的公开人物，或本 brief 中当前可见的既存人物，使用一个此前不存在的 public reported:<topic> 事实记录，并以同值的 knowledge.told（via 为该人物 id）交给主角；conditional_arrivals 中的既存人物也仅在同一提交含其对应移动时可这样使用。它表示‘此人这样说’，不是对既存世界真相的覆写。既存人物旧事实只能逐字复述 speakable_evidence，不能覆盖旧 reported:*；不得引入 hidden/past 或 secret/restricted 内容。",
            "item_state": "若叙事改变物品持有人或持有地点，必须提交 items.transfer；临时动作若没有可寻址目的地，不得声称已改变 canonical 持有状态。",
        },
    }


def _trusted_active_event_index(events: Iterable[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Index only uniquely identified active host events.

    Native context never serializes this input.  It is supplied directly by an
    EventStore-owning host and is used solely to verify that a projected player
    statement originated in the narrow ``heard_player`` event form.
    """
    grouped: dict[str, list[dict[str, object]]] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("retracted"):
            continue
        event_id = event.get("id")
        if isinstance(event_id, str) and event_id:
            grouped.setdefault(event_id, []).append(event)
    return {event_id: entries[0] for event_id, entries in grouped.items() if len(entries) == 1}


def _recipient_player_statement_evidence(canonical, visible, recipient: str, actor: object,
                                         trusted_event_index: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    """Project only verified ``heard_player`` records held by one safe recipient.

    A record is not a world fact.  It becomes author-facing recall material
    only where the recipient is already safe current/arrival scene material,
    its graph fact names the source event, and that event proves the exact
    protagonist wording, recipient, digest key, and ``player_statement`` kind.
    """
    if not isinstance(actor, str) or not actor:
        return []
    evidence: list[dict[str, str]] = []
    for fact in canonical.current_facts(recipient):
        if not fact.predicate.startswith("knows:") or not isinstance(fact.source_event, str):
            continue
        fact_key = fact.predicate[len("knows:"):]
        event = trusted_event_index.get(fact.source_event)
        deltas = event.get("deltas") if isinstance(event, dict) else None
        if not isinstance(deltas, dict) or event.get("type") != "knowledge_set":
            continue
        statement = deltas.get("value")
        if (deltas.get("source_kind") != "player_statement"
                or deltas.get("knower") != recipient
                or deltas.get("via") != actor
                or not isinstance(statement, str) or not statement
                or deltas.get("fact_key") != fact_key
                or fact.value != statement
                or fact_key != player_statement_fact_key(actor, statement)):
            continue
        evidence.append({
            "fact_key": fact_key,
            "value": statement,
            "source": "recipient_heard_player_statement",
            "confidence": "heard_exact_words",
            "speaker": actor,
            "meaning": "recipient_heard_statement_not_world_truth",
        })
    return evidence


def _arrival_public_people(world: dict, scene: dict, *,
                           trusted_event_index: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """Project only public people observable after a legal same-turn arrival.

    ``pov_world`` normally treats co-location as observable so ordinary scenes
    can render their cast.  A conditional preview is stricter: a canonical
    hidden/secret/undiscovered person must never become visible merely because
    the host knows their current location.
    """
    actor = scene.get("protagonist")
    location = scene.get("location")
    day = int(scene.get("day") or world.get("meta", {}).get("day") or 1)
    canonical = world.get("systems", {}).get("ontology")
    visible = pov_world(world, scene, pov=actor, redact_facts=True).get("systems", {}).get("ontology")
    if not isinstance(actor, str) or not isinstance(location, str) or canonical is None or visible is None:
        return []

    people: list[dict[str, object]] = []
    for person_id, entity in sorted(visible.entities.items()):
        if entity.etype != "Person" or person_id == actor:
            continue
        canonical_person = canonical.get_entity(person_id)
        if canonical_person is None or canonical_person.attrs.get("visibility") in {
            "hidden", "secret", "undiscovered",
        }:
            continue
        if location not in visible.neighbors(person_id, "located_in", day):
            continue
        observable = {}
        for predicate in ("sketch", "mood", "state"):
            value = visible.value_at(person_id, predicate, day)
            if value is not None:
                observable[predicate] = _safe_value(value)
        evidence = []
        seen_evidence: set[str] = set()
        for fact in canonical.current_facts(person_id):
            if not fact.predicate.startswith("knows:"):
                continue
            fact_key = fact.predicate[len("knows:"):]
            public_fact = _current_public_fact(canonical, fact_key, day)
            subject = fact_key.split(".", 1)[0] if "." in fact_key else None
            if (public_fact is None or subject is None
                    or not _is_public_subject_in_view(canonical, visible, subject)):
                continue
            evidence.append({
                "fact_key": fact_key,
                "value": _safe_value(public_fact.value),
                "source": "arrival_npc_declared_knowledge",
                "confidence": "declared",
            })
            seen_evidence.add(fact_key)
        evidence.extend(_recipient_player_statement_evidence(
            canonical, visible, person_id, actor, trusted_event_index,
        ))
        for fact in canonical.current_facts(person_id):
            if not fact.predicate.startswith("reported:") or fact.secrecy != "public":
                continue
            fact_key = f"{person_id}.{fact.predicate}"
            if fact_key in seen_evidence:
                continue
            evidence.append({
                "fact_key": fact_key,
                "value": _safe_value(fact.value),
                "source": "arrival_npc_persisted_report",
                "confidence": "self_reported",
            })
            seen_evidence.add(fact_key)
        people.append({
            "id": str(person_id),
            "observable": observable,
            "speakable_evidence": evidence,
            "absence_meaning": "not_supplied_not_npc_unknown",
        })
    return people


def _conditional_arrivals(world: dict, scene: dict, *,
                          trusted_event_index: dict[str, dict[str, object]] | None = None) -> list[dict[str, object]]:
    """Return arrival-only material for each already legal public exit.

    This projection is shared by brief construction and native policy checks.
    It is deliberately conditional rather than current player knowledge: an
    author may use a row only while committing its exact protagonist move.
    """
    actor = scene.get("protagonist")
    if not isinstance(actor, str) or not actor:
        return []
    arrivals = []
    for destination in scene_capabilities(world, scene).exits:
        arrival_scene = {**scene, "location": destination.id, "present": []}
        arrivals.append({
            "condition": "仅当本 TurnCommit 同时提交该主角的对应合法移动后，以下人物才是抵达时可呈现的场景材料；它不是行动开始时主角已知的事实。",
            "move": {"who": actor, "to": destination.id},
            "location": {"id": destination.id, "label": destination.label, "kind": destination.kind},
            "present_people": _arrival_public_people(
                world, arrival_scene, trusted_event_index=trusted_event_index or {},
            ),
        })
    return arrivals


def host_player_statement_receipt(world: dict, scene: dict, *, recipient: str,
                                  statement: str, arrival_destination: str | None = None) -> dict[str, object]:
    """Create one trusted, explicit player-to-NPC communication receipt.

    The host supplies the exact spoken fragment and recipient.  No parser
    extracts dialogue from the player's whole command, so thoughts, actions,
    and private asides remain absent unless a trusted caller selected them
    explicitly (which the caller must not do for non-public speech).
    """
    if not isinstance(recipient, str) or not recipient:
        raise ValueError("host player statement receipt requires a recipient")
    if not isinstance(statement, str) or not statement:
        raise ValueError("host player statement receipt requires a non-empty exact statement")
    actor = scene.get("protagonist")
    if not isinstance(actor, str) or not actor:
        raise ValueError("host player statement receipt requires a protagonist")
    context = native_authoring_context(world, scene)
    current = {
        person.get("id") for person in context.get("present_people", [])
        if isinstance(person, dict) and isinstance(person.get("id"), str)
    }
    when: dict[str, str]
    if arrival_destination is None:
        if recipient not in current:
            raise ValueError("host player statement recipient must be publicly co-present now")
        when = {"kind": "current_scene"}
    else:
        if not isinstance(arrival_destination, str) or not arrival_destination:
            raise ValueError("host arrival player statement requires a destination")
        matched = next((arrival for arrival in context.get("conditional_arrivals", [])
                        if isinstance(arrival, dict)
                        and isinstance(arrival.get("move"), dict)
                        and arrival["move"].get("to") == arrival_destination), None)
        people = matched.get("present_people", []) if isinstance(matched, dict) else []
        if recipient not in {person.get("id") for person in people if isinstance(person, dict)}:
            raise ValueError("host player statement recipient must be public at the matching legal arrival")
        when = {"kind": "matching_move", "to": arrival_destination}
    receipt_id = f"{recipient}:{player_statement_fact_key(actor, statement)}:{when.get('to', 'current')}"
    return {
        "id": receipt_id,
        "op": "heard_player",
        "speaker": actor,
        "recipient": recipient,
        "statement": statement,
        "when": when,
        "rule": "宿主显式选定的公开直述片段；仅该收件人可记录主角说过此句，不把它当世界事实。",
    }


def player_statement_receipt_errors(world: dict, scene: dict, commit, *,
                                    receipts: Iterable[dict[str, object]] = ()) -> list[ValidationError]:
    """Validate the narrow host capability behind ``knowledge.heard_player``.

    This is deliberately usable by the ordinary turn gateway as well as the
    native importer.  ``heard_player`` is not a model inference from the
    player's whole input: a trusted host must first select the exact public
    utterance, recipient, and (when applicable) legal arrival.  Recomputing
    the receipt from the current public projection prevents a caller from
    smuggling a hand-written receipt for an absent or hidden recipient.

    Other KnowledgeSystem operations remain ordinary authoring operations.
    This helper governs only the special source-bound player-to-NPC record.
    """
    supplied: dict[str, dict[str, object]] = {}
    malformed_ids: set[object] = set()
    for receipt in receipts:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("id"), str) or not receipt["id"]:
            malformed_ids.add(None)
            continue
        receipt_id = receipt["id"]
        if receipt_id in supplied:
            malformed_ids.add(receipt_id)
            continue
        supplied[receipt_id] = receipt

    graph = world.get("systems", {}).get("ontology")
    day = int(scene.get("day") or world.get("meta", {}).get("day") or 1)
    seen: set[tuple[str, str]] = set()
    errors: list[ValidationError] = []

    for index, item in enumerate(commit.sections.get("knowledge", [])):
        if not isinstance(item, dict) or item.get("op", "told") != "heard_player":
            continue

        field = f"[{index}]"
        receipt_id = item.get("receipt_id")
        receipt = supplied.get(receipt_id) if isinstance(receipt_id, str) else None
        if receipt is None or receipt_id in malformed_ids:
            errors.append(ValidationError(
                "knowledge", f"{field}.receipt_id", "missing_host_receipt",
                "heard_player 需要一份宿主明确签发的通信收据",
            ))
            continue

        # No author-controlled extension fields: the event's complete meaning
        # is the receipt plus its exact recipient/speaker/wording projection.
        if set(item) != {"op", "receipt_id", "knower", "speaker", "statement"}:
            errors.append(ValidationError(
                "knowledge", field, "invalid_host_receipt",
                "heard_player 只能包含收据绑定的字段",
            ))
            continue

        recipient = item.get("knower")
        speaker = item.get("speaker")
        statement = item.get("statement")
        if not all(isinstance(value, str) and value for value in (recipient, speaker, statement)):
            errors.append(ValidationError(
                "knowledge", field, "invalid_host_receipt",
                "heard_player 必须逐字匹配宿主收据",
            ))
            continue

        when = receipt.get("when")
        destination: str | None
        if when == {"kind": "current_scene"}:
            destination = None
        elif (isinstance(when, dict) and when.get("kind") == "matching_move"
              and isinstance(when.get("to"), str) and when["to"]):
            destination = when["to"]
        else:
            errors.append(ValidationError(
                "knowledge", f"{field}.receipt_id", "invalid_host_receipt",
                "通信收据缺少有效的当前场景或匹配移动条件",
            ))
            continue

        try:
            expected = host_player_statement_receipt(
                world, scene, recipient=recipient, statement=statement,
                arrival_destination=destination,
            )
        except ValueError:
            errors.append(ValidationError(
                "knowledge", f"{field}.receipt_id", "invalid_host_receipt",
                "通信收据的收件人不在当前公开场景或合法抵达场景中",
            ))
            continue

        expected_fields = ("id", "op", "speaker", "recipient", "statement", "when")
        if (any(receipt.get(name) != expected[name] for name in expected_fields)
                or speaker != expected["speaker"] or recipient != expected["recipient"]
                or statement != expected["statement"]):
            errors.append(ValidationError(
                "knowledge", f"{field}.receipt_id", "invalid_host_receipt",
                "heard_player 必须逐字匹配当前宿主通信收据",
            ))
            continue

        if destination is not None and not any(
                isinstance(move, dict)
                and move.get("who") == scene.get("protagonist")
                and move.get("to") == destination
                for move in commit.sections.get("moves", [])):
            errors.append(ValidationError(
                "knowledge", f"{field}.receipt_id", "invalid_host_receipt",
                "抵达场景通信收据需要本回合匹配的主角移动",
            ))
            continue

        fact_key = player_statement_fact_key(speaker, statement)
        marker = (recipient, fact_key)
        if marker in seen:
            errors.append(ValidationError(
                "knowledge", field, "duplicate_host_receipt",
                "同一收件人和原话每回合只能记录一次",
            ))
            continue
        seen.add(marker)
        if graph is not None and graph.value_at(recipient, f"knows:{fact_key}", day) is not None:
            errors.append(ValidationError(
                "knowledge", field, "existing_player_statement",
                "已有该收件人的同一玩家陈述记录，不能覆盖",
            ))
    return errors


def _is_player_statement_digest_key(fact_key: object) -> bool:
    """Return whether ``fact_key`` belongs to the reserved receipt namespace.

    The full digest is deliberately not parsed here.  A corrupted or shortened
    suffix must remain protected too: otherwise a materializer could evade the
    final gate merely by changing one character of a planned digest key.
    """
    return (isinstance(fact_key, str)
            and ".reported_player_statement:" in fact_key
            and not fact_key.startswith(".reported_player_statement:"))


def player_statement_receipt_event_errors(world: dict, scene: dict, events: Iterable[dict[str, object]], *,
                                          receipts: Iterable[dict[str, object]] = (),
                                          authorized_receipt_ids: Iterable[str] = ()) -> list[str]:
    """Fail closed if final payload events invent a player-statement record.

    ``produce_turn`` validates the declaration. This companion runs in the
    final EventBatch validator with the *declared* receipt ids, so a hook or
    materialization change cannot delete/change the protected digest key,
    remove/change ``source_kind``, append another player-statement record, or
    accept an arrival after its effective move was retracted/superseded.
    """
    events = tuple(events)
    expected: list[dict[str, object]] = []
    authorized = tuple(authorized_receipt_ids)
    requested_ids = set(authorized)
    if any(not isinstance(receipt_id, str) or not receipt_id for receipt_id in authorized):
        return ["final player-statement plan contains an invalid receipt id"]
    if len(requested_ids) != len(authorized):
        return ["final player-statement plan repeats a receipt id"]

    seen_receipts: set[str] = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        receipt_id = receipt.get("id")
        if receipt_id not in requested_ids:
            continue
        recipient = receipt.get("recipient")
        statement = receipt.get("statement")
        when = receipt.get("when")
        if not isinstance(recipient, str) or not recipient or not isinstance(statement, str) or not statement:
            return ["final player-statement plan has an invalid receipt"]
        if when == {"kind": "current_scene"}:
            destination = None
        elif (isinstance(when, dict) and when.get("kind") == "matching_move"
              and isinstance(when.get("to"), str) and when["to"]):
            destination = when["to"]
        else:
            return ["final player-statement plan has an invalid receipt"]
        try:
            canonical = host_player_statement_receipt(
                world, scene, recipient=recipient, statement=statement,
                arrival_destination=destination,
            )
        except ValueError:
            return ["final player-statement plan cannot be reconstructed"]
        if any(receipt.get(field) != canonical[field]
               for field in ("id", "op", "speaker", "recipient", "statement", "when")):
            return ["final player-statement plan does not match its host receipt"]
        receipt_id = canonical["id"]
        if receipt_id in seen_receipts:
            return ["final player-statement plan repeats a receipt id"]
        seen_receipts.add(receipt_id)
        expected.append({
            "deltas": {
                "knower": canonical["recipient"],
                "fact_key": player_statement_fact_key(canonical["speaker"], canonical["statement"]),
                "value": canonical["statement"],
                "via": canonical["speaker"],
                "source_kind": "player_statement",
            },
            "destination": destination,
        })

    if seen_receipts != requested_ids:
        return ["final player-statement plan refers to a missing host receipt"]

    errors: list[str] = []
    matched: set[int] = set()
    arrival_matches: set[int] = set()
    graph = world.get("systems", {}).get("ontology")
    actor = scene.get("protagonist")
    day = int(scene.get("day") or world.get("meta", {}).get("day") or 1)
    locations = (graph.neighbors(actor, "located_in", day)
                 if graph is not None and isinstance(actor, str) and actor else [])
    current_location = locations[0] if len(locations) == 1 else None
    last_active_move_index: int | None = None

    for event_index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("retracted"):
            continue
        deltas = event.get("deltas")
        if event.get("type") == "entity_moved" and isinstance(deltas, dict) and deltas.get("who") == actor:
            destination = deltas.get("to")
            current_location = destination if isinstance(destination, str) and destination else None
            last_active_move_index = event_index
            continue
        if event.get("type") != "knowledge_set" or not isinstance(deltas, dict):
            continue
        protected = (deltas.get("source_kind") == "player_statement"
                     or _is_player_statement_digest_key(deltas.get("fact_key")))
        if not protected:
            continue
        match = next((index for index, allowed in enumerate(expected)
                      if allowed["deltas"] == deltas and index not in matched), None)
        if match is None:
            errors.append("final payload contains an untrusted player-statement knowledge event")
        else:
            matched.add(match)
            destination = expected[match]["destination"]
            if destination is not None and (
                    current_location != destination or last_active_move_index is None):
                errors.append("final payload lacks an active effective arrival before a player-statement receipt")
            elif destination is not None:
                arrival_matches.add(match)
    if len(matched) != len(expected):
        errors.append("final payload omits an authorized player-statement knowledge event")
    for index in arrival_matches:
        if current_location != expected[index]["destination"]:
            errors.append("final payload leaves the arrival destination before publication")
    for index in matched:
        destination = expected[index]["destination"]
        if destination is not None and not any(
                isinstance(event, dict) and not event.get("retracted")
                and event.get("type") == "entity_moved"
                and isinstance(event.get("deltas"), dict)
                and event["deltas"].get("who") == actor
                and event["deltas"].get("to") == destination
                for event in events):
            # Defensive consistency check for a future change that alters the
            # ordered state machine above.
            errors.append("final payload omits the active move required by a player-statement receipt")
    return errors


class NativeAuthoringPolicyError(ValueError):
    """A native hand-off attempted to change authority it was not given.

    The policy intentionally applies only to the offline/native importer.  The
    ordinary AuthorStrategy remains the product's creative author: it can use
    the complete validated TurnCommit schema.  A hand-off has a smaller trust
    boundary because its brief is a deliberately redacted player/author view.
    """


def _native_new_ids(commit) -> tuple[set[str], set[str], set[str]]:
    """Return newly declared people, places, and generic entities."""
    sections = commit.sections
    people = {
        item["id"] for item in sections.get("cast", [])
        if isinstance(item, dict) and item.get("op", "create") == "create"
        and isinstance(item.get("id"), str) and item["id"]
    }
    places = {
        item["id"] for item in sections.get("places", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }
    entities = {
        item["id"] for item in sections.get("entities", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }
    return people, places, entities


def _native_speakable_sources(world: dict, scene: dict, *,
                              arrival_destinations: frozenset[str] = frozenset(),
                              trusted_events: Iterable[dict[str, object]] = ()) -> dict[str, tuple[Any, str]]:
    """Map supplied public facts to speakers usable in this exact proposal."""
    supplied: dict[str, tuple[Any, str]] = {}
    context = native_authoring_context(world, scene, trusted_events=trusted_events)
    people = list(context.get("present_people", []))
    for arrival in context.get("conditional_arrivals", []):
        move = arrival.get("move") if isinstance(arrival, dict) else None
        if isinstance(move, dict) and move.get("to") in arrival_destinations:
            people.extend(arrival.get("present_people", []))
    for person in people:
        speaker = person.get("id")
        if not isinstance(speaker, str):
            continue
        for evidence in person.get("speakable_evidence", []):
            # A recipient's recall of the player's words is author-facing
            # conversation grounding, not authority to manufacture a public
            # canonical fact from those words.  A visible recipient may still
            # make a new source-bound ``reported:*`` statement in response.
            if (isinstance(evidence, dict)
                    and evidence.get("source") == "recipient_heard_player_statement"):
                continue
            if isinstance(evidence, dict) and isinstance(evidence.get("fact_key"), str):
                supplied[evidence["fact_key"]] = (evidence.get("value"), speaker)
    return supplied


def _native_visible_speakers(world: dict, scene: dict, *,
                             arrival_destinations: frozenset[str] = frozenset(),
                             trusted_events: Iterable[dict[str, object]] = ()) -> set[str]:
    """Return only the sources explicitly present in the redacted brief.

    Presence here is a capability, not a claim about every canonical person at
    the location.  Reusing ``native_authoring_context`` keeps policy and the
    material actually handed to the author on the same POV boundary.
    """
    context = native_authoring_context(world, scene, trusted_events=trusted_events)
    people = list(context.get("present_people", []))
    for arrival in context.get("conditional_arrivals", []):
        move = arrival.get("move") if isinstance(arrival, dict) else None
        if isinstance(move, dict) and move.get("to") in arrival_destinations:
            people.extend(arrival.get("present_people", []))
    return {
        person["id"]
        for person in people
        if isinstance(person, dict) and isinstance(person.get("id"), str)
    }


def native_authoring_policy_errors(world: dict, scene: dict, commit, *,
                                   trusted_discovery_keys=frozenset(),
                                   player_statement_receipts: list[dict[str, object]] | None = None,
                                   trusted_events: Iterable[dict[str, object]] = ()) -> list[str]:
    """Validate the native author's *authority*, not ordinary creative quality.

    A native author may create public places/people and may preserve a new
    first-hand or hearsay statement as a fact of a same-turn new speaker:
    ``speaker.reported:<topic>``.  Matching ``knowledge.told`` records that
    the protagonist heard that speaker.  This makes the provenance durable
    without silently declaring the report to be the world truth.

    Existing canonical facts remain host authority unless their exact public,
    attributed evidence was placed in the brief.  One narrow exception keeps a
    real conversation usable: a visible present speaker may append a *new*
    public ``reported:<topic>`` key about what that speaker says now, paired
    with exact protagonist ``knowledge.told`` provenance.  It cannot replace
    an older report, upgrade it into a world fact, or write for an absent
    person.  Hidden/restricted material, existing-person evolution, and
    topology edits between pre-existing places are denied rather than guessed
    from prose.  A genuinely new public place remains creative scope, but it
    needs a fresh identity and a direct link from the action-start location;
    that is world creation, not discovery of an existing hidden route.
    """
    graph = world.get("systems", {}).get("ontology")
    trusted_events = tuple(trusted_events)
    day = int(scene.get("day") or world.get("meta", {}).get("day") or 1)
    existing = set(graph.entities) if graph is not None else set()
    people, places, entities = _native_new_ids(commit)
    new_ids = people | places | entities
    errors: list[str] = []

    if any(entity_id in existing for entity_id in new_ids):
        errors.append("native author may not recreate an existing canonical entity")
    for left_name, left_ids, right_name, right_ids in (
        ("cast", people, "places", places),
        ("cast", people, "entities", entities),
        ("places", places, "entities", entities),
    ):
        if left_ids & right_ids:
            errors.append(f"native author may not create one id through both {left_name} and {right_name}")

    for item in commit.sections.get("cast", []):
        if not isinstance(item, dict):
            continue
        if item.get("op", "create") != "create":
            errors.append("native author may create a new public person but may not evolve an existing person")
        if "past" in item or "hidden" in item:
            errors.append("native author may not attach past or hidden facets to a person")

    for section in ("places", "links"):
        for item in commit.sections.get(section, []):
            if not isinstance(item, dict):
                continue
            if item.get("visibility") in {"hidden", "secret", "undiscovered"}:
                errors.append("native author may only create public scene topology")
    origin = scene.get("location")
    for item in commit.sections.get("places", []):
        if not isinstance(item, dict):
            continue
        parent = item.get("parent")
        if parent is not None and parent != origin:
            errors.append("native author new places may only use the current location as an optional parent")
    for item in commit.sections.get("entities", []):
        if isinstance(item, dict):
            attrs = item.get("attrs")
            if item.get("etype") in {"Person", "Place"}:
                errors.append("native author must create public people via cast and public places via places")
            elif isinstance(attrs, dict) and attrs.get("visibility") in {"hidden", "secret", "undiscovered"}:
                errors.append("native author may not create hidden/generic entity authority")
    if commit.sections.get("materialize"):
        errors.append("native author may not materialize an existing place")
    for relation in commit.sections.get("relations", []):
        if not isinstance(relation, dict):
            continue
        if relation.get("rel") == "adjacent_to" or ({relation.get("src"), relation.get("dst")} - entities):
            errors.append("native author generic relations may only join same-turn new non-place entities")

    protagonist_destinations = {
        move.get("to") for move in commit.sections.get("moves", [])
        if isinstance(move, dict) and move.get("who") == scene.get("protagonist")
        and isinstance(move.get("to"), str)
    }
    allowed_locations = {scene.get("location")} | protagonist_destinations
    for move in commit.sections.get("moves", []):
        if not isinstance(move, dict):
            continue
        who, target = move.get("who"), move.get("to")
        if who == scene.get("protagonist"):
            continue
        elif who not in people or target not in allowed_locations:
            errors.append("native author may only place a same-turn new public person at the current or protagonist destination scene")

    frontier_link_counts: dict[str, int] = {}
    for link in commit.sections.get("links", []):
        if not isinstance(link, dict):
            continue
        endpoints = {link.get("a"), link.get("b")}
        new_endpoints = endpoints & places
        if not new_endpoints:
            errors.append("native author may not alter topology between existing places")
            continue
        if origin is None or len(new_endpoints) != 1 or origin not in endpoints:
            errors.append("native author new public topology must link exactly one new place directly to the current location")
            continue
        new_place = next(iter(new_endpoints))
        frontier_link_counts[new_place] = frontier_link_counts.get(new_place, 0) + 1
    if any(frontier_link_counts.get(place, 0) != 1 for place in places):
        errors.append("each native-author new public place requires exactly one direct current-location link in the same turn")

    # ``world`` normally means a regional/global cascade declaration.  Native
    # authors do not receive that authority.  Their one narrow use is an
    # explicitly public observation of the action-start place, with durable
    # range and stability metadata for future player-safe context.
    for item in commit.sections.get("world", []):
        if not isinstance(item, dict):
            continue
        if item.get("public_observation") is not True:
            errors.append("native author world entries must be explicit public observations, not generic world changes")
            continue
        if item.get("visibility") != "public":
            errors.append("native public observations must explicitly use visibility='public'")
        if item.get("observation_kind") not in {"stable_layout", "single_observation"}:
            errors.append("native public observations need observation_kind stable_layout or single_observation")
        observation_range = item.get("observation_range")
        if not isinstance(observation_range, str) or not observation_range.strip():
            errors.append("native public observations need a non-empty observation_range")
        if (not isinstance(item.get("areas"), list) or len(item["areas"]) != 1
                or item["areas"][0] not in allowed_locations):
            errors.append("native public observations may only describe the action-start or same-turn protagonist destination location")
        if item.get("level") != 1:
            errors.append("native public observations must use local level 1")

    committed_arrival_destinations = frozenset(
        destination for destination in protagonist_destinations if isinstance(destination, str)
    )
    supplied = _native_speakable_sources(
        world, scene, arrival_destinations=committed_arrival_destinations,
        trusted_events=trusted_events,
    )
    visible_speakers = _native_visible_speakers(
        world, scene, arrival_destinations=committed_arrival_destinations,
        trusted_events=trusted_events,
    )
    reports: dict[str, tuple[Any, str]] = {}
    for fact in commit.sections.get("facts", []):
        if not isinstance(fact, dict):
            continue
        subject, predicate = fact.get("subject"), fact.get("predicate")
        fact_key = f"{subject}.{predicate}" if isinstance(subject, str) and isinstance(predicate, str) else ""
        if fact.get("secrecy") in {"secret", "restricted"}:
            errors.append("native author may not introduce secret or restricted facts")
            continue
        if subject in existing:
            # A present source may add a new, source-scoped statement to a
            # conversation.  ``reported:*`` is deliberately not a world
            # predicate, and the predicate must be genuinely new so an older
            # statement cannot be silently superseded or upgraded.
            existing_report = (
                subject in visible_speakers
                and isinstance(predicate, str)
                and predicate.startswith("reported:")
                and fact.get("secrecy") == "public"
                and graph is not None
                and all(current.predicate != predicate for current in graph.current_facts(subject))
            )
            if existing_report:
                reports[fact_key] = (fact.get("value"), subject)
                continue
            supplied_value = supplied.get(fact_key)
            if supplied_value is None or fact.get("value") != supplied_value[0] or fact.get("secrecy") != "public":
                errors.append("native author may change an existing fact only by exactly recording supplied public speakable evidence")
            continue
        if subject not in new_ids:
            errors.append("native author facts must concern a same-turn new public entity or supplied evidence")
            continue
        if subject in people:
            if not isinstance(predicate, str) or not predicate.startswith("reported:"):
                errors.append("a new public speaker may persist information only as reported:<topic>, not as an unqualified world fact")
                continue
            if fact.get("secrecy") != "public":
                errors.append("a reported statement must be explicitly public")
                continue
            reports[fact_key] = (fact.get("value"), subject)
        elif fact.get("secrecy") not in {None, "public"}:
            errors.append("new public scene facts may not be restricted")

    receipts_by_id = {
        receipt.get("id"): receipt
        for receipt in (player_statement_receipts or [])
        if isinstance(receipt, dict) and isinstance(receipt.get("id"), str)
    }

    heard_statement_keys: set[tuple[str, str]] = set()
    told: dict[str, tuple[Any, str | None]] = {}
    for item in commit.sections.get("knowledge", []):
        if not isinstance(item, dict):
            continue
        op = item.get("op", "told")
        discovery_key = None
        if op == "discover_place" and isinstance(item.get("place"), str):
            discovery_key = f"{item['place']}.discovered"
        elif (op == "discover_route" and isinstance(item.get("a"), str)
              and isinstance(item.get("b"), str)):
            first, second = sorted((item["a"], item["b"]))
            discovery_key = f"route:{first}|{second}.discovered"
        if discovery_key is not None:
            if (item.get("knower") != scene.get("protagonist")
                    or discovery_key not in trusted_discovery_keys):
                errors.append("native author discovery records require an exact host-authorized route grant")
            continue
        if op == "heard_player":
            recipient = item.get("knower")
            speaker = item.get("speaker")
            statement = item.get("statement")
            receipt_id = item.get("receipt_id")
            receipt = receipts_by_id.get(receipt_id)
            if receipt is None:
                errors.append("native heard_player requires one exact host communication receipt")
                continue
            if speaker != receipt.get("speaker") or speaker != scene.get("protagonist"):
                errors.append("native heard_player speaker must match the host receipt and current protagonist")
            if recipient != receipt.get("recipient") or statement != receipt.get("statement"):
                errors.append("native heard_player recipient and statement must exactly match the host receipt")
            when = receipt.get("when")
            if not isinstance(when, dict):
                errors.append("native heard_player receipt must have a host-selected scene condition")
            elif when.get("kind") == "current_scene":
                pass
            elif (when.get("kind") == "matching_move"
                  and isinstance(when.get("to"), str)
                  and when["to"] in committed_arrival_destinations):
                pass
            else:
                errors.append("native heard_player receipt requires its matching legal move")
            if set(item) - {"op", "receipt_id", "knower", "speaker", "statement"}:
                errors.append("native heard_player may not add author-controlled knowledge fields")
            if isinstance(recipient, str) and isinstance(speaker, str) and isinstance(statement, str):
                fact_key = player_statement_fact_key(speaker, statement)
                if graph is not None and graph.value_at(recipient, f"knows:{fact_key}", day) is not None:
                    errors.append("native heard_player may not overwrite an existing recipient knowledge record")
                marker = (recipient, fact_key)
                if marker in heard_statement_keys:
                    errors.append("native heard_player may record each recipient statement only once per turn")
                heard_statement_keys.add(marker)
            continue
        if item.get("op", "told") != "told" or item.get("knower") != scene.get("protagonist"):
            errors.append("native author knowledge may only be protagonist told records")
            continue
        key = item.get("fact_key")
        if not isinstance(key, str):
            errors.append("native author knowledge must name a supported fact_key")
            continue
        told[key] = (item.get("value"), item.get("via"))
        if key in reports:
            value, source = reports[key]
            if item.get("value") != value or item.get("via") != source:
                errors.append("a new report must be told with its exact value and source id in via")
        elif key in supplied:
            value, source = supplied[key]
            if item.get("value") != value or item.get("via") != source:
                errors.append("supplied evidence must retain its exact value and present-speaker source")
        else:
            errors.append("native author knowledge must come from this-turn report or supplied speakable evidence")

    for key in reports:
        if key not in told:
            errors.append("a persisted new report must include matching protagonist knowledge.told provenance")
    return errors
