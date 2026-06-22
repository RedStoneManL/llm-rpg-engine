from __future__ import annotations

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event
from facts.graph import FactGraph
from engine.log import get_logger

log = get_logger("systems.ontology")


class OntologySystem(ContextSystem):
    """Registered ContextSystem whose slice is a FactGraph.

    Owns generic event-types: entity_created, fact_asserted, relation_added,
    tier_changed. Commit sections: entities, facts, relations.

    validate() checks:
      1. Required fields are present (missing → ValidationError code="missing").
         Gate: if validate() returns no errors, to_events()+apply() cannot
         KeyError/TypeError on LLM-supplied fields.
      2. Entity references (subject/src/dst) in facts and relations already
         exist in the current graph (dangling_ref).

    NOTE: Cross-section "introduced this same turn" validation is DEFERRED.
    (A fact referencing an entity declared in the same commit's 'entities'
    section cannot be validated here because world hasn't been updated yet.
    S1a validates only the simple case: the entity must already be in the graph.)
    """

    name = "ontology"

    def event_types(self) -> set[str]:
        return {"entity_created", "fact_asserted", "relation_added", "tier_changed"}

    def commit_sections(self) -> set[str]:
        return {"entities", "facts", "relations"}

    def empty_state(self) -> FactGraph:
        return FactGraph()

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def apply(self, world: dict, event: dict) -> None:
        g: FactGraph = world["systems"][self.name]
        d = event.get("deltas", {})
        t = event["type"]
        if t == "entity_created":
            entity_id = d.get("id")
            etype = d.get("etype")
            if not entity_id or not etype:
                log.warning(
                    "entity_created event missing id or etype in deltas; skipping. "
                    "event_id=%s deltas=%r", event.get("id"), d
                )
                return
            g.add_entity(entity_id, etype, tier=d.get("tier", "mentioned"), **d.get("attrs", {}))
        elif t == "fact_asserted":
            subject = d.get("subject")
            predicate = d.get("predicate")
            value = d.get("value")
            if not subject or not predicate or value is None:
                log.warning(
                    "fact_asserted event missing subject/predicate/value in deltas; skipping. "
                    "event_id=%s deltas=%r", event.get("id"), d
                )
                return
            g.assert_fact(
                subject, predicate, value,
                day=event["day"],
                turn=event.get("turn") or 0,
                source_event=event["id"],
                secrecy=d.get("secrecy"),
            )
        elif t == "relation_added":
            src = d.get("src")
            rel = d.get("rel")
            dst = d.get("dst")
            if not src or not rel or not dst:
                log.warning(
                    "relation_added event missing src/rel/dst in deltas; skipping. "
                    "event_id=%s deltas=%r", event.get("id"), d
                )
                return
            g.add_relation(
                src, rel, dst,
                day=event["day"],
                turn=event.get("turn") or 0,
                source_event=event["id"],
            )
        elif t == "tier_changed":
            entity_id = d.get("id")
            tier = d.get("tier")
            if not entity_id or not tier:
                log.warning(
                    "tier_changed event missing id or tier in deltas; skipping. "
                    "event_id=%s deltas=%r", event.get("id"), d
                )
                return
            g.set_tier(entity_id, tier)

    # ------------------------------------------------------------------
    # Write path: validate + to_events
    # ------------------------------------------------------------------

    def created_ids(self, section: str, decl: list) -> set[str]:
        # Only 'entities' introduces new entity ids; facts/relations reference.
        if section != "entities":
            return set()
        return super().created_ids(section, decl)

    def validate(self, section: str, decl: list, world: dict) -> list[ValidationError]:
        """Validate required fields and entity references.

        Stage 1 — required-field gate (code="missing"):
          entities : each item must have "id" (str) and "etype" (str).
          facts    : each item must have "subject" (str), "predicate" (str),
                     "value" (any non-absent key).
          relations: each item must have "src" (str), "rel" (str), "dst" (str).

        Stage 2 — dangling_ref check:
          subject/src/dst must exist in the current graph (or the set of ids
          being created by this same commit, pre-registered by the kernel).

        GUARANTEE: if this method returns no errors for a section,
        to_events()+apply() of that section cannot KeyError/TypeError on
        any LLM-supplied field.
        """
        g: FactGraph | None = world.get("systems", {}).get(self.name)

        errs: list[ValidationError] = []

        if section == "entities":
            for i, item in enumerate(decl or []):
                if not isinstance(item, dict):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}]",
                        code="missing",
                        hint="实体声明必须是对象 (dict)",
                    ))
                    continue
                if not item.get("id") or not isinstance(item.get("id"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="缺少必填字段 'id'（实体唯一标识，字符串）",
                    ))
                if not item.get("etype") or not isinstance(item.get("etype"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].etype",
                        code="missing",
                        hint="缺少必填字段 'etype'（实体类型，如 Person/Place/Object，字符串）",
                    ))

        elif section == "facts":
            for i, item in enumerate(decl or []):
                if not isinstance(item, dict):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}]",
                        code="missing",
                        hint="事实声明必须是对象 (dict)",
                    ))
                    continue
                if not item.get("subject") or not isinstance(item.get("subject"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].subject",
                        code="missing",
                        hint="缺少必填字段 'subject'（事实主体实体 id，字符串）",
                    ))
                if not item.get("predicate") or not isinstance(item.get("predicate"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].predicate",
                        code="missing",
                        hint="缺少必填字段 'predicate'（谓词/属性名，字符串）",
                    ))
                if "value" not in item:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].value",
                        code="missing",
                        hint="缺少必填字段 'value'（属性值，可以是字符串、数字或布尔值）",
                    ))
                # dangling_ref check only when g is available and subject passed
                # basic type check above (so we don't double-error on missing subject)
                if g and item.get("subject") and isinstance(item.get("subject"), str):
                    subject = item["subject"]
                    if g.get_entity(subject) is None:
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].subject",
                            code="dangling_ref",
                            hint=f"实体 '{subject}' 不存在于图中，请先在 entities 段声明",
                        ))

        elif section == "relations":
            for i, item in enumerate(decl or []):
                if not isinstance(item, dict):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}]",
                        code="missing",
                        hint="关系声明必须是对象 (dict)",
                    ))
                    continue
                if not item.get("src") or not isinstance(item.get("src"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].src",
                        code="missing",
                        hint="缺少必填字段 'src'（关系起点实体 id，字符串）",
                    ))
                if not item.get("rel") or not isinstance(item.get("rel"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].rel",
                        code="missing",
                        hint="缺少必填字段 'rel'（关系类型，如 located_in/knows，字符串）",
                    ))
                if not item.get("dst") or not isinstance(item.get("dst"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].dst",
                        code="missing",
                        hint="缺少必填字段 'dst'（关系终点实体 id，字符串）",
                    ))
                # dangling_ref checks (only for fields that passed basic check)
                if g:
                    src = item.get("src") if isinstance(item.get("src"), str) else None
                    dst = item.get("dst") if isinstance(item.get("dst"), str) else None
                    if src and g.get_entity(src) is None:
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].src",
                            code="dangling_ref",
                            hint=f"实体 '{src}' 不存在于图中，请先在 entities 段声明",
                        ))
                    if dst and g.get_entity(dst) is None:
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].dst",
                            code="dangling_ref",
                            hint=f"实体 '{dst}' 不存在于图中，请先在 entities 段声明",
                        ))

        return errs

    def to_events(self, section: str, decl: list, *, turn: int, day: int, scene: str) -> list[dict]:
        out = []
        if section == "entities":
            for i, e in enumerate(decl or []):
                entity_id = e.get("id") if isinstance(e, dict) else None
                etype = e.get("etype") if isinstance(e, dict) else None
                if not entity_id or not etype:
                    log.warning(
                        "entities[%d] missing id or etype; skipping item. item=%r", i, e
                    )
                    continue
                out.append(kernel_event(
                    "entity_created", day=day, scene=scene,
                    summary=f"{entity_id} 登场", deltas=e, turn=turn,
                ))
        elif section == "facts":
            for i, f in enumerate(decl or []):
                subject = f.get("subject") if isinstance(f, dict) else None
                predicate = f.get("predicate") if isinstance(f, dict) else None
                # value=None/0/False are legitimate; only missing key is invalid
                has_value = isinstance(f, dict) and "value" in f
                if not subject or not predicate or not has_value:
                    log.warning(
                        "facts[%d] missing subject/predicate/value; skipping item. item=%r", i, f
                    )
                    continue
                out.append(kernel_event(
                    "fact_asserted", day=day, scene=scene,
                    summary=f"{subject}.{predicate}={f['value']}", deltas=f, turn=turn,
                ))
        elif section == "relations":
            for i, r in enumerate(decl or []):
                src = r.get("src") if isinstance(r, dict) else None
                rel = r.get("rel") if isinstance(r, dict) else None
                dst = r.get("dst") if isinstance(r, dict) else None
                if not src or not rel or not dst:
                    log.warning(
                        "relations[%d] missing src/rel/dst; skipping item. item=%r", i, r
                    )
                    continue
                out.append(kernel_event(
                    "relation_added", day=day, scene=scene,
                    summary=f"{src} {rel} {dst}", deltas=r, turn=turn,
                ))
        return out

    # ------------------------------------------------------------------
    # Read path: inject + recall
    # ------------------------------------------------------------------

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        g: FactGraph | None = world.get("systems", {}).get(self.name)
        if not g:
            return None
        tracked = [e.id for e in g.entities.values() if e.tier == "tracked"]
        if not tracked:
            return None
        return Fragment("ontology", "scene", "已知实体: " + "、".join(tracked))

    def recall(self, query: str, world: dict) -> list[RecallHit]:
        g: FactGraph | None = world.get("systems", {}).get(self.name)
        if not g:
            return []
        return [
            RecallHit("ontology", 1.0, f"{e.id}({e.etype})")
            for e in g.entities.values()
            if query in e.id
        ]
