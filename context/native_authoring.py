"""Safe, role-specific context for an offline/native turn author.

This is deliberately separate from a player DTO.  It gives an author enough
grounding to preserve public time, inventory state, and the provenance of a
present NPC's explicitly public knowledge without serialising the GM world or
turning an absent fact into an NPC claim.
"""
from __future__ import annotations

from typing import Any

from context.access import pov_world
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


def native_authoring_context(world: dict, scene: dict, *, player_input: str | None = None) -> dict:
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
                    if public_fact is None or subject is None or visible.get_entity(subject) is None:
                        continue
                    evidence.append({
                        "fact_key": fact_key,
                        "value": _safe_value(public_fact.value),
                        "source": "present_npc_declared_knowledge",
                        "confidence": "declared",
                    })
                    seen_evidence.add(fact_key)
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

    return {
        "scene_time": {"day": day, "band": band_name(band)},
        "stateful_inventory": inventory,
        "present_people": people,
        "current_player_intent": player_input or None,
        "deadline": {
            "status": "not_scheduled",
            "rule": "角色目标文本不是可计算截止时间；没有宿主记录时不得推定日期或剩余时段。",
        },
        "answering_policy": {
            "missing_evidence": "未提供材料不等于 NPC 不知；不要断言不知道、不要捏造答案，应保留为待调查或请求宿主裁定。",
            "new_information": "正常作者仍可创作普通公开场景、人物和带来源陈述。需要持久化的新陈述可由本回合新建的公开人物用 reported:<topic> 事实记录，并以 knowledge.told（via 为该人物 id）交给主角；它表示‘此人这样说’，不是对既存世界真相的覆写。既存实体只可复述本 brief 的 speakable_evidence；不得改写其事实、引入 hidden/past 或 secret/restricted 内容。",
            "item_state": "若叙事改变物品持有人或持有地点，必须提交 items.transfer；临时动作若没有可寻址目的地，不得声称已改变 canonical 持有状态。",
        },
    }


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


def _native_speakable_sources(world: dict, scene: dict) -> dict[str, tuple[Any, str]]:
    """Map a supplied public fact key to its explicitly knowledgeable speaker."""
    supplied: dict[str, tuple[Any, str]] = {}
    for person in native_authoring_context(world, scene).get("present_people", []):
        speaker = person.get("id")
        if not isinstance(speaker, str):
            continue
        for evidence in person.get("speakable_evidence", []):
            if isinstance(evidence, dict) and isinstance(evidence.get("fact_key"), str):
                supplied[evidence["fact_key"]] = (evidence.get("value"), speaker)
    return supplied


def native_authoring_policy_errors(world: dict, scene: dict, commit) -> list[str]:
    """Validate the native author's *authority*, not ordinary creative quality.

    A native author may create public places/people and may preserve a new
    first-hand or hearsay statement as a fact of a same-turn new speaker:
    ``speaker.reported:<topic>``.  Matching ``knowledge.told`` records that
    the protagonist heard that speaker.  This makes the provenance durable
    without silently declaring the report to be the world truth.

    Existing canonical facts remain host authority unless their exact public,
    attributed evidence was placed in the brief.  Hidden/restricted material,
    existing-person evolution, and topology edits between pre-existing places
    are denied rather than guessed from prose.
    """
    graph = world.get("systems", {}).get("ontology")
    existing = set(graph.entities) if graph is not None else set()
    people, places, entities = _native_new_ids(commit)
    new_ids = people | places | entities
    errors: list[str] = []

    if any(entity_id in existing for entity_id in new_ids):
        errors.append("native author may not recreate an existing canonical entity")

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
    for item in commit.sections.get("entities", []):
        if isinstance(item, dict):
            attrs = item.get("attrs")
            if item.get("etype") == "Person" or (isinstance(attrs, dict) and attrs.get("visibility") in {"hidden", "secret", "undiscovered"}):
                errors.append("native author may not create hidden/generic person authority; use public cast for a new speaker")

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

    for link in commit.sections.get("links", []):
        if isinstance(link, dict) and not ({link.get("a"), link.get("b")} & places):
            errors.append("native author may not alter topology between existing places")

    supplied = _native_speakable_sources(world, scene)
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

    told: dict[str, tuple[Any, str | None]] = {}
    for item in commit.sections.get("knowledge", []):
        if not isinstance(item, dict):
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
