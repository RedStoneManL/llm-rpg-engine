from __future__ import annotations

from kernel.contextsystem import ContextSystem
from engine.log import get_logger

log = get_logger("kernel.registry")


class Registry:
    """Holds registered ContextSystems and routes by event-type / commit-section.
    Each event-type and each commit-section may have exactly one owner."""

    def __init__(self):
        self._systems: list[ContextSystem] = []
        self._by_event: dict[str, ContextSystem] = {}
        self._by_section: dict[str, ContextSystem] = {}

    def register(self, system: ContextSystem) -> "Registry":
        registered_names = {s.name for s in self._systems}
        for dep in system.requires():
            if dep not in registered_names:
                raise ValueError(
                    f"system {system.name!r} requires {dep!r} to be registered first"
                )
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
