# engine/archive.py
import json
import sqlite3
from pathlib import Path

from engine.log import get_logger

log = get_logger("archive")


def _fts_query(q):
    """Arbitrary user text → safe FTS5 query: each whitespace token becomes a
    quoted literal phrase, so operators/colons/quotes are treated as text."""
    toks = [t for t in q.split() if t]
    return " ".join('"' + t.replace('"', '""') + '"' for t in toks)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    day INTEGER, scene TEXT, turn INTEGER, kind TEXT,
    text TEXT NOT NULL, entities TEXT, event_ids TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='rowid', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS chunks_bi BEFORE INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text)
    SELECT 'delete', c.rowid, c.text FROM chunks c WHERE c.chunk_id = new.chunk_id;
END;
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""

class ArchiveStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_chunk(self, *, day, scene, turn, text, entities=None, event_ids=None, kind="narration"):
        chunk_id = f"c_{scene}_{turn}"
        self._conn.execute(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, day, scene, turn, kind, text, entities, event_ids)
               VALUES (?,?,?,?,?,?,?,?)""",
            (chunk_id, day, scene, turn, kind, text,
             json.dumps(entities or [], ensure_ascii=False),
             json.dumps(event_ids or [], ensure_ascii=False)))
        self._conn.commit()
        log.debug("add_chunk id=%s day=%s scene=%s turn=%s len=%d",
                  chunk_id, day, scene, turn, len(text))
        return chunk_id

    def get_chunk(self, chunk_id):
        r = self._conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        return self._row(r) if r else None

    def fts_search(self, query, k=5, entity=None, day=None):
        q = (query or "").strip()
        if not q:
            return []
        ent = f'%"{entity}"%' if entity else None
        if len(q) >= 3:
            fq = _fts_query(q)
            if not fq:
                return []
            sql = ("SELECT c.* FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid "
                   "WHERE chunks_fts MATCH ?")
            params = [fq]
            if day is not None:
                sql += " AND c.day=?"; params.append(day)
            if ent is not None:
                sql += " AND c.entities LIKE ?"; params.append(ent)
            sql += " ORDER BY rank LIMIT ?"; params.append(k)
        else:
            sql = "SELECT * FROM chunks WHERE text LIKE ?"
            params = [f"%{q}%"]
            if day is not None:
                sql += " AND day=?"; params.append(day)
            if ent is not None:
                sql += " AND entities LIKE ?"; params.append(ent)
            sql += " ORDER BY day DESC, turn DESC LIMIT ?"; params.append(k)
        rows = [self._row(r) for r in self._conn.execute(sql, params)]
        log.debug("fts_search q=%r entity=%s day=%s hits=%d", query, entity, day, len(rows))
        return rows

    def _row(self, r):
        return {"chunk_id": r["chunk_id"], "day": r["day"], "scene": r["scene"],
                "turn": r["turn"], "kind": r["kind"], "text": r["text"],
                "entities": json.loads(r["entities"] or "[]"),
                "event_ids": json.loads(r["event_ids"] or "[]")}

    def delete_from_turn(self, turn):
        cur = self._conn.execute("DELETE FROM chunks WHERE turn>=?", (turn,))
        self._conn.commit()
        log.debug("delete_from_turn turn>=%s removed=%s", turn, cur.rowcount)
        return cur.rowcount

    def max_turn(self):
        r = self._conn.execute("SELECT COALESCE(MAX(turn),0) FROM chunks").fetchone()
        return r[0]

    def next_turn(self):
        return self.max_turn() + 1

    def min_turn_of_scene(self, scene):
        r = self._conn.execute("SELECT MIN(turn) FROM chunks WHERE scene=?", (scene,)).fetchone()
        return r[0]

    def iter_chunks(self):
        for r in self._conn.execute("SELECT * FROM chunks ORDER BY rowid"):
            yield self._row(r)

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
