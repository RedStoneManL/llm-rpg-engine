# engine/vectorstore.py
import sqlite3
from pathlib import Path

import numpy as np

from engine.log import get_logger

log = get_logger("vectorstore")

class VectorStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors (block_id TEXT PRIMARY KEY, vec BLOB NOT NULL)")
        self._conn.commit()

    def add(self, block_id, vector):
        v = np.asarray(vector, dtype=np.float32)
        self._conn.execute("INSERT OR REPLACE INTO vectors (block_id, vec) VALUES (?,?)",
                            (block_id, v.tobytes()))
        self._conn.commit()
        log.debug("add block_id=%s dim=%d", block_id, v.shape[0])

    def search(self, vector, k=5, *, allowed_ids=None):
        """Rank only caller-selected IDs when a candidate scope is supplied.

        None preserves legacy whole-index search; an empty scope means no hits.
        The vector index does not decide authorization, event validity or facts.
        Excluded rows are never decoded or scored, including stale dimensions.
        """
        if allowed_ids is None:
            rows = self._conn.execute("SELECT block_id, vec FROM vectors").fetchall()
        else:
            if isinstance(allowed_ids, (str, bytes)):
                raise TypeError('allowed_ids must be a collection of strings')
            ids = list(allowed_ids)
            if any(not isinstance(bid, str) for bid in ids):
                raise TypeError('allowed_ids must contain only strings')
            ids = sorted(set(ids))
            rows = []
            # Keep each query below SQLite's conservative parameter limit.
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset + 500]
                placeholders = ','.join('?' for _ in batch)
                rows.extend(self._conn.execute(
                    f"SELECT block_id, vec FROM vectors WHERE block_id IN ({placeholders})", batch).fetchall())
        if not rows:
            log.debug("search empty store")
            return []
        q = np.asarray(vector, dtype=np.float32)
        qn = q / (np.linalg.norm(q) or 1.0)
        ids, mats = [], []
        for bid, blob in rows:
            ids.append(bid); mats.append(np.frombuffer(blob, dtype=np.float32))
        M = np.vstack(mats)
        norms = np.linalg.norm(M, axis=1)
        norms[norms == 0] = 1.0
        sims = (M @ qn) / norms
        order = np.argsort(-sims)[:k]
        out = [(ids[i], float(sims[i])) for i in order]
        log.debug("search k=%d candidates=%d → %d", k, len(rows), len(out))
        return out

    def clear(self):
        self._conn.execute("DELETE FROM vectors"); self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
