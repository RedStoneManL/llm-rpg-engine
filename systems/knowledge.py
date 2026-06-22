"""KnowledgeSystem — Bitemporal knowledge-as-facts: models information asymmetry.

Architecture: Same system pattern (own events/sections; write shared graph
world["systems"]["ontology"]; require OntologySystem registered).

Knowledge model:
  A knower's knowledge of a topic is a bitemporal Fact on the knower:
    subject=knower, predicate="knows:{fact_key}", value=believed_value
  Supersession (from S1a) gives point-in-time belief and stale/false belief
  for free — no separate 'believes' structure needed.

Three grant ops (declared in "knowledge" commit section):
  told        → one knowledge_set event
  endowment   → N knowledge_set events (one per grant)
  broadcast   → one knowledge_broadcast event (audience resolved at apply time)

Events:
  knowledge_set       atomic: one knower learns one fact_key=value
  knowledge_broadcast audience-resolved at apply (faction or place occupants)

Module query helpers (pure functions over FactGraph):
  knows(graph, knower, fact_key, day) -> value | None
  knowers_of(graph, fact_key, day)    -> list[str]
"""
from __future__ import annotations

from typing import Any

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event
from facts.graph import FactGraph
from engine.log import get_logger

log = get_logger("systems.knowledge")


# ---------------------------------------------------------------------------
# Module-level query helpers
# ---------------------------------------------------------------------------

def knows(graph: FactGraph, knower: str, fact_key: str, day: int) -> object | None:
    """Return the value believed by knower about fact_key at day, or None.

    Equivalent to graph.value_at(knower, f"knows:{fact_key}", day).
    Returns None when no grant has been made or the belief has expired.
    """
    return graph.value_at(knower, f"knows:{fact_key}", day)


def knowers_of(graph: FactGraph, fact_key: str, day: int) -> list[str]:
    """Return the list of entity ids that have a current knows:{fact_key} fact at day.

    A knower is included regardless of whether their believed value matches
    the shared-graph truth (stale belief = a knows fact with a divergent value).
    """
    predicate = f"knows:{fact_key}"
    result: list[str] = []
    for f in graph.facts:
        if f.predicate == predicate and f.valid_at(day):
            result.append(f.subject)
    return result


# ---------------------------------------------------------------------------
# KnowledgeSystem
# ---------------------------------------------------------------------------

class KnowledgeSystem(ContextSystem):
    """ContextSystem that owns knowledge events and writes to the shared FactGraph.

    Requires OntologySystem to be registered (its shared FactGraph is at
    world["systems"]["ontology"]).

    For broadcast audience resolution via faction, also requires FactionSystem
    helpers (imported at apply time to avoid circular import).

    Commit sections:
        "knowledge"  → told / endowment / broadcast items
    """

    name = "knowledge"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"knowledge_set", "knowledge_broadcast"}

    def commit_sections(self) -> set[str]:
        return {"knowledge"}

    def empty_state(self) -> dict:
        """KnowledgeSystem owns no separate slice — knowledge lives in the shared graph."""
        return {}

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def apply(self, world: dict, event: dict) -> None:
        g: FactGraph = world["systems"]["ontology"]
        d = event.get("deltas", {})
        t = event["type"]
        day = event["day"]
        turn = event.get("turn") or 0
        src = event["id"]

        if t == "knowledge_set":
            knower = d.get("knower")
            fact_key = d.get("fact_key")
            value = d.get("value")
            if not knower or not fact_key or value is None:
                log.warning(
                    "knowledge_set event %s missing required delta fields "
                    "(knower=%r fact_key=%r value present=%s) — skipped",
                    src, knower, fact_key, "value" in d,
                )
                return
            g.assert_fact(
                knower, f"knows:{fact_key}", value,
                day=day, turn=turn, source_event=src,
            )
            log.debug(
                "knowledge_set knower=%s fact_key=%s value=%s day=%d",
                knower, fact_key, value, day,
            )

        elif t == "knowledge_broadcast":
            # Resolve audience and grant knows facts to each resolved member.
            # Audience spec:
            #   {"faction": str, min_rank?: str, group?: str}  → members_of(g, ...)
            #   {"place": str}                                  → direct occupants via located_in
            fact_key = d.get("fact_key")
            value = d.get("value")
            if not fact_key or value is None:
                log.warning(
                    "knowledge_broadcast event %s missing required delta fields "
                    "(fact_key=%r value present=%s) — skipped",
                    src, fact_key, "value" in d,
                )
                return
            audience = d.get("audience", {})

            members = self._resolve_audience(g, audience, day)
            for member in members:
                g.assert_fact(
                    member, f"knows:{fact_key}", value,
                    day=day, turn=turn, source_event=src,
                )
            log.debug(
                "knowledge_broadcast fact_key=%s value=%s audience=%s resolved=%s day=%d",
                fact_key, value, audience, members, day,
            )

    def _resolve_audience(
        self,
        g: FactGraph,
        audience: dict,
        day: int,
    ) -> list[str]:
        """Resolve audience spec to a list of entity ids at the given day.

        Supported audience forms:
          {"faction": str, min_rank?: str, group?: str}
              → members_of(g, faction, day, min_rank=..., group=...)
          {"place": str}
              → entities with a current located_in → place relation (direct occupants)
        Subtree place resolution: DEFERRED (S1e spec).
        """
        if "faction" in audience:
            # Import here to avoid circular import at module load time
            from systems.faction import members_of
            return members_of(
                g,
                audience["faction"],
                day,
                min_rank=audience.get("min_rank"),
                group=audience.get("group"),
            )
        elif "place" in audience:
            place = audience["place"]
            members: list[str] = []
            for eid in list(g.entities):
                if place in g.neighbors(eid, "located_in", day):
                    members.append(eid)
            return members
        else:
            log.warning("knowledge_broadcast: unknown audience spec %s", audience)
            return []

    # ------------------------------------------------------------------
    # Write path: validate + to_events
    # ------------------------------------------------------------------

    def validate(self, section: str, decl: list, world: dict) -> list[ValidationError]:
        """Validate knowledge declarations.

        Checks:
          told / endowment items: knower entity must exist in graph → dangling_ref.
          told / endowment / broadcast items: required fields present → missing.
        broadcast items: audience entities are resolved at apply time, not validated here.
        """
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        errs: list[ValidationError] = []

        if section != "knowledge":
            return errs

        for i, item in enumerate(decl or []):
            op = item.get("op", "told")

            if op == "told":
                # --- required field checks ---
                if not isinstance(item.get("knower"), str) or not item.get("knower"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].knower",
                        code="missing",
                        hint="told 操作必须提供 knower（告知对象的实体id）",
                    ))
                if not isinstance(item.get("fact_key"), str) or not item.get("fact_key"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].fact_key",
                        code="missing",
                        hint="told 操作必须提供 fact_key（知识条目的键名）",
                    ))
                if "value" not in item:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].value",
                        code="missing",
                        hint="told 操作必须提供 value（知识内容）",
                    ))
                # --- ref check (only when knower present) ---
                knower = item.get("knower")
                if knower and g and g.get_entity(knower) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].knower",
                        code="dangling_ref",
                        hint=f"告知目标 '{knower}' 不存在于图中",
                    ))

            elif op == "endowment":
                # --- required field checks ---
                if not isinstance(item.get("knower"), str) or not item.get("knower"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].knower",
                        code="missing",
                        hint="endowment 操作必须提供 knower（赋知对象的实体id）",
                    ))
                # validate each grant sub-item
                for j, grant in enumerate(item.get("grants") or []):
                    if not isinstance(grant.get("fact_key"), str) or not grant.get("fact_key"):
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].grants[{j}].fact_key",
                            code="missing",
                            hint="endowment 每条 grant 必须提供 fact_key",
                        ))
                    if "value" not in grant:
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].grants[{j}].value",
                            code="missing",
                            hint="endowment 每条 grant 必须提供 value",
                        ))
                # --- ref check (only when knower present) ---
                knower = item.get("knower")
                if knower and g and g.get_entity(knower) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].knower",
                        code="dangling_ref",
                        hint=f"赋知目标 '{knower}' 不存在于图中",
                    ))

            elif op == "broadcast":
                # --- required field checks ---
                if not isinstance(item.get("fact_key"), str) or not item.get("fact_key"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].fact_key",
                        code="missing",
                        hint="broadcast 操作必须提供 fact_key",
                    ))
                if "value" not in item:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].value",
                        code="missing",
                        hint="broadcast 操作必须提供 value",
                    ))
                if not isinstance(item.get("audience"), dict) or not item.get("audience"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].audience",
                        code="missing",
                        hint="broadcast 操作必须提供 audience（dict，含 faction 或 place 键）",
                    ))
                # broadcast: audience entities are resolved at apply time; skip ref-checking here

        return errs

    def to_events(
        self, section: str, decl: list, *, turn: int, day: int, scene: str
    ) -> list[dict]:
        """Map knowledge declarations to events by op field.

        told       → one knowledge_set event
        endowment  → N knowledge_set events (one per grant item)
        broadcast  → one knowledge_broadcast event (audience in deltas)
        """
        out: list[dict] = []
        if section != "knowledge":
            return out

        for item in decl:
            op = item.get("op", "told")

            if op == "told":
                knower = item.get("knower")
                fact_key = item.get("fact_key")
                if not knower or not fact_key or "value" not in item:
                    log.warning(
                        "to_events told item missing required fields "
                        "(knower=%r fact_key=%r value present=%s) — skipped",
                        knower, fact_key, "value" in item,
                    )
                    continue
                out.append(kernel_event(
                    "knowledge_set", day=day, scene=scene,
                    summary=f"{knower} 得知 {fact_key}",
                    deltas={
                        "knower": knower,
                        "fact_key": fact_key,
                        "value": item["value"],
                        "via": item.get("via"),
                    },
                    turn=turn,
                ))

            elif op == "endowment":
                knower = item.get("knower")
                if not knower:
                    log.warning(
                        "to_events endowment item missing knower — skipped",
                    )
                    continue
                for j, grant in enumerate(item.get("grants") or []):
                    grant_fk = grant.get("fact_key")
                    if not grant_fk or "value" not in grant:
                        log.warning(
                            "to_events endowment grant[%d] missing required fields "
                            "(fact_key=%r value present=%s) — skipped",
                            j, grant_fk, "value" in grant,
                        )
                        continue
                    out.append(kernel_event(
                        "knowledge_set", day=day, scene=scene,
                        summary=f"{knower} 赋知 {grant_fk}",
                        deltas={
                            "knower": knower,
                            "fact_key": grant_fk,
                            "value": grant["value"],
                        },
                        turn=turn,
                    ))

            elif op == "broadcast":
                # One knowledge_broadcast event; audience resolution happens in apply()
                fact_key = item.get("fact_key")
                audience = item.get("audience")
                if not fact_key or "value" not in item or not audience:
                    log.warning(
                        "to_events broadcast item missing required fields "
                        "(fact_key=%r value present=%s audience=%r) — skipped",
                        fact_key, "value" in item, audience,
                    )
                    continue
                out.append(kernel_event(
                    "knowledge_broadcast", day=day, scene=scene,
                    summary=f"广播 {fact_key} 至 {audience}",
                    deltas={
                        "fact_key": fact_key,
                        "value": item["value"],
                        "audience": audience,
                    },
                    turn=turn,
                ))

        return out

    # ------------------------------------------------------------------
    # Read path: inject (KnowledgeSystem has no scene inject for now)
    # ------------------------------------------------------------------

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        """KnowledgeSystem does not inject scene context directly (assembled in S3+)."""
        return None
