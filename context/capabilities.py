"""One scene-time contract for player-visible places and legal protagonist travel.

The canonical ontology remains authoritative for simulation.  This module first
projects it through :func:`context.access.pov_world`, then derives a compact,
deterministic capability value from that already-authorized graph.  Consumers
must not independently re-query canonical ``adjacent_to`` relations for a
player presentation or player-authored move.

Legacy public places remain visible through ``pov_world`` for compatibility.
New discovery mechanics can tighten that policy in one place by changing the
POV projection; this contract will then update prompts, UI and validation
together.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from context.access import pov_world
from systems.knowledge import place_discovery_key, route_discovery_key
from kernel.contextsystem import ValidationError


@dataclass(frozen=True)
class PlaceCapability:
    id: str
    label: str
    kind: str


@dataclass(frozen=True)
class RouteCapability:
    source: str
    destination: str
    cost: str


@dataclass(frozen=True)
class SceneCapabilities:
    """Read-only player contract for one action-start scene."""

    actor: str | None
    location: str | None
    places: tuple[PlaceCapability, ...]
    routes: tuple[RouteCapability, ...]
    exits: tuple[PlaceCapability, ...]

    @property
    def allowed_move_ids(self) -> frozenset[str]:
        """Destinations the current actor can move to during this turn."""
        return frozenset(place.id for place in self.exits)

    def map_dto(self) -> dict[str, list[dict[str, str]]]:
        return {
            "nodes": [{"id": p.id, "label": p.label, "kind": p.kind} for p in self.places],
            "edges": [{"from": r.source, "to": r.destination, "cost": r.cost} for r in self.routes],
        }

    def prompt_fragment(self) -> str:
        """Return explicit, non-canonical movement authority for an author prompt."""
        if not self.actor or not self.location:
            return "【行动能力】当前位置未确定；不要声明主角移动。"
        if not self.exits:
            return f"【行动能力】主角 {self.actor} 当前在 {self.location}；没有已知可移动出口。"
        destinations = "、".join(f"{place.id}（{place.label}）" for place in self.exits)
        return (f"【行动能力】主角 {self.actor} 当前在 {self.location}；本回合仅可移动到：{destinations}。"
                "不要把主角直接移动到未列出的地点；若本回合发现新地点，须创建地点并声明从当前位置直连的 links。")


def native_player_dto(world: dict, scene: dict) -> dict:
    """Safe native-evaluation observation: POV only, never host grants/raw truth."""
    capability = scene_capabilities(world, scene)
    return {"location": capability.location, "map": capability.map_dto(),
            "exits": [place.id for place in capability.exits]}


def _display_cost(value: Any) -> str:
    return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else "1"


def scene_capabilities(world: dict, scene: dict, *, actor: str | None = None,
                       canonical: bool = False) -> SceneCapabilities:
    """Derive action-start topology without mutation.

    ``canonical=True`` is reserved for a separately selected, read-only GM
    surface.  The default always derives from the scene actor's POV.
    """
    actor = actor or scene.get("protagonist")
    view = world if canonical else pov_world(world, scene, pov=actor, redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    day = scene.get("day") or view.get("meta", {}).get("day") or 1
    location = scene.get("location")
    if graph is None:
        return SceneCapabilities(actor, location, (), (), ())

    places = tuple(
        PlaceCapability(
            id=entity_id,
            label=str(entity.attrs.get("label") or entity.attrs.get("name")
                      or entity.attrs.get("seed") or entity_id),
            kind=str(entity.attrs.get("kind") or "地点"),
        )
        for entity_id, entity in sorted(graph.entities.items())
        if entity.etype == "Place"
    )
    by_id = {place.id: place for place in places}
    routes: list[RouteCapability] = []
    seen: set[tuple[str, str]] = set()
    for source in sorted(by_id):
        for destination, attrs in graph.relation_attrs_at(source, "adjacent_to", day):
            if destination not in by_id:
                continue
            pair = tuple(sorted((source, destination)))
            if pair in seen:
                continue
            seen.add(pair)
            routes.append(RouteCapability(pair[0], pair[1], _display_cost(attrs.get("travel_cost", 1))))
    exits = tuple(by_id[destination] for destination in graph.neighbors(location, "adjacent_to", day)
                  if destination in by_id) if location else ()
    return SceneCapabilities(actor, location, places, tuple(routes), exits)


def _new_place_route(commit, world: dict, origin: str, destination: str) -> bool:
    """Allow an atomically declared discovery, never a silent player teleport."""
    graph = world.get("systems", {}).get("ontology")
    # A declaration is a creation only when the canonical graph did not already
    # own this id at action start.  Re-declaring a hidden canonical id must not
    # relabel it as newly discovered or replace its visibility metadata.
    if graph is not None and graph.get_entity(destination) is not None:
        return False
    sections = getattr(commit, "sections", {}) or {}
    places = sections.get("places") or []
    if destination not in {item.get("id") for item in places if isinstance(item, dict)}:
        return False
    for link in sections.get("links") or []:
        if not isinstance(link, dict):
            continue
        if {link.get("a"), link.get("b")} == {origin, destination}:
            return True
    return False


def _host_discovered_route(world: dict, origin: str, destination: str, trusted_keys, effective_keys=frozenset()) -> bool:
    graph = world.get("systems", {}).get("ontology")
    place_key, route_key = place_discovery_key(destination), route_discovery_key(origin, destination)
    target = graph.get_entity(destination) if graph else None
    if (graph is None or target is None or target.attrs.get("visibility") != "undiscovered"
            or place_key not in trusted_keys or route_key not in trusted_keys
            or place_key not in effective_keys or route_key not in effective_keys):
        return False
    day = world.get("meta", {}).get("day") or 1
    return any(attrs.get("visibility") == "undiscovered"
               for neighbor, attrs in graph.relation_attrs_at(origin, "adjacent_to", day)
               if neighbor == destination)


def discovery_grant_errors(commit, actor: str | None, trusted_keys=frozenset()) -> list[ValidationError]:
    """Discovery is host authority, never an author-declared capability."""
    errors = []
    for index, item in enumerate((getattr(commit, "sections", {}) or {}).get("knowledge") or []):
        if not isinstance(item, dict) or item.get("knower") != actor:
            continue
        op = item.get("op")
        key = (place_discovery_key(item["place"]) if op == "discover_place" and isinstance(item.get("place"), str)
               else route_discovery_key(item["a"], item["b"]) if op == "discover_route" and isinstance(item.get("a"), str) and isinstance(item.get("b"), str)
               else item.get("fact_key"))
        if isinstance(key, str) and key.endswith(".discovered") and key not in trusted_keys:
            errors.append(ValidationError("knowledge", f"[{index}]", "untrusted_discovery",
                                          "地点或路线发现必须由可信宿主授权，不能由作者声明。"))
        if item.get("op") == "broadcast" and isinstance(item.get("fact_key"), str) and item["fact_key"].endswith(".discovered"):
            errors.append(ValidationError("knowledge", f"[{index}]", "protected_discovery_write",
                                          "发现权限不能通过 broadcast 写入。"))
        if item.get("op") == "endowment":
            for grant_index, grant in enumerate(item.get("grants") or []):
                key = grant.get("fact_key") if isinstance(grant, dict) else None
                if isinstance(key, str) and key.endswith(".discovered"):
                    errors.append(ValidationError("knowledge", f"[{index}].grants[{grant_index}]", "protected_discovery_write",
                                                  "发现权限不能通过 endowment 写入。"))
    for index, item in enumerate((getattr(commit, "sections", {}) or {}).get("facts") or []):
        if (isinstance(item, dict) and item.get("subject") == actor
                and isinstance(item.get("predicate"), str) and item["predicate"].startswith("knows:")
                and item["predicate"].endswith(".discovered")):
            errors.append(ValidationError("facts", f"[{index}]", "protected_discovery_write",
                                          "发现权限不能通过 facts 写入。"))
    return errors


def validate_protagonist_moves(
    commit,
    world: dict,
    scene: dict,
    *,
    narration_rewrite_from: str | None = None,
    trusted_keys=frozenset(),
) -> list[ValidationError]:
    """Return repairable errors when the scene protagonist leaves its capability.

    NPC simulation and generic direct ``PlaceSystem`` operations remain outside
    this player-turn policy.  A just-created destination remains valid only if
    the same commit declares an explicit direct route from the action-start
    location, so discovery/replay stays atomic and visible to later readers.
    """
    capability = scene_capabilities(world, scene)
    if not capability.actor:
        return []
    allowed = capability.allowed_move_ids | {capability.location}
    errors: list[ValidationError] = []
    requires_narration_rewrite = narration_rewrite_from is not None
    for index, move in enumerate((getattr(commit, "sections", {}) or {}).get("moves") or []):
        if not isinstance(move, dict) or move.get("who") != capability.actor:
            continue
        destination = move.get("to")
        if capability.location is None:
            errors.append(ValidationError(
                section="moves",
                field=f"[{index}].to",
                code="missing_scene_location",
                hint="主角当前位置未确定；本回合不能声明主角移动。",
            ))
            requires_narration_rewrite = True
            continue
        if not isinstance(destination, str) or destination in allowed:
            continue
        values = {}
        for item in (getattr(commit, "sections", {}).get("knowledge") or []):
            if not isinstance(item, dict) or item.get("knower") != capability.actor:
                continue
            key = (place_discovery_key(item.get("place")) if item.get("op") == "discover_place"
                   else route_discovery_key(item.get("a"), item.get("b")) if item.get("op") == "discover_route" else item.get("fact_key"))
            if isinstance(key, str) and key.endswith(".discovered"):
                values[key] = item.get("value", True)
        grants = {key for key, value in values.items() if value is True}
        if (_new_place_route(commit, world, capability.location, destination)
                or _host_discovered_route(world, capability.location, destination, trusted_keys, grants)):
            continue
        errors.append(ValidationError(
            section="moves",
            field=f"[{index}].to",
            code="outside_scene_capability",
            hint=(f"主角本回合只能从 '{capability.location}' 移动到已知相邻地点；"
                  "目标不在行动能力内。若发现新地点，须同时在 places 创建它并在 links 声明直连路线。"),
        ))
        requires_narration_rewrite = True

    # This obligation is intentionally stateful across repair attempts.  A
    # later ``moves=[]`` does not make the original arrival prose safe: the
    # repaired narration must explicitly differ from the proposal that caused
    # the travel violation.  The loop records that proposal and supplies it on
    # every subsequent validation pass.
    if (requires_narration_rewrite
            and (narration_rewrite_from is None
                 or getattr(commit, "narration", None) == narration_rewrite_from)):
        errors.append(ValidationError(
            section="narration",
            field="",
            code="scene_capability_narration",
            hint="移动目标不在主角行动能力内；请重写 narration，不能描述已到达该地点。",
        ))
    return errors


def validate_protagonist_move_events(events: list[dict], world: dict, scene: dict, *, trusted_keys=frozenset()) -> list[ValidationError]:
    """Recheck final publish payloads so later staging cannot bypass the contract."""
    capability = scene_capabilities(world, scene)
    if not capability.actor:
        return []
    allowed = capability.allowed_move_ids | {capability.location}
    errors: list[ValidationError] = []
    for index, event in enumerate(events):
        delta = event.get("deltas") or {}
        fact_key = delta.get("fact_key")
        predicate = delta.get("predicate")
        protected = ((isinstance(fact_key, str) and fact_key.endswith(".discovered"))
                     or (isinstance(predicate, str) and predicate.startswith("knows:")
                         and predicate.endswith(".discovered")))
        if protected and event.get("type") != "knowledge_set":
            errors.append(ValidationError("knowledge", f"event[{index}]", "protected_discovery_write",
                                          "发现权限只能由受宿主授权的 knowledge_set 写入。"))
        if (event.get("type") == "knowledge_set" and delta.get("knower") == capability.actor
                and isinstance(delta.get("fact_key"), str) and delta["fact_key"].endswith(".discovered")
                and delta["fact_key"] not in trusted_keys):
            errors.append(ValidationError("knowledge", f"event[{index}]", "untrusted_discovery",
                                          "最终 payload 含未经宿主授权的发现权限。"))
    # Final payloads use the same effective-event semantics as projection:
    # events marked retracted are not allowed to create a discoverable place or
    # route for an otherwise invalid move.
    active_events = [event for event in events if not event.get("retracted")]
    grant_values = {}
    for event in active_events:
        delta = event.get("deltas") or {}
        if event.get("type") == "knowledge_set" and delta.get("knower") == capability.actor:
            key = delta.get("fact_key")
            if isinstance(key, str) and key.endswith(".discovered"):
                grant_values[key] = delta.get("value")
    effective_grants = {key for key, value in grant_values.items() if value is True}
    canonical_graph = world.get("systems", {}).get("ontology")
    created = {
        (event.get("deltas") or {}).get("id")
        for event in active_events if event.get("type") == "place_created"
        and (canonical_graph is None
             or canonical_graph.get_entity((event.get("deltas") or {}).get("id")) is None)
    }
    direct_links = {
        frozenset({(event.get("deltas") or {}).get("a"), (event.get("deltas") or {}).get("b")})
        for event in active_events if event.get("type") == "place_linked"
    }
    for index, event in enumerate(events):
        if event.get("type") != "entity_moved":
            continue
        deltas = event.get("deltas") or {}
        if deltas.get("who") != capability.actor:
            continue
        destination = deltas.get("to")
        if capability.location is None:
            errors.append(ValidationError(
                section="moves", field=f"event[{index}].deltas.to", code="missing_scene_location",
                hint="最终发布的主角移动没有可验证的 action-start 当前位置。",
            ))
            continue
        discovered = (isinstance(destination, str) and destination in created
                      and frozenset((capability.location, destination)) in direct_links)
        host_discovered = (isinstance(destination, str)
                           and _host_discovered_route(world, capability.location, destination, trusted_keys, effective_grants))
        if not isinstance(destination, str) or destination in allowed or discovered or host_discovered:
            continue
        errors.append(ValidationError(
            section="moves", field=f"event[{index}].deltas.to", code="outside_scene_capability",
            hint="最终发布的主角移动不在 action-start POV 行动能力内。",
        ))
    return errors
