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
CREATE INDEX IF NOT EXISTS idx_active_turn ON events(turn) WHERE retracted=0;
CREATE TABLE IF NOT EXISTS store_revision (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
);
INSERT OR IGNORE INTO store_revision VALUES (1, 0);
CREATE TABLE IF NOT EXISTS action_receipts (
    action_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
    first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL, revision INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipt_last ON action_receipts(last_seq) WHERE first_seq>0;
CREATE TABLE IF NOT EXISTS player_inputs (
    action_id TEXT PRIMARY KEY, actor TEXT NOT NULL, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS player_inputs_actor ON player_inputs(actor);
CREATE TABLE IF NOT EXISTS speech_names (
    event_id TEXT PRIMARY KEY, label TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_speech_label ON speech_names(label);
CREATE TABLE IF NOT EXISTS action_result_metadata (
    action_id TEXT PRIMARY KEY, metadata TEXT NOT NULL
);
"""


class RevisionConflict(RuntimeError):
    """A proposal was based on a world that has since changed."""


class ActionAlreadyCommitted(RevisionConflict):
    """A caller retried an active action before any new generation began."""

    def __init__(self, receipt, result_metadata, *, metadata_status='missing'):
        super().__init__('action_id has already been committed')
        self.receipt = receipt
        self.result_metadata = result_metadata
        self.metadata_status = metadata_status


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
        from engine.history import prepare_history_index
        self.history_indexed = prepare_history_index(self._conn)
        self.preflight = None
        self.mirror_error = None

    def append(self, ev) -> int:
        return self.append_many([ev])["last_seq"]

    @property
    def revision(self) -> int:
        return self._conn.execute('SELECT revision FROM store_revision WHERE singleton=1').fetchone()[0]

    def recent_player_narration(self, limit=8):
        from systems.interpretive_memory import PRIVATE_EVENT_TYPES
        private = tuple(PRIVATE_EVENT_TYPES)
        slots = ','.join('?' for _ in private)
        self._conn.execute('BEGIN')
        try:
            if self._conn.execute(f'SELECT 1 FROM events WHERE type IN ({slots}) LIMIT 1',private).fetchone():
                unsafe = self._conn.execute(f'''SELECT 1 FROM events p,events e WHERE p.type IN ({slots})
                    AND e.type NOT IN ({slots}) AND (p.id IN (SELECT value FROM json_each(e.thread_refs))
                    OR p.id IN (SELECT value FROM json_each(e.chunk_ids))) LIMIT 1''', (*private,*private)).fetchone()
                if unsafe:
                    raise ValueError('public event references private memory')
            rows = self._conn.execute('''SELECT json_extract(deltas,'$.text') FROM events
                WHERE type='narration_recorded' AND retracted=0 AND (secrecy IS NULL OR secrecy IN ('null','"public"'))
                AND json_type(deltas,'$.text')='text' AND json_extract(deltas,'$.text')!=''
                ORDER BY seq DESC LIMIT ?''',(limit,)).fetchall()
            return [r[0] for r in reversed(rows)]
        finally:
            self._conn.rollback()

    def next_turn(self) -> int:
        return self._conn.execute('SELECT COALESCE(MAX(turn),0)+1 FROM events WHERE retracted=0').fetchone()[0]

    def get_action_receipt(self, action_id):
        """Return a durable action receipt, if one was already committed."""
        row = self._conn.execute(
            'SELECT * FROM action_receipts WHERE action_id=?', (action_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def get_action_result_metadata(self, action_id):
        """Return the optional, versioned result sidecar for an action."""
        row = self._conn.execute(
            'SELECT metadata FROM action_result_metadata WHERE action_id=?', (action_id,)
        ).fetchone()
        return json.loads(row['metadata']) if row is not None else None

    def latest_active_action_prefix(self):
        """Return ``(receipt, active_history_before_it)`` from one read snapshot.

        A controller uses this on cold start to recover the action-start scene
        for catch-up.  Keeping receipt selection and prefix replay in the same
        SQLite snapshot prevents a concurrent rewind from mixing a formerly
        active receipt with a differently retracted prefix.
        """
        self._conn.execute('BEGIN')
        try:
            rows = self._conn.execute(
                'SELECT * FROM action_receipts WHERE first_seq > 0 '
                'ORDER BY revision DESC, last_seq DESC'
            ).fetchall()
            for row in rows:
                receipt = dict(row)
                if self.action_receipt_is_retracted(receipt):
                    continue
                history = [self._row_to_event(event) for event in self._conn.execute(
                    'SELECT * FROM events WHERE seq < ? AND retracted=0 ORDER BY seq ASC',
                    (receipt['first_seq'],),
                )]
                self._conn.commit()
                return receipt, history
            self._conn.commit()
            return None
        except BaseException:
            self._conn.rollback()
            raise

    def action_receipt_is_retracted(self, receipt):
        return self._conn.execute(
            'SELECT 1 FROM events WHERE seq BETWEEN ? AND ? AND retracted=1 LIMIT 1',
            (receipt['first_seq'], receipt['last_seq']),
        ).fetchone() is not None

    def mirror_pending_for_history(self, history):
        """Return whether the JSONL mirror differs from a SQLite snapshot.

        This is deliberately read-only: a response-loss retry must not silently
        repair a failed mirror.  Any unreadable, malformed, or non-identical
        mirror is conservatively reported as pending without logging event
        bodies.
        """
        try:
            with self.jsonl_path.open('r', encoding='utf-8') as handle:
                mirror = [json.loads(line) for line in handle]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return True
        return mirror != history

    def read_committed_action(self, action_id):
        """Read one completed action from a consistent, short SQLite snapshot.

        This is deliberately separate from append_many's transaction helpers:
        callers use it only to stop a retried run before any generation starts.
        Metadata stays raw so the caller can classify legacy, corrupt and future
        versions without turning malformed stored bytes into a JSON exception.
        """
        self._conn.execute('BEGIN')
        try:
            receipt = self.get_action_receipt(action_id)
            if receipt is None:
                self._conn.commit()
                return None
            # Receipt fingerprints are an internal duplicate-write guard, not a
            # field normal EventBatch.publish callers receive.  Keep recovery
            # responses shape-compatible with their original successful result.
            receipt.pop('fingerprint', None)
            retracted = self.action_receipt_is_retracted(receipt)
            row = self._conn.execute(
                'SELECT metadata FROM action_result_metadata WHERE action_id=?', (action_id,)
            ).fetchone()
            revision = self.revision
            history = [self._row_to_event(row) for row in self._conn.execute(
                'SELECT * FROM events ORDER BY seq ASC')]
            events = [self._row_to_event(row) for row in self._conn.execute(
                'SELECT * FROM events WHERE seq BETWEEN ? AND ? ORDER BY seq ASC',
                (receipt['first_seq'], receipt['last_seq']))]
            mirror_pending = self.mirror_pending_for_history(history)
            self._conn.commit()
            return {'receipt': receipt, 'retracted': retracted,
                    'metadata_raw': row['metadata'] if row is not None else None,
                    'revision': revision, 'history': history, 'events': events,
                    'mirror_pending': mirror_pending}
        except BaseException:
            self._conn.rollback()
            raise

    def snapshot(self, *, if_changed_from=None):
        """Read events and revision from one SQLite snapshot, then release it."""
        self._conn.execute('BEGIN')
        try:
            revision = self.revision
            events = None if revision == if_changed_from else list(self.iter_events(include_retracted=True))
            self._conn.commit()
            return revision, events
        except BaseException:
            self._conn.rollback()
            raise

    def append_many(self, events, *, expected_revision=None, action_id=None,
                    preflight=None, final_validate=None, retractions=(), result_metadata=None, player_input_record=None, speech_names=()):
        """Publish a complete proposal atomically; JSONL is a recoverable mirror.

        All LLM work belongs before this short transaction. The preflight callback
        folds the entire prospective history and must perform no external I/O.
        ``final_validate`` receives the exact, copied event payload after
        preflight and before insertion, while the SQLite transaction is open.
        """
        events = copy.deepcopy(list(events))
        for ev in events:
            validate_event(ev, self.allowed_types)
        retractions = list(retractions)
        if result_metadata is not None and not action_id:
            raise ValueError('result_metadata requires action_id')
        metadata_json = self._serialize_result_metadata(result_metadata)
        if any(kind not in {'seq', 'turn'} for kind, value in retractions):
            raise ValueError('unknown retraction boundary')
        fingerprint = hashlib.sha256(json.dumps({'events':events, 'retractions':retractions}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self._conn.execute('BEGIN IMMEDIATE')
        try:
            if action_id:
                prior = self.get_action_receipt(action_id)
                if prior:
                    if prior['fingerprint'] != fingerprint:
                        raise RevisionConflict('action_id already used for different events')
                    if self.action_receipt_is_retracted(prior):
                        raise RevisionConflict('action was retracted; submit a new action_id')
                    self._conn.rollback()
                    return {**prior, 'duplicate': True, 'mirror_pending': bool(self.mirror_error)}
            revision = self.revision
            if expected_revision is not None and revision != expected_revision:
                raise RevisionConflict('world changed while the action was being prepared')
            removed = 0
            for kind, value in retractions:
                removed += self._conn.execute(f'UPDATE events SET retracted=1 WHERE retracted=0 AND {kind}>=?', (value,)).rowcount
            check = preflight or self.preflight
            if check is not None:
                check(list(self.iter_events()) + events)
            if final_validate is not None:
                final_validate(events)
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
                if player_input_record is not None and first:
                    self._conn.execute('INSERT INTO player_inputs VALUES (?,?,?)',
                                       (action_id, *player_input_record))
                if speech_names:
                    self._conn.executemany('INSERT INTO speech_names VALUES (?,?)', speech_names)
                if result_metadata is not None:
                    self._insert_action_result_metadata(action_id, metadata_json)
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        self._sync_mirror_safely()
        return {'action_id': action_id, 'first_seq': first, 'last_seq': last,
                'revision': revision, 'duplicate': False, 'mirror_pending': bool(self.mirror_error)}

    @staticmethod
    def _serialize_result_metadata(result_metadata):
        """Encode the explicitly versioned, action-scoped retry sidecar."""
        if result_metadata is None:
            return None
        if not isinstance(result_metadata, dict) or type(result_metadata.get('version')) is not int:
            raise ValueError('result_metadata must be an object with an integer version')
        try:
            return json.dumps(result_metadata, sort_keys=True, ensure_ascii=False,
                              separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError('result_metadata must be JSON serializable') from exc

    def _insert_action_result_metadata(self, action_id, metadata_json):
        """Write the sidecar inside append_many's existing transaction."""
        self._conn.execute('INSERT INTO action_result_metadata VALUES (?,?)',
                           (action_id, metadata_json))

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

    def retract_from_seq(self, seq, *, expected_revision=None) -> int:
        return self._retract('seq>=?', seq, expected_revision=expected_revision)

    def retract_from_turn(self, turn, *, expected_revision=None) -> int:
        return self._retract('turn IS NOT NULL AND turn>=?', turn,
                             expected_revision=expected_revision)

    def _retract(self, condition, value, *, expected_revision=None):
        self._conn.execute('BEGIN IMMEDIATE')
        try:
            if expected_revision is not None and self.revision != expected_revision:
                raise RevisionConflict('world changed while the retraction was being prepared')
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
        """Register a validator for the exact event payload about to publish.

        Validators receive a list of payload dictionaries without transient
        ``seq`` fields.  They run once before publication and again after the
        preflight callback has seen the store's copied payload.
        """
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
        prior_retracted = [(ev, 'retracted' in ev, ev.get('retracted'))
                           for ev in self._snapshot + self.events]
        try:
            yield
            if self.preflight and len(self.events) != start:
                self.preflight(list(self.iter_events()))
        except BaseException:
            del self.events[start:]
            del self.retractions[retractions_start:]
            del self.publish_validators[validators_start:]
            for ev, had_retracted, was_retracted in prior_retracted:
                if had_retracted:
                    ev['retracted'] = was_retracted
                else:
                    ev.pop('retracted', None)
            raise

    def publish(self, *, action_id=None, result_metadata=None, player_input_record=None, speech_names=()):
        events = [{k:v for k,v in ev.items() if k != 'seq'} for ev in self.events]
        for validator in self.publish_validators:
            validator(events)
        original_preflight = self.preflight

        def final_preflight(history):
            if original_preflight:
                original_preflight(history)

        def final_validate(events):
            for validator in self.publish_validators:
                validator(events)

        return self.store.append_many(events, expected_revision=self.revision,
                                      action_id=action_id, preflight=final_preflight,
                                      final_validate=final_validate,
                                      retractions=self.retractions, result_metadata=result_metadata,
                                      **({'player_input_record': player_input_record} if player_input_record else {}),
                                      **({'speech_names': speech_names} if speech_names else {}))
