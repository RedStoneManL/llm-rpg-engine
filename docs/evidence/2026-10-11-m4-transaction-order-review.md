# M4 transaction-order source review

HEAD: e369ac4d547c6211b9cd2c08bca67905aab3a91c

## SHA-256

8d0b7315a5a7a9d66be2bd494edb630608d8d9b687e08a607fd5a817136d5a6a  engine/store.py
fa4d046f2321e24e55200dc1ab41a100e049410e9e57cd9a0bf59c022c446474  systems/interpretive_memory.py

## engine/store.py — EventStore.append_many

```python
        """Publish a complete proposal atomically; JSONL is a recoverable mirror.

        All LLM work belongs before this short transaction. The preflight callback
        folds the entire prospective history and must perform no external I/O.
        """
        events = copy.deepcopy(list(events))
        for ev in events:
            validate_event(ev, self.allowed_types)
        retractions = list(retractions)
        if any(kind not in {'seq', 'turn'} for kind, value in retractions):
            raise ValueError('unknown retraction boundary')
        fingerprint = hashlib.sha256(json.dumps({'events':events, 'retractions':retractions}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self._conn.execute('BEGIN IMMEDIATE')
        try:
            if action_id:
                prior = self._conn.execute('SELECT * FROM action_receipts WHERE action_id=?', (action_id,)).fetchone()
                if prior:
                    if prior['fingerprint'] != fingerprint:
                        raise RevisionConflict('action_id already used for different events')
                    if self._conn.execute('SELECT 1 FROM events WHERE seq BETWEEN ? AND ? AND retracted=1 LIMIT 1',
                                          (prior['first_seq'], prior['last_seq'])).fetchone():
                        raise RevisionConflict('action was retracted; submit a new action_id')
                    self._conn.rollback()
                    return {**dict(prior), 'duplicate': True, 'mirror_pending': bool(self.mirror_error)}
            revision = self.revision
            if expected_revision is not None and revision != expected_revision:
                raise RevisionConflict('world changed while the action was being prepared')
            removed = 0
            for kind, value in retractions:
                removed += self._conn.execute(f'UPDATE events SET retracted=1 WHERE retracted=0 AND {kind}>=?', (value,)).rowcount
            check = preflight or self.preflight
            if check is not None:
                check(list(self.iter_events()) + events)
            first = last = 0
            for ev in events:
                last = self._insert(ev)
                first = first or last
            if events or removed:
                revision += 1
                self._conn.execute('UPDATE store_revision SET revision=? WHERE singleton=1', (revision,))
            if action_id:
                self._conn.execute('INSERT INTO action_receipts VALUES (?,?,?,?,?)',
                                   (action_id, fingerprint, first, last, revision))
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        self._sync_mirror_safely()
        return {'action_id': action_id, 'first_seq': first, 'last_seq': last,
                'revision': revision, 'duplicate': False, 'mirror_pending': bool(self.mirror_error)}

    def _insert(self, ev) -> int:

```

## engine/store.py — EventBatch

```python


class EventBatch:
    """In-memory proposal, including backstage events, for a single action.

    No database lock is held during generation. A failed backstage savepoint
    rolls back only that hook; a failed publication rolls back the whole action.
    """
    def __init__(self, store, *, turn=None, preflight=None):
        self.store = store
        self.revision, self._snapshot = store.snapshot()
        self.events = []
        self.retractions = []
        self.allowed_types = store.allowed_types
        self.db_path, self.jsonl_path = store.db_path, store.jsonl_path
        self.turn = turn if turn is not None else max((e.get('turn') or 0 for e in self._snapshot if not e.get('retracted')), default=0) + 1
        self.preflight = preflight or store.preflight
        self.publish_validators = []
        self._last_seq = max((e.get('seq') or 0 for e in self._snapshot), default=0)

    def next_turn(self):
        return self.turn

    def _retract(self, kind, value):
        changed = 0
        for ev in self._snapshot + self.events:
            boundary = ev.get(kind)
            if boundary is not None and boundary >= value and not ev.get('retracted'):
                ev['retracted'] = True
                changed += 1
        self.retractions.append((kind, value))
        return changed

    def retract_from_seq(self, seq):
        return self._retract('seq', seq)

    def retract_from_turn(self, turn):
        return self._retract('turn', turn)

    def append(self, event):
        ev = copy.deepcopy(event)
        ev['turn'] = self.turn
        validate_event(ev, self.allowed_types)
        ev['seq'] = self._last_seq + len(self.events) + 1
        self.events.append(ev)
        return ev['seq']

    def append_many(self, events, **kwargs):
        start = len(self.events)
        with self.savepoint():
            for event in events:
                self.append(event)
        return {'first_seq': self._last_seq + start + 1 if len(self.events)>start else 0,
                'last_seq': self._last_seq + len(self.events) if len(self.events)>start else 0,
                'revision': self.revision, 'duplicate': False, 'mirror_pending': False}

    def add_publish_validator(self, validator):
        self.publish_validators.append(validator)

    def iter_events(self, include_retracted=False):
        for ev in self._snapshot + self.events:
            if include_retracted or not ev.get('retracted'):
                yield ev

    @contextmanager
    def savepoint(self):
        start = len(self.events)
        retractions_start = len(self.retractions)
        validators_start = len(self.publish_validators)
        # A hook may stage a rewind as well as append events.  Both are part of
        # its proposal: if the hook fails, leaving the retraction intent behind
        # would make a later publish retract already-committed history even
        # though the hook itself was rolled back.  Keep the exact pre-savepoint
        # flags so nested savepoints can restore only their own mutations.
        prior_retracted = [(ev, ev.get('retracted', False))
                           for ev in self._snapshot + self.events]
        try:
            yield
            if self.preflight and len(self.events) != start:
                self.preflight(list(self.iter_events()))
        except BaseException:
            del self.events[start:]
            del self.retractions[retractions_start:]
            del self.publish_validators[validators_start:]
            for ev, was_retracted in prior_retracted:
                ev['retracted'] = was_retracted
            raise

    def publish(self, *, action_id=None):
        for validator in self.publish_validators:
            validator()
        original_preflight = self.preflight
        def final_preflight(history):
            if original_preflight:
                original_preflight(history)
            for validator in self.publish_validators:
                validator()
        events = [{k:v for k,v in ev.items() if k != 'seq'} for ev in self.events]
        return self.store.append_many(events, expected_revision=self.revision,
                                      action_id=action_id, preflight=final_preflight,
                                      retractions=self.retractions)

```

## systems/interpretive_memory.py

```python
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

```
