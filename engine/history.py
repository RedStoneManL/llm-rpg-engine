"""Read-only, revision-bound sources from the same SQLite event ledger as play."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import sqlite3
from pathlib import Path

from systems.knowledge import player_statement_fact_key
from systems.interpretive_memory import PRIVATE_EVENT_TYPES


class StaleSource(ValueError):
    pass


def prepare_history_index(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='play_history_insert'").fetchone():
        return True
    db.execute('BEGIN IMMEDIATE')
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='play_history_insert'").fetchone():
            db.execute("CREATE VIRTUAL TABLE play_history_fts USING fts5(body, tokenize='trigram')")
            body = "CASE WHEN type='narration_recorded' THEN json_extract(deltas,'$.text') ELSE json_extract(deltas,'$.value') END"
            included = "(type='narration_recorded' OR (type='knowledge_set' AND json_extract(deltas,'$.source_kind')='player_statement'))"
            db.execute(f'INSERT INTO play_history_fts(rowid,body) SELECT seq,{body} FROM events WHERE {included}')
            db.execute(f'''CREATE TRIGGER play_history_insert AFTER INSERT ON events BEGIN
                INSERT INTO play_history_fts(rowid,body) SELECT seq,{body} FROM events WHERE seq=new.seq AND {included}; END''')
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


class PlayerHistory:
    def __init__(self, store, actor):
        self.store = store
        self.actor = actor
        self.db = store._conn
        self.scope = str(Path(store.db_path).resolve())

    def _ref(self, revision, event_id):
        data = json.dumps([self.scope,self.actor,revision,event_id], ensure_ascii=False).encode()
        return f'{revision}:{event_id}:{hashlib.sha256(data).hexdigest()[:24]}'

    def _records(self, *, condition, args, revision, limit, descending=False):
        private = tuple(PRIVATE_EVENT_TYPES)
        placeholders = ','.join('?' for _ in private)
        rows = self.db.execute(f'''WITH sources AS (
            SELECT e.id,e.type,e.day,e.turn,e.seq * 2 + 1 AS ordinal,e.seq,e.deltas FROM events e
            WHERE e.retracted=0 AND (e.secrecy IS NULL OR e.secrecy IN ('null','"public"'))
            AND (e.type='narration_recorded' OR (e.type='knowledge_set'
                AND json_extract(e.deltas,'$.source_kind')='player_statement'
                AND json_extract(e.deltas,'$.via')=?))
            AND NOT EXISTS (SELECT 1 FROM events p WHERE p.type IN ({placeholders}) AND
                (p.id IN (SELECT value FROM json_each(e.thread_refs)) OR p.id IN (SELECT value FROM json_each(e.chunk_ids))))
            UNION ALL
            SELECT 'input-' || e.id,'player_input',e.day,e.turn,e.seq * 2,e.seq,json_object('text',i.body)
            FROM player_inputs i JOIN action_receipts r USING(action_id)
            JOIN events e ON e.seq=r.first_seq
            WHERE i.actor=? AND NOT EXISTS (SELECT 1 FROM events gone
                WHERE gone.seq BETWEEN r.first_seq AND r.last_seq AND gone.retracted!=0))
            SELECT * FROM sources e WHERE ({condition})
            ORDER BY ordinal {'DESC' if descending else 'ASC'}''', (self.actor,*private,self.actor,*args))
        records = []
        for row in rows:
            delta = json.loads(row['deltas']); speech = row['type'] == 'knowledge_set'
            text = delta.get('value' if speech else 'text')
            if not isinstance(text, str) or not text:
                continue
            if speech and (not isinstance(delta.get('knower'), str) or not delta['knower']
                           or delta.get('fact_key') != player_statement_fact_key(self.actor,text)):
                continue
            records.append({'kind':{'player_input':'玩家输入','knowledge_set':'通信收据','narration_recorded':'公开旁白'}[row['type']],'day':row['day'],'turn':row['turn'],
                            'seq':row['ordinal'],'text':text,'recipient':delta.get('knower') if speech else None,
                            'ref':self._ref(revision,row['id'])})
            if len(records) >= limit:
                break
        return records

    def search(self, query, limit=5):
        query = str(query).strip()
        if not self.actor or not query:
            return []
        indexed = self.store.history_indexed and len(query) >= 3
        body = "CASE WHEN e.type='knowledge_set' THEN json_extract(e.deltas,'$.value') ELSE json_extract(e.deltas,'$.text') END"
        where, args = f'instr({body},?)>0', [query]
        if indexed:
            where += " AND (e.type='player_input' OR e.seq IN (SELECT rowid FROM play_history_fts WHERE play_history_fts MATCH ?))"
            args.append('"'+query.replace('"','""')+'"')
        self.db.execute('BEGIN')
        try:
            records = self._records(condition=where, args=args, revision=self.store.revision,
                                    limit=min(20, max(limit, 1)), descending=True)
            for record in records:
                full = record['text']
                start = max(0, full.find(query) - 60)
                record['text'] = full[start:start + 180]
            return records
        finally:
            self.db.rollback()

    def open(self, ref, radius=2):
        if not self.actor or not isinstance(ref,str) or len(ref)>300:
            raise StaleSource('来源不可用，请重新检索。')
        self.db.execute('BEGIN')
        try:
            revision=self.store.revision
            parts=ref.split(':')
            if len(parts)!=3 or ref!=self._ref(revision,parts[1]):
                raise StaleSource('来源版本或角色已变化，请重新检索。')
            def rows(condition, args, limit, descending=False):
                return self._records(condition=condition,args=args,revision=revision,limit=limit,descending=descending)
            found=rows('e.id=?',[parts[1]],1)
            if not found:
                raise StaleSource('原文已撤回或不可见，请重新检索。')
            seq=found[0]['seq'];radius=max(0,min(radius,8))
            previous=rows('e.ordinal<?',[seq],radius,True)[::-1] if radius else []
            following=rows('e.ordinal>?',[seq],radius) if radius else []
            return {'source':found[0],'before':previous,'after':following,'revision':revision}
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

    def locate(q):
        check()
        records = reader.search(q)
        check()
        return {'matches': records}

    def open_source(ref, radius=2):
        check()
        result = reader.open(ref, radius)
        check()
        return result

    return [
        Tool('history_locate', '在玩家可见原文中按字面定位。未命中可改用短词重新检索。摘要不能作为原话。',
             {'type': 'object', 'properties': {'q': {'type': 'string'}}, 'required': ['q']}, locate),
        Tool('history_open', '打开上一步命中的原文与可见邻文。引用须保留来源，区分旁白和收件人实听的通信。',
             {'type': 'object', 'properties': {'ref': {'type': 'string'},
              'radius': {'type': 'integer', 'minimum': 0, 'maximum': 8}}, 'required': ['ref']}, open_source),
    ]
