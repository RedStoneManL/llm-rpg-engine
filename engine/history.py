"""Read-only, revision-bound sources from the same SQLite event ledger as play."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import sqlite3
from pathlib import Path

from systems.knowledge import player_statement_fact_key
from systems.interpretive_memory import PRIVATE_EVENT_TYPES


_TYPES = {"input":"player_input", "receipt":"knowledge_set", "narration":"narration_recorded", "observation":"world_change"}
_KIND_LABELS = {"player_input":"玩家输入", "knowledge_set":"通信收据", "narration_recorded":"公开旁白", "world_change":"现场观察"}


class StaleSource(ValueError):
    pass


def prepare_history_index(db):
    def current():
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='play_history_insert'").fetchone()
        return trigger and "world_change" in trigger[0]

    if current():
        return True
    db.execute('BEGIN IMMEDIATE')
    try:
        if not current():
            existed = db.execute("SELECT 1 FROM sqlite_master WHERE name='play_history_fts'").fetchone()
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS play_history_fts USING fts5(body, tokenize='trigram')")
            body = """CASE type WHEN 'narration_recorded' THEN json_extract(deltas,'$.text')
                        WHEN 'world_change' THEN summary ELSE json_extract(deltas,'$.value') END"""
            observations = """(type='world_change' AND json_extract(deltas,'$.public_observation')=1
                               AND json_extract(deltas,'$.visibility')='public')"""
            included = f"""(type='narration_recorded' OR {observations}
                        OR (type='knowledge_set' AND json_extract(deltas,'$.source_kind')='player_statement'))"""
            # Upgrade the old derived index without touching canonical event rows.
            db.execute(f"INSERT OR REPLACE INTO play_history_fts(rowid,body) SELECT seq,{body} FROM events "
                       f"WHERE {observations if existed else included}")
            db.execute('DROP TRIGGER IF EXISTS play_history_insert')
            db.execute(f"""CREATE TRIGGER play_history_insert AFTER INSERT ON events BEGIN
                INSERT INTO play_history_fts(rowid,body) SELECT seq,{body} FROM events
                WHERE seq=new.seq AND {included}; END""")
        db.commit()
        return True
    except sqlite3.OperationalError as exc:
        db.rollback()
        if 'no such module' in str(exc) or 'tokenizer' in str(exc):
            return False
        raise
    except BaseException:
        db.rollback()
        raise


def public_speech_names(world, scene, receipts, events):
    """Preserve only the public name visible when this addressee heard the player."""
    from context.access import pov_world
    by_statement = {(row["speaker"], row["recipient"], row["statement"]): row for row in receipts}
    labels = []
    for event in events:
        delta = event.get("deltas") or {}
        if event["type"] != "knowledge_set" or delta.get("source_kind") != "player_statement":
            continue
        receipt = by_statement.get((delta.get("via"), delta.get("knower"), delta.get("value")))
        if not receipt:
            continue
        when = receipt["when"]
        at = {**scene, "location":when["to"], "present":[]} if when.get("to") else scene
        graph = pov_world(world, at, pov=scene["protagonist"])["systems"]["ontology"]
        name = graph.value_at(receipt["recipient"], "name", at.get("day") or 1)
        if isinstance(name, str) and name.strip():
            labels.append((event["id"], name.strip()))
    return labels


class PlayerHistory:
    def __init__(self, store, actor):
        self.store = store
        self.actor = actor
        self.db = store._conn
        self.scope = str(Path(store.db_path).resolve())

    def source_ref(self, revision, event_id):
        data = json.dumps([self.scope,self.actor,revision,event_id], ensure_ascii=False).encode()
        return f'{revision}:{event_id}:{hashlib.sha256(data).hexdigest()[:24]}'

    def _records(self, *, condition, args, revision, limit, descending=False):
        private = tuple(PRIVATE_EVENT_TYPES)
        placeholders = ','.join('?' for _ in private)
        rows = self.db.execute(f'''WITH sources AS (
            SELECT e.id,e.type,e.day,e.turn,e.seq * 2 + 1 AS ordinal,e.seq,
                CASE WHEN e.type='world_change' THEN json_set(e.deltas,'$.text',e.summary)
                     ELSE e.deltas END AS deltas FROM events e
            WHERE e.retracted=0 AND (e.secrecy IS NULL OR e.secrecy IN ('null','"public"'))
            AND (e.type='narration_recorded' OR (e.type='knowledge_set'
                AND json_extract(e.deltas,'$.source_kind')='player_statement'
                AND json_extract(e.deltas,'$.via')=?) OR (e.type='world_change'
                AND json_extract(e.deltas,'$.public_observation')=1 AND json_extract(e.deltas,'$.visibility')='public'
                AND json_extract(e.deltas,'$.source')='author_world' AND json_extract(e.deltas,'$.level')=1
                AND json_extract(e.deltas,'$.observation_kind') IN ('stable_layout','single_observation')
                AND json_type(e.deltas,'$.observation_range')='text' AND trim(json_extract(e.deltas,'$.observation_range'))!=''
                AND (json_type(e.deltas,'$.observer') IS NULL OR json_extract(e.deltas,'$.observer')=?)
                AND EXISTS (SELECT 1 FROM player_inputs i JOIN action_receipts r USING(action_id)
                            WHERE i.actor=? AND r.action_id=(SELECT owner.action_id FROM action_receipts owner
                                WHERE owner.first_seq>0 AND owner.last_seq>=e.seq ORDER BY owner.last_seq LIMIT 1)
                            AND e.seq BETWEEN r.first_seq AND r.last_seq
                            AND NOT EXISTS (SELECT 1 FROM events gone WHERE gone.retracted!=0
                                            AND gone.seq BETWEEN r.first_seq AND r.last_seq))))
            AND NOT EXISTS (SELECT 1 FROM events p WHERE p.type IN ({placeholders}) AND
                (p.id IN (SELECT value FROM json_each(e.thread_refs)) OR p.id IN (SELECT value FROM json_each(e.chunk_ids))))
            UNION ALL
            SELECT 'input-' || e.id,'player_input',
                COALESCE((SELECT MAX(prev.day) FROM events prev WHERE prev.seq<e.seq AND prev.retracted=0),1),
                e.turn,e.seq * 2,e.seq,json_object('text',i.body)
            FROM player_inputs i JOIN action_receipts r USING(action_id)
            JOIN events e ON e.seq=r.first_seq
            WHERE i.actor=? AND NOT EXISTS (SELECT 1 FROM events gone
                WHERE gone.seq BETWEEN r.first_seq AND r.last_seq AND gone.retracted!=0))
            SELECT * FROM sources e WHERE ({condition})
            ORDER BY ordinal {'DESC' if descending else 'ASC'}''', (self.actor, self.actor, self.actor, *private, self.actor, *args))
        records = []
        for row in rows:
            delta = json.loads(row['deltas']); speech = row['type'] == 'knowledge_set'
            text = delta.get('value' if speech else 'text')
            if not isinstance(text, str) or not text:
                continue
            if speech and (not isinstance(delta.get('knower'), str) or not delta['knower']
                           or delta.get('fact_key') != player_statement_fact_key(self.actor,text)):
                continue
            records.append({'kind':_KIND_LABELS[row['type']],'day':row['day'],'turn':row['turn'],
                            'seq':row['ordinal'],'text':text,'recipient':delta.get('knower') if speech else None,
                            'ref':self.source_ref(revision,row['id'])})
            if row['type'] == 'world_change':
                records[-1].update(source_event_id=row['id'], place=delta['place'], feature=delta.get('feature'))
            if len(records) >= limit:
                break
        return records

    def _add_recipient(self, conditions, args, recipient, revision):
        # First try the stable ID; old archives need no name index for that.
        base = " AND ".join(conditions) or "1"
        id_filter = "json_extract(e.deltas,'$.knower')=?"
        exact = self._records(condition=f"({base}) AND {id_filter}", args=[*args, recipient],
                              revision=revision, limit=1)
        if exact:
            conditions.append(id_filter)
            args.append(recipient)
            return
        label_filter = "e.id IN (SELECT event_id FROM speech_names WHERE label=?)"
        first = self._records(condition=f"({base}) AND {label_filter}", args=[*args, recipient],
                              revision=revision, limit=1)
        if first:
            target = first[0]["recipient"]
            others = self._records(condition=f"({base}) AND {label_filter} AND NOT ({id_filter})",
                                   args=[*args, recipient, target], revision=revision, limit=1)
            if others:
                raise ValueError(f"此范围有同名收件人，请按角色 ID 分别查证：{target}、{others[0]['recipient']}")
        conditions.append(label_filter)
        args.append(recipient)

    def search(self, query, limit=5):
        return self.locate(query, limit=limit)["matches"]

    def locate(self, query="", *, day=None, recipient=None, kind=None, before=None, limit=5, verify=False):
        """Locate records (and optionally open them) inside one revision-bound read."""
        if not isinstance(query, str):
            raise ValueError("检索关键词必须是文字")
        query = query.strip()
        if day is not None and (type(day) is not int or not 1 <= day <= 2**63 - 1):
            raise ValueError("游戏日期须为正整数")
        if recipient is not None:
            if not isinstance(recipient, str) or not recipient.strip() or kind not in (None, "receipt"):
                raise ValueError("收件人只适用于通信收据")
            recipient, kind = recipient.strip(), "receipt"
        if kind is not None and (not isinstance(kind, str) or kind not in _TYPES):
            raise ValueError("原文类型须为 input、receipt、narration 或 observation")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("一次原文检索最多返回 20 条")
        conditions, args = [], []
        if query:
            body = "CASE WHEN e.type='knowledge_set' THEN json_extract(e.deltas,'$.value') ELSE json_extract(e.deltas,'$.text') END"
            conditions.append(f"instr({body},?)>0")
            args.append(query)
            if self.store.history_indexed and len(query) >= 3:
                conditions.append("(e.type='player_input' OR e.seq IN (SELECT rowid FROM play_history_fts WHERE play_history_fts MATCH ?))")
                args.append('"' + query.replace('"', '""') + '"')
        for condition, value in (("e.day=?", day), ("e.type=?", _TYPES.get(kind))):
            if value is not None:
                conditions.append(condition)
                args.append(value)
        self.db.execute("BEGIN")
        try:
            revision = self.store.revision
            result = {"revision":revision, "matches":[], "next_before":None}
            if not self.actor or not conditions:
                return result
            if recipient:
                self._add_recipient(conditions, args, recipient, revision)
            if before is not None:
                anchor = self._source(before, revision)
                conditions.append("e.ordinal<?")
                args.append(anchor["seq"])
            rows = self._records(condition=" AND ".join(conditions), args=args, revision=revision,
                                 limit=limit + 1, descending=True)
            selected = rows[:limit]
            if len(rows) > limit:
                result["next_before"] = selected[-1]["ref"]
            if verify:
                result["opened"] = [self._with_neighbors(row, revision, 1) for row in selected]
            for row in selected:
                text = row["text"]
                start = max(0, text.find(query) - 60) if query else 0
                result["matches"].append({**row, "text":text[start:start + 180]})
            return result
        finally:
            self.db.rollback()

    def _source(self, ref, revision):
        if not self.actor or not isinstance(ref, str) or len(ref) > 1024:
            raise StaleSource("来源不可用，请重新检索。")
        _, _, suffix = ref.partition(":")
        event_id, separator, _ = suffix.rpartition(":")
        if not separator or ref != self.source_ref(revision, event_id):
            raise StaleSource("来源版本或角色已变化，请重新检索。")
        rows = self._records(condition="e.id=?", args=[event_id], revision=revision, limit=1)
        if not rows:
            raise StaleSource("原文已撤回或不可见，请重新检索。")
        return rows[0]

    def _with_neighbors(self, source, revision, radius):
        seq = source["seq"]
        previous = self._records(condition="e.ordinal<?", args=[seq], revision=revision,
                                 limit=radius, descending=True)[::-1] if radius else []
        following = self._records(condition="e.ordinal>?", args=[seq], revision=revision,
                                  limit=radius) if radius else []
        return {"source":source, "before":previous, "after":following, "revision":revision}

    def open(self, ref, radius=2):
        if type(radius) is not int:
            raise ValueError("邻文范围必须为整数")
        self.db.execute("BEGIN")
        try:
            revision = self.store.revision
            return self._with_neighbors(self._source(ref, revision), revision, min(8, max(0, radius)))
        finally:
            self.db.rollback()


# Only the host supplies a ledger and an actor. A tool registry cannot switch
# campaigns, extend its lifetime, or read across a concurrent publication.
_author_history = ContextVar('author_history', default=None)


@contextmanager
def bind_author_history(batch, actor):
    ledger = getattr(batch, 'store', None)
    reader = PlayerHistory(ledger, actor) if actor and hasattr(ledger, 'history_indexed') else None
    session = (reader, batch.revision) if reader else None
    token = _author_history.set(session)
    try:
        yield
    finally:
        _author_history.reset(token)


def author_source_tools(actor):
    from llm.tools import Tool
    session = _author_history.get()
    if session is None or session[0].actor != actor:
        return []
    reader, revision = session

    def check():
        if _author_history.get() is not session or reader.store.revision != revision:
            raise StaleSource('来源版本已变化，请重新检索。')

    def locate(q="", day=None, recipient=None, kind=None, before=None):
        check()
        page = reader.locate(q, day=day, recipient=recipient, kind=kind, before=before)
        check()
        return page

    def open_source(ref, radius=2):
        check()
        result = reader.open(ref, radius)
        check()
        return result

    return [
        Tool('history_locate', '在玩家可见原文中按字面定位。未命中可改用短词重新检索。摘要不能作为原话。',
             {'type':'object', 'properties': {
                 'q': {'type':'string', 'description':'可选字面片段；忘记原话时可只按日期或收件人查'},
                 'day': {'type':'integer', 'minimum':1},
                 'kind': {'type':'string', 'enum':list(_TYPES)},
                 'recipient': {'type':'string', 'description':'收件人角色 ID，仅匹配主角实际发送的通信收据'},
                 'before': {'type':'string', 'description':'上页 next_before，继续找更早的记录'}
             }}, locate),
        Tool('history_open', '打开上一步命中的原文与可见邻文。引用须保留来源，区分旁白和收件人实听的通信。',
             {'type': 'object', 'properties': {'ref': {'type': 'string'},
              'radius': {'type': 'integer', 'minimum': 0, 'maximum': 8}}, 'required': ['ref']}, open_source),
    ]
