"""Tests for loop.director.run_director (Phase B1)."""
from __future__ import annotations

import tempfile, os

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import open_store, kernel_event
from systems.ontology import OntologySystem
from systems.director import DirectorSystem
from loop.director import run_director


def _reg():
    return Registry().register(OntologySystem()).register(DirectorSystem())


def _store(reg):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "events.db"), os.path.join(d, "events.jsonl"),
                      allowed_types=reg.event_types())


def _seed_event(seed=999):
    return kernel_event("campaign_seeded", day=1, scene="genesis",
                        summary="seed", deltas={"campaign_seed": seed}, turn=0)


def _action(turn, scene, day=1):
    return kernel_event("entity_created", day=day, scene=scene,
                        summary="x", deltas={"id": f"e{turn}", "etype": "Object"}, turn=turn)


def _find_fire_seed():
    """Find a campaign_seed that fires at scene_ordinal with high scenes_since_event.
    run_director is deterministic given (campaign_seed, scene_ordinal), so we scan
    seeds offline to get a deterministic 'will fire' fixture."""
    for seed in range(2000):
        reg = _reg()
        store = _store(reg)
        store.append(_seed_event(seed))
        # build several distinct scenes with no director_fired → scenes_since_event high
        for i in range(1, 7):
            store.append(_action(i, f"s{i}"))
        world = project(reg, store.iter_events())
        appended = run_director(reg, store, world)
        if any(e["type"] == "director_fired" for e in appended):
            return seed
    raise AssertionError("no firing seed found in range")


def test_run_director_deterministic_same_seed():
    reg1, reg2 = _reg(), _reg()
    s1, s2 = _store(reg1), _store(reg2)
    for st in (s1, s2):
        st.append(_seed_event(777))
        for i in range(1, 7):
            st.append(_action(i, f"s{i}"))
    w1 = project(reg1, s1.iter_events())
    w2 = project(reg2, s2.iter_events())
    a1 = [(e["type"], e["deltas"]) for e in run_director(reg1, s1, w1)]
    a2 = [(e["type"], e["deltas"]) for e in run_director(reg2, s2, w2)]
    assert a1 == a2  # same seed + same stream → identical outcome


def test_run_director_appends_audit_and_directive_on_fire():
    seed = _find_fire_seed()
    reg = _reg()
    store = _store(reg)
    store.append(_seed_event(seed))
    for i in range(1, 7):
        store.append(_action(i, f"s{i}"))
    world = project(reg, store.iter_events())
    appended = run_director(reg, store, world)
    types = [e["type"] for e in appended]
    assert "oracle_roll" in types          # audit event
    assert "director_fired" in types       # the directive
    # events were accepted by the strict store
    fired = next(e for e in appended if e["type"] == "director_fired")
    assert fired["deltas"]["event_type"] in ("危机", "机遇", "人物", "世界", "羁绊")
    assert "twist" in fired["deltas"]


def test_run_director_never_two_turns_in_a_row():
    """If the immediately-preceding turn already fired, skip the roll this turn."""
    seed = _find_fire_seed()
    reg = _reg()
    store = _store(reg)
    store.append(_seed_event(seed))
    for i in range(1, 7):
        store.append(_action(i, f"s{i}"))
    # simulate that the last turn already fired
    store.append(kernel_event("director_fired", day=1, scene="s6",
                              summary="prior fire",
                              deltas={"type": "front_stage", "magnitude": "small",
                                      "event_type": "机遇", "twist": "无反转"}, turn=6))
    world = project(reg, store.iter_events())
    appended = run_director(reg, store, world)
    assert appended == []  # guard: no fire right after a fire


def test_run_director_skips_when_prior_player_turn_fired():
    """Real-flow guard (regression for the off-by-one a live run exposed): a fire
    occupies its own turn slot, so the next player turn applies at fire_turn+1 and
    the prior fire sits at last_turn-1. The guard must still suppress a re-fire."""
    reg = _reg()
    store = _store(reg)
    store.append(_seed_event(123))
    for i in range(1, 6):
        store.append(_action(i, f"s{i}"))           # player turns 1..5
    store.append(kernel_event("director_fired", day=1, scene="s5",
                              summary="prior fire",
                              deltas={"type": "front_stage", "magnitude": "small",
                                      "event_type": "机遇", "twist": "无反转"}, turn=5))
    store.append(_action(6, "s6"))                  # current player turn at turn 6 (= fire+1)
    world = project(reg, store.iter_events())
    appended = run_director(reg, store, world)
    # fire at turn 5 == last_turn(6) - 1 → suppressed even though it is NOT the max turn
    assert appended == []


def test_director_not_frozen_under_static_scene():
    """Regression (a live 6-turn run caught this): this engine has no scene
    progression, so the scene id is static across turns and scene_ordinal is
    constant. Pre-fix the Oracle was seeded ONLY on scene_ordinal → the SAME roll
    every turn → the director fired either always or never for a whole campaign.
    The fix salts the seed with the turn number. Build INDEPENDENT stores of
    increasing length (no prior director_fired, so the never-two-in-a-row guard
    never interferes) sharing one static scene; the per-turn decision must NOT be
    identical for every length."""
    for camp_seed in range(80):
        decisions = []
        for nturns in range(1, 14):
            reg = _reg()
            store = _store(reg)
            store.append(_seed_event(camp_seed))
            for t in range(1, nturns + 1):
                store.append(kernel_event("entity_created", day=1, scene="town",
                                          summary="x",
                                          deltas={"id": f"e{t}", "etype": "Object"}, turn=t))
            world = project(reg, store.iter_events())
            decisions.append(bool(run_director(reg, store, world)))
        if len(set(decisions)) == 2:
            return  # this campaign both fires and stays quiet across static-scene turns
    raise AssertionError("director appears frozen under a static scene for all tested seeds")


def test_run_director_quiet_appends_nothing():
    """A seed that doesn't fire appends no events (audit-only-on-fire)."""
    # scenes_since_event small → low prob; scan for a quiet seed at ordinal 1.
    for seed in range(2000):
        reg = _reg(); store = _store(reg)
        store.append(_seed_event(seed))
        store.append(_action(1, "s1"))
        world = project(reg, store.iter_events())
        appended = run_director(reg, store, world)
        if appended == []:
            break
    else:
        raise AssertionError("no quiet seed found")
    assert appended == []


def _fire_dormant_stub(monkeypatch):
    import loop.director as dirmod
    def _dormant_fire(scenes_since, tension, oracle, *, tables):
        et = tables["event_types"][0]; tw = tables["twists"][0]
        return {"triggered": True, "type": "dormant_thread", "magnitude": "small",
                "valence": None, "seed": {"event_type": et, "twist": tw},
                "prob": 0.6, "roll": 0.1}
    monkeypatch.setattr(dirmod, "director_check", _dormant_fire)


def test_dormant_fire_opens_thread_when_under_band(monkeypatch):
    _fire_dormant_stub(monkeypatch)
    reg = _reg(); store = _store(reg)
    store.append(_seed_event(5))
    for i in range(1, 7):
        store.append(_action(i, f"s{i}"))
    world = project(reg, store.iter_events())
    appended = run_director(reg, store, world)
    # under the 3-thread floor with no existing threads → opens a thread_open (dormant)
    assert any(e["type"] == "thread_open" for e in appended)
    assert any(e["type"] == "oracle_roll" for e in appended)
    world2 = project(reg, store.iter_events())
    assert len(world2["systems"]["director"]["threads"]) >= 1


def test_dormant_fire_advances_due_thread_when_band_full(monkeypatch):
    _fire_dormant_stub(monkeypatch)
    reg = _reg(); store = _store(reg)
    store.append(_seed_event(5))
    # pre-open 3 active (non-dormant) threads that are overdue → pick_thread_to_advance fires
    for n, sp in enumerate(("快", "快", "快"), start=1):
        store.append(kernel_event("thread_open", day=1, scene="s1", summary="t",
                     deltas={"id": f"pre{n}", "status": "活跃", "speed": sp,
                             "dormant": False, "trait": f"x{n}", "archetype": f"a{n}",
                             "last_advanced_scene": "s1"}, turn=1))
    for i in range(2, 9):
        store.append(_action(i, f"s{i}"))  # many scenes pass → threads overdue
    world = project(reg, store.iter_events())
    appended = run_director(reg, store, world)
    assert any(e["type"] == "thread_advance" for e in appended)


def test_consumed_directive_not_reinjected_after_reproject():
    """Regression: consumed flag must be event-sourced, not just in-memory.

    Full flow:
      Turn N:   run_director fires a directive (director_fired event stored).
      Turn N+1: run_director runs again; at the START it marks prior pending
                directives consumed. With the old code, that was an in-memory
                mutation only. After a fresh project() from the event log the
                mutation is lost, so inject() resurfaces the directive.
    After the fix, run_director emits a directive_consumed event that folds
    the watermark into the world on every project() call, so the directive
    stays hidden after reproject.
    """
    seed = _find_fire_seed()
    reg = _reg()
    store = _store(reg)
    store.append(_seed_event(seed))
    for i in range(1, 7):
        store.append(_action(i, f"s{i}"))

    # Turn N: fire the directive
    world = project(reg, store.iter_events())
    appended = run_director(reg, store, world)
    assert any(e["type"] == "director_fired" for e in appended), \
        "seed must fire a directive for this regression test to be meaningful"

    # After firing, inject() should show the directive (correct for turn N).
    world_n = project(reg, store.iter_events())
    ds = DirectorSystem()
    scene = {"protagonist": "hero", "day": 1, "id": "s6", "location": "s6"}
    frag_n = ds.inject(scene, world_n)
    assert frag_n is not None, "directive should be visible on the turn it was fired"

    # Turn N+1: run_director runs again. At the start it consumes prior pending.
    # Add a player action so the backstop guard doesn't immediately suppress.
    store.append(_action(8, "s8"))
    world_n1 = project(reg, store.iter_events())
    run_director(reg, store, world_n1)

    # Now simulate a reproject (as happens in run_turn or on rewind/replay).
    world_after = project(reg, store.iter_events())

    # INVARIANT: the directive consumed at Turn N+1 must NOT reappear after
    # reproject. Before the fix this assertion fails because project() reset
    # consumed=False; after the fix directive_consumed event folds the watermark.
    frag_after = ds.inject(scene, world_after)
    assert frag_after is None, (
        "replay-safety bug: consumed directive reappeared after project(). "
        "consumed state must be event-sourced via directive_consumed event, "
        "not held only in the in-memory world dict."
    )
