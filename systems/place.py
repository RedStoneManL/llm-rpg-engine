"""PlaceSystem — models the three-tier map as Place entities + relations
in the shared FactGraph (world["systems"]["ontology"]).

Requires OntologySystem to be registered before PlaceSystem in the Registry,
since PlaceSystem writes to the shared graph (world["systems"]["ontology"]).

Place entities have attrs: level (1|2|3), kind, seed, detail, (optional) tier.
Containment:  contained_by  relation (child → parent).
Adjacency:    adjacent_to   relation (both directions) carrying travel_cost attr.
             Multi-valued — does NOT supersede prior adjacent_to relations.
Movement:     located_in    relation (single-valued — supersedes prior location).

LLM-driven decisions (when to materialize, cost ladder, location-staleness)
belong to the S4 loop — this system only provides the mechanisms.

navigate() implements Dijkstra over adjacent_to edges; pure stdlib (heapq).
Multi-level containment ascend/descend routing is DEFERRED (same-graph adjacency
routing suffices for S1b).
"""

from __future__ import annotations

import heapq
from typing import Any

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event
from facts.graph import FactGraph
from engine.log import get_logger

log = get_logger("systems.place")

_VALID_LEVELS = {1, 2, 3}
_VALID_KINDS = {"settlement", "wilderness", "dungeon", "venue", "region"}


# ---------------------------------------------------------------------------
# Module-level pathfinding
# ---------------------------------------------------------------------------

def navigate(graph: FactGraph, src: str, dst: str, day: int) -> dict:
    """Dijkstra shortest path over adjacent_to edges at the given day.

    Scans the FactGraph's adjacent_to relations (which carry travel_cost attrs)
    and returns the minimum-cost route.

    Returns:
        {"path": [src, ..., dst], "total_cost": <int>}  on success.
        {"path": [src], "total_cost": 0}                 if src == dst.
        {"path": [], "total_cost": None}                 if unreachable.

    Edge cost = attrs.get("travel_cost", 1).
    Multi-level containment ascend/descend routing: DEFERRED (S1b uses same-graph
    adjacency only).
    """
    if src == dst:
        return {"path": [src], "total_cost": 0}

    # heap: (cost, node, path)
    heap: list[tuple[int, str, list[str]]] = [(0, src, [src])]
    visited: set[str] = set()

    while heap:
        cost, node, path = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == dst:
            return {"path": path, "total_cost": cost}
        for neighbor, attrs in graph.relation_attrs_at(node, "adjacent_to", day):
            if neighbor not in visited:
                edge_cost = attrs.get("travel_cost", 1)
                heapq.heappush(heap, (cost + edge_cost, neighbor, path + [neighbor]))

    return {"path": [], "total_cost": None}


# ---------------------------------------------------------------------------
# PlaceSystem
# ---------------------------------------------------------------------------

class PlaceSystem(ContextSystem):
    """ContextSystem that owns place events and writes to the shared FactGraph.

    Requires OntologySystem to be registered (its shared FactGraph is at
    world["systems"]["ontology"]).

    Commit sections:
        "places"  → place_created events
        "moves"   → entity_moved events
    """

    name = "place"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"place_created", "place_linked", "place_materialized", "entity_moved"}

    def commit_sections(self) -> set[str]:
        return {"places", "moves", "links", "materialize"}

    def empty_state(self) -> dict:
        """PlaceSystem owns no separate slice — places live in the shared graph."""
        return {}

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def apply(self, world: dict, event: dict) -> None:
        g: FactGraph = world["systems"]["ontology"]
        d = event.get("deltas", {})
        t = event["type"]

        if t == "place_created":
            pid = d.get("id")
            if not pid:
                log.warning("place_created %s missing id; skipped", event.get("id"))
                return
            tier = d.get("tier", "mentioned")
            attrs: dict[str, Any] = {}
            for k in ("level", "kind", "seed", "detail", "density"):
                if k in d:
                    attrs[k] = d[k]
            g.add_entity(pid, "Place", tier=tier, **attrs)
            # Phase D: stamp last_update for drift tracking
            g.get_entity(pid).attrs["last_update"] = event["day"]
            parent = d.get("parent")
            if parent:
                g.add_relation(
                    pid, "contained_by", parent,
                    day=event["day"],
                    turn=event.get("turn") or 0,
                    source_event=event["id"],
                )
            log.debug("place_created id=%s level=%s kind=%s parent=%s",
                      pid, attrs.get("level"), attrs.get("kind"), parent)

        elif t == "place_materialized":
            pid = d.get("id")
            if not pid:
                log.warning("place_materialized %s missing id; skipped", event.get("id"))
                return
            e = g.get_entity(pid)
            if e is not None:
                e.attrs["detail"] = "full"
                # Phase D: stamp last_update on materialization
                e.attrs["last_update"] = event["day"]
            log.debug("place_materialized id=%s", pid)

        elif t == "place_linked":
            a, b = d.get("a"), d.get("b")
            if not (a and b):
                log.warning("place_linked %s missing a/b (a=%r b=%r); skipped",
                            event.get("id"), a, b)
                return
            cost = d.get("travel_cost", 1)
            # adjacent_to is multi-valued — use supersede=False so prior edges survive
            g.add_relation(
                a, "adjacent_to", b,
                day=event["day"],
                turn=event.get("turn") or 0,
                source_event=event["id"],
                supersede=False,
                travel_cost=cost,
            )
            g.add_relation(
                b, "adjacent_to", a,
                day=event["day"],
                turn=event.get("turn") or 0,
                source_event=event["id"],
                supersede=False,
                travel_cost=cost,
            )
            log.debug("place_linked a=%s b=%s cost=%s", a, b, cost)

        elif t == "entity_moved":
            who, to = d.get("who"), d.get("to")
            if who and to:
                # located_in is single-valued (supersede=True default)
                g.add_relation(
                    who, "located_in", to,
                    day=event["day"],
                    turn=event.get("turn") or 0,
                    source_event=event["id"],
                )
                # Phase D: stamp last_update on the destination Place
                to_entity = g.get_entity(to)
                if to_entity is not None:
                    to_entity.attrs["last_update"] = event["day"]
                log.debug("entity_moved who=%s to=%s", who, to)
            else:
                # Projection must never crash on a malformed stored event.
                log.warning("entity_moved %s missing who/to (who=%r to=%r); skipped",
                            event.get("id"), who, to)

    # ------------------------------------------------------------------
    # Write path: validate + to_events
    # ------------------------------------------------------------------

    def created_ids(self, section: str, decl: list) -> set[str]:
        # Only 'places' creates location ids; moves/links/materialize reference them.
        if section != "places":
            return set()
        return super().created_ids(section, decl)

    def validate(self, section: str, decl: list, world: dict) -> list[ValidationError]:
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        errs: list[ValidationError] = []

        if section == "places":
            for i, item in enumerate(decl or []):
                pid = item.get("id")
                if not pid:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="地点声明必须包含 'id' 字段",
                    ))
                level = item.get("level")
                if level is not None and level not in _VALID_LEVELS:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].level",
                        code="bad_enum",
                        hint=f"level 必须为 1/2/3，当前值: {level!r}",
                    ))
                kind = item.get("kind")
                if kind is not None and kind not in _VALID_KINDS:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].kind",
                        code="bad_enum",
                        hint=f"kind 必须为 {sorted(_VALID_KINDS)} 之一，当前值: {kind!r}",
                    ))
                parent = item.get("parent")
                if parent and g and g.get_entity(parent) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].parent",
                        code="dangling_ref",
                        hint=f"父地点 '{parent}' 不存在于图中（跨节提交延迟校验）",
                    ))

        elif section == "moves":
            for i, item in enumerate(decl or []):
                who = item.get("who")
                to = item.get("to")
                if not who:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].who",
                        code="missing",
                        hint="移动声明必须包含 'who'（移动的实体 id，例如 protagonist）",
                    ))
                elif g and g.get_entity(who) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].who",
                        code="dangling_ref",
                        hint=f"移动主体 '{who}' 不存在于图中",
                    ))
                if not to:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].to",
                        code="missing",
                        hint="移动声明必须包含 'to'（目标地点 id）",
                    ))
                elif g and g.get_entity(to) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].to",
                        code="dangling_ref",
                        hint=f"目的地 '{to}' 不存在于图中",
                    ))
                arrive_day = item.get("arrive_day")
                if arrive_day is not None:
                    try:
                        int(arrive_day)
                    except (TypeError, ValueError):
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].arrive_day",
                            code="bad_type",
                            hint=f"arrive_day 必须为整数或可转换为整数的字符串，当前值: {arrive_day!r}",
                        ))

        elif section == "links":
            for i, item in enumerate(decl or []):
                a = item.get("a")
                b = item.get("b")
                if not a:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].a",
                        code="missing",
                        hint="链接声明必须包含 'a'（起点地点 id）",
                    ))
                elif g and g.get_entity(a) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].a",
                        code="dangling_ref",
                        hint=f"地点 '{a}' 不存在于图中",
                    ))
                if not b:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].b",
                        code="missing",
                        hint="链接声明必须包含 'b'（终点地点 id）",
                    ))
                elif g and g.get_entity(b) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].b",
                        code="dangling_ref",
                        hint=f"地点 '{b}' 不存在于图中",
                    ))

        elif section == "materialize":
            for i, item in enumerate(decl or []):
                pid = item.get("id")
                if not pid:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="实体化声明必须包含 'id'（目标地点 id）",
                    ))
                elif g and g.get_entity(pid) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="dangling_ref",
                        hint=f"地点 '{pid}' 不存在于图中",
                    ))

        return errs

    def to_events(
        self, section: str, decl: list, *, turn: int, day: int, scene: str
    ) -> list[dict]:
        out = []
        if section == "places":
            for p in decl:
                out.append(kernel_event(
                    "place_created", day=day, scene=scene,
                    summary=f"{p.get('id','?')} 地点创建", deltas=p, turn=turn,
                ))
        elif section == "moves":
            for m in decl:
                # Phase D: opt-in arrive_day shifts event day; max() prevents backward jump.
                # FactGraph.assert_fact requires non-decreasing day, which max() guarantees.
                ev_day = max(int(m.get("arrive_day", day)), day)
                out.append(kernel_event(
                    "entity_moved", day=ev_day, scene=scene,
                    summary=f"{m.get('who','?')} 移动至 {m.get('to','?')}",
                    deltas=m, turn=turn,
                ))
        elif section == "links":
            for lnk in decl:
                out.append(kernel_event(
                    "place_linked", day=day, scene=scene,
                    summary=f"{lnk.get('a','?')} ↔ {lnk.get('b','?')}",
                    deltas=lnk, turn=turn,
                ))
        elif section == "materialize":
            for m in decl:
                out.append(kernel_event(
                    "place_materialized", day=day, scene=scene,
                    summary=f"{m.get('id','?')} 实体化",
                    deltas=m, turn=turn,
                ))
        return out

    # ------------------------------------------------------------------
    # Read path: inject
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Read path: recall (M4)
    # ------------------------------------------------------------------

    def recall(self, query: str, world: dict) -> list[RecallHit]:
        """Return RecallHits for Place entities whose id or seed contains query.

        Substring matching only — semantic ranking happens in the assembler via S2.
        """
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        if not g:
            return []
        hits: list[RecallHit] = []
        for eid, entity in g.entities.items():
            if entity.etype != "Place":
                continue
            seed = entity.attrs.get("seed", "")
            kind = entity.attrs.get("kind", "")
            matched = (query in eid) or (seed and query in seed)
            if matched:
                text = f"{eid}（{kind}）：{seed}" if seed else f"{eid}（{kind}）"
                hits.append(RecallHit(
                    system="place",
                    score=1.0,
                    text=text,
                    ref={"id": eid},
                ))
                log.debug("recall hit place=%s query=%r", eid, query)
        return hits

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        """Return a scene-layer Fragment with current location + exits affordance, or None.

        Reads protagonist's located_in relation, renders the place name + kind,
        and lists all adjacent_to exits with their travel costs.
        Returns None if the protagonist has no recorded location.
        """
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        if not g:
            return None
        who = scene.get("protagonist")
        day = scene.get("day", 0)
        if not who:
            return None

        locations = g.neighbors(who, "located_in", day)
        if not locations:
            return None

        loc_id = locations[0]
        loc_entity = g.get_entity(loc_id)
        loc_kind = loc_entity.attrs.get("kind", "") if loc_entity else ""

        exits = g.relation_attrs_at(loc_id, "adjacent_to", day)
        if exits:
            exits_text = "、".join(
                f"{dst}({attrs.get('travel_cost', 1)}日)" for dst, attrs in exits
            )
            location_text = f"当前位置：{loc_id}（{loc_kind}）。出口：{exits_text}"
            affordance = "可移动目标：" + "、".join(dst for dst, _ in exits)
        else:
            location_text = f"当前位置：{loc_id}（{loc_kind}）。无已知出口。"
            affordance = ""

        return Fragment(
            system="place",
            layer="scene",
            text=location_text,
            affordance=affordance,
        )
