"""tests/loop/test_density_e2e.py — End-to-end offline pipeline test for density generation.

Covers the full lifecycle: auto-seeding -> 暗骰 brewing -> ambient disclosure ->
determinism / rewind-safety -> region-less degradation -> fault-tolerance -> refresh.

World layout (region-less, matching the unified-quest demo pattern):
    L2 青石镇 (no L1 parent) -> L3 venues: 市集, 酒馆, 码头
No lore lines are pre-seeded. The first run_turn where the protagonist enters
the town triggers auto-generation via run_density.

FakeLLMProvider wiring:
    narrator_fake:  one FakeLLMProvider with json_responses=[narrator_commit, ...]
                    AuthorStrategy.produce -> complete_messages -> uses _json_responses (cycles)
    cascade_fake:   separate FakeLLMProvider with json_responses=[batch_response, ...]
                    generate_lore_batch -> complete_json -> uses _json_responses (cycles)

    Using SEPARATE providers is essential: complete_messages and complete_json both
    consume from _json_responses and advance _json_idx, so mixing them in one
    provider requires precise call-count knowledge of every backstage hook.

Pinned values (SEED=20260621, town=青石镇, density=0.3, BASE=10):
    target = round(0.3 * 10) = 3 slots
    slot 0: Oracle(scene_seed(20260621,'density:青石镇:0',0)).d100()=14  -> simple
    slot 1: Oracle(scene_seed(20260621,'density:青石镇:1',0)).d100()=78  -> medium
    slot 2: Oracle(scene_seed(20260621,'density:青石镇:2',0)).d100()=85  -> medium
    expected_complexities = ['simple', 'medium', 'medium']
"""
from __future__ import annotations

import os
import tempfile

import pytest

from app.engine import build_engine
from app.play import _build_scene
from kernel.projection import project
from kernel.events import kernel_event
from loop.turn import run_turn
from loop.lore import run_lore
from loop.lore_disclosure import station_push_fragment
from loop.strategy import AuthorStrategy
from loop.density import BASE, REFRESH_INTERVAL_DAYS
from llm.provider import FakeLLMProvider


def _make_refresh_batch(venue="市集"):
    """Build a 1-line batch whose 'about' is distinct from the seed batch.

    The seed batch uses about="暗线{i}的故事"; using "刷新暗线0的故事" here
    ensures the refresh-spawned line gets a different deterministic id.
    """
    return {
        "lines": [{
            "about": "刷新暗线0的故事",
            "secret": "刷新秘密",
            "description": "刷新描述",
            "trigger": "刷新触发",
            "l3_anchor": venue,
            "stages": [{"hint": "刷新线索a"}, {"hint": "刷新线索b"}],
        }]
    }

# ---------------------------------------------------------------------------
# Pinned determinism constants (pre-computed offline)
# ---------------------------------------------------------------------------
SEED = 20260621
TOWN = "青石镇"
VENUES = ["市集", "酒馆", "码头"]
EXPECTED_COMPLEXITIES = ["simple", "medium", "medium"]  # computed from oracle roll 14/78/85


# ---------------------------------------------------------------------------
# Helpers: world seeding (region-less)
# ---------------------------------------------------------------------------

def _seed_events(eng, *, campaign_seed=SEED):
    """Seed a region-less world: L2 town + 3 L3 venues + protagonist outside town.

    Mirrors the unified-quest demo's _seed_events approach.
    No pre-seeded lore lines -- density generation must create them.
    """
    def place(pid, level, kind, seed, parent=None):
        d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
        if parent:
            d["parent"] = parent
        eng.store.append(kernel_event("place_created", day=1, scene="s1",
                                     summary=pid, deltas=d, turn=0))

    # campaign_seeded event (meta system reads campaign_seed from here)
    eng.store.append(kernel_event("campaign_seeded", day=1, scene="s1",
                                  summary="seed", deltas={"campaign_seed": campaign_seed},
                                  turn=0))

    # L2 town with NO L1 parent -> region-less
    place(TOWN, 2, "settlement", "雨季前的边陲集镇")
    for venue, vseed in [("市集", "嘈杂的露天集市"), ("酒馆", "镇口悦来酒馆"),
                          ("码头", "镇东旧码头")]:
        place(venue, 3, "venue", vseed, parent=TOWN)

    # Protagonist starts at an orphan L3 (outside town, so first move INTO town triggers seeding)
    place("路边驿站", 3, "venue", "镇外的小驿站")  # no parent -> orphan L3

    eng.store.append(kernel_event("character_created", day=1, scene="s1",
                                  summary="主角",
                                  deltas={"id": "主角", "tier": "tracked",
                                          "sketch": "游历至此的江湖客",
                                          "goal": "讨生活、管闲事"},
                                  turn=0))
    eng.store.append(kernel_event("entity_moved", day=1, scene="s1",
                                  summary="到路边驿站",
                                  deltas={"who": "主角", "to": "路边驿站"},
                                  turn=0))
    eng.world = project(eng.registry, eng.store.iter_events())


def _make_narrator_commit(to=None, days=0, bands=0):
    """Minimal narrator commit dict (all required sections present)."""
    commit = {
        "narration": "回合叙事文本。",
        "moves": [],
        "places": [],
        "cast": [],
        "facts": [],
        "clock": [{"advance": days > 0 or bands > 0, "days": days,
                   "bands": bands, "reason": "时间推进"}],
    }
    if to:
        commit["moves"] = [{"who": "主角", "to": to}]
    return commit


def _make_batch(n, venues=VENUES):
    """Build a canned {'lines': [...]} batch response for n skeletons."""
    lines = []
    for i in range(n):
        lines.append({
            "about": f"暗线{i}的故事",
            "secret": f"秘密{i}",
            "description": f"描述{i}",
            "trigger": f"触发{i}",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"线索{i}a"}, {"hint": f"线索{i}b"},
                       {"hint": f"线索{i}c"}],  # 3 hints; truncated by spec's stage_count
        })
    return {"lines": lines}


def _build_eng(*, seed=SEED):
    """Build a fresh Engine with FakeLLMProvider (for narrator) and empty world."""
    camp = tempfile.mkdtemp(prefix="density_e2e_")
    # Build with FakeLLMProvider as the base provider; we override cascade_provider per test
    narrator_fake = FakeLLMProvider(json_responses=[])
    eng = build_engine(camp, provider=narrator_fake)
    eng.campaign_seed = seed
    return eng


# ---------------------------------------------------------------------------
# Test A: Auto-seed -- first run_turn in town spawns lines
# ---------------------------------------------------------------------------

class TestAutoSeed:
    def test_first_turn_in_town_seeds_lines(self):
        """After first turn entering the town, lore lines are auto-seeded."""
        eng = _build_eng()
        _seed_events(eng)

        density = 0.3
        target = round(density * BASE)  # 3

        narrator_fake = FakeLLMProvider(json_responses=[
            _make_narrator_commit(to="市集"),   # T1: moves into town
        ])
        cascade_fake = FakeLLMProvider(json_responses=[
            _make_batch(target),               # T1 seeding batch
        ])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        strat = AuthorStrategy()

        result = run_turn(eng.registry, eng.store, eng.world, scene, "走进青石镇",
                         strategy=strat, provider=narrator_fake, cascade_provider=cascade_fake,
                         embedder=None, max_repairs=0)
        eng.world = result.world

        lines = eng.world["systems"]["lore"]["lines"]
        town_lines = [ln for ln in lines.values() if ln.get("anchor") == TOWN]
        assert len(town_lines) > 0, (
            f"No lines seeded in {TOWN!r}; all lines={list(lines.keys())}"
        )

    def test_seeded_lines_have_valid_fields(self):
        """Spawned lines have anchor=town, state=暗, valid complexity, l3_anchor in venues."""
        eng = _build_eng()
        _seed_events(eng)

        density = 0.3
        target = round(density * BASE)

        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake,
                         cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = result.world

        lines = eng.world["systems"]["lore"]["lines"]
        town_lines = [ln for ln in lines.values() if ln.get("anchor") == TOWN]

        for ln in town_lines:
            assert ln["anchor"] == TOWN, f"anchor mismatch: {ln['anchor']}"
            assert ln["state"] == "暗", f"expected 暗 state, got {ln['state']}"
            assert "status" not in ln, f"status must be absent; got {ln.get('status')!r}"
            assert ln["complexity"] in ("simple", "medium", "complex"), \
                f"invalid complexity: {ln['complexity']}"
            assert ln["l3_anchor"] in VENUES, \
                f"l3_anchor {ln['l3_anchor']!r} not in venues {VENUES}"
            assert isinstance(ln["stages"], list) and len(ln["stages"]) > 0, \
                f"stages empty or not a list: {ln['stages']}"

        # gen state: seeded=True
        gen = eng.world["systems"]["lore"]["gen"]
        assert gen.get(TOWN, {}).get("seeded") is True, \
            f"town not marked seeded; gen={gen}"

    def test_seeded_lines_complexity_matches_oracle(self):
        """Pinned seed -> pinned complexity sequence ['simple','medium','medium']."""
        eng = _build_eng(seed=SEED)
        _seed_events(eng, campaign_seed=SEED)

        density = 0.3
        target = round(density * BASE)  # 3

        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake,
                         cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = result.world

        lines = eng.world["systems"]["lore"]["lines"]
        town_lines = [ln for ln in lines.values() if ln.get("anchor") == TOWN]
        complexities = sorted(ln["complexity"] for ln in town_lines)
        expected = sorted(EXPECTED_COMPLEXITIES)
        assert complexities == expected, (
            f"Complexity mismatch. expected={expected} got={complexities}"
        )

    def test_no_double_seeding_on_re_entry(self):
        """Entering the same town again does NOT spawn additional lines (seeded marker)."""
        eng = _build_eng()
        _seed_events(eng)

        density = 0.3
        target = round(density * BASE)

        # T1: enter town -> seeding
        narrator_fake = FakeLLMProvider(json_responses=[
            _make_narrator_commit(to="市集"),   # T1
            _make_narrator_commit(to="市集"),   # T2 (stay in town)
        ])
        cascade_fake = FakeLLMProvider(json_responses=[
            _make_batch(target),               # T1 seeding batch
            # T2 should NOT consume another batch entry (seeded already)
        ])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        strat = AuthorStrategy()

        # T1
        r1 = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                      strategy=strat, provider=narrator_fake, cascade_provider=cascade_fake,
                      embedder=None, max_repairs=0)
        eng.world = r1.world
        lines_after_t1 = len([ln for ln in eng.world["systems"]["lore"]["lines"].values()
                               if ln.get("anchor") == TOWN])

        # T2: stay in town
        scene2 = {"protagonist": "主角", "present": [], "day": 1,
                  "id": "s2", "location": "市集"}
        r2 = run_turn(eng.registry, eng.store, eng.world, scene2, "在镇上转悠",
                      strategy=strat, provider=narrator_fake, cascade_provider=cascade_fake,
                      embedder=None, max_repairs=0)
        eng.world = r2.world
        lines_after_t2 = len([ln for ln in eng.world["systems"]["lore"]["lines"].values()
                               if ln.get("anchor") == TOWN])

        # Should not have gained more lines from T2 (no refresh yet -- within 3 days)
        assert lines_after_t2 == lines_after_t1, (
            f"Double-seeding detected: T1={lines_after_t1} T2={lines_after_t2}"
        )


# ---------------------------------------------------------------------------
# Test B: Brew -- run_lore advances at least one spawned line
# ---------------------------------------------------------------------------

class TestBrew:
    def test_run_lore_advances_generated_lines(self):
        """After seeding, explicit run_lore advances a specific named gen_ line.

        Pinned proof: with SEED=20260621 and GEN_THRESHOLD=50,
        gen_青石镇_c90820b1 (about="暗线0的故事") rolls <= 50 on the first
        explicit run_lore call after T1, so its stage_idx must strictly increase
        from -1 to 0 and a clue must appear.

        Snapshot timing: lines_before is captured IMMEDIATELY after T1 run_turn
        (before any explicit run_lore call), so the baseline is stable.
        The assertion is specific to the named line — it would fail if run_lore
        had a bug that skipped gen_ lines or the threshold gate was broken.
        """
        eng = _build_eng(seed=SEED)
        _seed_events(eng, campaign_seed=SEED)

        density = 0.3
        target = round(density * BASE)

        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake,
                         cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = result.world

        # Pinned line that rolls <= 50 on the next run_lore call (verified offline).
        # run_lore inside T1 runs BEFORE run_density, so gen_ lines don't exist yet
        # when that internal call fires; all gen_ lines start at stage_idx=-1 after T1.
        TARGET_LID = "gen_青石镇_c90820b1"

        # Snapshot IMMEDIATELY after T1 (before explicit run_lore).
        lore_lines = eng.world["systems"]["lore"]["lines"]
        assert TARGET_LID in lore_lines, (
            f"Expected pinned line {TARGET_LID!r} not found after seeding; "
            f"available ids={list(lore_lines.keys())}"
        )
        stage_before = lore_lines[TARGET_LID]["stage_idx"]
        clues_before = list(lore_lines[TARGET_LID].get("clues_dropped", []))
        # Confirm it starts at the unadvanced baseline.
        assert stage_before == -1, (
            f"Expected stage_idx=-1 immediately after seeding; got {stage_before}"
        )

        # Explicit run_lore call: the pinned line must advance.
        lore_events = run_lore(eng.registry, eng.store, eng.world)
        assert lore_events, (
            f"run_lore returned no events; expected {TARGET_LID!r} to advance. "
            f"lines={list(lore_lines.keys())}"
        )
        eng.world = project(eng.registry, eng.store.iter_events())

        stage_after = eng.world["systems"]["lore"]["lines"][TARGET_LID]["stage_idx"]
        clues_after = list(eng.world["systems"]["lore"]["lines"][TARGET_LID].get("clues_dropped", []))

        assert stage_after > stage_before, (
            f"Pinned gen_ line {TARGET_LID!r} did NOT advance: "
            f"stage_idx before={stage_before} after={stage_after}. "
            f"run_lore events={[e['type'] for e in lore_events]}"
        )
        assert len(clues_after) > len(clues_before), (
            f"No clue dropped for {TARGET_LID!r}: clues_before={clues_before} "
            f"clues_after={clues_after}"
        )


# ---------------------------------------------------------------------------
# Test C: Ambient -- station_push_fragment includes spawned line [id]
# ---------------------------------------------------------------------------

class TestAmbient:
    def test_station_push_fragment_includes_generated_lines(self):
        """After seeding, station_push_fragment includes at least one generated line's [id]."""
        eng = _build_eng(seed=SEED)
        _seed_events(eng, campaign_seed=SEED)

        density = 0.3
        target = round(density * BASE)

        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake,
                         cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = result.world

        # Protagonist is now at 市集; build a scene dict for that location
        scene_in_town = {"protagonist": "主角", "present": [], "day": 1,
                         "id": "s2", "location": "市集"}

        frag = station_push_fragment(eng.registry, eng.world, scene_in_town)
        # Fragment must be non-None and include at least one generated line id
        assert frag is not None, (
            f"station_push_fragment returned None; "
            f"lines={list(eng.world['systems']['lore']['lines'].keys())}"
        )

        # Check that at least one generated gen_* id appears in the fragment
        lines = eng.world["systems"]["lore"]["lines"]
        gen_ids = [lid for lid in lines if lid.startswith("gen_")]
        assert gen_ids, "No generated lines found (expected gen_ prefixed ids)"
        assert any(f"[{lid}]" in frag for lid in gen_ids), (
            f"No generated line id found in fragment.\n"
            f"gen_ids={gen_ids}\nfrag={frag!r}"
        )

        # F5 (optional L1-path): gen_青石镇_c90820b1 has l3_anchor=市集 (the current
        # scene's venue), so it must appear in the 「就在此処」 (L1) section, not
        # only the 「本镇其余风声」 (L0) section.
        # This is deterministic: _make_batch assigns l3_anchor=VENUES[0]="市集" for i=0.
        L1_LID = "gen_青石镇_c90820b1"
        assert L1_LID in lines, f"Pinned L1 line {L1_LID!r} not found; ids={list(lines.keys())}"
        assert lines[L1_LID].get("l3_anchor") == "市集", (
            f"Expected l3_anchor=市集 for {L1_LID!r}; got {lines[L1_LID].get('l3_anchor')!r}"
        )
        assert f"[{L1_LID}]" in frag, (
            f"L1 line {L1_LID!r} (l3_anchor=市集 == current venue) "
            f"not found in fragment.\nfrag={frag!r}"
        )
        # Confirm it appears in the 「就在此处」 section (L1 path, not just L0 index)
        assert "【就在此处】" in frag, (
            f"「就在此处」 L1 section missing from fragment; frag={frag!r}"
        )
        l1_section_start = frag.index("【就在此处】")
        # Find where L0 section starts (if any)
        l0_section_start = frag.find("【本镇其余风声】")
        l1_section = frag[l1_section_start: l0_section_start if l0_section_start >= 0 else None]
        assert f"[{L1_LID}]" in l1_section, (
            f"L1 line {L1_LID!r} not in the 「就在此处」 section.\n"
            f"l1_section={l1_section!r}\nfull_frag={frag!r}"
        )


# ---------------------------------------------------------------------------
# Test D: Determinism / Rewind-safety
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_produces_same_ids_and_complexities(self):
        """Building the same world twice and running the same first turn -> identical results.

        Proves rewind-safety: the complexity multiset and line ids are pinned by the Oracle.
        Pinned expected: sorted complexities = ['medium', 'medium', 'simple'].
        """
        def _run_once():
            eng = _build_eng(seed=SEED)
            _seed_events(eng, campaign_seed=SEED)
            density = 0.3
            target = round(density * BASE)
            narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
            cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
            eng.provider = narrator_fake
            eng.cascade_provider = cascade_fake
            scene = {"protagonist": "主角", "present": [], "day": 1,
                     "id": "s1", "location": "路边驿站"}
            result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                             strategy=AuthorStrategy(), provider=narrator_fake,
                             cascade_provider=cascade_fake, embedder=None, max_repairs=0)
            lines = result.world["systems"]["lore"]["lines"]
            town_lines = {lid: ln for lid, ln in lines.items() if ln.get("anchor") == TOWN}
            return (
                sorted(town_lines.keys()),
                sorted(ln["complexity"] for ln in town_lines.values()),
            )

        ids1, complexities1 = _run_once()
        ids2, complexities2 = _run_once()

        assert ids1 == ids2, f"Non-deterministic ids: run1={ids1} run2={ids2}"
        assert complexities1 == complexities2, (
            f"Non-deterministic complexities: run1={complexities1} run2={complexities2}"
        )
        # Pin the exact expected complexity sequence
        assert sorted(complexities1) == sorted(EXPECTED_COMPLEXITIES), (
            f"Complexity mismatch vs pinned. "
            f"expected={sorted(EXPECTED_COMPLEXITIES)} got={sorted(complexities1)}"
        )


# ---------------------------------------------------------------------------
# Test E: Region-less degradation
# ---------------------------------------------------------------------------

class TestRegionless:
    def test_regionless_world_seeds_with_default_density(self):
        """No L1 region -> density defaults to 0.3, generation works normally."""
        eng = _build_eng(seed=SEED)
        _seed_events(eng, campaign_seed=SEED)  # no L1 at all

        # Verify no L1 entity in the world
        g = eng.world["systems"]["ontology"]
        l1_entities = [e for e in g.entities.values() if e.attrs.get("level") == 1]
        assert len(l1_entities) == 0, \
            f"Expected no L1 entities; found {[e.id for e in l1_entities]}"

        density = 0.3  # default
        target = round(density * BASE)
        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake,
                         cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = result.world

        lines = eng.world["systems"]["lore"]["lines"]
        town_lines = [ln for ln in lines.values() if ln.get("anchor") == TOWN]
        assert len(town_lines) > 0, \
            "region-less world must still seed lines via default density"

        gen = eng.world["systems"]["lore"]["gen"]
        assert gen.get(TOWN, {}).get("seeded") is True

    def test_with_l1_region_higher_density_more_lines(self):
        """World WITH a L1 region at density=0.9 -> more lines (target=9) vs 0.3 (target=3)."""
        camp = tempfile.mkdtemp(prefix="density_l1_")
        narrator_fake_init = FakeLLMProvider(json_responses=[])
        eng = build_engine(camp, provider=narrator_fake_init)
        eng.campaign_seed = SEED

        def place(pid, level, kind, seed, parent=None):
            d = {"id": pid, "level": level, "kind": kind, "seed": seed, "tier": "tracked"}
            if parent:
                d["parent"] = parent
            eng.store.append(kernel_event("place_created", day=1, scene="s1",
                                         summary=pid, deltas=d, turn=0))

        eng.store.append(kernel_event("campaign_seeded", day=1, scene="s1",
                                      summary="seed",
                                      deltas={"campaign_seed": SEED}, turn=0))
        # L1 region with high density
        eng.store.append(kernel_event("place_created", day=1, scene="s1", summary="北境",
                                      deltas={"id": "北境", "level": 1, "kind": "region",
                                              "seed": "北境荒原", "tier": "tracked",
                                              "density": 0.9}, turn=0))
        place(TOWN, 2, "settlement", "雨季前的边陲集镇", parent="北境")
        for venue, vseed in [("市集", "嘈杂的露天集市"), ("酒馆", "镇口悦来酒馆"),
                              ("码头", "镇东旧码头")]:
            place(venue, 3, "venue", vseed, parent=TOWN)
        place("路边驿站", 3, "venue", "镇外的小驿站")

        eng.store.append(kernel_event("character_created", day=1, scene="s1",
                                      summary="主角",
                                      deltas={"id": "主角", "tier": "tracked",
                                              "sketch": "游历至此的江湖客",
                                              "goal": "讨生活"},
                                      turn=0))
        eng.store.append(kernel_event("entity_moved", day=1, scene="s1",
                                      summary="到路边驿站",
                                      deltas={"who": "主角", "to": "路边驿站"}, turn=0))
        eng.world = project(eng.registry, eng.store.iter_events())

        # density=0.9 -> target=round(0.9*10)=9
        target = round(0.9 * BASE)
        narrator_fake2 = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake2
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake2,
                         cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = result.world

        lines = eng.world["systems"]["lore"]["lines"]
        town_lines_l1 = [ln for ln in lines.values() if ln.get("anchor") == TOWN]
        # With density=0.9 (target=9) must generate more than the 3 from density=0.3
        assert len(town_lines_l1) > 3, (
            f"Expected more than 3 lines from high-density region; got {len(town_lines_l1)}"
        )


# ---------------------------------------------------------------------------
# Test F: Fault-tolerance -- cascade provider raises -> turn still completes
# ---------------------------------------------------------------------------

class _RaisingProvider:
    """Stub provider that always raises ValueError (uniformly failing — simulates LLM failure).

    All three call-paths that backstage hooks may use must raise so the test
    proves the engine degrades gracefully on ANY cascade provider failure,
    not just the specific method that happens to be called first.
    """
    def complete_json(self, system, user, schema, **kw):
        raise ValueError("simulated cascade failure")

    def complete(self, *args, **kw):
        raise ValueError("simulated cascade failure")

    def complete_messages(self, *args, **kw):
        raise ValueError("simulated cascade failure")


class TestFaultTolerance:
    def test_cascade_provider_raises_turn_still_completes(self):
        """When cascade_provider raises on every method, turn completes normally.

        Uses a SINGLE _RaisingProvider instance (not two) so the identity is
        consistent between eng.cascade_provider and the run_turn kwarg.
        Assertions: narration is truthy, no lines spawned, no exception escapes.
        """
        eng = _build_eng()
        _seed_events(eng)

        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        eng.provider = narrator_fake

        raising = _RaisingProvider()      # single instance, used everywhere
        eng.cascade_provider = raising

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}

        # Must NOT raise
        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake,
                         cascade_provider=raising,
                         embedder=None, max_repairs=0)

        # Turn must return with narration
        assert result.narration, "Turn returned empty narration after cascade failure"

        # World must still exist (no crash)
        assert isinstance(result.world["systems"]["lore"]["lines"], dict)

        # No lines spawned (generation failed gracefully inside generate_lore_batch)
        lines = result.world["systems"]["lore"]["lines"]
        town_lines = [ln for ln in lines.values() if ln.get("anchor") == TOWN]
        assert len(town_lines) == 0, (
            f"Expected no lines when cascade fails; got {len(town_lines)}"
        )

    def test_cascade_provider_none_turn_still_completes(self):
        """cascade_provider=None -> generation uses main provider fallback, no crash."""
        eng = _build_eng()
        _seed_events(eng)

        # cascade_provider=None -> run_turn uses (cascade_provider or provider)=narrator_fake
        # narrator_fake has 1 json_response; complete_json will cycle it back.
        # The narrator commit dict is not a valid batch -> generate_lore_batch returns []
        # -> seeded with 0 lines. Acceptable: no lines, no crash.
        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        eng.provider = narrator_fake
        eng.cascade_provider = None

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}

        result = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                         strategy=AuthorStrategy(), provider=narrator_fake,
                         cascade_provider=None,
                         embedder=None, max_repairs=0)

        assert result.narration, "Turn returned empty narration"
        assert isinstance(result.world["systems"]["lore"]["lines"], dict)


# ---------------------------------------------------------------------------
# Test G: Refresh -- density_refreshed emitted after REFRESH_INTERVAL_DAYS
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_density_refreshed_emitted_after_interval(self):
        """After seeding at day=1 and advancing >= REFRESH_INTERVAL_DAYS days,
        density_refreshed is emitted and last_refresh_day advances."""
        eng = _build_eng(seed=SEED)
        _seed_events(eng, campaign_seed=SEED)

        density = 0.3
        target = round(density * BASE)

        # T1: enter town -> seed
        narrator_fake = FakeLLMProvider(json_responses=[
            _make_narrator_commit(to="市集"),  # T1
        ])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        r1 = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                      strategy=AuthorStrategy(), provider=narrator_fake,
                      cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = r1.world

        gen_after_seed = eng.world["systems"]["lore"]["gen"]
        seed_day = gen_after_seed.get(TOWN, {}).get("last_refresh_day")
        assert seed_day is not None, "last_refresh_day not set after seeding"

        # T2: advance clock by REFRESH_INTERVAL_DAYS days to trigger refresh check
        # With SEED=20260621 and density=0.3, day=4 roll=30 (== density*100) -> no spawn
        # but density_refreshed is ALWAYS emitted at the refresh check
        narrator_fake2 = FakeLLMProvider(json_responses=[
            _make_narrator_commit(days=REFRESH_INTERVAL_DAYS),  # T2
        ])
        # Provide a batch in case the roll spawns a line
        cascade_fake2 = FakeLLMProvider(json_responses=[_make_batch(1)])
        eng.provider = narrator_fake2
        eng.cascade_provider = cascade_fake2

        scene2 = {"protagonist": "主角", "present": [], "day": seed_day,
                  "id": "s2", "location": "市集"}
        r2 = run_turn(eng.registry, eng.store, eng.world, scene2, "在镇上过了几天",
                      strategy=AuthorStrategy(), provider=narrator_fake2,
                      cascade_provider=cascade_fake2, embedder=None, max_repairs=0)
        eng.world = r2.world

        # density_refreshed event must have been emitted
        all_events = list(eng.store.iter_events())
        refresh_events = [e for e in all_events if e["type"] == "density_refreshed"
                         and e["deltas"].get("town") == TOWN]
        assert len(refresh_events) > 0, (
            "density_refreshed not emitted after REFRESH_INTERVAL_DAYS. "
            f"events_types={[e['type'] for e in all_events]}"
        )

        # last_refresh_day must have advanced
        gen_after_refresh = eng.world["systems"]["lore"]["gen"]
        new_last_day = gen_after_refresh.get(TOWN, {}).get("last_refresh_day")
        assert new_last_day is not None, "last_refresh_day missing after refresh"
        assert new_last_day > seed_day, (
            f"last_refresh_day did not advance: seed_day={seed_day}, now={new_last_day}"
        )

    def test_refresh_spawn_creates_new_line(self):
        """Refresh at a day where the d100 roll hits (day=5, roll=7 < 30) CREATES a new line.

        Pinned proof for SEED=20260621, density=0.3:
          Oracle(scene_seed(SEED, 'density:青石镇:refresh', 5)).d100() = 7 < 30 -> spawned=True.

        The 'if spawned:' branch in _run_density_inner is tested here:
          - seeded_ids captured after T1
          - T2 advances 4 days (day=1 -> day=5, which is >= last_refresh_day+3=4)
          - a lore_created event at day=5 appears in the store
          - the new line's id is NOT in seeded_ids (genuinely new, distinct id)
          - the total line count for the town increases by exactly 1
        """
        eng = _build_eng(seed=SEED)
        _seed_events(eng, campaign_seed=SEED)

        density = 0.3
        target = round(density * BASE)

        # T1: enter town -> seed 3 lines
        narrator_fake = FakeLLMProvider(json_responses=[_make_narrator_commit(to="市集")])
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}
        r1 = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                      strategy=AuthorStrategy(), provider=narrator_fake,
                      cascade_provider=cascade_fake, embedder=None, max_repairs=0)
        eng.world = r1.world

        # Snapshot after seeding
        seeded_ids = frozenset(
            lid for lid, ln in eng.world["systems"]["lore"]["lines"].items()
            if ln.get("anchor") == TOWN
        )
        lines_count_seed = len(seeded_ids)
        seed_day = eng.world["systems"]["lore"]["gen"].get(TOWN, {}).get("last_refresh_day")
        assert seed_day == 1, f"Expected seed_day=1, got {seed_day}"
        assert lines_count_seed == target, \
            f"Expected {target} lines after seeding; got {lines_count_seed}"

        # T2: advance 4 days -> day=5 (seed_day=1, 5-1=4 >= REFRESH_INTERVAL_DAYS=3)
        # Pinned: Oracle(scene_seed(SEED,'density:青石镇:refresh',5)).d100()=7 < 30 -> spawned=True
        narrator_fake2 = FakeLLMProvider(json_responses=[_make_narrator_commit(days=4)])
        # Use a distinct 'about' so the spawned line gets a different deterministic id
        cascade_fake2 = FakeLLMProvider(json_responses=[_make_refresh_batch()])
        eng.provider = narrator_fake2
        eng.cascade_provider = cascade_fake2

        scene2 = {"protagonist": "主角", "present": [], "day": seed_day,
                  "id": "s2", "location": "市集"}
        r2 = run_turn(eng.registry, eng.store, eng.world, scene2, "在镇上过了几天",
                      strategy=AuthorStrategy(), provider=narrator_fake2,
                      cascade_provider=cascade_fake2, embedder=None, max_repairs=0)
        eng.world = r2.world

        # density_refreshed event must exist
        all_events = list(eng.store.iter_events())
        refresh_events = [e for e in all_events if e["type"] == "density_refreshed"
                         and e["deltas"].get("town") == TOWN]
        assert len(refresh_events) > 0, (
            "density_refreshed not emitted; "
            f"events={[e['type'] for e in all_events]}"
        )

        # A lore_created event at day=5 (the refresh day) must exist
        lore_created_at_refresh = [
            e for e in all_events
            if e["type"] == "lore_created" and e.get("day") == 5
        ]
        assert len(lore_created_at_refresh) == 1, (
            f"Expected exactly 1 lore_created at day=5 (refresh spawn); "
            f"got {len(lore_created_at_refresh)}. "
            f"All lore_created: {[(e.get('day'), e['deltas'].get('id')) for e in all_events if e['type']=='lore_created']}"
        )

        # The spawned line must be a genuinely NEW id (not from the seed batch)
        spawned_id = lore_created_at_refresh[0]["deltas"].get("id")
        assert spawned_id not in seeded_ids, (
            f"Refresh spawn id {spawned_id!r} already existed in seed batch {seeded_ids}"
        )

        # Town line count must have grown by exactly 1
        town_lines_after = [ln for ln in eng.world["systems"]["lore"]["lines"].values()
                            if ln.get("anchor") == TOWN]
        assert len(town_lines_after) == lines_count_seed + 1, (
            f"Expected {lines_count_seed + 1} lines after spawn; "
            f"got {len(town_lines_after)}. spawned_id={spawned_id!r}"
        )


# ---------------------------------------------------------------------------
# Test H: Full pipeline -- multi-turn end-to-end composition
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_multi_turn_pipeline_composes(self):
        """Drive 3 turns: seed on T1, ambient on T2, no crash on T3."""
        eng = _build_eng(seed=SEED)
        _seed_events(eng, campaign_seed=SEED)

        density = 0.3
        target = round(density * BASE)

        # Narrator responses: T1 (enter town), T2 (stay), T3 (stay)
        narrator_responses = [
            _make_narrator_commit(to="市集"),   # T1
            _make_narrator_commit(),             # T2
            _make_narrator_commit(),             # T3
        ]
        narrator_fake = FakeLLMProvider(json_responses=narrator_responses)
        # Cascade: only T1 seeds; T2/T3 stay in interval so no refresh batch needed
        cascade_fake = FakeLLMProvider(json_responses=[_make_batch(target)])
        eng.provider = narrator_fake
        eng.cascade_provider = cascade_fake

        strat = AuthorStrategy()
        scene = {"protagonist": "主角", "present": [], "day": 1,
                 "id": "s1", "location": "路边驿站"}

        # T1: seed
        r1 = run_turn(eng.registry, eng.store, eng.world, scene, "进入青石镇",
                      strategy=strat, provider=narrator_fake, cascade_provider=cascade_fake,
                      embedder=None, max_repairs=0)
        eng.world = r1.world
        assert len([ln for ln in eng.world["systems"]["lore"]["lines"].values()
                    if ln.get("anchor") == TOWN]) > 0, "T1: must seed lines"

        # T2: ambient check before the turn
        scene2 = {"protagonist": "主角", "present": [], "day": 1,
                  "id": "s2", "location": "市集"}
        frag = station_push_fragment(eng.registry, eng.world, scene2)
        assert frag is not None, "T2: ambient fragment should be non-None after seeding"

        r2 = run_turn(eng.registry, eng.store, eng.world, scene2, "在市集转悠",
                      strategy=strat, provider=narrator_fake, cascade_provider=cascade_fake,
                      embedder=None, max_repairs=0)
        eng.world = r2.world
        assert r2.narration, "T2: narration must be non-empty"

        # T3: turn completes fine
        scene3 = {"protagonist": "主角", "present": [], "day": 1,
                  "id": "s3", "location": "市集"}
        r3 = run_turn(eng.registry, eng.store, eng.world, scene3, "喝杯茶歇一歇",
                      strategy=strat, provider=narrator_fake, cascade_provider=cascade_fake,
                      embedder=None, max_repairs=0)
        eng.world = r3.world
        assert r3.narration, "T3: narration must be non-empty"
