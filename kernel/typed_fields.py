"""Exact typed-field ownership on new writes; historical replay stays permissive."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TypedField:
    entity_type: str
    predicate: str
    canonical_storage: Literal["relation"] = "relation"
    repair_section: str = ""

    def __post_init__(self):
        for name in ("entity_type", "predicate", "repair_section"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"typed field {name} must be a nonempty string")
        if self.canonical_storage != "relation":
            raise ValueError("typed fields currently support only canonical relation storage")


class TypedFieldWriteError(ValueError):
    """Safe routing information, never the current value or relation target."""

    def __init__(self, event_id, subject, predicate, owner_section):
        self.event_id = event_id
        self.subject = subject
        self.predicate = predicate
        self.owner_section = owner_section
        self.hint = (
            f"该字段由 {owner_section} 段以 relation 维护，不能写成事实；"
            f"删除此事实声明，并按 {owner_section} 段格式声明实际变化。"
            "没有实际变化时只删除此条；保留各段已有的合法声明，不得猜测不可见状态。"
        )
        super().__init__(self.hint)


@dataclass(frozen=True)
class _WriteGuard:
    registry: object
    new_ids: frozenset[str]
    creator_types: dict[str, frozenset[str]]
    new_creator_types: dict[str, frozenset[str]]


_write_guard: ContextVar[_WriteGuard | None] = ContextVar("typed_field_write_guard", default=None)
_CREATOR_TYPES = {
    "object_created": "Object", "character_created": "Person",
    "place_created": "Place", "faction_created": "Faction",
}


@contextmanager
def field_write_guard(registry, events, *, new_ids=None):
    """Observe actual fact writes only for the selected new source event IDs.

    Creator declarations are a read-only type lookup for facts before creation;
    they do not create stubs or reorder the stored event stream. Multiple types
    remain multiple candidates, so conflicting declarations cannot hide a field.
    """
    events = list(events)
    if new_ids is None:
        new_ids = {event["id"] for event in events}
    new_ids = frozenset(new_ids)
    creators: dict[str, set[str]] = {}
    new_creators: dict[str, set[str]] = {}
    for event in events:
        if event.get("retracted"):
            continue
        kind, data = event.get("type"), event.get("deltas", {})
        if not isinstance(data, dict):
            continue
        entity_type = data.get("etype") if kind == "entity_created" else _CREATOR_TYPES.get(kind)
        subject = data.get("id")
        if isinstance(subject, str) and isinstance(entity_type, str) and entity_type:
            creators.setdefault(subject, set()).add(entity_type)
            if event.get("id") in new_ids:
                new_creators.setdefault(subject, set()).add(entity_type)
    state = _WriteGuard(
        registry, new_ids,
        {subject: frozenset(types) for subject, types in creators.items()},
        {subject: frozenset(types) for subject, types in new_creators.items()},
    )
    token = _write_guard.set(state)
    try:
        yield
    finally:
        _write_guard.reset(token)


def check_fact_write(graph, subject, predicate, source_event):
    """Reject a new shadow fact for an exact, registered entity-type/field pair."""
    guard = _write_guard.get()
    if guard is None or source_event not in guard.new_ids:
        return
    entity = graph.get_entity(subject)
    types = ([entity.etype] if entity is not None
             else sorted(guard.creator_types.get(subject, ())))
    # A current type or earlier creator cannot mask a NEW declared reserved
    # type later in this proposal. Historical retypes do not enter this set.
    types += sorted(guard.new_creator_types.get(subject, ()))
    for entity_type in types:
        owner = guard.registry.owner_of_field(entity_type, predicate)
        if owner is not None:
            raise TypedFieldWriteError(source_event, subject, predicate, owner.contract.repair_section)


def project_new_fields(registry, history, prior_ids, *, before_apply=None):
    """Run ordinary projection with field ownership enabled only for new events."""
    from kernel.projection import project

    history = list(history)
    prior_ids = set(prior_ids)
    new_ids = {event["id"] for event in history if event["id"] not in prior_ids}
    with field_write_guard(registry, history, new_ids=new_ids):
        return project(registry, history, before_apply=before_apply)


def validate_typed_field_commit(registry, commit, world):
    """Privately preview generated events and map violations to their source rows.

    Validation must run after shape validation and after temporary pending stubs
    are removed. The shared world and the proposal are never changed.
    """
    from kernel.clock import advance
    from kernel.contextsystem import ValidationError
    from kernel.item_integrity import creation_first_sections
    from kernel.projection import apply_event_metadata
    from systems.time import normalize_clock

    if not any(system.typed_fields() for system in registry.systems):
        return []
    preview = copy.deepcopy(world)
    current = world.get("meta", {})
    day, band = current.get("day") or 1, current.get("band") or 0
    sections = copy.deepcopy(commit.sections)
    clock = normalize_clock(sections.get("clock") or [], world)
    if "clock" in sections:
        sections["clock"] = clock
    if clock and clock[0].get("advance"):
        day, _ = advance(day, band, clock[0].get("days", 0), clock[0].get("bands", 0))
    turn = world.get("_action_turn")
    if type(turn) is not int or turn < 0:
        graph = world.get("systems", {}).get("ontology")
        turns = ([fact.ingest_turn for fact in graph.facts]
                 + [relation.ingest_turn for relation in graph.relations]) if graph is not None else []
        records = world.get("systems", {}).get("return_commitments", {}).get("records", {})
        turns += [record.get("created_turn", 0) for record in records.values()]
        turn = max((value for value in turns if type(value) is int), default=0) + 1

    events, sources = [], {}
    for section, declarations in creation_first_sections(sections):
        owner = registry.owner_of_section(section)
        if owner is None or not declarations:
            continue
        for index, declaration in enumerate(declarations):
            try:
                row_events = owner.to_events(section, [declaration], turn=turn, day=day, scene="validation")
            except (TypeError, ValueError, KeyError, AttributeError):
                return [ValidationError(section, f"[{index}]", "state_preview",
                                        "无法生成本条声明的状态事件；请核对该段格式和实体声明")]
            for event in row_events:
                events.append(event)
                sources[event["id"]] = (section, index)

    errors = []
    with field_write_guard(registry, events):
        for event in events:
            if event.get("retracted"):
                continue
            owner = registry.owner_of_event(event["type"])
            if owner is None:
                continue
            try:
                apply_event_metadata(preview, event)
                owner.apply(preview, event)
            except TypedFieldWriteError as error:
                section, index = sources[error.event_id]
                errors.append(ValidationError(section, f"[{index}]", "typed_field_route",
                                              error.hint, repair_sections=(error.owner_section,)))
            except (TypeError, ValueError, KeyError, AttributeError):
                section, index = sources[event["id"]]
                errors.append(ValidationError(section, f"[{index}]", "state_preview",
                                              "本条声明无法应用到当前状态；请核对实体类型、声明顺序和操作条件"))
                return errors
    return errors
