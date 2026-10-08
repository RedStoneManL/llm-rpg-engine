from __future__ import annotations

from typing import NamedTuple

from kernel.contextsystem import ContextSystem, TypedField
from engine.log import get_logger

log = get_logger("kernel.registry")


class FieldOwner(NamedTuple):
    contract: TypedField
    system: ContextSystem


class Registry:
    """Holds registered ContextSystems and routes by event-type / commit-section.
    Each event-type and each commit-section may have exactly one owner."""

    def __init__(self):
        self._systems: list[ContextSystem] = []
        self._by_event: dict[str, ContextSystem] = {}
        self._by_section: dict[str, ContextSystem] = {}
        self._by_field: dict[tuple[str, str], FieldOwner] = {}

    def register(self, system: ContextSystem) -> "Registry":
        registered_names = {s.name for s in self._systems}
        for dep in system.requires():
            if dep not in registered_names:
                raise ValueError(
                    f"system {system.name!r} requires {dep!r} to be registered first"
                )
        fields = {}
        for contract in system.typed_fields():
            if not isinstance(contract, TypedField):
                raise TypeError("typed_fields must contain TypedField declarations")
            key = (contract.entity_type, contract.predicate)
            if key in fields or key in self._by_field:
                raise ValueError(f"typed field {key!r} already has an owner")
            if contract.repair_section not in system.commit_sections():
                raise ValueError("typed field repair section must belong to its system")
            fields[key] = FieldOwner(contract, system)
        for et in system.event_types():
            if et in self._by_event:
                raise ValueError(
                    f"event type {et!r} already owned by {self._by_event[et].name!r}")
            self._by_event[et] = system
        if "narration" in system.commit_sections():
            raise ValueError(
                "commit section 'narration' is reserved (it is the TurnCommit prose field)")
        for sec in system.commit_sections():
            if sec in self._by_section:
                raise ValueError(
                    f"commit section {sec!r} already owned by {self._by_section[sec].name!r}")
            self._by_section[sec] = system
        self._systems.append(system)
        self._by_field.update(fields)
        log.debug("registered system=%s events=%s sections=%s",
                  system.name, sorted(system.event_types()), sorted(system.commit_sections()))
        return self

    @property
    def systems(self) -> list[ContextSystem]:
        return list(self._systems)

    def event_types(self) -> set[str]:
        return set(self._by_event)

    def owner_of_event(self, etype: str) -> ContextSystem | None:
        return self._by_event.get(etype)

    def owner_of_section(self, section: str) -> ContextSystem | None:
        return self._by_section.get(section)

    def owner_of_field(self, entity_type: str, predicate: str) -> FieldOwner | None:
        return self._by_field.get((entity_type, predicate))
