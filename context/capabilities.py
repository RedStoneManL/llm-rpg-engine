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


def validate_protagonist_moves(
    commit,
    world: dict,
    scene: dict,
    *,
    narration_rewrite_from: str | None = None,
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
        if _new_place_route(commit, world, capability.location, destination):
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


def validate_protagonist_move_events(events: list[dict], world: dict, scene: dict) -> list[ValidationError]:
    """Recheck final publish payloads so later staging cannot bypass the contract."""
    capability = scene_capabilities(world, scene)
    if not capability.actor:
        return []
    allowed = capability.allowed_move_ids | {capability.location}
    # Final payloads use the same effective-event semantics as projection:
    # events marked retracted are not allowed to create a discoverable place or
    # route for an otherwise invalid move.
    active_events = [event for event in events if not event.get("retracted")]
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
    errors: list[ValidationError] = []
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
        if not isinstance(destination, str) or destination in allowed or discovered:
            continue
        errors.append(ValidationError(
            section="moves", field=f"event[{index}].deltas.to", code="outside_scene_capability",
            hint="最终发布的主角移动不在 action-start POV 行动能力内。",
        ))
    return errors
