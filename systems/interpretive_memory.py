"""Opt-in, event-sourced private character interpretations.

These records are deliberately not facts, permissions, or prompt context.  A
host may opt in by registering this system in an isolated registry; the normal
application registry does not register it.  Reading is actor-scoped through
``records_for_actor`` and never uses ``inject`` or ``recall``.
"""
from __future__ import annotations
import hashlib
import json

from kernel.contextsystem import ContextSystem
from engine.log import get_logger

log = get_logger("systems.interpretive_memory")
PRIVATE_EVENT_TYPES = frozenset({"character_interpretation_recorded"})


def public_event_history(events):
    """Return public DTOs, omitting private-memory rows without mutation.

    This is deliberately separate from EventStore.iter_events(): raw storage is
    trusted-host-only.  Public callers receive copied ordinary events in their
    original order, including their body and retracted flag.  A normal row that
    names a private event id in an explicit reference list is rejected rather
    than silently leaving a dangling private reference.
    """
    source = list(events)
    private_ids = {ev.get("id") for ev in source if ev.get("type") in PRIVATE_EVENT_TYPES}
    out = []
    for ev in source:
        if ev.get("type") in PRIVATE_EVENT_TYPES:
            continue
        refs = list(ev.get("thread_refs") or []) + list(ev.get("chunk_ids") or [])
        if any(ref in private_ids for ref in refs):
            raise ValueError("public event references private memory")
        out.append(dict(ev))
    return out


class MemoryReadSession:
    """Trusted-host-only binding for one in-memory world/store/role session."""
    def __init__(self, world: dict, store, role: str):
        if not isinstance(role, str) or not role:
            raise ValueError("role must be a non-empty host-selected id")
        self._world, self._store, self._role = world, store, role
        self._closed = False
        self._handles: set[object] = set()

    def issue_handle(self):
        if self._closed:
            raise RuntimeError("memory read session is closed")
        handle = object()
        self._handles.add(handle)
        return handle

    def close(self):
        self._closed = True
        self._handles.clear()

    def read(self, handle, world: dict, store) -> list[dict]:
        if self._closed or world is not self._world or store is not self._store or handle not in self._handles:
            raise PermissionError("invalid or revoked interpretive-memory handle")
        return records_for_actor(world, self._role)


def open_memory_read_session(world: dict, store, role: str) -> MemoryReadSession:
    """Trusted host factory; intentionally not wired into model tools or app defaults."""
    return MemoryReadSession(world, store, role)


class MemoryWriteSession:
    """Trusted-host staging adapter bound to one foreground EventBatch."""
    def __init__(self, batch, registry, role: str, source_event: dict):
        self.batch, self.registry, self.role = batch, registry, role
        self.registry_identity = registry
        self.store, self.revision, self.turn = batch.store, batch.revision, batch.turn
        self.source_id = source_event.get("id")
        self.source_digest = _event_digest(source_event)

    def _verify_binding(self):
        if (self.batch.store is not self.store or self.registry is not self.registry_identity
                or self.batch.revision != self.revision or self.batch.turn != self.turn):
            raise PermissionError("memory write session no longer matches batch")

    def stage(self, text: str, memory_id: str):
        self._verify_binding()
        source = next((e for e in self.batch.events if e.get("id") == self.source_id), None)
        if (source is None or source.get("retracted") or source.get("type") == "character_interpretation_recorded"
                or _event_digest(source) != self.source_digest):
            raise PermissionError("memory source is missing, retracted, or changed")
        if self.registry.owner_of_event("character_interpretation_recorded") is None:
            raise PermissionError("interpretive memory is not enabled for this registry")
        def verify_before_publish():
            self._verify_binding()
            source_now = next((e for e in self.batch.events if e.get("id") == self.source_id), None)
            if source_now is None or source_now.get("retracted") or _event_digest(source_now) != self.source_digest:
                raise PermissionError("memory source changed before publish")
        from kernel.events import kernel_event
        record = kernel_event("character_interpretation_recorded", day=source["day"], scene=source["scene"],
            summary="private character interpretation", deltas={"actor":self.role, "text":text,
            "memory_id":memory_id, "source_event_id":self.source_id}, turn=self.turn)
        self.batch.append(record)
        record = self.batch.events[-1]
        record_digest = _event_digest(record)
        def verify_record():
            if record not in self.batch.events or _event_digest(record) != record_digest:
                raise PermissionError("memory record changed before publish")
        self.batch.add_publish_validator(verify_before_publish)
        self.batch.add_publish_validator(verify_record)


def _event_digest(event):
    return hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def open_memory_write_session(batch, registry, role: str, source_event: dict) -> MemoryWriteSession:
    """Trusted host factory; source must already be staged in this batch."""
    if not isinstance(role, str) or not role or source_event not in batch.events:
        raise PermissionError("host must bind a current staged source and role")
    return MemoryWriteSession(batch, registry, role, source_event)


class InterpretiveMemorySystem(ContextSystem):
    """Private, replayable interpretations with no commit section."""

    name = "interpretive_memory"

    def event_types(self) -> set[str]:
        return {"character_interpretation_recorded"}

    def commit_sections(self) -> set[str]:
        # A model cannot publish these through a foreground TurnCommit.
        return set()

    def empty_state(self) -> dict:
        return {"by_actor": {}}

    def apply(self, world: dict, event: dict) -> None:
        data = event.get("deltas") or {}
        actor = data.get("actor")
        text = data.get("text")
        memory_id = data.get("memory_id") or event.get("id")
        if not isinstance(actor, str) or not actor or not isinstance(text, str) or not text:
            # Do not include private text in diagnostics.
            log.warning("interpretive memory event ignored: malformed actor/text (id=%s)", event.get("id"))
            return
        bucket = world["systems"][self.name]["by_actor"].setdefault(actor, [])
        if any(item["id"] == memory_id for item in bucket):
            return
        bucket.append({
            "id": memory_id,
            "text": text,
            "day": event["day"],
            "turn": event.get("turn"),
            "source_event": event.get("id"),
        })


def records_for_actor(world: dict, actor: str) -> list[dict]:
    """Return copies of only one actor's private interpretations."""
    state = world.get("systems", {}).get(InterpretiveMemorySystem.name) or {}
    return [dict(item) for item in state.get("by_actor", {}).get(actor, [])]
