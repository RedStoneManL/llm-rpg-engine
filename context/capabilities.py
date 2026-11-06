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
import hashlib
from typing import Any

from context.access import current_public_people, pov_world
from kernel.action_effects import transfer_sequence, turn_end
from systems.knowledge import knows, place_discovery_key, route_discovery_key
from kernel.contextsystem import ValidationError
from loop.graph_utils import ancestor_of_level


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
class HostDiscoveryGrant:
    """One opaque, host-selected reveal authorization for an existing route.

    The destination stays inside this trusted value.  Player DTOs, strategy
    prompts and durable host envelopes use only ``semantic_id`` (an opaque
    digest), never the destination id or discovery keys.
    """
    version: int
    actor: str
    origin: str
    destination: str
    keys: frozenset[str]
    semantic_id: str


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


def host_route_discovery_grant(world: dict, scene: dict, destination: str) -> dict[str, str]:
    """Create one explicit host-side authorization for an existing hidden route.

    This is deliberately not derived from author input.  A caller in the
    trusted host position selects the destination, after which the returned
    pair of discovery keys must still appear in the author's committed
    ``knowledge`` records and pass both proposal and publish validation.  The
    helper refuses public/nonexistent/non-adjacent targets, so it cannot be
    used as a generic teleport or topology-creation API.
    """
    if not isinstance(destination, str) or not destination:
        raise ValueError("host route authorization requires a destination id")
    origin = scene.get("location")
    graph = world.get("systems", {}).get("ontology")
    if not isinstance(origin, str) or graph is None:
        raise ValueError("host route authorization requires a current scene location")
    target = graph.get_entity(destination)
    if target is None or target.attrs.get("visibility") != "undiscovered":
        raise ValueError("host route authorization requires an existing undiscovered destination")
    day = scene.get("day") or world.get("meta", {}).get("day") or 1
    if not any(attrs.get("visibility") == "undiscovered"
               for neighbor, attrs in graph.relation_attrs_at(origin, "adjacent_to", day)
               if neighbor == destination):
        raise ValueError("host route authorization requires an existing undiscovered adjacent route")
    return {
        "from": origin,
        "to": destination,
        "place_discovery_key": place_discovery_key(destination),
        "route_discovery_key": route_discovery_key(origin, destination),
    }


def host_nearby_discovery_grant(world: dict, scene: dict, *, allow_known: bool = False) -> HostDiscoveryGrant | None:
    """Select at most one hidden adjacent route for a trusted reveal attempt.

    This is deliberately a host resolver, not a player lookup: callers cannot
    name a target.  A route is eligible only when its opaque host marker has a
    matching durable, public local clue; exactly one eligible route is required
    or the resolver fails closed.  A previously committed retry may set
    ``allow_known`` so recovery does not need to mint a second opportunity
    after the first turn already made the route visible.
    """
    actor = scene.get("protagonist")
    origin = scene.get("location")
    graph = world.get("systems", {}).get("ontology")
    if not isinstance(actor, str) or not isinstance(origin, str) or graph is None:
        return None
    day = scene.get("day") or world.get("meta", {}).get("day") or 1
    candidates: list[str] = []
    for destination, attrs in graph.relation_attrs_at(origin, "adjacent_to", day):
        target = graph.get_entity(destination)
        if target is None or target.attrs.get("visibility") != "undiscovered":
            continue
        if attrs.get("visibility") != "undiscovered":
            continue
        clue_id = attrs.get("discovery_clue_id")
        if not _has_active_host_discovery_clue(world, origin, clue_id):
            continue
        place_key = place_discovery_key(destination)
        route_key = route_discovery_key(origin, destination)
        if not allow_known and knows(graph, actor, place_key, day) is True and knows(graph, actor, route_key, day) is True:
            continue
        candidates.append(destination)
    if not candidates:
        return None
    # A public clue must name one particular host opportunity.  Treat a host
    # configuration mistake (the same clue marker on multiple hidden edges) as
    # no opportunity rather than picking a lexical winner that players could
    # enumerate by repeating a generic request.
    if len(candidates) != 1:
        return None
    destination = candidates[0]
    keys = frozenset({place_discovery_key(destination), route_discovery_key(origin, destination)})
    # The digest is a durable recovery discriminator, not a target handle.
    material = f"hidden-discovery-v1\0{actor}\0{origin}\0{destination}".encode("utf-8")
    return HostDiscoveryGrant(
        version=1, actor=actor, origin=origin, destination=destination, keys=keys,
        semantic_id=hashlib.sha256(material).hexdigest(),
    )


def _has_active_host_discovery_clue(world: dict, origin: str, clue_id: Any) -> bool:
    """Require a durable, local, public clue minted by a trusted host path.

    A hidden adjacency alone is never an opportunity.  The matching clue is a
    public `world_change` already visible at the action-start place, while its
    opaque id stays in canonical route metadata.  Ordinary author world writes
    cannot forge this because their event materializer fixes source to
    ``author_world``; the resolver accepts only the explicit host source.
    """
    if not isinstance(clue_id, str) or not clue_id or len(clue_id) > 128:
        return False
    changes = (world.get("systems", {}).get("cascade", {}) or {}).get("changes", [])
    return any(
        isinstance(item, dict)
        and item.get("place") == origin
        and item.get("source") == "host_discovery_clue"
        and item.get("discovery_clue_id") == clue_id
        and item.get("public_observation") is True
        and item.get("visibility") == "public"
        for item in changes
    )


def validate_host_nearby_discovery_grant(world: dict, scene: dict, grant: HostDiscoveryGrant, *, allow_known: bool = False) -> None:
    """Fail closed unless a grant is exactly the current host selection."""
    if not isinstance(grant, HostDiscoveryGrant):
        raise ValueError("host discovery authorization is invalid")
    expected = host_nearby_discovery_grant(world, scene, allow_known=allow_known)
    if expected != grant:
        raise ValueError("host discovery authorization is no longer available")


def host_discovery_records(grant: HostDiscoveryGrant) -> tuple[dict[str, str], dict[str, str]]:
    """Return the exact two knowledge declarations only the trusted host adds."""
    return (
        {"op": "discover_place", "knower": grant.actor, "place": grant.destination},
        {"op": "discover_route", "knower": grant.actor, "a": grant.origin, "b": grant.destination},
    )


def discovery_grant_errors(commit, actor: str | None, trusted_keys=frozenset()) -> list[ValidationError]:
    """Discovery is host authority, never an author-declared capability."""
    errors = []
    for index, item in enumerate((getattr(commit, "sections", {}) or {}).get("knowledge") or []):
        if not isinstance(item, dict):
            continue
        # Broadcast and endowment do not necessarily name the current actor in
        # ``knower``.  They are nevertheless forbidden discovery write paths,
        # so check them before the actor-specific host-grant handling below.
        if item.get("op") == "broadcast" and isinstance(item.get("fact_key"), str) and item["fact_key"].endswith(".discovered"):
            errors.append(ValidationError("knowledge", f"[{index}]", "protected_discovery_write",
                                          "发现权限不能通过 broadcast 写入。"))
        if item.get("op") == "endowment":
            for grant_index, grant in enumerate(item.get("grants") or []):
                key = grant.get("fact_key") if isinstance(grant, dict) else None
                if isinstance(key, str) and key.endswith(".discovered"):
                    errors.append(ValidationError("knowledge", f"[{index}].grants[{grant_index}]", "protected_discovery_write",
                                                  "发现权限不能通过 endowment 写入。"))
        if item.get("knower") != actor:
            continue
        op = item.get("op")
        key = (place_discovery_key(item["place"]) if op == "discover_place" and isinstance(item.get("place"), str)
               else route_discovery_key(item["a"], item["b"]) if op == "discover_route" and isinstance(item.get("a"), str) and isinstance(item.get("b"), str)
               else item.get("fact_key"))
        if isinstance(key, str) and key.endswith(".discovered") and key not in trusted_keys:
            errors.append(ValidationError("knowledge", f"[{index}]", "untrusted_discovery",
                                          "地点或路线发现必须由可信宿主授权，不能由作者声明。"))
    for index, item in enumerate((getattr(commit, "sections", {}) or {}).get("facts") or []):
        if (isinstance(item, dict) and item.get("subject") == actor
                and isinstance(item.get("predicate"), str) and item["predicate"].startswith("knows:")
                and item["predicate"].endswith(".discovered")):
            errors.append(ValidationError("facts", f"[{index}]", "protected_discovery_write",
                                          "发现权限不能通过 facts 写入。"))
    return errors


def player_turn_knowledge_errors(commit, actor: str | None) -> list[ValidationError]:
    """Constrain knowledge authority for an ordinary player-authored turn.

    The normal CLI/GUI author may record knowledge acquired by the protagonist.
    It cannot make an NPC learn the player's free-form input through a generic
    ``told``/``endowment`` declaration, nor fan it out with ``broadcast``.
    Explicit ``heard_player`` is handled by the separate host-receipt validator;
    it is the only ordinary player-turn route that may write player speech to an
    NPC.  Trusted host/director/bootstrap paths do not opt into this function.
    """
    if not isinstance(actor, str) or not actor:
        return []
    sections = getattr(commit, "sections", {}) or {}
    errors: list[ValidationError] = []
    for index, item in enumerate(sections.get("knowledge") or []):
        if not isinstance(item, dict):
            continue
        op = item.get("op", "told")
        if op == "heard_player":
            continue
        if op == "broadcast":
            errors.append(ValidationError(
                "knowledge", f"[{index}]", "untrusted_npc_knowledge",
                "普通玩家回合不能用 broadcast 授予他人知识；玩家原话须用宿主通信收据。",
            ))
            continue
        if op in {"told", "endowment", "discover_place", "discover_route"}:
            if item.get("knower") != actor:
                errors.append(ValidationError(
                    "knowledge", f"[{index}].knower", "untrusted_npc_knowledge",
                    "普通玩家回合只能把通用知识写给主角；NPC 获知主角原话须用宿主通信收据。",
                ))
    for index, item in enumerate(sections.get("facts") or []):
        if (isinstance(item, dict) and isinstance(item.get("predicate"), str)
                and item["predicate"].startswith("knows:")):
            errors.append(ValidationError(
                "facts", f"[{index}].predicate", "untrusted_npc_knowledge",
                "普通玩家回合不能通过 facts 直接写入任何角色知识；请使用受约束的 knowledge 段。",
            ))
    return errors


def player_turn_knowledge_event_errors(
    events: list[dict], actor: str | None, *, allow_player_statement: bool = True,
) -> list[ValidationError]:
    """Final-payload companion for :func:`player_turn_knowledge_errors`.

    Run after materialization, so an owner hook cannot substitute a generic
    knowledge event, a broadcast, or a raw ``knows:`` fact for the declaration
    that proposal validation saw.  Receipt-shaped rows are left for
    ``player_statement_receipt_event_errors`` to match exactly against the
    host-issued plan.  A recovery path that cannot reproduce that host plan
    must set ``allow_player_statement=False`` and fail closed instead.
    """
    if not isinstance(actor, str) or not actor:
        return []
    errors: list[ValidationError] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("retracted"):
            continue
        deltas = event.get("deltas")
        if not isinstance(deltas, dict):
            continue
        event_type = event.get("type")
        if event_type == "knowledge_broadcast":
            errors.append(ValidationError(
                "knowledge", f"event[{index}]", "untrusted_npc_knowledge",
                "普通玩家回合的最终 payload 不能广播知识。",
            ))
        elif event_type == "knowledge_set" and deltas.get("knower") != actor:
            # A host-receipt materialization is deliberately recognizable only
            # by its canonical tag; the receipt validator checks every other
            # field and rejects even a one-field mutation.
            if (deltas.get("source_kind") != "player_statement"
                    or not allow_player_statement):
                errors.append(ValidationError(
                    "knowledge", f"event[{index}]", "untrusted_npc_knowledge",
                    "普通玩家回合的最终 payload 含未经宿主授权的 NPC 知识写入。",
                ))
        if (event_type == "fact_asserted"
                and isinstance(deltas.get("predicate"), str)
                and deltas["predicate"].startswith("knows:")):
            errors.append(ValidationError(
                "facts", f"event[{index}]", "untrusted_npc_knowledge",
                "普通玩家回合的最终 payload 不能以 fact 伪造知识写入。",
            ))
    return errors


def _ordinary_fact_subjects(world: dict, scene: dict) -> tuple[str | None, frozenset[str]]:
    """Return the narrow fact subjects an ordinary player author can address.

    A player turn may always record a public fact about the protagonist.  It
    may also preserve a *current, public* person's attributable ``reported:``
    statement.  Other canonical facts (including local geography, objects,
    remote places and hidden entities) need an owner/host path instead.  Local
    physical observations use the separately bounded ``world`` section.
    """
    actor = scene.get("protagonist")
    if not isinstance(actor, str) or not actor:
        return None, frozenset()
    view = pov_world(world, scene, pov=actor, redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    day = scene.get('day') or world.get('meta', {}).get('day') or 1
    return actor, frozenset(current_public_people(graph, scene, day))


def _player_fact_error(section: str, field: str, hint: str) -> ValidationError:
    return ValidationError(section, field, "player_fact_authority_boundary", hint)


def player_turn_fact_errors(commit, world: dict, scene: dict) -> list[ValidationError]:
    """Constrain ordinary player fact declarations to public, local authority.

    The normal author receives a redacted player POV, not authority to assert
    truth about every entity in the canonical graph.  Facts about a present
    person are deliberately limited to public, attributable ``reported:``
    statements; world observations have their own bounded section.  This keeps
    useful player/provenance writes while rejecting remote or hidden facts.
    """
    actor, people = _ordinary_fact_subjects(world, scene)
    if actor is None:
        return []
    errors: list[ValidationError] = []
    for index, item in enumerate((getattr(commit, "sections", {}) or {}).get("facts") or []):
        if not isinstance(item, dict):
            errors.append(_player_fact_error("facts", f"[{index}]",
                                              "普通玩家回合的 facts 段只能写公开、局部的允许事实。"))
            continue
        subject, predicate = item.get("subject"), item.get("predicate")
        if subject == actor:
            if item.get("secrecy") != "public":
                errors.append(_player_fact_error("facts", f"[{index}].secrecy",
                                                  "普通玩家回合的主角事实必须显式为 public。"))
        elif subject in people:
            if not (isinstance(predicate, str) and predicate.startswith("reported:")
                    and item.get("secrecy") == "public"):
                errors.append(_player_fact_error(
                    "facts", f"[{index}]",
                    "当前公开人物只能记录 public 的 reported: 来源陈述；不能由普通作者断言其余世界真相。",
                ))
        else:
            errors.append(_player_fact_error(
                "facts", f"[{index}].subject",
                "普通玩家回合不能为远端、隐藏或非当前公开人物写入 canonical 事实。",
            ))
    return errors


def player_turn_fact_event_errors(events: list[dict], world: dict, scene: dict) -> list[ValidationError]:
    """Final-payload companion for :func:`player_turn_fact_errors`."""
    actor, people = _ordinary_fact_subjects(world, scene)
    if actor is None:
        return []
    errors: list[ValidationError] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("retracted") or event.get("type") != "fact_asserted":
            continue
        delta = event.get("deltas")
        if not isinstance(delta, dict):
            errors.append(_player_fact_error("facts", f"event[{index}]",
                                              "普通玩家回合的最终事实 payload 无效。"))
            continue
        subject, predicate = delta.get("subject"), delta.get("predicate")
        if subject == actor:
            valid = delta.get("secrecy") == "public"
        elif subject in people:
            valid = (isinstance(predicate, str) and predicate.startswith("reported:")
                     and delta.get("secrecy") == "public")
        else:
            valid = False
        if not valid:
            errors.append(_player_fact_error(
                "facts", f"event[{index}]",
                "普通玩家回合的最终 facts 事件越过了本地公开事实边界。",
            ))
    return errors


def _player_entity_error(section: str, field: str, hint: str) -> ValidationError:
    return ValidationError(section, field, "player_entity_authority_boundary", hint)


def _ordinary_character_ids(world: dict, scene: dict) -> tuple[str | None, frozenset[str], object | None]:
    """Return ordinary local character authority plus the action-start graph."""
    actor, people = _ordinary_fact_subjects(world, scene)
    graph = world.get("systems", {}).get("ontology")
    return actor, people, graph


def player_turn_entity_errors(commit, world: dict, scene: dict) -> list[ValidationError]:
    """Keep ordinary authors from mutating canonical people/topology remotely.

    New public characters remain a normal storytelling capability, but may not
    reuse an existing (possibly hidden) id or smuggle ``past``/``hidden``
    material.  Evolutions are restricted to directly observable state/mood of
    the protagonist or a current public person.  Generic ontology entities and
    relations are host/native authority: they can otherwise alter remote
    topology or bind arbitrary hidden ids with no scene-local evidence.
    """
    actor, people, graph = _ordinary_character_ids(world, scene)
    if actor is None:
        return []
    sections = getattr(commit, "sections", {}) or {}
    errors: list[ValidationError] = []
    for section in ("entities", "relations"):
        for index, _item in enumerate(sections.get(section) or []):
            errors.append(_player_entity_error(
                section, f"[{index}]",
                "普通玩家回合不能直接写通用实体或关系；请使用受约束的地点、物品或角色机制。",
            ))
    for index, item in enumerate(sections.get("cast") or []):
        if not isinstance(item, dict):
            errors.append(_player_entity_error("cast", f"[{index}]",
                                              "普通玩家回合的角色声明必须是受约束的本地公开操作。"))
            continue
        op = item.get("op", "create")
        person_id = item.get("id")
        if op == "create":
            exists = isinstance(person_id, str) and graph is not None and graph.get_entity(person_id) is not None
            if exists or "past" in item or "hidden" in item:
                errors.append(_player_entity_error(
                    "cast", f"[{index}]",
                    "普通玩家回合只能创建全新的公开人物，不能复用既有 id 或写入 past/hidden。",
                ))
        elif op == "evolve":
            if (person_id not in ({actor} | set(people))
                    or item.get("predicate") not in {"mood", "state"}):
                errors.append(_player_entity_error(
                    "cast", f"[{index}]",
                    "普通玩家回合只能演化主角或当前公开人物可观察的 mood/state。",
                ))
        elif op == "relationship":
            if person_id != actor or item.get("toward") not in people:
                errors.append(_player_entity_error(
                    "cast", f"[{index}]",
                    "普通玩家回合的关系变化只能由主角指向当前公开人物。",
                ))
        else:
            errors.append(_player_entity_error("cast", f"[{index}].op",
                                              "普通玩家回合不支持该角色 canonical 写入操作。"))
    return errors


def player_turn_entity_event_errors(events: list[dict], world: dict, scene: dict) -> list[ValidationError]:
    """Final-payload companion for :func:`player_turn_entity_errors`."""
    actor, people, graph = _ordinary_character_ids(world, scene)
    if actor is None:
        return []
    errors: list[ValidationError] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("retracted"):
            continue
        event_type = event.get("type")
        if event_type not in {"entity_created", "relation_added", "character_created",
                              "character_evolved", "relationship_changed"}:
            continue
        delta = event.get("deltas")
        if not isinstance(delta, dict):
            errors.append(_player_entity_error("entity", f"event[{index}]",
                                              "普通玩家回合的最终实体 payload 无效。"))
            continue
        if event_type in {"entity_created", "relation_added"}:
            valid = False
        elif event_type == "character_created":
            person_id = delta.get("id")
            valid = (isinstance(person_id, str)
                     and (graph is None or graph.get_entity(person_id) is None)
                     and "past" not in delta and "hidden" not in delta)
        elif event_type == "character_evolved":
            valid = (delta.get("id") in ({actor} | set(people))
                     and delta.get("predicate") in {"mood", "state"})
        else:  # relationship_changed
            valid = delta.get("id") == actor and delta.get("toward") in people
        if not valid:
            errors.append(_player_entity_error(
                "entity", f"event[{index}]",
                "普通玩家回合的最终实体或关系事件越过了本地公开角色边界。",
            ))
    return errors


def _player_faction_error(section: str, field: str, hint: str) -> ValidationError:
    return ValidationError(section, field, "player_faction_authority_boundary", hint)


def player_turn_faction_errors(commit, world: dict, scene: dict) -> list[ValidationError]:
    """Keep ordinary player turns out of canonical faction governance.

    Faction creation and membership can change a remote person's rank and can
    later fan out knowledge broadcasts.  The current player UI and safe
    authoring DTO expose neither a public faction encounter nor a narrow host
    receipt for joining one, so ordinary CLI/GUI/compare authors have no
    evidence-based faction write capability.  Native import and host systems
    remain independently authorized.
    """
    if scene.get("protagonist") is None:
        return []
    errors: list[ValidationError] = []
    for index, _item in enumerate((getattr(commit, "sections", {}) or {}).get("factions") or []):
        errors.append(_player_faction_error(
            "factions", f"[{index}]",
            "普通玩家回合不能创建势力或变更成员／位阶；这属于宿主或导入历史权限。",
        ))
    return errors


def player_turn_faction_event_errors(events: list[dict], world: dict, scene: dict) -> list[ValidationError]:
    """Final-payload companion for :func:`player_turn_faction_errors`."""
    if scene.get("protagonist") is None:
        return []
    errors: list[ValidationError] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("retracted"):
            continue
        if event.get("type") in {"faction_created", "member_changed"}:
            errors.append(_player_faction_error(
                "factions", f"event[{index}]",
                "普通玩家回合的最终势力 payload 越过了宿主／导入历史权限边界。",
            ))
    return errors


def _player_place_error(section: str, field: str, hint: str) -> ValidationError:
    return ValidationError(section, field, "player_place_authority_boundary", hint)


def player_turn_place_errors(commit, world: dict, scene: dict) -> list[ValidationError]:
    """Constrain ordinary topology and non-protagonist movement writes.

    A normal player turn can extend the immediately visible public frontier,
    but cannot rewrite pre-existing topology, materialize a hidden place, or
    simulate arbitrary NPC travel.  A fresh public place needs exactly one
    direct public edge from the action-start location.  A same-turn new public
    character may be placed only at that location or alongside the protagonist.
    """
    actor = scene.get("protagonist")
    origin = scene.get("location")
    if not isinstance(actor, str) or not actor or not isinstance(origin, str) or not origin:
        return []
    sections = getattr(commit, "sections", {}) or {}
    graph = world.get("systems", {}).get("ontology")
    existing = set(graph.entities) if graph is not None else set()
    errors: list[ValidationError] = []
    fresh_places: set[str] = set()
    for index, item in enumerate(sections.get("places") or []):
        if not isinstance(item, dict):
            continue
        place_id = item.get("id")
        if not isinstance(place_id, str) or not place_id or place_id in existing:
            errors.append(_player_place_error("places", f"[{index}].id",
                "普通玩家回合只能创建全新的公开前沿地点。"))
            continue
        fresh_places.add(place_id)
        if item.get("visibility") != "public":
            errors.append(_player_place_error("places", f"[{index}].visibility",
                "普通玩家新地点必须显式为 public。"))
        if item.get("parent") not in {None, origin}:
            errors.append(_player_place_error("places", f"[{index}].parent",
                "普通玩家新地点只能可选地以 action-start 当前位置为 parent。"))
    link_counts = {place_id: 0 for place_id in fresh_places}
    for index, item in enumerate(sections.get("links") or []):
        if not isinstance(item, dict):
            continue
        endpoints = {item.get("a"), item.get("b")}
        new_places = endpoints & fresh_places
        if len(new_places) != 1 or origin not in endpoints or item.get("visibility") != "public":
            errors.append(_player_place_error("links", f"[{index}]",
                "普通玩家只能以一条 public 直连边把一个新地点接到 action-start 当前位置。"))
            continue
        link_counts[next(iter(new_places))] += 1
    for place_id, count in link_counts.items():
        if count != 1:
            errors.append(_player_place_error("places", place_id,
                "每个普通玩家新地点必须在同回合恰有一条直连当前位置的 public 边。"))
    if sections.get("materialize"):
        errors.append(_player_place_error("materialize", "",
            "普通玩家回合不能实体化既有地点。"))
    fresh_people = {
        item.get("id") for item in sections.get("cast") or []
        if isinstance(item, dict) and item.get("op", "create") == "create"
        and isinstance(item.get("id"), str) and item["id"]
    }
    actor_destinations = {
        item.get("to") for item in sections.get("moves") or []
        if isinstance(item, dict) and item.get("who") == actor and isinstance(item.get("to"), str)
    }
    for index, item in enumerate(sections.get("moves") or []):
        if not isinstance(item, dict) or item.get("who") == actor:
            continue
        if item.get("who") not in fresh_people or item.get("to") not in ({origin} | actor_destinations):
            errors.append(_player_place_error("moves", f"[{index}]",
                "普通玩家只能把同回合新建公开人物放在当前位置或主角的同回合目的地。"))
    return errors


def player_turn_place_event_errors(events: list[dict], world: dict, scene: dict) -> list[ValidationError]:
    """Final-payload companion for :func:`player_turn_place_errors`."""
    actor = scene.get("protagonist")
    origin = scene.get("location")
    if not isinstance(actor, str) or not actor or not isinstance(origin, str) or not origin:
        return []
    active = [event for event in events if isinstance(event, dict) and not event.get("retracted")]
    graph = world.get("systems", {}).get("ontology")
    existing = set(graph.entities) if graph is not None else set()
    errors: list[ValidationError] = []
    created: set[str] = set()
    for index, event in enumerate(active):
        if event.get("type") != "place_created":
            continue
        delta = event.get("deltas")
        place_id = delta.get("id") if isinstance(delta, dict) else None
        if (not isinstance(place_id, str) or not place_id or place_id in existing
                or not isinstance(delta, dict) or delta.get("visibility") != "public"
                or delta.get("parent") not in {None, origin}):
            errors.append(_player_place_error("places", f"event[{index}]",
                "普通玩家最终 payload 只能创建新的公开本地前沿地点。"))
            continue
        if place_id in created:
            errors.append(_player_place_error("places", f"event[{index}]",
                "普通玩家最终 payload 不能重复创建同一地点。"))
            continue
        created.add(place_id)
    link_counts = {place_id: 0 for place_id in created}
    for index, event in enumerate(active):
        if event.get("type") == "place_materialized":
            errors.append(_player_place_error("materialize", f"event[{index}]",
                "普通玩家最终 payload 不能实体化既有地点。"))
        if event.get("type") != "place_linked":
            continue
        delta = event.get("deltas")
        endpoints = {delta.get("a"), delta.get("b")} if isinstance(delta, dict) else set()
        new_places = endpoints & created
        if (not isinstance(delta, dict) or len(new_places) != 1 or origin not in endpoints
                or delta.get("visibility") != "public"):
            errors.append(_player_place_error("links", f"event[{index}]",
                "普通玩家最终 topology payload 越过了公开本地前沿边界。"))
            continue
        link_counts[next(iter(new_places))] += 1
    for place_id, count in link_counts.items():
        if count != 1:
            errors.append(_player_place_error("places", place_id,
                "普通玩家最终 payload 的每个新地点必须恰有一条公开直连边。"))
    fresh_people = {
        event.get("deltas", {}).get("id") for event in active
        if event.get("type") == "character_created" and isinstance(event.get("deltas"), dict)
        and isinstance(event["deltas"].get("id"), str) and event["deltas"]["id"]
    }
    actor_destinations = {
        event.get("deltas", {}).get("to") for event in active
        if event.get("type") == "entity_moved" and isinstance(event.get("deltas"), dict)
        and event["deltas"].get("who") == actor and isinstance(event["deltas"].get("to"), str)
    }
    for index, event in enumerate(active):
        if event.get("type") != "entity_moved" or not isinstance(event.get("deltas"), dict):
            continue
        delta = event["deltas"]
        if delta.get("who") != actor and (delta.get("who") not in fresh_people
                or delta.get("to") not in ({origin} | actor_destinations)):
            errors.append(_player_place_error("moves", f"event[{index}]",
                "普通玩家最终 payload 不能移动既有人物或把新人物放到场景外。"))
    return errors


def _player_item_error(section: str, field: str, hint: str) -> ValidationError:
    return ValidationError(section, field, "player_item_authority_boundary", hint)


def _ordinary_item_authority(world: dict, scene: dict) -> tuple[str | None, frozenset[str], frozenset[str]]:
    """Return the action-start inventory and public local transfer targets.

    This intentionally derives both inventory and destinations from the player
    POV projection.  An ordinary author may hand over only an item the player
    already holds, either to a current public person or by placing it at the
    current visible location.  It cannot create an asset or manipulate an
    object held elsewhere just because the canonical graph happens to contain
    it.
    """
    actor, people = _ordinary_fact_subjects(world, scene)
    if actor is None:
        return None, frozenset(), frozenset()
    view = pov_world(world, scene, pov=actor, redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    if graph is None:
        return actor, frozenset(), frozenset()
    day = scene.get("day") or view.get("meta", {}).get("day") or 1
    inventory = frozenset(
        item_id for item_id, entity in graph.entities.items()
        if entity.etype == "Object" and actor in graph.neighbors(item_id, "held_by", day)
    )
    location = scene.get("location")
    local_targets = set(people)
    if isinstance(location, str) and graph.get_entity(location) is not None:
        local_targets.add(location)
    return actor, inventory, frozenset(local_targets)


def _item_sequence_errors(rows, world, scene):
    actor, _, targets = _ordinary_item_authority(world, scene)
    if not actor or not rows:
        return []
    view = pov_world(world, scene, pov=actor)
    graph = view.get('systems', {}).get('ontology')
    day = scene.get('day') or world.get('meta', {}).get('day') or 1
    return [_player_item_error('items', f'[{i}]', '按顺序交接手中物品，或拾回自己留在当前位置的物品。')
            for i in transfer_sequence(graph, day, actor, scene.get('location'), targets, rows)]


def player_turn_item_errors(commit, world, scene):
    return _item_sequence_errors(commit.sections.get('items') or [], world, scene)


def player_turn_item_event_errors(events, world, scene):
    rows = [{**(e.get('deltas') or {}), 'op': 'transfer' if e['type'] == 'item_transferred' else 'create'}
            for e in events if not e.get('retracted') and e['type'] in {'item_transferred', 'object_created'}]
    return _item_sequence_errors(rows, world, scene)


def _player_quest_error(section: str, field: str, hint: str) -> ValidationError:
    return ValidationError(section, field, "player_quest_authority_boundary", hint)


def _ordinary_quest_authority(world: dict, scene: dict) -> tuple[set[str], set[str], set[str]]:
    """Return fresh-id, locally disclosed dark, and local active quest authority.

    A normal author may always open a fresh public task: this records a player
    decision or a present encounter and creates no hidden-line authority.  An
    existing dark line is different: only an id actually disclosed by the
    local station-push rule may surface.  Likewise an existing public task can
    advance or resolve only at its local town; unanchored ordinary open tasks
    remain usable because their creator did not attach a hidden world anchor.
    """
    actor = scene.get("protagonist")
    if not isinstance(actor, str) or not actor:
        return set(), set(), set()
    view = pov_world(world, scene, pov=actor, redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    location = scene.get("location")
    day = scene.get("day") or view.get("meta", {}).get("day") or 1
    town = (ancestor_of_level(graph, location, day, 2)
            if graph is not None and isinstance(location, str) else None)
    lines = (world.get("systems", {}).get("lore") or {}).get("lines", {})
    if not isinstance(lines, dict):
        return set(), set(), set()
    fresh = {line_id for line_id in lines if isinstance(line_id, str)}
    local_dark: set[str] = set()
    local_active: set[str] = set()
    for line_id, line in lines.items():
        if not isinstance(line_id, str) or not isinstance(line, dict):
            continue
        anchor = line.get("anchor")
        if line.get("state") == "暗" and (line.get("l3_anchor") == location or anchor == town):
            local_dark.add(line_id)
        if line.get("state") == "明" and (anchor is None or anchor == town):
            local_active.add(line_id)
    return fresh, local_dark, local_active


def _objective_available(world, scene, objective):
    if not isinstance(objective, dict):
        return False
    if objective.get('kind') == 'investigate':
        return objective.get('place') == scene.get('location')
    _, inventory, targets = _ordinary_item_authority(world, scene)
    return (isinstance(objective.get('item'), str) and objective['item'] in inventory
            and isinstance(objective.get('recipient'), str) and objective['recipient'] in targets)


def _quest_has_evidence(world, line_id, declaration):
    from kernel.quest_outcomes import observation_supports_resolution
    line = world['systems']['lore']['lines'].get(line_id)
    return bool(line and observation_supports_resolution(world, line, declaration))


def player_turn_quest_errors(commit, world: dict, scene: dict) -> list[ValidationError]:
    """Keep ordinary task-ledger changes tied to public local play."""
    existing, local_dark, local_active = _ordinary_quest_authority(world, scene)
    if scene.get("protagonist") is None:
        return []
    errors: list[ValidationError] = []
    for index, item in enumerate((getattr(commit, "sections", {}) or {}).get("quests") or []):
        if not isinstance(item, dict):
            errors.append(_player_quest_error("quests", f"[{index}]",
                                              "普通玩家回合的任务操作必须是本地公开任务操作。"))
            continue
        op, line_id = item.get("op"), item.get("id")
        if op == "open":
            valid = isinstance(line_id, str) and bool(line_id) and line_id not in existing
            if 'objective' in item:
                valid = valid and _objective_available(world, scene, item['objective'])
        elif op == "surface":
            valid = line_id in local_dark
        elif op in {"advance", "resolve"}:
            valid = line_id in local_active
            if valid and op == "resolve":
                valid = _quest_has_evidence(world, line_id, item)
        else:
            # LoreSystem owns structural diagnostics for unsupported/malformed
            # ops; this boundary only needs to prevent authority laundering.
            valid = True
        if not valid:
            errors.append(_player_quest_error(
                "quests", f"[{index}]",
                "普通玩家回合只能接取新公开任务，或变更当前地点已披露的暗线／本地明态任务；完成调查须引用接取后实际观察的 source_event_id 和原文。",
            ))
    return errors


def player_turn_quest_event_errors(events: list[dict], world: dict, scene: dict) -> list[ValidationError]:
    """Final-payload companion for :func:`player_turn_quest_errors`."""
    existing, local_dark, local_active = _ordinary_quest_authority(world, scene)
    if scene.get("protagonist") is None:
        return []
    errors: list[ValidationError] = []
    # This publish batch may also contain host-owned density/lore lifecycle
    # events generated after the ordinary candidate has been accepted.  The
    # ordinary ``quests`` section itself can only materialize these four event
    # types, so validate that surface rather than blocking unrelated host work.
    quest_events = {"quest_opened", "quest_surfaced", "quest_advanced", "quest_resolved"}
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("retracted") or event.get("type") not in quest_events:
            continue
        delta = event.get("deltas")
        line_id = delta.get("id") if isinstance(delta, dict) else None
        event_type = event.get("type")
        valid = False
        if event_type == "quest_opened":
            valid = (isinstance(line_id, str) and bool(line_id) and line_id not in existing
                     and isinstance(delta.get("summary"), str) and bool(delta.get("summary")))
            if 'objective' in delta:
                valid = valid and _objective_available(world, scene, delta['objective'])
        elif event_type == "quest_surfaced":
            valid = line_id in local_dark
        elif event_type in {"quest_advanced", "quest_resolved"}:
            valid = line_id in local_active
        if valid and event_type == "quest_resolved":
            valid = _quest_has_evidence(world, line_id, delta)
        if not valid:
            errors.append(_player_quest_error(
                "quests", f"event[{index}]",
                "普通玩家回合的最终任务事件越过了本地公开任务边界。",
            ))
    return errors


def _declared_discovery_keys(commit, actor: str | None) -> frozenset[str]:
    """Return truthy discovery keys declared for this exact candidate.

    The host still owns authorization; this helper only lets the world-observation
    boundary use the same same-turn route semantics as move validation.
    """
    values = {}
    for item in (getattr(commit, "sections", {}) or {}).get("knowledge") or []:
        if not isinstance(item, dict) or item.get("knower") != actor:
            continue
        key = (place_discovery_key(item.get("place")) if item.get("op") == "discover_place"
               else route_discovery_key(item.get("a"), item.get("b"))
               if item.get("op") == "discover_route" else item.get("fact_key"))
        if isinstance(key, str) and key.endswith(".discovered"):
            values[key] = item.get("value", True)
    return frozenset(key for key, value in values.items() if value is True)


def _candidate_observation_places(commit, world: dict, scene: dict, *, trusted_keys=frozenset(),
                                  reveal_only_discovery_keys=frozenset()) -> frozenset[str]:
    """Return locations an ordinary player candidate may observe this turn.

    This intentionally uses the player POV capability rather than the canonical
    graph.  A fresh directly-linked place and a host-authorized discovered route
    remain valid only when the candidate also moves the protagonist there.
    """
    capability = scene_capabilities(world, scene)
    if not capability.actor or not capability.location:
        return frozenset()
    visible = {place.id for place in capability.places}
    allowed = ({capability.location} if capability.location in visible else set())
    effective_keys = _declared_discovery_keys(commit, capability.actor)
    for move in (getattr(commit, "sections", {}) or {}).get("moves") or []:
        if not isinstance(move, dict) or move.get("who") != capability.actor:
            continue
        destination = move.get("to")
        if not isinstance(destination, str):
            continue
        if (destination in capability.allowed_move_ids
                or _new_place_route(commit, world, capability.location, destination)
                or (_host_discovered_route(world, capability.location, destination,
                                           trusted_keys, effective_keys)
                    and not {place_discovery_key(destination), route_discovery_key(capability.location, destination)}
                    <= set(reveal_only_discovery_keys))):
            allowed.add(destination)
    return frozenset(allowed)


def player_turn_world_errors(commit, world: dict, scene: dict, *, trusted_keys=frozenset(),
                             reveal_only_discovery_keys=frozenset()) -> list[ValidationError]:
    """Constrain ordinary-player world writes to local public observations.

    A normal player author receives a redacted, action-start POV, not director
    access to the canonical world.  It may record a bounded observation at the
    current place or a legal same-turn destination, but may not turn prose into
    a remote/hidden world change or trigger a global cascade.  Trusted native
    import and host/director paths opt in separately and do not use this guard.
    """
    allowed_places = _candidate_observation_places(
        commit, world, scene, trusted_keys=trusted_keys,
        reveal_only_discovery_keys=reveal_only_discovery_keys,
    )
    errors: list[ValidationError] = []
    for index, item in enumerate((getattr(commit, "sections", {}) or {}).get("world") or []):
        if not isinstance(item, dict):
            errors.append(ValidationError("world", f"[{index}]", "player_world_observation_boundary",
                                          "普通玩家回合的 world 段只能是本地公开观察。"))
            continue
        areas = item.get("areas")
        if (not isinstance(areas, list) or len(areas) != 1
                or not isinstance(areas[0], str) or areas[0] not in allowed_places):
            errors.append(ValidationError("world", f"[{index}].areas", "player_world_observation_boundary",
                                          "普通玩家回合只能观察 action-start 或合法同回合到达的公开地点。"))
        if item.get("public_observation") is not True or item.get("visibility") != "public":
            errors.append(ValidationError("world", f"[{index}]", "player_world_observation_boundary",
                                          "普通玩家的 world 段必须显式标为公开观察。"))
        if item.get("observation_kind") not in {"stable_layout", "single_observation"}:
            errors.append(ValidationError("world", f"[{index}].observation_kind", "player_world_observation_boundary",
                                          "普通玩家的公开观察需要稳定布局或单次观察类型。"))
        if not isinstance(item.get("observation_range"), str) or not item["observation_range"].strip():
            errors.append(ValidationError("world", f"[{index}].observation_range", "player_world_observation_boundary",
                                          "普通玩家的公开观察需要说明可见范围。"))
        if type(item.get("level")) is not int or item["level"] != 1:
            errors.append(ValidationError("world", f"[{index}].level", "player_world_observation_boundary",
                                          "普通玩家的公开观察只能使用 level=1，不能触发全局级联。"))
    return errors


def _event_observation_places(events: list[dict], world: dict, scene: dict, *, trusted_keys=frozenset(),
                              reveal_only_discovery_keys=frozenset()) -> frozenset[str]:
    """Final-payload equivalent of :func:`_candidate_observation_places`."""
    capability = scene_capabilities(world, scene)
    if not capability.actor or not capability.location:
        return frozenset()
    visible = {place.id for place in capability.places}
    allowed = ({capability.location} if capability.location in visible else set())
    active = [event for event in events if isinstance(event, dict) and not event.get("retracted")]
    grant_values = {}
    for event in active:
        delta = event.get("deltas") or {}
        if event.get("type") == "knowledge_set" and delta.get("knower") == capability.actor:
            key = delta.get("fact_key")
            if isinstance(key, str) and key.endswith(".discovered"):
                grant_values[key] = delta.get("value")
    effective_keys = frozenset(key for key, value in grant_values.items() if value is True)
    canonical = world.get("systems", {}).get("ontology")
    created = {
        (event.get("deltas") or {}).get("id")
        for event in active if event.get("type") == "place_created"
        and (canonical is None or canonical.get_entity((event.get("deltas") or {}).get("id")) is None)
    }
    direct_links = {
        frozenset({(event.get("deltas") or {}).get("a"), (event.get("deltas") or {}).get("b")})
        for event in active if event.get("type") == "place_linked"
    }
    for event in active:
        if event.get("type") != "entity_moved":
            continue
        delta = event.get("deltas") or {}
        if delta.get("who") != capability.actor:
            continue
        destination = delta.get("to")
        if not isinstance(destination, str):
            continue
        if (destination in capability.allowed_move_ids
                or (destination in created
                    and frozenset((capability.location, destination)) in direct_links)
                or (_host_discovered_route(world, capability.location, destination,
                                           trusted_keys, effective_keys)
                    and not {place_discovery_key(destination), route_discovery_key(capability.location, destination)}
                    <= set(reveal_only_discovery_keys))):
            allowed.add(destination)
    return frozenset(allowed)


def player_turn_world_event_errors(events: list[dict], world: dict, scene: dict, *, trusted_keys=frozenset(),
                                   reveal_only_discovery_keys=frozenset()) -> list[ValidationError]:
    """Final-payload guard for ordinary-player public place observations."""
    allowed_places = _event_observation_places(
        events, world, scene, trusted_keys=trusted_keys,
        reveal_only_discovery_keys=reveal_only_discovery_keys,
    )
    errors: list[ValidationError] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("retracted") or event.get("type") != "world_change":
            continue
        delta = event.get("deltas")
        if not isinstance(delta, dict):
            errors.append(ValidationError("world", f"event[{index}]", "player_world_observation_boundary",
                                          "普通玩家回合的最终 world 事件必须是本地公开观察。"))
            continue
        if (delta.get("place") not in allowed_places
                or delta.get("source") != "author_world"
                or delta.get("visibility") != "public"
                or delta.get("public_observation") is not True
                or delta.get("observation_kind") not in {"stable_layout", "single_observation"}
                or not isinstance(delta.get("observation_range"), str)
                or not delta["observation_range"].strip()
                or type(delta.get("level")) is not int or delta["level"] != 1
                or not isinstance(delta.get("summary"), str) or not delta["summary"].strip()):
            errors.append(ValidationError("world", f"event[{index}]", "player_world_observation_boundary",
                                          "普通玩家回合的最终 world 事件越过了本地公开观察边界。"))
    return errors


def validate_protagonist_moves(
    commit,
    world: dict,
    scene: dict,
    *,
    narration_rewrite_from: str | None = None,
    trusted_keys=frozenset(),
    reveal_only_discovery_keys=frozenset(),
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
    actor_moves = [row for row in commit.sections.get('moves') or [] if isinstance(row, dict) and row.get('who') == capability.actor]
    if len(actor_moves) > 1:
        errors.append(ValidationError('moves', '', 'outside_scene_capability', '一次普通行动只选一个目的地；抵达后再选择下一段。'))
        requires_narration_rewrite = True
    end_day = turn_end(world, commit.sections.get('clock'))[0]
    for row in actor_moves:
        if 'arrive_day' in row and row['arrive_day'] != end_day:
            errors.append(ValidationError('moves', 'arrive_day', 'outside_scene_capability', '抵达日期须与本回合结束时钟一致。'))
            requires_narration_rewrite = True
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
        host_discovered = _host_discovered_route(world, capability.location, destination, trusted_keys, grants)
        if (host_discovered
                and {place_discovery_key(destination), route_discovery_key(capability.location, destination)}
                <= set(reveal_only_discovery_keys)):
            errors.append(ValidationError(
                section="moves", field=f"[{index}].to", code="discovery_reveal_only",
                hint="本回合只揭示新的相邻出口；请留在当前位置，下回合再决定是否前往。",
            ))
            requires_narration_rewrite = True
            continue
        if _new_place_route(commit, world, capability.location, destination) or host_discovered:
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


def validate_protagonist_move_events(events: list[dict], world: dict, scene: dict, *, trusted_keys=frozenset(),
                                     reveal_only_discovery_keys=frozenset(),
                                     required_discovery_keys=frozenset()) -> list[ValidationError]:
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
    clock = [e['deltas'] for e in active_events if e['type'] == 'clock_advanced']
    end_day = turn_end(world, clock)[0]
    moves = [e for e in active_events if e['type'] == 'entity_moved' and e['deltas'].get('who') == capability.actor]
    if len(moves) > 1 or any(e['day'] != end_day for e in moves):
        errors.append(ValidationError('moves', '', 'outside_scene_capability', '最终移动必须是同一时钟下的一段合法旅程。'))
    grant_values = {}
    for event in active_events:
        delta = event.get("deltas") or {}
        if event.get("type") == "knowledge_set" and delta.get("knower") == capability.actor:
            key = delta.get("fact_key")
            if isinstance(key, str) and key.endswith(".discovered"):
                grant_values[key] = delta.get("value")
    effective_grants = {key for key, value in grant_values.items() if value is True}
    if required_discovery_keys:
        discovered = {key for key in effective_grants if key.endswith(".discovered")}
        if discovered != set(required_discovery_keys):
            errors.append(ValidationError(
                section="knowledge", field="", code="host_discovery_incomplete",
                hint="宿主授权的地点和路线发现必须作为同一回合的完整一对写入。",
            ))
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
        if (host_discovered
                and {place_discovery_key(destination), route_discovery_key(capability.location, destination)}
                <= set(reveal_only_discovery_keys)):
            errors.append(ValidationError(
                section="moves", field=f"event[{index}].deltas.to", code="discovery_reveal_only",
                hint="最终发布的发现回合不能把主角移动到刚揭示的出口。",
            ))
            continue
        if not isinstance(destination, str) or destination in allowed or discovered or host_discovered:
            continue
        errors.append(ValidationError(
            section="moves", field=f"event[{index}].deltas.to", code="outside_scene_capability",
            hint="最终发布的主角移动不在 action-start POV 行动能力内。",
        ))
    return errors
