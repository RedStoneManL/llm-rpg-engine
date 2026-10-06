"""Isolated audit prototypes, NOT a production migration.

Uses the engine's real event schema and project() function. LLM work must finish
before commit(); the only writer performs preflight and insertion atomically.
"""
import copy
import hashlib
import json

from kernel.projection import project
from engine.schema import validate_event


class Conflict(Exception):
    pass


class IncompleteAction(Exception):
    pass


class AtomicGateway:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry
        store._conn.execute('''CREATE TABLE IF NOT EXISTS audit_actions (
            id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL,
            revision INTEGER NOT NULL)''')
        store._conn.execute('''CREATE TABLE IF NOT EXISTS audit_revision (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)''')
        store._conn.execute('INSERT OR IGNORE INTO audit_revision VALUES (1,0)')
        store._conn.commit()

    def revision(self):
        return self.store._conn.execute('SELECT revision FROM audit_revision').fetchone()[0]

    def commit(self, events, *, action_id, expected_revision, fail_at=None):
        events = copy.deepcopy(events)
        digest = hashlib.sha256(json.dumps(events, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        c = self.store._conn
        c.execute('BEGIN IMMEDIATE')
        try:
            prior = c.execute('SELECT * FROM audit_actions WHERE id=?', (action_id,)).fetchone()
            if prior:
                if prior['payload_hash'] != digest:
                    raise Conflict('idempotency key reused with different payload')
                c.rollback()
                return {'duplicate': True, 'revision': prior['revision'], 'mirror_ok': None}
            revision = self.revision()
            if revision != expected_revision:
                raise Conflict('stale world version')
            for ev in events:
                validate_event(ev, self.store.allowed_types)
            # Preserve strict invariants. Never silently skip a broken event.
            world = project(self.registry, list(self.store.iter_events()) + events)
            first = last = 0
            cols = ['id','type','day','scene','arc','actors','summary','deltas',
                    'thread_refs','chunk_ids','secrecy','roll','turn','retracted']
            encoded = {'actors': [], 'deltas': {}, 'thread_refs': [], 'chunk_ids': [],
                       'secrecy': None, 'roll': None}
            for index, ev in enumerate(events):
                if fail_at == index:
                    raise OSError('injected write failure')
                row = dict(ev)
                for key, default in encoded.items():
                    row[key] = json.dumps(ev.get(key, default), ensure_ascii=False)
                row['retracted'] = int(bool(ev.get('retracted')))
                last = c.execute('INSERT INTO events (' + ','.join(cols) + ') VALUES (' + ','.join('?' for _ in cols) + ')', [row.get(k) for k in cols]).lastrowid
                first = first or last
            c.execute('INSERT INTO audit_actions VALUES (?,?,?,?,?)', (action_id, digest, first, last, revision + 1))
            c.execute('UPDATE audit_revision SET revision=?', (revision + 1,))
            c.commit()
        except BaseException:
            c.rollback()
            raise
        # SQLite is authoritative. A failed derivative export is not a failed turn.
        mirror_ok = True
        try:
            self.store.sync_jsonl()
        except OSError:
            mirror_ok = False
        return {'duplicate': False, 'revision': revision + 1, 'mirror_ok': mirror_ok, 'world': world}

    def undo(self, turn):
        c = self.store._conn
        with c:
            c.execute('UPDATE events SET retracted=1 WHERE turn>=?', (turn,))
            c.execute('UPDATE audit_revision SET revision=revision+1')
        self.store.sync_jsonl()
        return project(self.registry, self.store.iter_events())


class BufferedStore:
    """Capture an unmodified run_turn's writes; normalize root action identity.

    This proves a centralized write boundary is possible. Hook cooldown/trigger
    behavior still needs migration tests before using this as runtime semantics.
    """
    def __init__(self, store, turn):
        self.base = list(store.iter_events())
        self.staged = []
        self.turn = turn

    def iter_events(self, include_retracted=False):
        yield from copy.deepcopy(self.base + self.staged)

    def append(self, ev):
        ev = copy.deepcopy(ev)
        ev['turn'] = self.turn
        self.staged.append(ev)
        return len(self.base) + len(self.staged)


def validated_apply(registry, store, commit, *, day, scene, action_id='action', expected_revision=0, dropped_sections=()):
    if dropped_sections:
        raise IncompleteAction('Refusing a turn with discarded effects: ' + ','.join(dropped_sections))
    gateway = AtomicGateway(store, registry)
    events = []
    for section, decl in commit.sections.items():
        owner = registry.owner_of_section(section)
        if owner:
            events.extend(owner.to_events(section, decl, turn=expected_revision+1, day=day, scene=scene))
    return gateway.commit(events, action_id=action_id, expected_revision=expected_revision)


def visible_world(world, scene):
    """Prototype read view: explicit discovery plus co-presence and current place.

    Tests intentionally seed explicit visibility attrs; existing saves need a
    discovery migration. Filtering must precede matching, navigation and recall.
    """
    view = copy.deepcopy(world)
    graph = view['systems']['ontology']
    pov = scene['protagonist']
    visible = set(scene.get('present', [])) | {pov, scene.get('location')}
    for eid, ent in graph.entities.items():
        if ent.attrs.get('visibility') == 'public' or pov in ent.attrs.get('discovered_by', []):
            visible.add(eid)
    graph.entities = {k: v for k, v in graph.entities.items() if k in visible}
    graph.facts = [f for f in graph.facts if f.subject in visible]
    graph.relations = [r for r in graph.relations if r.src in visible and r.dst in visible]
    return view


def pov_world(world, scene):
    """Fact-level extension for player-visible retrieval (not a full DM view).

    Preserve the protagonist's beliefs, including incorrect beliefs. Give the
    resolver a separate scoped truth view in a production design.
    """
    from systems.knowledge import knows
    view = visible_world(world, scene)
    original = world['systems']['ontology']
    graph = view['systems']['ontology']
    pov, day = scene['protagonist'], scene.get('day', 1)
    kept = []
    for fact in graph.facts:
        if fact.predicate.startswith('knows:'):
            if fact.subject == pov:
                kept.append(fact)
            continue
        believed = knows(original, pov, f'{fact.subject}.{fact.predicate}', day)
        if believed is not None:
            fact.value = believed
            kept.append(fact)
        elif fact.secrecy == 'public':
            kept.append(fact)
    graph.facts = kept
    return view


from facts.graph import FactGraph
from facts.fact import Fact


class IndexedFactGraph(FactGraph):
    """Index only fact assertion/current/history reads; relations unchanged."""
    def __init__(self):
        super().__init__()
        self._fact_current = {}
        self._fact_history = {}
        self._by_subject = {}

    def assert_fact(self, subject, predicate, value, *, day, turn, source_event, secrecy=None):
        key = (subject, predicate)
        prior = self._fact_current.get(key)
        if prior:
            if prior.event_time_start > day:
                raise ValueError('non-monotonic assert')
            prior.event_time_end = day
        fact = Fact(subject=subject, predicate=predicate, value=value,
                    event_time_start=day, ingest_turn=turn,
                    source_event=source_event, secrecy=secrecy)
        self.facts.append(fact)
        self._fact_current[key] = fact
        self._fact_history.setdefault(key, []).append(fact)
        by_subject = self._by_subject.setdefault(subject, {})
        by_subject.pop(predicate, None)
        by_subject[predicate] = fact
        return fact

    def current_facts(self, subject):
        return list(self._by_subject.get(subject, {}).values())

    def value_at(self, subject, predicate, day):
        for fact in self._fact_history.get((subject, predicate), []):
            if fact.valid_at(day):
                return fact.value
        return None

    def fact_history(self, subject, predicate):
        return list(self._fact_history.get((subject, predicate), []))
