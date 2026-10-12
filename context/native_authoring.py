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
        for person_id in scene.get("present") or []:
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
            "new_information": "若叙事让主角获得一条 speakable_evidence，应在 TurnCommit 的 knowledge 中记录其来源；不要把未经材料支持的结论写成事实。",
            "item_state": "若叙事改变物品持有人或持有地点，必须提交 items.transfer；临时动作若没有可寻址目的地，不得声称已改变 canonical 持有状态。",
        },
    }
