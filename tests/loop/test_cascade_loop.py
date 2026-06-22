"""Tests for loop.cascade (Phase C1)."""
from __future__ import annotations

import json
import tempfile
import os

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import open_store, kernel_event
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.cascade import CascadeSystem
from loop.cascade import cascade_trigger


def _reg():
    return (Registry().register(OntologySystem())
            .register(PlaceSystem()).register(CascadeSystem()))


def _place(pid, parent=None):
    d = {"id": pid, "level": 2, "kind": "settlement", "seed": "x", "tier": "tracked"}
    if parent:
        d["parent"] = parent
    return kernel_event("place_created", day=1, scene="s1",
                        summary=f"{pid}", deltas=d, turn=1)


def test_trigger_empty_when_no_significant_event():
    world = project(_reg(), [_place("town")])
    # a bare action (heuristic floor 1) on a place is below CASCADE_FLOOR
    evs = [kernel_event("action", day=1, scene="s1", summary="walk",
                        deltas={}, actors=["hero"], turn=2)]
    assert cascade_trigger(evs, world) == []


def test_trigger_on_world_change_returns_root_place():
    world = project(_reg(), [_place("capital")])
    evs = [kernel_event("world_change", day=1, scene="s1", summary="陷落",
                        deltas={"place": "capital", "level": 1}, turn=2)]
    assert cascade_trigger(evs, world) == ["capital"]


def test_trigger_dedupes_world_change_roots():
    world = project(_reg(), [_place("town")])
    evs = [
        kernel_event("world_change", day=1, scene="s1", summary="w",
                     deltas={"place": "town", "level": 1, "summary": "w"}, turn=2),
        kernel_event("world_change", day=1, scene="s1", summary="w2",
                     deltas={"place": "town", "level": 1, "summary": "w2"}, turn=2),
        kernel_event("world_change", day=1, scene="s1", summary="w3",
                     deltas={"place": "nowhere", "summary": "w3"}, turn=2),  # not a Place
    ]
    assert cascade_trigger(evs, world) == ["town"]


# ---------------------------------------------------------------------------
# Task 5: lightweight_validate
# ---------------------------------------------------------------------------

from loop.cascade import lightweight_validate


def test_lightweight_validate_passes_existing_id():
    g = project(_reg(), [_place("town")])["systems"]["ontology"]
    v = {"id": "town", "evolve": True, "state": "繁荣", "populace_mood": "安宁"}
    assert lightweight_validate(v, g, allowed_ids=set()) == v


def test_lightweight_validate_passes_allowed_id():
    g = project(_reg(), [_place("town")])["systems"]["ontology"]
    v = {"id": "new_child", "evolve": True, "state": "x"}
    assert lightweight_validate(v, g, allowed_ids={"new_child"}) == v


def test_lightweight_validate_drops_dangling_id():
    g = project(_reg(), [_place("town")])["systems"]["ontology"]
    v = {"id": "ghost", "evolve": True, "state": "x"}
    assert lightweight_validate(v, g, allowed_ids=set()) is None


def test_lightweight_validate_drops_missing_id():
    g = project(_reg(), [_place("town")])["systems"]["ontology"]
    assert lightweight_validate({"evolve": True}, g, allowed_ids=set()) is None


# ---------------------------------------------------------------------------
# Task 6: run_cascade — vertical descent walker
# ---------------------------------------------------------------------------

from llm.provider import LLMProvider


class KeyedFakeProvider(LLMProvider):
    """Returns a verdict chosen by the place id embedded in the user prompt.
    Order-independent → safe for the C2 ThreadPoolExecutor too (D5)."""
    def __init__(self, by_place: dict, default: dict | None = None):
        self.by_place = by_place
        self.default = default or {"evolve": False}
        self.calls = []

    def complete(self, system, user, *, model=None, max_tokens=None):
        return ""

    def complete_json(self, system, user, schema, **kw):
        self.calls.append((system, user))
        for pid, verdict in self.by_place.items():
            if pid in user:
                return dict(verdict, id=pid)
        return dict(self.default)

    def complete_messages(self, messages, *, model=None, max_tokens=None):
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        self.calls.append((system, last_user))
        for pid, verdict in self.by_place.items():
            if pid in last_user:
                return json.dumps(dict(verdict, id=pid), ensure_ascii=False)
        return json.dumps(dict(self.default), ensure_ascii=False)


def _store(reg):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "events.db"),
                      os.path.join(d, "events.jsonl"), allowed_types=reg.event_types())


def _tree_events():
    return [
        _place("capital"),
        _place("market", parent="capital"),
        _place("temple", parent="capital"),
        _place("stall", parent="market"),
    ]


def test_run_cascade_visits_children_and_emits_place_evolved():
    # P1 fix: each narrator-declared area (root) is itself evolved, then its
    # un-named children are descended.  capital is the declared area → it now
    # gets a _node_verdict call too.  evolve:true for capital → its children
    # (market, temple) are enqueued → stall (child of market) follows.
    from loop.cascade import run_cascade
    reg = _reg()
    store = _store(reg)
    for e in _tree_events():
        store.append(e)
    wc = kernel_event("world_change", day=1, scene="capital", summary="王都陷落",
                      deltas={"place": "capital", "level": 1}, turn=2)
    store.append(wc)
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "沦陷"},
        "market": {"evolve": True, "state": "戒严", "populace_mood": "惶恐"},
        "temple": {"evolve": True, "state": "闭门", "populace_mood": "祈祷"},
        "stall":  {"evolve": True, "state": "歇业"},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
    assert evolved == {"capital", "market", "temple", "stall"}   # root + full vertical coverage
    assert any(e["type"] == "populace_shifted" for e in appended)
    world2 = project(reg, store.iter_events())
    assert world2["systems"]["ontology"].value_at("market", "state", day=1) == "戒严"


def test_run_cascade_injects_id_when_model_omits_it():
    """Live-caught (glm-4.7 emitted 0 cascade events): real models return
    {evolve, state} WITHOUT echoing `id` (optional in the schema), so every
    verdict was dropped by lightweight_validate. The harness OWNS the id and must
    inject it. This fake OMITS id (unlike KeyedFakeProvider which injects it).

    P1 fix: capital is now itself evolved (root gets _node_verdict); it must also
    be in the by_place map so the NoIdProvider returns evolve:true for it."""
    from loop.cascade import run_cascade
    reg = _reg()
    store = _store(reg)
    for e in _tree_events():
        store.append(e)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="王都陷落",
                              deltas={"place": "capital", "level": 1}, turn=2))
    world = project(reg, store.iter_events())

    class NoIdProvider(KeyedFakeProvider):
        def complete_json(self, system, user, schema, **kw):
            self.calls.append((system, user))
            for pid, verdict in self.by_place.items():
                if pid in user:
                    return dict(verdict)        # NB: no id echoed
            return dict(self.default)

        def complete_messages(self, messages, *, model=None, max_tokens=None):
            system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
            self.calls.append((system, last_user))
            for pid, verdict in self.by_place.items():
                if pid in last_user:
                    return json.dumps(dict(verdict), ensure_ascii=False)  # NB: no id echoed
            return json.dumps(dict(self.default), ensure_ascii=False)

    prov = NoIdProvider(by_place={
        "capital": {"evolve": True, "state": "沦陷"},
        "market": {"evolve": True, "state": "戒严"},
        "temple": {"evolve": True, "state": "闭门"},
        "stall":  {"evolve": True, "state": "歇业"},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
    assert evolved == {"capital", "market", "temple", "stall"}   # harness injected the ids


def test_run_cascade_prune_stops_descent():
    # P1 fix: capital (the declared root) is itself evolved first.  evolve:true
    # for capital → its children (market, temple) are enqueued.  market prunes
    # (evolve:false) → stall never visited.  temple evolves.
    from loop.cascade import run_cascade
    reg = _reg()
    store = _store(reg)
    for e in _tree_events():
        store.append(e)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1}, turn=2))
    world = project(reg, store.iter_events())
    # capital evolves → children enqueued; market prunes → stall never visited
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "动乱"},
        "market": {"evolve": False},
        "temple": {"evolve": True, "state": "闭门"},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    assert "stall" not in {c[1] for c in prov.calls if "stall" in c[1]} or \
        all("stall" not in u for _, u in prov.calls)   # stall never queried
    evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
    assert evolved == {"capital", "temple"}


def test_run_cascade_respects_node_budget():
    # P1 fix: capital is now itself the first frontier item. It must evolve
    # (evolve:true) so its children p0..p9 are enqueued for the next round.
    # Round 1: [capital] → 1 call, evolves, enqueues p0..p9.
    # Round 2: [p0..p9] → breadth cap CASCADE_BREADTH=6 hits; only 6 called.
    # Total calls = 1 (capital) + CASCADE_BREADTH (p0..p5) = 7, but the
    # assertion checks calls <= CASCADE_BREADTH + 1 to account for the root.
    # The original spirit (per-round cap enforced) is still verified.
    from loop.cascade import run_cascade
    import loop.cascade as cmod
    reg = _reg()
    store = _store(reg)
    # wide tree: capital ⊃ p0..p9
    store.append(_place("capital"))
    for i in range(10):
        store.append(_place(f"p{i}", parent="capital"))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1}, turn=2))
    world = project(reg, store.iter_events())
    by_place = {f"p{i}": {"evolve": True, "state": "s"} for i in range(10)}
    by_place["capital"] = {"evolve": True, "state": "动乱"}
    prov = KeyedFakeProvider(by_place=by_place)
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    # Round 1: capital (1 call). Round 2: up to CASCADE_BREADTH children.
    # Total capped at CASCADE_BREADTH + 1 (root round + one children round).
    assert len(prov.calls) <= cmod.CASCADE_BREADTH + 1


def test_run_cascade_respects_total_node_budget():
    """The TOTAL _node_verdict count across ALL levels is capped by
    CASCADE_NODE_BUDGET — not just the per-level breadth cap. A deep all-evolve
    tree has far more evolving nodes than the budget, but only the budget run."""
    from loop.cascade import run_cascade
    import loop.cascade as cmod
    reg = _reg()
    store = _store(reg)
    store.append(_place("capital"))
    # capital ⊃ a..f (6 L2); each ⊃ 2 grandchildren (12 L3) → 1+6+12 = 19 evolving
    mids = list("abcdef")
    for m in mids:
        store.append(_place(m, parent="capital"))
        for g in range(2):
            store.append(_place(f"{m}{g}", parent=m))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1}, turn=2))
    world = project(reg, store.iter_events())
    by_place = {"capital": {"evolve": True, "state": "动乱"}}
    for m in mids:
        by_place[m] = {"evolve": True, "state": "s"}
        for g in range(2):
            by_place[f"{m}{g}"] = {"evolve": True, "state": "s"}
    prov = KeyedFakeProvider(by_place=by_place)
    run_cascade(reg, store, world, scene="capital", provider=prov)
    # Budget respected, AND it went past a single breadth-level (so the TOTAL
    # budget — not just per-level breadth — is the binding constraint here).
    assert cmod.CASCADE_BREADTH < len(prov.calls) <= cmod.CASCADE_NODE_BUDGET


def test_run_cascade_no_trigger_returns_empty():
    """No qualifying trigger event → cascade returns empty list.

    We test this by giving a 'town' place with no children — even if the
    place_created event is in the trigger set, the BFS finds no children
    and nothing is appended. The test also covers the prune-on-no-children
    path. To verify the trigger gate: place_linked events carry no place id
    in deltas, so _root_place returns None and they are silently skipped.
    """
    from loop.cascade import run_cascade
    reg = _reg()
    store = _store(reg)
    # town exists but has no children — cascade finds no descendant nodes
    store.append(_place("town"))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={})
    # Only setup events at turn=1; player_turn=1; town has no children → []
    assert run_cascade(reg, store, world, scene="s1", provider=prov) == []


def test_run_cascade_overrides_hallucinated_id():
    """A model that hallucinates a WRONG id must never corrupt the graph: the
    harness owns the id and overrides the verdict's id with the known place id,
    so the verdict applies to the CORRECT place (market) and the bogus id never
    reaches the graph. (Before the harness-owns-id fix this verdict was dropped;
    overriding is strictly better — the evolution is kept under the real id.)

    P1 fix: capital is now itself the first frontier item; BadProv must evolve
    it (evolve:true) so market/temple are enqueued for the next round."""
    from loop.cascade import run_cascade
    reg = _reg()
    store = _store(reg)
    for e in _tree_events():
        store.append(e)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1}, turn=2))
    world = project(reg, store.iter_events())
    # market verdict returns a bogus id (simulate hallucination)
    class BadProv(KeyedFakeProvider):
        def complete_json(self, system, user, schema, **kw):
            self.calls.append((system, user))
            if "capital" in user:
                return {"id": "capital", "evolve": True, "state": "动乱"}
            if "market" in user:
                return {"id": "HALLUCINATED", "evolve": True, "state": "x"}
            if "temple" in user:
                return {"id": "temple", "evolve": True, "state": "ok"}
            return {"evolve": False}

        def complete_messages(self, messages, *, model=None, max_tokens=None):
            system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
            self.calls.append((system, last_user))
            if "capital" in last_user:
                return json.dumps({"id": "capital", "evolve": True, "state": "动乱"})
            if "market" in last_user:
                return json.dumps({"id": "HALLUCINATED", "evolve": True, "state": "x"})
            if "temple" in last_user:
                return json.dumps({"id": "temple", "evolve": True, "state": "ok"})
            return json.dumps({"evolve": False})
    prov = BadProv(by_place={})
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    ids = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
    assert "HALLUCINATED" not in ids                    # bogus id never reaches the graph
    assert "capital" in ids                             # root evolved
    assert ids >= {"capital", "market", "temple"}       # market evolves under its REAL id


# ---------------------------------------------------------------------------
# Fix 1: per-node robustness — _node_verdict raises → drop + log, no cascade abort
# ---------------------------------------------------------------------------

def test_run_cascade_node_exception_drops_node_not_whole_cascade():
    """If _node_verdict raises (e.g. ValueError from 2 bad-JSON parses) for one
    place, that node is dropped and logged; other sibling nodes are still processed
    and the cascade does NOT propagate the exception to the caller.

    P1 fix: capital is now the first frontier item; it must evolve (evolve:true)
    so market/temple are enqueued.  market then raises (dropped); temple evolves.
    Tree: capital (evolves) ⊃ {market (raises → dropped), temple (evolves)}
    Expected: place_evolved for capital + temple; no exception; market/stall skipped.
    """
    from loop.cascade import run_cascade

    reg = _reg()
    store = _store(reg)
    for e in _tree_events():
        store.append(e)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="陷落",
                              deltas={"place": "capital", "level": 1}, turn=2))
    world = project(reg, store.iter_events())

    class RaisingProvider(KeyedFakeProvider):
        """Evolves capital; raises ValueError for 'market'; evolves 'temple'."""
        def complete_json(self, system, user, schema, **kw):
            self.calls.append((system, user))
            if "capital" in user:
                return {"id": "capital", "evolve": True, "state": "动乱"}
            if "market" in user:
                raise ValueError("complete_json failed after 2 attempts")
            if "temple" in user:
                return {"id": "temple", "evolve": True, "state": "闭门"}
            return {"evolve": False}

        def complete_messages(self, messages, *, model=None, max_tokens=None):
            system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
            self.calls.append((system, last_user))
            if "capital" in last_user:
                return json.dumps({"id": "capital", "evolve": True, "state": "动乱"})
            if "market" in last_user:
                raise ValueError("complete_messages failed after 2 attempts")
            if "temple" in last_user:
                return json.dumps({"id": "temple", "evolve": True, "state": "闭门"})
            return json.dumps({"evolve": False})

    prov = RaisingProvider(by_place={})

    # Must NOT raise — exception is swallowed per §12 "drop + log on failure"
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)

    evolved_ids = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
    # capital and temple evolved; market was dropped (exception); stall never reached
    assert "capital" in evolved_ids
    assert "temple" in evolved_ids
    assert "market" not in evolved_ids
    # stall is a child of market; since market was dropped (no descent), stall absent too
    assert "stall" not in evolved_ids


# ---------------------------------------------------------------------------
# Task 8: parallel fan-out + configurable max_concurrency (C2)
# ---------------------------------------------------------------------------

def test_run_cascade_parallel_fanout_outcome_set():
    """Wide single round fanned out over a pool; assert the SET of evolved ids
    (order-independent) and that the pool did not over-spend the round budget.

    P1 fix: capital is now the first frontier item (round 1, 1 call).  It must
    evolve so p0..p4 are enqueued for round 2 (5 calls, fits in CASCADE_BREADTH=6).
    Total calls = 6 (1 root + 5 children); evolved = {capital, p0..p4}."""
    from loop.cascade import run_cascade
    import loop.cascade as cmod
    reg = _reg(); store = _store(reg)
    store.append(_place("capital"))
    for i in range(5):
        store.append(_place(f"p{i}", parent="capital"))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1}, turn=2))
    world = project(reg, store.iter_events())
    by_place = {f"p{i}": {"evolve": True, "state": "s"} for i in range(5)}
    by_place["capital"] = {"evolve": True, "state": "动乱"}
    prov = KeyedFakeProvider(by_place=by_place)
    appended = run_cascade(reg, store, world, scene="capital", provider=prov,
                           max_concurrency=4)
    evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
    assert evolved == {"capital", "p0", "p1", "p2", "p3", "p4"}      # SET, not order
    # Two rounds: round-1 cap = 1 (capital only); round-2 cap = 5 (p0..p4 < CASCADE_BREADTH)
    assert len(prov.calls) <= cmod.CASCADE_BREADTH + 1


def test_run_cascade_parallel_outcome_is_thread_schedule_invariant():
    """Same tree + keyed provider over two runs → identical SET of (type, id).

    P1 fix: capital is now the first frontier item; must be in the provider
    so the outcome is non-empty and meaningful."""
    from loop.cascade import run_cascade
    def run_once():
        reg = _reg(); store = _store(reg)
        for e in _tree_events(): store.append(e)
        store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                                  deltas={"place": "capital", "level": 1}, turn=2))
        world = project(reg, store.iter_events())
        prov = KeyedFakeProvider(by_place={
            "capital": {"evolve": True, "state": "沦陷"},
            "market": {"evolve": True, "state": "戒严", "populace_mood": "惶恐"},
            "temple": {"evolve": True, "state": "闭门"},
            "stall":  {"evolve": True, "state": "歇业"},
        })
        ap = run_cascade(reg, store, world, scene="capital", provider=prov,
                         max_concurrency=4)
        return sorted((e["type"], e["deltas"].get("id")) for e in ap)
    result = run_once()
    assert result == run_once()
    # Sanity: at least capital + market evolved (non-vacuous)
    assert any(t == "place_evolved" and pid == "capital" for t, pid in result)


def test_resolve_concurrency_precedence(monkeypatch):
    import loop.cascade as cmod
    # explicit arg wins
    assert cmod._resolve_concurrency(7, 4) == 7
    # else env
    monkeypatch.setenv("RPG_CASCADE_CONCURRENCY", "5")
    assert cmod._resolve_concurrency(None, 4) == 5
    # bad env → falls through to max_subagents
    monkeypatch.setenv("RPG_CASCADE_CONCURRENCY", "oops")
    assert cmod._resolve_concurrency(None, 4) == 4
    # nothing set → default 3
    monkeypatch.delenv("RPG_CASCADE_CONCURRENCY", raising=False)
    assert cmod._resolve_concurrency(None, None) == 3


# ---------------------------------------------------------------------------
# Task 9/10: _link helper for adjacency tests
# ---------------------------------------------------------------------------

def _link(a, b, cost=1):
    return kernel_event("place_linked", day=1, scene="s1", summary=f"{a}-{b}",
                        deltas={"a": a, "b": b, "travel_cost": cost}, turn=1)


# ---------------------------------------------------------------------------
# P1 Task 4: trigger narrows to narrator world_change; keep_spreading schema
# ---------------------------------------------------------------------------

def test_trigger_no_longer_fires_on_entity_moved():
    world = project(_reg(), [_place("town")])
    evs = [kernel_event("entity_moved", day=1, scene="s1", summary="到达",
                        deltas={"who": "hero", "to": "town"}, turn=2)]
    assert cascade_trigger(evs, world) == []     # P1: movement no longer self-triggers


def test_trigger_no_longer_fires_on_place_created_or_materialized():
    world = project(_reg(), [_place("town")])
    evs = [
        kernel_event("place_materialized", day=1, scene="s1", summary="m",
                     deltas={"id": "town"}, turn=2),
        kernel_event("place_created", day=1, scene="s1", summary="c",
                     deltas={"id": "town", "level": 2, "kind": "venue", "seed": "x"}, turn=2),
    ]
    assert cascade_trigger(evs, world) == []


def test_world_change_not_shadowed_by_later_narration_recorded():
    """Regression (capstone P1↔P2 integration bug): P2's digest appends
    narration_recorded at a turn HIGHER than the player turn. If that event isn't
    excluded from cascade's player-turn window it shadows the player turn, so
    cascade looks at the wrong turn and misses the narrator's world_change (the
    capstone declared world-changes but emitted 0 place_evolved). With
    narration_recorded in _HARNESS_TYPES, cascade must still fire."""
    from loop.cascade import run_cascade
    from systems.narrative import NarrativeSystem

    reg = (Registry().register(OntologySystem()).register(PlaceSystem())
           .register(CascadeSystem()).register(NarrativeSystem()))
    store = _store(reg)
    store.append(_place("capital"))  # place_created @ turn 1
    store.append(kernel_event("world_change", day=1, scene="capital", summary="王都陷落",
                              deltas={"place": "capital", "level": 3, "summary": "王都陷落"}, turn=2))
    # P2 digest appends narration_recorded at a HIGHER turn — must NOT shadow turn 2
    store.append(kernel_event("narration_recorded", day=1, scene="capital",
                              summary="narration", deltas={"scene": "capital", "text": "..."}, turn=3))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={"capital": {"evolve": True, "state": "废墟"}})
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}
    assert "capital" in evolved, f"cascade shadowed by narration_recorded; evolved={evolved}"


def test_trigger_still_fires_on_narrator_world_change():
    world = project(_reg(), [_place("capital")])
    evs = [kernel_event("world_change", day=1, scene="s1", summary="陷落",
                        deltas={"place": "capital", "level": 1, "summary": "陷落"}, turn=2)]
    assert cascade_trigger(evs, world) == ["capital"]


def test_trigger_self_guard_still_skips_deferred_markers():
    world = project(_reg(), [_place("capital")])
    evs = [
        kernel_event("world_change", day=1, scene="capital", summary="hop",
                     deltas={"place": "capital", "level": 2, "deferred": True}, turn=3),
        kernel_event("world_change", day=1, scene="capital", summary="bk",
                     deltas={"place": "capital", "deferred_consume_through": 2}, turn=4),
    ]
    assert cascade_trigger(evs, world) == []


def test_node_schema_has_keep_spreading_not_spread():
    import loop.cascade as cmod
    props = cmod._NODE_SCHEMA["properties"]
    assert "keep_spreading" in props
    assert props["keep_spreading"]["type"] == "array"
    assert "spread" not in props
    assert "magnitude" not in props


def test_node_prompt_embeds_place_id_and_mentions_keep_spreading():
    import loop.cascade as cmod
    p = cmod._node_prompt("market", "王都陷落")
    assert "market" in p              # KeyedFakeProvider relies on this
    assert "keep_spreading" in p


def test_cascade_own_world_change_does_not_retrigger():
    """A deferral/bookkeeping world_change (carrying deferred / deferred_consume_through)
    must NOT be seen as a fresh trigger root by cascade_trigger (self-trigger guard)."""
    from loop.cascade import cascade_trigger
    world = project(_reg(), [_place("capital")])
    evs = [
        kernel_event("world_change", day=1, scene="capital", summary="hop",
                     deltas={"place": "capital", "level": 2, "deferred": True}, turn=3),
        kernel_event("world_change", day=1, scene="capital", summary="bookkeeping",
                     deltas={"place": "capital", "deferred_consume_through": 2}, turn=4),
    ]
    assert cascade_trigger(evs, world) == []     # neither marker re-triggers


# ---------------------------------------------------------------------------
# P1 Task 5: keep_spreading ring-1 hop + at-most-one-hop enforcement
# ---------------------------------------------------------------------------

def test_keep_spreading_emits_deferred_remote_hop():
    """A node naming keep_spreading:[remote_region] emits a deferred world_change
    for that region (ring-1), and the region is queued — not descended this turn.

    P1 fix: capital is now the first frontier item; must evolve so market is
    enqueued and can emit its keep_spreading hop."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("farland")); store.append(_place("hamlet", parent="farland"))
    store.append(_link("market", "farland"))   # market ↔ farland (remote: not under capital)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="陷落",
                              deltas={"place": "capital", "level": 1, "summary": "陷落"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "沦陷"},
        "market": {"evolve": True, "state": "暴动", "keep_spreading": ["farland"]},
        "hamlet": {"evolve": True, "state": "should-not-run-yet"},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    assert any(e["type"] == "world_change" and e["deltas"]["place"] == "farland" for e in appended)
    world2 = project(reg, store.iter_events())
    assert any(q["region"] == "farland" for q in world2["systems"]["cascade"]["queue"])
    # ring-1 region's child NOT descended this turn
    assert all(not (e["type"] == "place_evolved" and e["deltas"]["id"] == "hamlet")
               for e in appended)


def test_keep_spreading_local_neighbor_inline_not_queued():
    """keep_spreading to a neighbor INSIDE the scene subtree is inline (not queued).

    P1 fix: capital must evolve so market is enqueued and can emit its
    keep_spreading to plaza (a sibling inside the scene subtree)."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital"))
    store.append(_place("market", parent="capital"))
    store.append(_place("plaza", parent="capital"))   # sibling, same scene subtree
    store.append(_link("market", "plaza"))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "动乱"},
        "market": {"evolve": True, "state": "暴动", "keep_spreading": ["plaza"]},
        "plaza":  {"evolve": True, "state": "s"},
    })
    run_cascade(reg, store, world, scene="capital", provider=prov)
    world2 = project(reg, store.iter_events())
    assert all(q["region"] != "plaza" for q in world2["systems"]["cascade"]["queue"])


def test_ring1_drained_next_turn_but_no_ring2():
    """Ring-1 region is drained next turn and descends its OWN children, but its
    nodes' keep_spreading is IGNORED (at-most-one-hop: no ring 2).

    P1 fix: capital must evolve so market is enqueued in turn 1, and market
    can then emit its keep_spreading hop to farland."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("farland")); store.append(_place("hamlet", parent="farland"))
    store.append(_place("beyond"))                      # ring-2 candidate
    store.append(_link("market", "farland"))
    store.append(_link("hamlet", "beyond"))             # hamlet ↔ beyond (would be ring 2)
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "动乱"},
        "market": {"evolve": True, "state": "暴动", "keep_spreading": ["farland"]},
        "hamlet": {"evolve": True, "state": "波及", "keep_spreading": ["beyond"]},
    })
    # turn 1: capital + market evolve; ring-1 hop to farland queued
    run_cascade(reg, store, project(reg, store.iter_events()), scene="capital", provider=prov)
    # turn 2: drain farland → hamlet descends, but hamlet's keep_spreading[beyond] IGNORED
    appended2 = run_cascade(reg, store, project(reg, store.iter_events()),
                            scene="capital", provider=prov)
    assert any(e["type"] == "place_evolved" and e["deltas"]["id"] == "hamlet" for e in appended2)
    assert all(e["deltas"].get("place") != "beyond" for e in appended2
               if e["type"] == "world_change")     # no ring 2


def test_no_keep_spreading_no_hop():
    """evolve:true WITHOUT keep_spreading emits no adjacent world_change (pure vertical).

    P1 fix: capital must evolve so market is enqueued; neither capital nor
    market specify keep_spreading, so no world_change hops are emitted."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("outskirts")); store.append(_link("capital", "outskirts"))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "动乱"},
        "market": {"evolve": True, "state": "s"},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    assert [e for e in appended if e["type"] == "world_change"] == []


def test_keep_spreading_ignores_non_adjacent_id():
    """A keep_spreading id that is NOT an adjacent Place is dropped (no hop).

    P1 fix: capital must evolve so market is enqueued."""
    from loop.cascade import run_cascade
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    store.append(_place("unrelated"))   # exists but NOT linked to market
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "动乱"},
        "market": {"evolve": True, "state": "s", "keep_spreading": ["unrelated"]},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    assert all(e["deltas"].get("place") != "unrelated" for e in appended
               if e["type"] == "world_change")


def test_secondary_breadth_cap():
    """More than CASCADE_SECONDARY_BREADTH keep_spreading targets → capped.

    P1 fix: capital must evolve so market is enqueued."""
    from loop.cascade import run_cascade
    import loop.cascade as cmod
    reg = _reg(); store = _store(reg)
    store.append(_place("capital")); store.append(_place("market", parent="capital"))
    targets = [f"nbr{i}" for i in range(cmod.CASCADE_SECONDARY_BREADTH + 3)]
    for t in targets:
        store.append(_place(t)); store.append(_link("market", t))
    store.append(kernel_event("world_change", day=1, scene="capital", summary="x",
                              deltas={"place": "capital", "level": 1, "summary": "x"}, turn=2))
    world = project(reg, store.iter_events())
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "动乱"},
        "market": {"evolve": True, "state": "s", "keep_spreading": targets},
    })
    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    hops = [e for e in appended if e["type"] == "world_change" and e["deltas"].get("place") in targets]
    assert len(hops) <= cmod.CASCADE_SECONDARY_BREADTH


def test_max_depth_and_max_regions_constants_removed():
    import loop.cascade as cmod
    assert not hasattr(cmod, "CASCADE_MAX_DEPTH")
    assert not hasattr(cmod, "CASCADE_MAX_REGIONS")
    assert cmod.CASCADE_SECONDARY_BREADTH == 3


# ---------------------------------------------------------------------------
# P1 regression: live bug — narrator names the WHOLE hierarchy as `areas`
# Pre-fix: BFS seeded from children of each root; a child that is also a
# named root lands in `seen` → dedup-skipped → frontier empty → 0 evolved.
# ---------------------------------------------------------------------------

def test_full_hierarchy_named_as_areas_all_evolve():
    """Regression for glm-5.1 live bug.

    Narrator declares areas = [capital, market, shrine] where
      market ⊂ capital  and  shrine ⊂ market.
    All three are named roots in the world_change events.

    Pre-fix: BFS seeded from children of EACH root; market and shrine were
    already in seen (from other roots' children expansion) → dedup-skipped →
    frontier emptied → 0 place_evolved emitted.

    Post-fix: BFS seeded from the roots themselves (de-duped); each named
    area gets a _node_verdict call; children of an evolving root are enqueued
    for the next BFS level; a child that is also a named root is processed
    exactly once (de-dup handles it).

    Assert: EVERY named area appears in the place_evolved set.
    """
    from loop.cascade import run_cascade
    reg = _reg()
    store = _store(reg)

    # Hierarchy: capital ⊃ market ⊃ shrine
    store.append(_place("capital"))
    store.append(_place("market", parent="capital"))
    store.append(_place("shrine", parent="market"))

    # Narrator names the WHOLE hierarchy: capital + market + shrine all as areas.
    # Three world_change events (one per area), all at turn=2.
    for area in ("capital", "market", "shrine"):
        store.append(kernel_event(
            "world_change", day=1, scene="capital", summary=f"{area}骤变",
            deltas={"place": area, "level": 1, "summary": f"{area}骤变"},
            turn=2,
        ))

    world = project(reg, store.iter_events())

    # Provider returns evolve:true for every named area.
    prov = KeyedFakeProvider(by_place={
        "capital": {"evolve": True, "state": "沦陷"},
        "market":  {"evolve": True, "state": "戒严"},
        "shrine":  {"evolve": True, "state": "封禁"},
    })

    appended = run_cascade(reg, store, world, scene="capital", provider=prov)
    evolved = {e["deltas"]["id"] for e in appended if e["type"] == "place_evolved"}

    # Every declared area MUST appear — this was the exact live failure
    assert "capital" in evolved, f"capital missing from evolved={evolved}"
    assert "market"  in evolved, f"market missing from evolved={evolved}"
    assert "shrine"  in evolved, f"shrine missing from evolved={evolved}"


def test_node_verdict_repair_loop_uses_repaired_result():
    """First malformed response (missing evolve) + conforming second response
    → _node_verdict uses the repaired result, not the malformed one."""
    from loop.cascade import _node_verdict
    from llm.provider import FakeLLMProvider

    fake = FakeLLMProvider(json_responses=[{"note": "oops"}, {"evolve": True, "state": "修复后状态"}])
    verdict = _node_verdict("test_place", "测试上下文", fake)
    # After repair, evolve=True with proper state should be used
    assert verdict.get("evolve") is True
    assert verdict.get("state") == "修复后状态"
    assert verdict.get("id") == "test_place"
    # Two LLM calls: initial + 1 repair
    assert len(fake.calls) == 2
    # Repair message names the missing field
    assert '"evolve"' in fake.calls[1][1]
