import json
import sqlite3
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
"""

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

    def append(self, ev) -> int:
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
        self._conn.commit()
        seq = cur.lastrowid
        log.debug("append id=%s seq=%s type=%s", ev["id"], seq, ev["type"])
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**ev, "seq": seq}, ensure_ascii=False) + "\n")
        return seq

    def iter_events(self, include_retracted=False):
        q = "SELECT * FROM events"
        if not include_retracted:
            q += " WHERE retracted=0"
        q += " ORDER BY seq ASC"
        for r in self._conn.execute(q):
            yield self._row_to_event(r)

    def retract_from_seq(self, seq) -> int:
        cur = self._conn.execute(
            "UPDATE events SET retracted=1 WHERE seq>=? AND retracted=0", (seq,))
        self._conn.commit()
        log.debug("retract from seq=%s affected=%s", seq, cur.rowcount)
        self._rewrite_jsonl()
        return cur.rowcount

    def retract_from_turn(self, turn) -> int:
        cur = self._conn.execute(
            "UPDATE events SET retracted=1 WHERE turn IS NOT NULL AND turn>=? AND retracted=0",
            (turn,))
        self._conn.commit()
        self._rewrite_jsonl()
        log.debug("retract_from_turn turn>=%s affected=%s", turn, cur.rowcount)
        return cur.rowcount

    def sync_jsonl(self):
        """Rebuild events.jsonl from SQLite (authoritative)."""
        self._rewrite_jsonl()

    def _rewrite_jsonl(self):
        rows = list(self.iter_events(include_retracted=True))
        tmp = self.jsonl_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for ev in rows:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        tmp.replace(self.jsonl_path)

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
