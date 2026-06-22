"""ObjectSystem — items (Object entities) + possession (held_by relation)
in the shared FactGraph (world["systems"]["ontology"]).

Requires OntologySystem to be registered before ObjectSystem in the Registry,
since ObjectSystem writes to the shared graph (world["systems"]["ontology"]).

Object entities have attrs: any keyword attrs passed in deltas (e.g. material,
weight, description, etc.) plus an optional tier.
Possession:  held_by  relation (item → holder: Person or Place).
             Single-valued — supersedes prior holder on transfer.

inject() returns a scene-layer Fragment listing all Object entities currently
held_by the scene protagonist at the query day, or None if there are none.
"""
from __future__ import annotations

from typing import Any

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event
from facts.graph import FactGraph
from engine.log import get_logger

log = get_logger("systems.object")

_RESERVED_KEYS = {"op", "item", "to"}


class ObjectSystem(ContextSystem):
    """ContextSystem that owns item events and writes to the shared FactGraph.

    Requires OntologySystem to be registered (its shared FactGraph is at
    world["systems"]["ontology"]).

    Commit sections:
        "items"  → object_created / item_transferred events
    """

    name = "object"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"object_created", "item_transferred"}

    def commit_sections(self) -> set[str]:
        return {"items"}

    def empty_state(self) -> dict:
        """ObjectSystem owns no separate slice — objects live in the shared graph."""
        return {}

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def apply(self, world: dict, event: dict) -> None:
        g: FactGraph = world["systems"]["ontology"]
        d = event.get("deltas", {})
        t = event["type"]

        if t == "object_created":
            oid = d.get("id")
            if not oid:
                log.warning("object_created event missing 'id' in deltas — skipping (event %s)", event.get("id"))
                return
            tier = d.get("tier", "mentioned")
            # Collect all extra attrs (exclude id/tier)
            attrs: dict[str, Any] = {
                k: v for k, v in d.items()
                if k not in ("id", "tier")
            }
            g.add_entity(oid, "Object", tier=tier, **attrs)
            log.debug("object_created id=%s tier=%s attrs=%s", oid, tier, attrs)

        elif t == "item_transferred":
            item = d.get("item")
            to = d.get("to")
            if not item or not to:
                log.warning(
                    "item_transferred event missing 'item' or 'to' in deltas — skipping (event %s)",
                    event.get("id"),
                )
                return
            # held_by is single-valued — supersedes prior holder (default supersede=True)
            g.add_relation(
                item, "held_by", to,
                day=event["day"],
                turn=event.get("turn") or 0,
                source_event=event["id"],
            )
            log.debug("item_transferred item=%s to=%s", item, to)

    # ------------------------------------------------------------------
    # Write path: validate + to_events
    # ------------------------------------------------------------------

    def validate(self, section: str, decl: list, world: dict) -> list[ValidationError]:
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        errs: list[ValidationError] = []

        if section != "items":
            return errs

        for i, item in enumerate(decl or []):
            op = item.get("op", "create")

            if op == "create":
                # apply(object_created) requires d["id"]
                id_val = item.get("id")
                if not id_val:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="物品声明必须包含非空 'id' 字段",
                    ))
                elif not isinstance(id_val, str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="'id' 字段必须是字符串",
                    ))

            elif op == "transfer":
                # apply(item_transferred) requires d["item"] and d["to"] via bare subscript
                item_id = item.get("item")
                to_id = item.get("to")

                if "item" not in item or not item_id:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].item",
                        code="missing",
                        hint="转移声明必须包含非空 'item' 字段（被转移物品的 id）",
                    ))
                elif not isinstance(item_id, str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].item",
                        code="missing",
                        hint="'item' 字段必须是字符串",
                    ))
                elif g and g.get_entity(item_id) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].item",
                        code="dangling_ref",
                        hint=f"物品 '{item_id}' 不存在于图中",
                    ))

                if "to" not in item or not to_id:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].to",
                        code="missing",
                        hint="转移声明必须包含非空 'to' 字段（持有者的 id）",
                    ))
                elif not isinstance(to_id, str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].to",
                        code="missing",
                        hint="'to' 字段必须是字符串",
                    ))
                elif g and g.get_entity(to_id) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].to",
                        code="dangling_ref",
                        hint=f"持有者 '{to_id}' 不存在于图中",
                    ))

        return errs

    def to_events(
        self, section: str, decl: list, *, turn: int, day: int, scene: str
    ) -> list[dict]:
        """Map items declarations to events by op field (default: "create")."""
        out = []
        if section != "items":
            return out

        for item in decl:
            op = item.get("op", "create")
            if op == "create":
                out.append(kernel_event(
                    "object_created", day=day, scene=scene,
                    summary=f"{item.get('id', '?')} 物品创建",
                    deltas=item, turn=turn,
                ))
            elif op == "transfer":
                out.append(kernel_event(
                    "item_transferred", day=day, scene=scene,
                    summary=f"{item.get('item', '?')} 转移至 {item.get('to', '?')}",
                    deltas=item, turn=turn,
                ))
        return out

    # ------------------------------------------------------------------
    # Read path: inject
    # ------------------------------------------------------------------

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        """Return a scene-layer Fragment listing items currently held_by protagonist.

        Scans all Object entities and checks held_by relation at query day.
        Returns None if protagonist is absent or holds nothing.
        """
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        if not g:
            return None
        who = scene.get("protagonist")
        if not who:
            return None
        day = scene.get("day", 0)

        held: list[str] = []
        for eid, entity in g.entities.items():
            if entity.etype != "Object":
                continue
            holders = g.neighbors(eid, "held_by", day)
            if who in holders:
                held.append(eid)

        if not held:
            return None

        text = "持有物品：" + "、".join(held)
        return Fragment(
            system="object",
            layer="scene",
            text=text,
        )
