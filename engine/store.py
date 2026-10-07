import copy
import hashlib
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from engine.schema import validate_event
from engine.log import get_logger

log = get_logger("store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL, day INTEGER NOT NULL, scene TEXT NOT NULL,
    arc TEXT, actors TEXT, summary TEXT NOT NULL,
    deltas TEXT, thread_refs TEXT, chunk_ids TEXT,
    secrecy TEXT, roll TEXT, turn INTEGER, retracted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(day);
CREATE INDEX IF NOT EXISTS idx_events_scene ON events(scene);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE TABLE IF NOT EXISTS store_revision (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
);
INSERT OR IGNORE INTO store_revision VALUES (1, 0);
CREATE TABLE IF NOT EXISTS action_receipts (
    action_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
    first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL, revision INTEGER NOT NULL
);
"""


class RevisionConflict(RuntimeError):
    """A proposal was based on a world that has since changed."""


class EventStore:
    def __init__(self, db_path, jsonl_path, allowed_types=None):
        self.db_path = Path(db_path)
        self.jsonl_path = Path(jsonl_path)
        self.allowed_types = allowed_types
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.preflight = None
        self.mirror_error = None

    def append(self, ev) -> int:
        return self.append_many([ev])["last_seq"]

    @property
    def revision(self) -> int:
        return self._conn.execute('SELECT revision FROM store_revision WHERE singleton=1').fetchone()[0]

    def next_turn(self) -> int:
        return self._conn.execute('SELECT COALESCE(MAX(turn),0)+1 FROM events WHERE retracted=0').fetchone()[0]

    def snapshot(self):
        """Read events and revision from one SQLite snapshot, then release it."""
        self._conn.execute('BEGIN')
        try:
            revision = self.revision
            events = list(self.iter_events(include_retracted=True))
            self._conn.commit()
            return revision, events
        except BaseException:
            self._conn.rollback()
            raise

    def append_many(self, events, *, expected_revision=None, action_id=None, preflight=None, retractions=()):
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
        validate_event(ev, self.allowed_types)
        row = {
            "id": ev["id"], "type": ev["type"], "day": ev["day"], "scene": ev["scene"],
            "arc": ev.get("arc"),
            "actors": json.dumps(ev.get("actors", []), ensure_ascii=False),
            "summary": ev["summary"],
            "deltas": json.dumps(ev.get("deltas", {}), ensure_ascii=False),
            "thread_refs": json.dumps(ev.get("thread_refs", []), ensure_ascii=False),
            "chunk_ids": json.dumps(ev.get("chunk_ids", []), ensure_ascii=False),
            "secrecy": json.dumps(ev.get("secrecy"), ensure_ascii=False),
            "roll": json.dumps(ev.get("roll"), ensure_ascii=False),
            "turn": ev.get("turn"),
            "retracted": 1 if ev.get("retracted") else 0,
        }
        cur = self._conn.execute(
            """INSERT INTO events
               (id,type,day,scene,arc,actors,summary,deltas,thread_refs,chunk_ids,secrecy,roll,turn,retracted)
               VALUES (:id,:type,:day,:scene,:arc,:actors,:summary,:deltas,:thread_refs,:chunk_ids,:secrecy,:roll,:turn,:retracted)""",
            row)
        seq = cur.lastrowid
        log.debug("append id=%s seq=%s type=%s", ev["id"], seq, ev["type"])
        return seq

    def iter_events(self, include_retracted=False):
        q = "SELECT * FROM events"
        if not include_retracted:
            q += " WHERE retracted=0"
        q += " ORDER BY seq ASC"
        for r in self._conn.execute(q):
            yield self._row_to_event(r)

    def retract_from_seq(self, seq) -> int:
        return self._retract('seq>=?', seq)

    def retract_from_turn(self, turn) -> int:
        return self._retract('turn IS NOT NULL AND turn>=?', turn)

    def _retract(self, condition, value):
        self._conn.execute('BEGIN IMMEDIATE')
        try:
            cur = self._conn.execute('UPDATE events SET retracted=1 WHERE retracted=0 AND ' + condition, (value,))
            if cur.rowcount:
                self._conn.execute('UPDATE store_revision SET revision=revision+1 WHERE singleton=1')
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        self._sync_mirror_safely()
        return cur.rowcount

    def _sync_mirror_safely(self):
        try:
            self.sync_jsonl()
        except (OSError, sqlite3.Error) as exc:
            self.mirror_error = str(exc)
            log.warning('SQLite commit saved; JSONL mirror needs sync: %s', exc)

    def sync_jsonl(self):
        """Rebuild events.jsonl from SQLite (authoritative)."""
        # Serialize mirror publication with writers so an older snapshot cannot
        # replace the export of a newer commit from another connection.
        self._conn.execute('BEGIN IMMEDIATE')
        try:
            self._rewrite_jsonl()
            self._conn.commit()
            self.mirror_error = None
        except BaseException:
            self._conn.rollback()
            raise

    def _rewrite_jsonl(self):
        rows = list(self.iter_events(include_retracted=True))
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.jsonl_path.parent,
                                             prefix=self.jsonl_path.name + '.', delete=False) as f:
                tmp = Path(f.name)
                for ev in rows:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            tmp.replace(self.jsonl_path)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def _row_to_event(r):
        def _j(v, default):
            return json.loads(v) if v not in (None, "null") else default
        return {
            "seq": r["seq"], "id": r["id"], "type": r["type"], "day": r["day"],
            "scene": r["scene"], "arc": r["arc"],
            "actors": _j(r["actors"], []), "summary": r["summary"],
            "deltas": _j(r["deltas"], {}), "thread_refs": _j(r["thread_refs"], []),
            "chunk_ids": _j(r["chunk_ids"], []),
            "secrecy": _j(r["secrecy"], None), "roll": _j(r["roll"], None),
            "turn": r["turn"],
            "retracted": bool(r["retracted"]),
        }


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

    def iter_events(self, include_retracted=False):
        for ev in self._snapshot + self.events:
            if include_retracted or not ev.get('retracted'):
                # Like EventStore.iter_events(), readers receive independent
                # values. A hook must not rewrite snapshot history in memory.
                yield copy.deepcopy(ev)

    @contextmanager
    def savepoint(self):
        start = len(self.events)
        try:
            yield
            if self.preflight and len(self.events) != start:
                self.preflight(list(self.iter_events()))
        except BaseException:
            del self.events[start:]
            raise

    def publish(self, *, action_id=None):
        events = [{k:v for k,v in ev.items() if k != 'seq'} for ev in self.events]
        return self.store.append_many(events, expected_revision=self.revision,
                                      action_id=action_id, preflight=self.preflight,
                                      retractions=self.retractions)
