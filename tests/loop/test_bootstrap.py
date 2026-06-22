import json
import pathlib
import tempfile
from collections import Counter

from engine.oracle import Oracle, load_table, scene_seed
from loop.bootstrap import _draw_distinct, gen_frame, gen_regions, gen_local_map, gen_protagonist, gen_factions, gen_npcs, gen_threads, gen_opening, _build_world_summary
from loop.lore import _REQUIRED


def scene_seed_helper():
    return scene_seed(999, "genesis:frame:0", 0)


def test_draw_distinct_returns_k_distinct_and_deterministic():
    table = load_table("thread_types", "genesis")
    a = _draw_distinct(Oracle(123), table, 3)
    b = _draw_distinct(Oracle(123), table, 3)
    assert len(a) == 3
    assert len({e["name"] for e in a}) == 3          # all distinct
    assert [e["name"] for e in a] == [e["name"] for e in b]  # deterministic per seed

def test_draw_distinct_caps_at_pool_size():
    table = load_table("place_kinds", "genesis")      # only 3 entries
    out = _draw_distinct(Oracle(1), table, 10)
    assert len(out) == 3

def test_genesis_tables_load():
    for name in ("thread_types","npc_roles","place_kinds","tone_axes","terrains"):
        t = load_table(name, "genesis")
        assert isinstance(t, list) and t and all("name" in e for e in t)


class ScriptedProvider:
    """Returns canned strings in order; supports_tools=False.

    complete() and complete_messages() both consume from the same reply queue
    so prose calls (gen_opening) and structured calls (gen_frame, etc.) can be
    scripted uniformly.
    """
    def __init__(self, replies): self._r = list(replies); self.i = 0
    def supports_tools(self): return False
    def _next(self):
        r = self._r[self.i] if self.i < len(self._r) else self._r[-1]; self.i += 1; return r
    def complete_messages(self, messages): return self._next()
    def complete(self, system, user, **kw): return self._next()


def test_gen_frame_rolls_counts_deterministically():
    p = ScriptedProvider([json.dumps({"world_name":"河谷王国","central_conflict":"漕运断绝引发的暗斗"})])
    evs, frame = gen_frame(p, Oracle(scene_seed_helper()), "东方武侠悬疑")
    assert 3 <= frame["n_factions"] <= 5
    assert 3 <= frame["n_regions"] <= 5
    assert frame["world_name"] == "河谷王国"
    assert frame["tone"] in {"悬疑","冒险","权谋","生存","恩怨"}
    # world entity + public frame facts emitted
    types = [e["type"] for e in evs]
    assert "entity_created" in types
    assert any(e["type"]=="fact_asserted" and e["deltas"].get("secrecy")=="public" for e in evs)


def test_gen_frame_falls_back_without_provider():
    evs, frame = gen_frame(None, Oracle(7), "x")
    assert frame["world_name"]            # non-empty stub name
    assert evs                            # still emits world entity


# ---------------------------------------------------------------------------
# Task 3: gen_regions tests
# ---------------------------------------------------------------------------

def test_gen_regions_pins_connected_macro_graph():
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    evs, summ = gen_regions(ScriptedProvider([json.dumps({"regions":[
        {"name":"河谷","terrain":"水乡","seed":"依河而建"},
        {"name":"雪原","terrain":"雪原","seed":"苦寒之地"},
        {"name":"铁峰","terrain":"山地","seed":"矿脉纵横"},
        {"name":"商港","terrain":"平原","seed":"百货云集"}]})]), Oracle(5), frame)
    assert len(summ["regions"]) == 4
    starts = [r for r in summ["regions"] if r["tier"]=="start"]
    assert len(starts) == 1 and starts[0]["id"] == summ["start_region"]
    links = [e for e in evs if e["type"]=="place_linked"]
    assert len(links) == 3                                   # connected, n-1 edges
    # star topology: EVERY link touches the start region (directions anchored to start)
    assert all(summ["start_region"] in (e["deltas"]["a"], e["deltas"]["b"]) for e in links)
    start_pc = [e for e in evs if e["type"]=="place_created" and e["deltas"]["id"]==summ["start_region"]][0]
    assert isinstance(start_pc["deltas"]["attrs"]["density"], (int,float))


def test_gen_regions_summary_fields():
    """Summary has start_region, density, and all regions have id/name/tier/terrain."""
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":3}
    evs, summ = gen_regions(ScriptedProvider([json.dumps({"regions":[
        {"name":"A","terrain":"平原","seed":"一"},
        {"name":"B","terrain":"山地","seed":"二"},
        {"name":"C","terrain":"森林","seed":"三"}]})]), Oracle(42), frame)
    assert summ["start_region"] == "region_0"
    assert 0.2 <= summ["density"] <= 0.5
    for r in summ["regions"]:
        assert "id" in r and "name" in r and "tier" in r and "terrain" in r


def test_gen_regions_tiers():
    """i=0 => start, i=1..k => neighbor (k depends on n_regions), rest => far."""
    frame = {"genre":"x","tone":"冒险","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":5}
    evs, summ = gen_regions(ScriptedProvider([json.dumps({"regions":[
        {"name":"A","terrain":"平原","seed":"s1"},
        {"name":"B","terrain":"山地","seed":"s2"},
        {"name":"C","terrain":"森林","seed":"s3"},
        {"name":"D","terrain":"水乡","seed":"s4"},
        {"name":"E","terrain":"荒漠","seed":"s5"}]})]), Oracle(99), frame)
    tier_map = {r["id"]: r["tier"] for r in summ["regions"]}
    assert tier_map["region_0"] == "start"
    # at least region_1 is neighbor
    assert tier_map["region_1"] == "neighbor"


def test_gen_regions_place_created_events():
    """Every region emits a place_created event with level=1, kind=region."""
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    evs, summ = gen_regions(ScriptedProvider([json.dumps({"regions":[
        {"name":"A","terrain":"平原","seed":"s1"},
        {"name":"B","terrain":"山地","seed":"s2"},
        {"name":"C","terrain":"森林","seed":"s3"},
        {"name":"D","terrain":"水乡","seed":"s4"}]})]), Oracle(7), frame)
    created = [e for e in evs if e["type"]=="place_created"]
    assert len(created) == 4
    for e in created:
        assert e["deltas"]["level"] == 1
        assert e["deltas"]["kind"] == "region"
        assert e["deltas"]["attrs"]["terrain"]
        seed_val = e["deltas"].get("seed") or e["deltas"]["attrs"].get("seed")
        assert seed_val  # non-empty seed


def test_gen_regions_no_dangling_regions():
    """INVARIANT: every region_id in place_linked also has a place_created."""
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    evs, summ = gen_regions(ScriptedProvider([json.dumps({"regions":[
        {"name":"A","terrain":"平原","seed":"s1"},
        {"name":"B","terrain":"山地","seed":"s2"},
        {"name":"C","terrain":"森林","seed":"s3"},
        {"name":"D","terrain":"水乡","seed":"s4"}]})]), Oracle(3), frame)
    created_ids = {e["deltas"]["id"] for e in evs if e["type"]=="place_created"}
    for e in evs:
        if e["type"] == "place_linked":
            assert e["deltas"]["a"] in created_ids
            assert e["deltas"]["b"] in created_ids


def test_gen_regions_fallback_without_provider():
    """Fallback without provider still emits n_regions regions + n-1 link events."""
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    evs, summ = gen_regions(None, Oracle(11), frame)
    assert len(summ["regions"]) == 4
    created = [e for e in evs if e["type"]=="place_created"]
    assert len(created) == 4
    links = [e for e in evs if e["type"]=="place_linked"]
    assert len(links) == 3


def test_gen_regions_start_density_in_events():
    """Start region place_created attrs must carry density in [0.2, 0.5]."""
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":3}
    evs, summ = gen_regions(None, Oracle(55), frame)
    start_pc = next(e for e in evs if e["type"]=="place_created" and e["deltas"]["id"]=="region_0")
    d = start_pc["deltas"]["attrs"]["density"]
    assert isinstance(d, (int, float))
    assert 0.2 <= d <= 0.5


def test_gen_regions_engine_terrain_wins_over_llm_echo():
    """Engine-drawn terrains are stored even when the LLM echoes a different value.

    seed=5, n_regions=2 → engine draws ['水乡', '森林'].
    The LLM reply intentionally echoes wrong terrain ('错误地形', '另错误地形');
    the stored attrs and summary terrain must be the engine values, not the LLM echoes.
    """
    from engine.oracle import Oracle, load_table
    from loop.bootstrap import _draw_distinct
    oracle_probe = Oracle(5)
    terrain_entries = _draw_distinct(oracle_probe, load_table("terrains", "genesis"), 2)
    engine_terrains = [e["name"] for e in terrain_entries]

    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":2}
    # LLM echoes the wrong terrain strings deliberately
    llm_reply = json.dumps({"regions": [
        {"name": "河谷地带", "terrain": "错误地形", "seed": "一"},
        {"name": "幽深森林", "terrain": "另错误地形", "seed": "二"},
    ]})
    evs, summ = gen_regions(ScriptedProvider([llm_reply]), Oracle(5), frame)
    # Summary terrain must match engine-drawn value
    for i, r in enumerate(summ["regions"]):
        assert r["terrain"] == engine_terrains[i], (
            f"region_{i}: expected engine terrain '{engine_terrains[i]}', got '{r['terrain']}'"
        )
    # Events attrs terrain must also match engine-drawn value
    for i, ev in enumerate([e for e in evs if e["type"] == "place_created"]):
        assert ev["deltas"]["attrs"]["terrain"] == engine_terrains[i], (
            f"place_created region_{i}: expected '{engine_terrains[i]}', "
            f"got '{ev['deltas']['attrs']['terrain']}'"
        )


# ---------------------------------------------------------------------------
# Task 4: gen_local_map tests
# ---------------------------------------------------------------------------
# Oracle seed reference (pre-computed):
#   seed=1:  n_extra_l2=1, kinds=['wilderness'], n_venues=2
#   seed=11: n_extra_l2=2, kinds=['wilderness','dungeon'], n_venues=3
#   seed=14: n_extra_l2=1, kinds=['wilderness'], n_venues=4
#   seed=25: n_extra_l2=2, kinds=['wilderness','dungeon'], n_venues=2

def _regions_summary(n_regions=4):
    """Build a minimal regions_summary dict as gen_regions would return."""
    return {
        "start_region": "region_0",
        "regions": [{"id": f"region_{i}", "name": f"地域{i}", "tier": "start" if i==0 else "neighbor", "terrain": "平原"} for i in range(n_regions)],
        "density": 0.3,
    }


def _canned_local_map_reply(n_venues, n_neighbors):
    """Return a JSON string matching the expected LLM schema for gen_local_map."""
    venues = [{"name": f"场所{i}", "seed": f"场所{i}风味"} for i in range(n_venues)]
    neighbors = [{"name": f"邻地{i}", "seed": f"邻地{i}风味"} for i in range(n_neighbors)]
    return json.dumps({
        "town": {"name": "起始镇", "seed": "烟火气浓厚的小镇"},
        "venues": venues,
        "neighbors": neighbors,
    })


def test_gen_local_map_start_town_settlement():
    """Exactly one settlement L2 place == summary['start_town'].
    Seed 1: n_extra_l2=1 kind=wilderness, n_venues=2 (no settlement neighbors)."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    # seed=1 => n_extra_l2=1, n_venues=2; canned reply matches exactly
    p = ScriptedProvider([_canned_local_map_reply(n_venues=2, n_neighbors=1)])
    evs, summ = gen_local_map(p, Oracle(1), frame, regions_summary)

    assert "start_town" in summ
    assert summ["start_town"] == "town_0"

    # Exactly one L2 settlement (wilderness neighbor won't count)
    settlements = [
        e for e in evs
        if e["type"] == "place_created"
        and e["deltas"]["level"] == 2
        and e["deltas"]["kind"] == "settlement"
    ]
    assert len(settlements) == 1
    assert settlements[0]["deltas"]["id"] == summ["start_town"]


def test_gen_local_map_extra_l2_count():
    """1-2 additional L2 neighbor places are generated.
    Seed 11: n_extra_l2=2, n_venues=3."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    # seed=11 => n_extra_l2=2, n_venues=3
    p = ScriptedProvider([_canned_local_map_reply(n_venues=3, n_neighbors=2)])
    evs, summ = gen_local_map(p, Oracle(11), frame, regions_summary)

    l2_neighbors = [
        e for e in evs
        if e["type"] == "place_created"
        and e["deltas"]["level"] == 2
        and e["deltas"]["id"] != "town_0"
    ]
    assert 1 <= len(l2_neighbors) <= 2
    # All neighbor l2 IDs appear in summary["l2"]
    l2_summary_ids = {item["id"] for item in summ["l2"]}
    for e in l2_neighbors:
        assert e["deltas"]["id"] in l2_summary_ids


def test_gen_local_map_venues_count_and_parent():
    """2-4 L3 venues all have parent==town_0 and appear in summary['venues'].
    Seed 11: n_venues=3."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    p = ScriptedProvider([_canned_local_map_reply(n_venues=3, n_neighbors=2)])
    evs, summ = gen_local_map(p, Oracle(11), frame, regions_summary)

    venues_ev = [
        e for e in evs
        if e["type"] == "place_created" and e["deltas"]["level"] == 3
    ]
    assert 2 <= len(venues_ev) <= 4
    for e in venues_ev:
        assert e["deltas"]["parent"] == "town_0"
        assert e["deltas"]["kind"] == "venue"

    venue_ids_in_events = {e["deltas"]["id"] for e in venues_ev}
    assert venue_ids_in_events == set(summ["venues"])


def test_gen_local_map_summary_venues_min_two():
    """summary['venues'] always has >= 2 entries; seed 1 gives minimum n_venues=2."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    # seed=1 => n_venues=2 (minimum possible from oracle)
    p = ScriptedProvider([_canned_local_map_reply(n_venues=2, n_neighbors=1)])
    evs, summ = gen_local_map(p, Oracle(1), frame, regions_summary)
    assert len(summ["venues"]) >= 2


def test_gen_local_map_neighbors_linked_to_town():
    """Each neighbor L2 has a place_linked event to town_0.
    Seed 1: n_extra_l2=1."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    p = ScriptedProvider([_canned_local_map_reply(n_venues=2, n_neighbors=1)])
    evs, summ = gen_local_map(p, Oracle(1), frame, regions_summary)

    neighbor_ids = [
        e["deltas"]["id"] for e in evs
        if e["type"] == "place_created"
        and e["deltas"]["level"] == 2
        and e["deltas"]["id"] != "town_0"
    ]
    links = [e for e in evs if e["type"] == "place_linked"]
    linked_pairs = {(e["deltas"]["a"], e["deltas"]["b"]) for e in links}
    linked_pairs |= {(b, a) for a, b in linked_pairs}

    assert len(neighbor_ids) >= 1
    for nid in neighbor_ids:
        assert ("town_0", nid) in linked_pairs


def test_gen_local_map_summary_l2_fields():
    """summary['l2'] items each have id, kind, name fields. Seed 11."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    p = ScriptedProvider([_canned_local_map_reply(n_venues=3, n_neighbors=2)])
    evs, summ = gen_local_map(p, Oracle(11), frame, regions_summary)
    for item in summ["l2"]:
        assert "id" in item
        assert "kind" in item
        assert "name" in item


def test_gen_local_map_fallback_no_provider():
    """Fallback (provider=None) still emits a town_0 + >= 2 venues."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    evs, summ = gen_local_map(None, Oracle(1), frame, regions_summary)

    assert summ["start_town"] == "town_0"

    town_ev = [e for e in evs if e["type"]=="place_created" and e["deltas"]["id"]=="town_0"]
    assert len(town_ev) == 1
    assert town_ev[0]["deltas"]["kind"] == "settlement"

    assert len(summ["venues"]) >= 2
    venues_ev = [e for e in evs if e["type"]=="place_created" and e["deltas"]["level"]==3]
    assert len(venues_ev) >= 2
    for e in venues_ev:
        assert e["deltas"]["parent"] == "town_0"


def test_gen_local_map_all_events_genesis():
    """All emitted events have turn=0, day=1, scene='genesis'. Seed 1."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    p = ScriptedProvider([_canned_local_map_reply(n_venues=2, n_neighbors=1)])
    evs, summ = gen_local_map(p, Oracle(1), frame, regions_summary)
    for e in evs:
        assert e["turn"] == 0
        assert e["day"] == 1
        assert e["scene"] == "genesis"


def test_gen_local_map_place_created_tier_tracked():
    """town_0 and all venues must have tier='tracked'. Seed 1."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    p = ScriptedProvider([_canned_local_map_reply(n_venues=2, n_neighbors=1)])
    evs, summ = gen_local_map(p, Oracle(1), frame, regions_summary)

    town_ev = next(e for e in evs if e["type"]=="place_created" and e["deltas"]["id"]=="town_0")
    assert town_ev["deltas"]["tier"] == "tracked"

    for vid in summ["venues"]:
        venue_ev = next(e for e in evs if e["type"]=="place_created" and e["deltas"]["id"]==vid)
        assert venue_ev["deltas"]["tier"] == "tracked"

    # neighbor L2 places must ALSO be tracked (real navigable places, not mentioned)
    neighbor_ids = [l["id"] for l in summ["l2"] if l["id"] != "town_0"]
    assert neighbor_ids  # at least one neighbor exists
    for nid in neighbor_ids:
        nb_ev = next(e for e in evs if e["type"]=="place_created" and e["deltas"]["id"]==nid)
        assert nb_ev["deltas"]["tier"] == "tracked"


def test_gen_local_map_no_dangling_links():
    """Every id in place_linked events also has a place_created event. Seed 11."""
    regions_summary = _regions_summary()
    frame = {"genre":"x","tone":"悬疑","central_conflict":"c","world_name":"w","n_factions":3,"n_regions":4}
    p = ScriptedProvider([_canned_local_map_reply(n_venues=3, n_neighbors=2)])
    evs, summ = gen_local_map(p, Oracle(11), frame, regions_summary)

    created_ids = {e["deltas"]["id"] for e in evs if e["type"]=="place_created"}
    for e in evs:
        if e["type"] == "place_linked":
            assert e["deltas"]["a"] in created_ids
            assert e["deltas"]["b"] in created_ids


# ---------------------------------------------------------------------------
# Task 5: gen_factions tests
# ---------------------------------------------------------------------------

def _base_frame(n_factions=3, n_regions=4):
    """Minimal frame dict for gen_factions tests."""
    return {
        "genre": "东方武侠",
        "tone": "权谋",
        "world_name": "碎镜大陆",
        "central_conflict": "皇权与江湖的生死角力",
        "n_factions": n_factions,
        "n_regions": n_regions,
    }


def _canned_factions_reply(n):
    """Return a valid JSON string for n factions."""
    factions = [
        {"name": f"势力{i+1}_{chr(65+i)}", "motivation": f"动机{i+1}"}
        for i in range(n)
    ]
    return json.dumps({"factions": factions})


def _regions_summary_for_factions(n_regions=4):
    return {
        "start_region": "region_0",
        "regions": [
            {"id": f"region_{i}", "name": f"地域{i}", "tier": "start" if i == 0 else "neighbor", "terrain": "平原"}
            for i in range(n_regions)
        ],
        "density": 0.3,
    }


def test_gen_factions_event_count():
    """Emits exactly n_factions faction_created events."""
    frame = _base_frame(n_factions=3)
    p = ScriptedProvider([_canned_factions_reply(3)])
    evs, summ = gen_factions(p, Oracle(1), frame, _regions_summary_for_factions())
    assert len(evs) == 3
    assert all(e["type"] == "faction_created" for e in evs)


def test_gen_factions_event_count_five():
    """Works for n_factions=5."""
    frame = _base_frame(n_factions=5)
    p = ScriptedProvider([_canned_factions_reply(5)])
    evs, summ = gen_factions(p, Oracle(2), frame, _regions_summary_for_factions())
    assert len(evs) == 5
    assert all(e["type"] == "faction_created" for e in evs)


def test_gen_factions_ids_distinct():
    """All emitted faction ids are distinct strings matching faction_{i} pattern."""
    frame = _base_frame(n_factions=4)
    p = ScriptedProvider([_canned_factions_reply(4)])
    evs, summ = gen_factions(p, Oracle(3), frame, _regions_summary_for_factions())
    ids = [e["deltas"]["id"] for e in evs]
    assert len(set(ids)) == 4
    for i, eid in enumerate(ids):
        assert eid == f"faction_{i}"


def test_gen_factions_deltas_shape():
    """Each event deltas has op=='faction', non-empty seed, and non-empty motivation."""
    frame = _base_frame(n_factions=3)
    p = ScriptedProvider([_canned_factions_reply(3)])
    evs, summ = gen_factions(p, Oracle(4), frame, _regions_summary_for_factions())
    for e in evs:
        d = e["deltas"]
        assert d.get("op") == "faction"
        assert isinstance(d.get("seed"), str) and d["seed"].strip()
        assert isinstance(d.get("motivation"), str) and d["motivation"].strip()
        assert d.get("tier") == "mentioned"


def test_gen_factions_genesis_timestamps():
    """All events carry turn=0, day=1, scene='genesis'."""
    frame = _base_frame(n_factions=3)
    p = ScriptedProvider([_canned_factions_reply(3)])
    evs, summ = gen_factions(p, Oracle(5), frame, _regions_summary_for_factions())
    for e in evs:
        assert e["turn"] == 0
        assert e["day"] == 1
        assert e["scene"] == "genesis"


def test_gen_factions_summary_shape():
    """summary['factions'] is a list of {id, name} dicts, length == n_factions."""
    frame = _base_frame(n_factions=3)
    p = ScriptedProvider([_canned_factions_reply(3)])
    evs, summ = gen_factions(p, Oracle(6), frame, _regions_summary_for_factions())
    factions = summ.get("factions")
    assert isinstance(factions, list)
    assert len(factions) == 3
    for item in factions:
        assert "id" in item and "name" in item
        assert item["id"].startswith("faction_")
        assert item["name"].strip()


def test_gen_factions_fallback_no_provider():
    """Fallback (provider=None) emits n stub factions with deterministic names."""
    frame = _base_frame(n_factions=4)
    evs, summ = gen_factions(None, Oracle(7), frame, _regions_summary_for_factions())
    assert len(evs) == 4
    assert all(e["type"] == "faction_created" for e in evs)
    for i, e in enumerate(evs):
        d = e["deltas"]
        assert d["op"] == "faction"
        assert d["id"] == f"faction_{i}"
        assert d["seed"].strip()
        assert d["motivation"].strip()
    factions = summ["factions"]
    assert len(factions) == 4
    for item in factions:
        assert item["name"].strip()


def test_gen_factions_seed_matches_name():
    """The seed in deltas matches the name in summary for the same index."""
    frame = _base_frame(n_factions=3)
    p = ScriptedProvider([_canned_factions_reply(3)])
    evs, summ = gen_factions(p, Oracle(8), frame, _regions_summary_for_factions())
    for i, (e, s) in enumerate(zip(evs, summ["factions"])):
        assert e["deltas"]["seed"] == s["name"]
        assert s["id"] == f"faction_{i}"


def test_gen_factions_non_vacuous_with_provider():
    """ScriptedProvider names are used (not stub placeholders)."""
    frame = _base_frame(n_factions=2)
    # Distinct real names from canned reply
    reply = json.dumps({"factions": [
        {"name": "铁血盟", "motivation": "统一大陆"},
        {"name": "云隐宫", "motivation": "守护古法"},
    ]})
    p = ScriptedProvider([reply])
    evs, summ = gen_factions(p, Oracle(9), frame, _regions_summary_for_factions())
    names = [e["deltas"]["seed"] for e in evs]
    assert "铁血盟" in names
    assert "云隐宫" in names


# ---------------------------------------------------------------------------
# Task 6: gen_npcs tests
# ---------------------------------------------------------------------------

def _base_local_map(n_venues=3):
    """Minimal local_map dict (as gen_local_map returns in summary)."""
    return {
        "start_town": "town_0",
        "venues": [f"venue_{i}" for i in range(n_venues)],
        "l2": [{"id": "town_0", "kind": "settlement", "name": "起始镇"}],
    }


def _base_factions_summary(n=3):
    return {"factions": [{"id": f"faction_{i}", "name": f"势力{i+1}"} for i in range(n)]}


def _canned_npcs_reply(npcs):
    """Return a valid JSON string for gen_npcs structured call.

    npcs: list of dicts with sketch/goal/secret keys.
    """
    return json.dumps({"npcs": npcs})


# Oracle seed reference (pre-computed for gen_npcs via _precompute_n):
#   seed=10: n=4, seed=20: n=3, seed=30: n=4 (use _precompute_n(seed) to verify)

def _precompute_n(seed):
    """Determine n that oracle will produce for gen_npcs with given seed."""
    from engine.oracle import Oracle
    return Oracle(seed).randint(2, 4)


def test_gen_npcs_count_in_range():
    """gen_npcs emits 2-4 character_created events."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    created = [e for e in evs if e["type"] == "character_created"]
    assert 2 <= len(created) <= 4


def test_gen_npcs_each_has_secret_fact():
    """Every NPC must have a fact_asserted with secrecy=='secret'."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    created = [e for e in evs if e["type"] == "character_created"]
    for ce in created:
        npc_id = ce["deltas"]["id"]
        secrets = [
            e for e in evs
            if e["type"] == "fact_asserted"
            and e["deltas"]["subject"] == npc_id
            and e["deltas"].get("secrecy") == "secret"
        ]
        assert len(secrets) >= 1, f"{npc_id} has no secret fact_asserted"


def test_gen_npcs_roles_distinct():
    """All NPC roles in summary are distinct (no two NPCs share a role)."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    roles = [item["role"] for item in summ["npcs"]]
    assert len(roles) == len(set(roles)), f"Duplicate roles: {roles}"


def test_gen_npcs_each_entity_moved_to_venue():
    """Every NPC emits an entity_moved pointing to a venue in local_map['venues']."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    valid_venues = set(local_map["venues"])
    created = [e for e in evs if e["type"] == "character_created"]
    for ce in created:
        npc_id = ce["deltas"]["id"]
        move_evs = [
            e for e in evs
            if e["type"] == "entity_moved"
            and e["deltas"]["who"] == npc_id
        ]
        assert len(move_evs) == 1, f"{npc_id} must have exactly one entity_moved"
        assert move_evs[0]["deltas"]["to"] in valid_venues, (
            f"{npc_id} placed in {move_evs[0]['deltas']['to']}, not in {valid_venues}"
        )


def test_gen_npcs_fallback_emits_two_npcs_with_secrets():
    """Fallback (provider=None) still emits >= 2 NPCs each with a secret fact."""
    local_map = _base_local_map(2)
    frame = _base_frame(n_factions=3)
    evs, summ = gen_npcs(None, Oracle(7), frame, local_map, _base_factions_summary())
    created = [e for e in evs if e["type"] == "character_created"]
    assert len(created) >= 2
    for ce in created:
        npc_id = ce["deltas"]["id"]
        secrets = [
            e for e in evs
            if e["type"] == "fact_asserted"
            and e["deltas"]["subject"] == npc_id
            and e["deltas"].get("secrecy") == "secret"
        ]
        assert len(secrets) >= 1, f"Fallback: {npc_id} has no secret fact"


def test_gen_npcs_summary_shape():
    """summary['npcs'] is a list of {id, role, sketch} dicts, length == n NPCs."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    npc_list = summ.get("npcs")
    assert isinstance(npc_list, list)
    created = [e for e in evs if e["type"] == "character_created"]
    assert len(npc_list) == len(created)
    for item in npc_list:
        assert "id" in item and "role" in item and "sketch" in item
        assert item["id"].startswith("npc_")
        assert item["role"].strip()
        assert item["sketch"].strip()


def test_gen_npcs_character_created_deltas():
    """character_created deltas has id, tier='mentioned', non-empty sketch and goal."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物素描{i}", "goal": f"人物目标{i}", "secret": f"秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    created = [e for e in evs if e["type"] == "character_created"]
    for i, e in enumerate(created):
        d = e["deltas"]
        assert d["id"] == f"npc_{i}"
        assert d["tier"] == "mentioned"
        assert isinstance(d.get("sketch"), str) and d["sketch"].strip()
        assert isinstance(d.get("goal"), str) and d["goal"].strip()


def test_gen_npcs_secret_fact_predicate():
    """The secret fact_asserted uses predicate '真实身份' and non-empty value."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"真实秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    created = [e for e in evs if e["type"] == "character_created"]
    for ce in created:
        npc_id = ce["deltas"]["id"]
        secret_ev = next(
            (e for e in evs
             if e["type"] == "fact_asserted"
             and e["deltas"]["subject"] == npc_id
             and e["deltas"].get("secrecy") == "secret"),
            None
        )
        assert secret_ev is not None
        assert secret_ev["deltas"]["predicate"] == "真实身份"
        assert isinstance(secret_ev["deltas"]["value"], str)
        assert secret_ev["deltas"]["value"].strip()


def test_gen_npcs_all_events_genesis():
    """All emitted events have turn=0, day=1, scene='genesis'."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"} for i in range(n)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    for e in evs:
        assert e["turn"] == 0
        assert e["day"] == 1
        assert e["scene"] == "genesis"


def test_gen_npcs_npc_traits_table_loaded():
    """npc_traits oracle table exists with >= 6 entries, each having weight and name."""
    traits = load_table("npc_traits", "genesis")
    assert isinstance(traits, list)
    assert len(traits) >= 6
    for entry in traits:
        assert isinstance(entry.get("weight"), int) and entry["weight"] > 0
        assert isinstance(entry.get("name"), str) and entry["name"].strip()


def test_gen_npcs_non_vacuous_with_provider():
    """LLM-supplied sketch/goal/secret strings appear in events (not just stubs)."""
    local_map = _base_local_map(3)
    frame = _base_frame(n_factions=3)
    n = _precompute_n(10)
    canned = [
        {"sketch": "神秘的旅人，总是戴着兜帽", "goal": "寻找失散的兄弟", "secret": "实为被通缉的刺客"},
        {"sketch": "镇上最富有的商人", "goal": "垄断商路", "secret": "暗中资助叛军"},
    ][:n]
    # Pad if n > 2
    while len(canned) < n:
        i = len(canned)
        canned.append({"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"})
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(10), frame, local_map, _base_factions_summary())
    created = [e for e in evs if e["type"] == "character_created"]
    # The first NPC's sketch must match what the LLM provided (non-stub)
    assert created[0]["deltas"]["sketch"] == "神秘的旅人，总是戴着兜帽"
    # The first NPC's secret must appear in a fact_asserted
    secret_ev = next(
        e for e in evs
        if e["type"] == "fact_asserted"
        and e["deltas"]["subject"] == "npc_0"
        and e["deltas"].get("secrecy") == "secret"
    )
    assert secret_ev["deltas"]["value"] == "实为被通缉的刺客"


def test_gen_npcs_venue_placement_uses_modulo():
    """With 2 venues and 4 NPCs, each venue gets 2 NPCs (round-robin modulo)."""
    # Force n=4 by finding a seed that gives randint(2,4)==4
    # seed=30 gives n=4
    n = _precompute_n(30)
    assert n == 4, f"Expected n=4 for seed=30, got {n}"
    local_map = _base_local_map(2)  # only 2 venues
    frame = _base_frame(n_factions=3)
    canned = [{"sketch": f"人物{i}", "goal": f"目标{i}", "secret": f"秘密{i}"} for i in range(4)]
    p = ScriptedProvider([_canned_npcs_reply(canned)])
    evs, summ = gen_npcs(p, Oracle(30), frame, local_map, _base_factions_summary())
    move_evs = [e for e in evs if e["type"] == "entity_moved"]
    assert len(move_evs) == 4
    venues_used = [e["deltas"]["to"] for e in move_evs]
    # Both venues should appear (modulo placement)
    assert "venue_0" in venues_used
    assert "venue_1" in venues_used


# ---------------------------------------------------------------------------
# Task 7: gen_threads tests
# ---------------------------------------------------------------------------

def _base_threads_local_map(n_venues=3):
    """Minimal local_map dict for gen_threads tests (includes venues list)."""
    return {
        "start_town": "town_0",
        "venues": [f"venue_{i}" for i in range(n_venues)],
        "l2": [{"id": "town_0", "kind": "settlement", "name": "起始镇"}],
    }


def _canned_threads_reply(n, venues):
    """Return a valid JSON string for gen_threads structured call (n thread skeletons)."""
    lines = []
    for i in range(n):
        lines.append({
            "about": f"暗线{i}表面钩子",
            "description": f"暗线{i}玩家可见描述",
            "trigger": f"暗线{i}触发条件",
            "secret": f"暗线{i}隐藏真相",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"暗线{i}阶段提示{j}"} for j in range(3)],
        })
    return json.dumps({"lines": lines})


def _thread_counts(seed):
    """Return (n_campaign, n_p) that gen_threads ACTUALLY draws for a given seed.

    Simulates the exact oracle draw sequence used in _gen_threads_inner:
      1. randint(3,5) → n_campaign
      2. _draw_distinct(oracle, thread_types, n) → n draws
      3. n * 2 draws for complexity + speed per campaign thread
      4. randint(1,2) → n_p
    This avoids the false pre-roll that used a fresh Oracle(seed).randint(1,2) for n_p,
    which only matched the real n_p for 3 of 11 tested seeds.
    """
    from engine.oracle import Oracle, load_table
    from loop.bootstrap import _draw_distinct, _COMPLEXITY_TABLE, _SPEED_TABLE
    oracle = Oracle(seed)
    n = oracle.randint(3, 5)
    _draw_distinct(oracle, load_table("thread_types", "genesis"), n)
    for _ in range(n):
        oracle.draw(_COMPLEXITY_TABLE)
        oracle.draw(_SPEED_TABLE)
    n_p = oracle.randint(1, 2)
    return n, n_p


def test_gen_threads_campaign_count_in_range():
    """gen_threads returns 3-5 campaign threads."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(1)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(1), frame, local_map, protagonist)
    campaign_threads = [s for s in skeletons if s["anchor"] == local_map["start_town"]]
    assert 3 <= len(campaign_threads) <= 5


def test_gen_threads_protagonist_count_in_range():
    """gen_threads returns 1-2 protagonist-bound threads."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(1)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(1), frame, local_map, protagonist)
    protagonist_threads = [s for s in skeletons if s["anchor"] == protagonist]
    assert 1 <= len(protagonist_threads) <= 2


def test_gen_threads_campaign_types_distinct():
    """Campaign thread types drawn from thread_types table are all distinct."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(5)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(5), frame, local_map, protagonist)
    campaign = [s for s in skeletons if s["anchor"] == local_map["start_town"]]
    # Retrieve the type from summary for campaign threads
    campaign_ids = {s["id"] for s in campaign}
    types_in_summary = [
        t["type"] for t in summ["threads"]
        if t["id"] in campaign_ids
    ]
    assert len(types_in_summary) == len(set(types_in_summary)), \
        f"Campaign thread types not distinct: {types_in_summary}"


def test_gen_threads_l3_anchor_in_venues():
    """Every skeleton's l3_anchor is a real venue from local_map['venues']."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(2)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(2), frame, local_map, protagonist)
    valid_venues = set(venues)
    for sk in skeletons:
        assert sk["l3_anchor"] in valid_venues, \
            f"l3_anchor '{sk['l3_anchor']}' not in venues {valid_venues}"


def test_gen_threads_all_required_keys_present():
    """Every skeleton has ALL keys from loop.lore._REQUIRED."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(3)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(3), frame, local_map, protagonist)
    for sk in skeletons:
        missing = [k for k in _REQUIRED if k not in sk]
        assert not missing, f"Skeleton {sk.get('id')} missing keys: {missing}"


def test_gen_threads_complexity_valid():
    """Every skeleton has complexity in {simple, medium, complex}."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(4)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(4), frame, local_map, protagonist)
    valid = {"simple", "medium", "complex"}
    for sk in skeletons:
        assert sk["complexity"] in valid, \
            f"Skeleton {sk['id']} has invalid complexity '{sk['complexity']}'"


def test_gen_threads_protagonist_anchor():
    """All protagonist-bound threads have anchor == protagonist."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(6)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(6), frame, local_map, protagonist)
    protagonist_threads = [s for s in skeletons if s["anchor"] == protagonist]
    assert len(protagonist_threads) >= 1
    for sk in protagonist_threads:
        assert sk["anchor"] == protagonist


def test_gen_threads_fallback_emits_three_or_more():
    """Fallback (provider=None) still emits >= 3 valid skeletons."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    skeletons, summ = gen_threads(None, Oracle(7), frame, local_map, protagonist)
    assert len(skeletons) >= 3
    venues = set(local_map["venues"])
    for sk in skeletons:
        missing = [k for k in _REQUIRED if k not in sk]
        assert not missing, f"Fallback skeleton {sk.get('id')} missing keys: {missing}"
        assert sk["l3_anchor"] in venues
        assert sk["complexity"] in {"simple", "medium", "complex"}


def test_gen_threads_summary_shape():
    """summary['threads'] is a list of dicts each with id/type/complexity/anchor/about."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(8)
    p = ScriptedProvider([
        _canned_threads_reply(n_campaign, venues),
        _canned_threads_reply(n_p, venues),
    ])
    skeletons, summ = gen_threads(p, Oracle(8), frame, local_map, protagonist)
    assert "threads" in summ
    assert isinstance(summ["threads"], list)
    assert len(summ["threads"]) == len(skeletons)
    for item in summ["threads"]:
        assert "id" in item
        assert "type" in item
        assert "complexity" in item
        assert "anchor" in item
        assert "about" in item
        assert isinstance(item["about"], str) and item["about"].strip()


def test_gen_threads_non_vacuous_with_provider():
    """LLM-supplied story strings appear in skeletons (not just stub values).

    Uses seed 9 and _thread_counts(9) so both campaign and protagonist replies
    have the EXACT line count the function draws — exercising the real LLM path
    for every thread rather than silently falling back to stubs.
    """
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(9)
    # Use recognizable unique strings for campaign threads
    lines = []
    for i in range(n_campaign):
        lines.append({
            "about": f"UNIQUE_ABOUT_{i}",
            "description": f"UNIQUE_DESC_{i}",
            "trigger": f"UNIQUE_TRIGGER_{i}",
            "secret": f"UNIQUE_SECRET_{i}",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"UNIQUE_HINT_{i}"}],
        })
    # Use recognizable unique strings for protagonist threads
    p_lines = []
    for i in range(n_p):
        p_lines.append({
            "about": f"PROT_ABOUT_{i}",
            "description": f"PROT_DESC_{i}",
            "trigger": f"PROT_TRIGGER_{i}",
            "secret": f"PROT_SECRET_{i}",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"PROT_HINT_{i}"}],
        })
    p = ScriptedProvider([
        json.dumps({"lines": lines}),
        json.dumps({"lines": p_lines}),
    ])
    skeletons, summ = gen_threads(p, Oracle(9), frame, local_map, protagonist)
    campaign = [s for s in skeletons if s["anchor"] == local_map["start_town"]]
    # LLM content must flow through — not the stub placeholder
    assert campaign[0]["about"] == "UNIQUE_ABOUT_0", (
        f"Expected UNIQUE_ABOUT_0 but got '{campaign[0]['about']}' — "
        "LLM path not exercised for campaign threads"
    )
    prot = [s for s in skeletons if s["anchor"] == protagonist]
    assert prot[0]["about"] == "PROT_ABOUT_0", (
        f"Expected PROT_ABOUT_0 but got '{prot[0]['about']}' — "
        "LLM path not exercised for protagonist threads"
    )


def test_gen_threads_stage_count_capped():
    """stages list in each skeleton is capped to stage_count for its complexity."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    stage_count_map = {"simple": 2, "medium": 3, "complex": 5}
    n_campaign, n_p = _thread_counts(10)
    # Supply 5 stages for each thread so capping is visible
    lines = []
    for i in range(n_campaign):
        lines.append({
            "about": f"暗线{i}",
            "description": f"描述{i}",
            "trigger": f"触发{i}",
            "secret": f"秘密{i}",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"提示{i}_{j}"} for j in range(5)],
        })
    p_lines = []
    for i in range(n_p):
        p_lines.append({
            "about": f"主角线{i}",
            "description": f"主角描述{i}",
            "trigger": f"主角触发{i}",
            "secret": f"主角秘密{i}",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"主角提示{i}_{j}"} for j in range(5)],
        })
    p = ScriptedProvider([
        json.dumps({"lines": lines}),
        json.dumps({"lines": p_lines}),
    ])
    skeletons, summ = gen_threads(p, Oracle(10), frame, local_map, protagonist)
    for sk in skeletons:
        expected_count = stage_count_map[sk["complexity"]]
        assert len(sk["stages"]) == expected_count, \
            f"Skeleton {sk['id']} has {len(sk['stages'])} stages, expected {expected_count}"


def test_gen_threads_l3_anchor_reject_floating():
    """Validate rejects l3_anchor not in venues; repair path is exercised."""
    local_map = _base_threads_local_map(3)
    frame = _base_frame(n_factions=3)
    protagonist = "protagonist_0"
    venues = local_map["venues"]
    n_campaign, n_p = _thread_counts(11)
    # All lines have invalid l3_anchor — should trigger repair/discard
    bad_lines = []
    good_lines = []
    for i in range(n_campaign):
        bad_lines.append({
            "about": f"坏锚{i}",
            "description": f"描述{i}",
            "trigger": f"触发{i}",
            "secret": f"秘密{i}",
            "l3_anchor": "NONEXISTENT_VENUE",
            "stages": [{"hint": f"提示{i}"}],
        })
        good_lines.append({
            "about": f"好锚{i}",
            "description": f"描述{i}",
            "trigger": f"触发{i}",
            "secret": f"秘密{i}",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"提示{i}"}],
        })
    good_p_lines = []
    for i in range(n_p):
        good_p_lines.append({
            "about": f"主角好锚{i}",
            "description": f"主角描述{i}",
            "trigger": f"主角触发{i}",
            "secret": f"主角秘密{i}",
            "l3_anchor": venues[i % len(venues)],
            "stages": [{"hint": f"主角提示{i}"}],
        })
    # First reply: bad anchors; second+: good anchors (repair path)
    p = ScriptedProvider([
        json.dumps({"lines": bad_lines}),
        json.dumps({"lines": good_lines}),
        json.dumps({"lines": good_lines}),
        json.dumps({"lines": good_p_lines}),
        json.dumps({"lines": good_p_lines}),
    ])
    skeletons, summ = gen_threads(p, Oracle(11), frame, local_map, protagonist)
    valid_venues = set(venues)
    for sk in skeletons:
        assert sk["l3_anchor"] in valid_venues, \
            f"Floating anchor found: '{sk['l3_anchor']}' not in {valid_venues}"


# ---------------------------------------------------------------------------
# Task 8: gen_opening tests
# ---------------------------------------------------------------------------

def _base_opening_frame():
    """Minimal frame dict for gen_opening tests."""
    return {
        "genre": "东方武侠",
        "tone": "悬疑",
        "world_name": "碎镜大陆",
        "central_conflict": "皇权与江湖的生死角力",
        "n_factions": 3,
        "n_regions": 4,
    }


def test_gen_opening_returns_exactly_one_narration_recorded():
    """gen_opening emits exactly one narration_recorded event."""
    frame = _base_opening_frame()
    p = ScriptedProvider(["清晨，你踏入了碎石镇那条青石板铺就的主街……"])
    evs, narration = gen_opening(p, frame, "世界摘要内容", scene_loc="venue_0")
    narr_evs = [e for e in evs if e["type"] == "narration_recorded"]
    assert len(narr_evs) == 1


def test_gen_opening_deltas_text_equals_returned_narration():
    """deltas['text'] in the narration_recorded event equals the returned narration string."""
    frame = _base_opening_frame()
    prose = "秋风卷过镇口的旗幡，你背着行囊站在起始镇的集市前……"
    p = ScriptedProvider([prose])
    evs, narration = gen_opening(p, frame, "摘要", scene_loc="venue_1")
    narr_ev = next(e for e in evs if e["type"] == "narration_recorded")
    assert narr_ev["deltas"]["text"] == narration
    assert narration == prose


def test_gen_opening_narration_non_empty():
    """Returned narration must be a non-empty string."""
    frame = _base_opening_frame()
    p = ScriptedProvider(["某开场叙事段落。"])
    evs, narration = gen_opening(p, frame, "摘要", scene_loc="venue_0")
    assert isinstance(narration, str)
    assert narration.strip()


def test_gen_opening_scripted_prose_used():
    """With a ScriptedProvider the scripted prose string is returned (not a stub)."""
    frame = _base_opening_frame()
    unique_prose = "UNIQUE_OPENING_PROSE_FOR_TEST_碎镜大陆"
    p = ScriptedProvider([unique_prose])
    evs, narration = gen_opening(p, frame, "摘要", scene_loc="venue_0")
    assert narration == unique_prose


def test_gen_opening_fallback_no_provider_non_empty():
    """Fallback (provider=None) returns a non-empty stub narration; never raises."""
    frame = _base_opening_frame()
    evs, narration = gen_opening(None, frame, "摘要", scene_loc="venue_0")
    assert isinstance(narration, str)
    assert narration.strip()


def test_gen_opening_fallback_mentions_world_name():
    """Fallback stub narration must mention frame['world_name']."""
    frame = _base_opening_frame()
    evs, narration = gen_opening(None, frame, "摘要", scene_loc="venue_0")
    assert frame["world_name"] in narration, (
        f"Stub narration does not mention world_name '{frame['world_name']}': {narration!r}"
    )


def test_gen_opening_event_genesis_timestamps():
    """narration_recorded event has turn=0, day=1, scene='genesis'."""
    frame = _base_opening_frame()
    p = ScriptedProvider(["开场叙事。"])
    evs, narration = gen_opening(p, frame, "摘要", scene_loc="venue_0")
    narr_ev = next(e for e in evs if e["type"] == "narration_recorded")
    assert narr_ev["turn"] == 0
    assert narr_ev["day"] == 1
    assert narr_ev["scene"] == "genesis"


def test_gen_opening_event_deltas_scene_genesis():
    """narration_recorded deltas['scene'] == 'genesis'."""
    frame = _base_opening_frame()
    p = ScriptedProvider(["开场叙事。"])
    evs, narration = gen_opening(p, frame, "摘要", scene_loc="venue_0")
    narr_ev = next(e for e in evs if e["type"] == "narration_recorded")
    assert narr_ev["deltas"]["scene"] == "genesis"


def test_gen_opening_fallback_emits_one_event():
    """Fallback path still emits exactly one narration_recorded event."""
    frame = _base_opening_frame()
    evs, narration = gen_opening(None, frame, "摘要", scene_loc="venue_0")
    narr_evs = [e for e in evs if e["type"] == "narration_recorded"]
    assert len(narr_evs) == 1
    assert narr_evs[0]["deltas"]["text"] == narration


def test_gen_opening_provider_exception_fallback():
    """If provider.complete raises, fallback stub is used and function does not raise."""
    frame = _base_opening_frame()

    class BrokenProvider:
        def supports_tools(self): return False
        def complete(self, system, user, **kw): raise RuntimeError("simulated LLM failure")
        def complete_messages(self, messages): raise RuntimeError("simulated LLM failure")

    evs, narration = gen_opening(BrokenProvider(), frame, "摘要", scene_loc="venue_0")
    assert isinstance(narration, str) and narration.strip()
    assert frame["world_name"] in narration
    narr_evs = [e for e in evs if e["type"] == "narration_recorded"]
    assert len(narr_evs) == 1


def test_build_world_summary_uses_sketches_and_abouts():
    """_build_world_summary must embed NPC sketches and thread abouts, not entity ids.

    Updated for fix #10: local_map now carries venue_names; summary must use venue NAMES
    (not raw ids like venue_0/venue_1) for the 场所 line.
    """
    frame = {
        "world_name": "测试世界",
        "tone": "悬疑",
        "central_conflict": "核心矛盾",
        "n_factions": 2,
        "n_regions": 2,
    }
    regions_summary = {
        "regions": [
            {"id": "region_0", "name": "东部平原", "tier": "start", "terrain": "平原"},
            {"id": "region_1", "name": "西部山地", "tier": "neighbor", "terrain": "山地"},
        ],
        "start_region": "region_0",
        "density": 0.3,
    }
    local_map = {
        "start_town": "town_0",
        "venues": ["venue_0", "venue_1"],
        # Fix #10: venue_names now carried in local_map summary
        "venue_names": {"venue_0": "老醉酒馆", "venue_1": "铁铺"},
        "l2": [{"id": "town_0", "kind": "settlement", "name": "起始镇"}],
    }
    npcs_summary = {
        "npcs": [
            {"id": "npc_0", "role": "商人", "sketch": "戴兜帽的旅人，目光深邃"},
            {"id": "npc_1", "role": "侍卫", "sketch": "身披铠甲的老兵"},
        ]
    }
    threads_summary = {
        "threads": [
            {"id": "thread_0", "type": "阴谋", "complexity": "medium",
             "anchor": "town_0", "about": "皇室继承权之争"},
            {"id": "thread_1", "type": "秘密", "complexity": "simple",
             "anchor": "town_0", "about": "失踪商队的下落"},
        ]
    }
    summary = _build_world_summary(frame, regions_summary, local_map, npcs_summary, threads_summary)
    # Must contain NPC sketches (not npc_0/npc_1 ids)
    assert "戴兜帽的旅人，目光深邃" in summary, "NPC sketch 0 missing from world summary"
    assert "身披铠甲的老兵" in summary, "NPC sketch 1 missing from world summary"
    assert "npc_0" not in summary, "Entity id npc_0 must not appear in world summary"
    assert "npc_1" not in summary, "Entity id npc_1 must not appear in world summary"
    # Must contain thread abouts (not thread_0/thread_1 ids)
    assert "皇室继承权之争" in summary, "Thread about 0 missing from world summary"
    assert "失踪商队的下落" in summary, "Thread about 1 missing from world summary"
    assert "thread_0" not in summary, "Entity id thread_0 must not appear in world summary"
    assert "thread_1" not in summary, "Entity id thread_1 must not appear in world summary"
    # Fix #10: venue NAMES must appear; raw ids must NOT
    assert "老醉酒馆" in summary, "Venue name '老醉酒馆' missing from world summary"
    assert "铁铺" in summary, "Venue name '铁铺' missing from world summary"
    assert "venue_0" not in summary, "Venue id 'venue_0' must not appear in world summary"
    assert "venue_1" not in summary, "Venue id 'venue_1' must not appear in world summary"


# ---------------------------------------------------------------------------
# Task 9: bootstrap_world orchestrator + reroll tests
# ---------------------------------------------------------------------------

# Campaign dir name used throughout Task 9 tests (deterministic seed derived from name)
_T9_CAMPAIGN_NAME = "bootstrap_fixed_name"

# Pre-computed parameters for _T9_CAMPAIGN_NAME at attempt=0
# (run `python3 -c "from app.engine import _derive_campaign_seed; ..."` to verify)
# campaign_seed=49346256305563
# frame: n_factions=5, n_regions=5
# local_map: n_extra_l2=1, n_venues=2  → venues=['venue_0','venue_1']
# npcs: n_npcs=3
# threads: n_threads=3, n_p=1
_T9_N_FACTIONS = 5
_T9_N_REGIONS = 5
_T9_N_VENUES = 2
_T9_N_NPCS = 3
_T9_N_THREADS = 3   # campaign threads
_T9_N_P = 1         # protagonist threads


def _make_t9_scripted_provider(attempt=0):
    """Build a ScriptedProvider with canned replies for all 9 LLM calls in bootstrap_world.

    Steps:
        1. gen_frame           → 1 call  (JSON world_name + central_conflict)
        2. gen_regions         → 1 call  (JSON regions array, n_regions entries)
        3. gen_local_map       → 1 call  (JSON town + venues + neighbors)
        4. gen_protagonist     → 1 call  (JSON name + origin + goal + objective)
        5. gen_factions        → 1 call  (JSON factions array, n_factions entries)
        6. gen_npcs            → 1 call  (JSON npcs array, n_npcs entries)
        7. gen_threads (campaign)    → 1 call
        8. gen_threads (protagonist) → 1 call
        9. gen_opening         → 1 call  (plain prose)
    """
    venues = [f"venue_{i}" for i in range(_T9_N_VENUES)]

    frame_reply = json.dumps({
        "world_name": f"测试世界_{attempt}",
        "central_conflict": f"测试冲突_{attempt}",
    })

    regions_reply = json.dumps({
        "regions": [
            {"name": f"地域{i}", "terrain": ["山地","荒漠","森林","水乡","平原"][i], "seed": f"地域{i}描述"}
            for i in range(_T9_N_REGIONS)
        ]
    })

    local_map_reply = json.dumps({
        "town": {"name": "起始镇", "seed": "古老的集镇"},
        "venues": [{"name": f"场所{i}", "seed": f"场所{i}描述"} for i in range(_T9_N_VENUES)],
        "neighbors": [{"name": "荒野", "seed": "危险地带"}],  # n_extra_l2=1
    })

    protagonist_reply = json.dumps({
        "name": f"测试主角_{attempt}",
        "origin": f"测试主角_{attempt}的身世背景，出身于一个普通家庭。",
        "goal": f"测试主角_{attempt}的核心目标",
        "objective": f"测试主角_{attempt}的当前任务",
    })

    factions_reply = json.dumps({
        "factions": [
            {"name": f"势力{i}", "motivation": f"势力{i}的动机"}
            for i in range(_T9_N_FACTIONS)
        ]
    })

    npcs_reply = json.dumps({
        "npcs": [
            {"sketch": f"NPC{i}外貌", "goal": f"NPC{i}目标", "secret": f"NPC{i}秘密"}
            for i in range(_T9_N_NPCS)
        ]
    })

    campaign_threads_reply = json.dumps({
        "lines": [
            {
                "about": f"暗线{i}表象",
                "description": f"暗线{i}描述",
                "trigger": f"暗线{i}触发",
                "secret": f"暗线{i}真相",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"暗线{i}阶段{j}"} for j in range(3)],
            }
            for i in range(_T9_N_THREADS)
        ]
    })

    prot_threads_reply = json.dumps({
        "lines": [
            {
                "about": f"主角线{i}表象",
                "description": f"主角线{i}描述",
                "trigger": f"主角线{i}触发",
                "secret": f"主角线{i}真相",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"主角线{i}阶段{j}"} for j in range(3)],
            }
            for i in range(_T9_N_P)
        ]
    })

    opening_reply = f"你踏入了测试世界_{attempt}的起始之地，四周充满了可能性。"

    return ScriptedProvider([
        frame_reply,
        regions_reply,
        local_map_reply,
        protagonist_reply,
        factions_reply,
        npcs_reply,
        campaign_threads_reply,
        prot_threads_reply,
        opening_reply,
    ])


def _make_t9_engine(tmp_path, *, attempt=0):
    """Build a fresh engine backed by a temp campaign dir with a fixed name."""
    from app.engine import build_engine
    campaign_dir = tmp_path / _T9_CAMPAIGN_NAME
    provider = _make_t9_scripted_provider(attempt=attempt)
    return build_engine(campaign_dir, provider=provider)


def test_bootstrap_world_returns_summary_dict(tmp_path):
    """bootstrap_world returns a dict with a 'summary' key containing frame info."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    result = bootstrap_world(engine, "东方武侠")
    assert isinstance(result, dict)
    assert "summary" in result
    assert "world_name" in result["summary"]


def test_bootstrap_world_has_internal_state(tmp_path):
    """Result contains _state with all keys required by reroll_step."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    result = bootstrap_world(engine, "东方武侠")
    state = result.get("_state", {})
    for key in (
        "frame", "regions_summary", "local_map", "factions_summary",
        "npcs_summary", "threads_summary", "protagonist", "protagonist_authored",
        "pitch", "attempts",
    ):
        assert key in state, f"_state missing key: {key}"
    # attempts must be a dict so reroll_step can look up per-step counters
    assert isinstance(state["attempts"], dict), "_state['attempts'] must be a dict"
    # protagonist_authored must have all four authored fields
    pa = state["protagonist_authored"]
    for field in ("name", "origin", "goal", "objective"):
        assert field in pa and pa[field].strip(), (
            f"protagonist_authored missing or empty field: {field}"
        )


def test_bootstrap_world_has_boundaries(tmp_path):
    """Result contains _boundaries with seq integers for major steps."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    result = bootstrap_world(engine, "东方武侠")
    boundaries = result.get("_boundaries", {})
    for step in ("frame", "regions", "local_map", "factions", "npcs", "threads", "opening"):
        assert step in boundaries, f"_boundaries missing step: {step}"
        assert isinstance(boundaries[step], int), f"boundary {step} is not int"


def _graph_places(world):
    """Return {id: attrs} for all Place entities in the ontology FactGraph."""
    onto = world.get("systems", {}).get("ontology")
    if onto is None:
        return {}
    return {k: v.attrs for k, v in onto.entities.items() if v.etype == "Place"}


def _graph_persons(world):
    """Return {id: entity} for all Person entities in the ontology FactGraph."""
    onto = world.get("systems", {}).get("ontology")
    if onto is None:
        return {}
    return {k: v for k, v in onto.entities.items() if v.etype == "Person"}


def test_bootstrap_world_regions_in_world(tmp_path):
    """After bootstrap, world has >= n_regions Place entities at level 1."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    places = _graph_places(engine.world)
    l1_places = [attrs for attrs in places.values() if attrs.get("level") == 1]
    assert len(l1_places) >= _T9_N_REGIONS, (
        f"Expected >= {_T9_N_REGIONS} L1 places, got {len(l1_places)}"
    )


def test_bootstrap_world_start_town_in_world(tmp_path):
    """After bootstrap, world has a level-2 settlement town_0."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    places = _graph_places(engine.world)
    assert "town_0" in places
    assert places["town_0"].get("level") == 2
    assert places["town_0"].get("kind") == "settlement"


def test_bootstrap_world_venues_in_world(tmp_path):
    """After bootstrap, world has >= 2 level-3 venue places."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    places = _graph_places(engine.world)
    venues = [attrs for attrs in places.values() if attrs.get("level") == 3 and attrs.get("kind") == "venue"]
    assert len(venues) >= 2, f"Expected >= 2 venues, got {len(venues)}"


def test_bootstrap_world_npcs_have_secret_facts(tmp_path):
    """After bootstrap, every NPC person entity has a secret fact in the graph."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    # Collect all facts with secrecy=='secret'
    events = list(engine.store.iter_events())
    secret_subjects = {
        e["deltas"]["subject"]
        for e in events
        if e["type"] == "fact_asserted" and e["deltas"].get("secrecy") == "secret"
    }
    # Must be >= 2 NPCs with secrets
    assert len(secret_subjects) >= 2, (
        f"Expected >= 2 NPCs with secret facts, got {len(secret_subjects)}"
    )


def test_bootstrap_world_lore_lines_in_world(tmp_path):
    """After bootstrap, world.systems.lore.lines has >= 3 entries."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    lore = engine.world.get("systems", {}).get("lore", {})
    lines = lore.get("lines", {})
    assert len(lines) >= 3, f"Expected >= 3 lore lines, got {len(lines)}"


def test_bootstrap_world_narration_recorded(tmp_path):
    """After bootstrap, exactly one narration_recorded event is in the store."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    events = list(engine.store.iter_events())
    narrations = [e for e in events if e["type"] == "narration_recorded"]
    assert len(narrations) == 1


def test_bootstrap_world_protagonist_in_world(tmp_path):
    """After bootstrap, protagonist is tracked and is the SOLE tracked Person."""
    from loop.bootstrap import bootstrap_world
    from app.engine import _PROTAGONIST_ID
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    persons = _graph_persons(engine.world)
    assert _PROTAGONIST_ID in persons
    assert persons[_PROTAGONIST_ID].tier == "tracked"
    # Sole-tracked-protagonist invariant: no NPC may be tier="tracked"
    for pid, entity in persons.items():
        if pid != _PROTAGONIST_ID:
            assert entity.tier != "tracked", (
                f"NPC {pid} is unexpectedly tracked — violates sole-tracked-protagonist invariant"
            )


def test_bootstrap_world_determinism(tmp_path):
    """Two engines with the SAME campaign dir name (→ same campaign_seed) + same
    scripted replies produce identical event-type histograms.

    Both engines use the leaf name 'determinism_test_bootstrap'; _derive_campaign_seed
    hashes only campaign_dir.name, so placing them under different parent paths
    (a/ vs b/) must NOT affect the seed.  The test asserts seed equality explicitly
    so that any regression in seed derivation would be caught here first.
    """
    from loop.bootstrap import bootstrap_world
    campaign_name = "determinism_test_bootstrap"
    from app.engine import build_engine

    def _scripted():
        return _make_t9_scripted_provider(attempt=0)

    # Same leaf name, different parent directories — seeds must be equal
    campaign_dir_a = tmp_path / "a" / campaign_name
    campaign_dir_b = tmp_path / "b" / campaign_name

    engine_a = build_engine(campaign_dir_a, provider=_scripted())
    engine_b = build_engine(campaign_dir_b, provider=_scripted())

    # Guard: seeds must be identical (seeding regression would surface here)
    assert engine_a.campaign_seed == engine_b.campaign_seed, (
        f"Seeds differ despite identical campaign name: "
        f"{engine_a.campaign_seed} vs {engine_b.campaign_seed}"
    )

    bootstrap_world(engine_a, "东方武侠")
    bootstrap_world(engine_b, "东方武侠")

    hist_a = Counter(e["type"] for e in engine_a.store.iter_events())
    hist_b = Counter(e["type"] for e in engine_b.store.iter_events())
    assert hist_a == hist_b, f"Histograms differ:\nA={hist_a}\nB={hist_b}"


def test_bootstrap_world_no_drift_invariant(tmp_path):
    """INVARIANT: every region id referenced by place_linked has a place_created."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    events = list(engine.store.iter_events())
    created_ids = {e["deltas"]["id"] for e in events if e["type"] == "place_created"}
    for ev in events:
        if ev["type"] == "place_linked":
            a = ev["deltas"]["a"]
            b = ev["deltas"]["b"]
            assert a in created_ids, f"place_linked references undeclared place: {a}"
            assert b in created_ids, f"place_linked references undeclared place: {b}"


def test_reroll_all_fresh_genesis(tmp_path):
    """reroll_all retracts all turn-0 events and runs a fresh genesis."""
    from loop.bootstrap import bootstrap_world, reroll_all
    from app.engine import build_engine
    campaign_dir = tmp_path / _T9_CAMPAIGN_NAME

    # First bootstrap
    engine = build_engine(campaign_dir, provider=_make_t9_scripted_provider(attempt=0))
    result1 = bootstrap_world(engine, "东方武侠")

    # Reroll: attach new scripted provider for fresh replies
    engine.provider = _make_t9_scripted_provider(attempt=1)
    result2 = reroll_all(engine, result1)

    assert isinstance(result2, dict)
    # Store should still have events — it's a fresh genesis
    events = list(engine.store.iter_events())
    assert len(events) > 0
    # Previous genesis events are retracted — only new ones survive
    narrations = [e for e in events if e["type"] == "narration_recorded"]
    assert len(narrations) == 1, "Should have exactly one narration after reroll_all"


def test_reroll_all_attempt_increments(tmp_path):
    """reroll_all bumps the overall attempt counter."""
    from loop.bootstrap import bootstrap_world, reroll_all
    from app.engine import build_engine
    campaign_dir = tmp_path / _T9_CAMPAIGN_NAME

    engine = build_engine(campaign_dir, provider=_make_t9_scripted_provider(attempt=0))
    result1 = bootstrap_world(engine, "东方武侠", attempt=0)

    engine.provider = _make_t9_scripted_provider(attempt=1)
    result2 = reroll_all(engine, result1)

    # The attempt counters in the new result's _state must be incremented
    attempts = result2.get("_state", {}).get("attempts", {})
    assert attempts.get("frame", 0) >= 1, f"Expected frame attempt >= 1, got {attempts}"
    assert "summary" in result2


def test_reroll_step_threads_replaces_lore(tmp_path):
    """reroll_step('threads') retracts threads+opening, re-runs them; region/npc events survive."""
    from loop.bootstrap import bootstrap_world, reroll_step
    from app.engine import build_engine
    campaign_dir = tmp_path / _T9_CAMPAIGN_NAME

    engine = build_engine(campaign_dir, provider=_make_t9_scripted_provider(attempt=0))
    result1 = bootstrap_world(engine, "东方武侠")

    # Verify lore lines exist before reroll
    lore_before = engine.world.get("systems", {}).get("lore", {}).get("lines", {})
    lore_ids_before = set(lore_before.keys())
    assert len(lore_ids_before) >= 3

    # Verify region events survive after reroll
    events_before = list(engine.store.iter_events())
    region_created_ids = {
        e["deltas"]["id"] for e in events_before
        if e["type"] == "place_created" and e["deltas"].get("level") == 1
    }

    # Reroll threads only — need fresh thread + opening replies
    # Attach the additional replies needed (threads+opening = 3 more)
    venues = [f"venue_{i}" for i in range(_T9_N_VENUES)]
    campaign_threads = json.dumps({
        "lines": [
            {
                "about": f"重掷暗线{i}表象",
                "description": f"重掷暗线{i}描述",
                "trigger": f"重掷暗线{i}触发",
                "secret": f"重掷暗线{i}真相",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"重掷暗线{i}阶段{j}"} for j in range(3)],
            }
            for i in range(_T9_N_THREADS)
        ]
    })
    prot_threads = json.dumps({
        "lines": [
            {
                "about": f"重掷主角线{i}表象",
                "description": f"重掷主角线{i}描述",
                "trigger": f"重掷主角线{i}触发",
                "secret": f"重掷主角线{i}真相",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"重掷主角线{i}阶段{j}"} for j in range(3)],
            }
            for i in range(_T9_N_P)
        ]
    })
    engine.provider = ScriptedProvider([
        campaign_threads,
        prot_threads,
        "你踏入了重掷后的起始之地。",
    ])

    result2 = reroll_step(engine, result1, "threads")
    assert isinstance(result2, dict)

    # Region places must still be in the store (not retracted)
    events_after = list(engine.store.iter_events())
    region_ids_after = {
        e["deltas"]["id"] for e in events_after
        if e["type"] == "place_created" and e["deltas"].get("level") == 1
    }
    assert region_created_ids == region_ids_after, (
        f"Region events lost after reroll_step: before={region_created_ids}, after={region_ids_after}"
    )

    # New lore lines exist
    lore_after = engine.world.get("systems", {}).get("lore", {}).get("lines", {})
    assert len(lore_after) >= 3


def test_reroll_step_threads_lore_ids_replaced(tmp_path):
    """After reroll_step('threads'), old lore line ids are replaced (re-created)."""
    from loop.bootstrap import bootstrap_world, reroll_step
    from app.engine import build_engine
    campaign_dir = tmp_path / _T9_CAMPAIGN_NAME

    engine = build_engine(campaign_dir, provider=_make_t9_scripted_provider(attempt=0))
    result1 = bootstrap_world(engine, "东方武侠")

    venues = [f"venue_{i}" for i in range(_T9_N_VENUES)]
    campaign_threads = json.dumps({
        "lines": [
            {
                "about": f"新暗线{i}",
                "description": f"新描述{i}",
                "trigger": f"新触发{i}",
                "secret": f"新真相{i}",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"新阶段{i}_{j}"} for j in range(3)],
            }
            for i in range(_T9_N_THREADS)
        ]
    })
    prot_threads = json.dumps({
        "lines": [
            {
                "about": f"新主角线{i}",
                "description": f"新主角描述{i}",
                "trigger": f"新主角触发{i}",
                "secret": f"新主角真相{i}",
                "l3_anchor": venues[0],
                "stages": [{"hint": f"新主角阶段{i}_{j}"} for j in range(3)],
            }
            for i in range(_T9_N_P)
        ]
    })
    engine.provider = ScriptedProvider([
        campaign_threads,
        prot_threads,
        "你踏入了新起始之地。",
    ])

    result2 = reroll_step(engine, result1, "threads")

    # After reroll: exactly one narration_recorded in store
    events_after = list(engine.store.iter_events())
    narrations = [e for e in events_after if e["type"] == "narration_recorded"]
    assert len(narrations) == 1, (
        f"Expected exactly 1 narration after reroll_step, got {len(narrations)}"
    )


def test_bootstrap_world_campaign_seeded_event(tmp_path):
    """bootstrap_world appends a campaign_seeded event with the engine's campaign_seed."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    events = list(engine.store.iter_events())
    seeded = [e for e in events if e["type"] == "campaign_seeded"]
    assert len(seeded) == 1
    assert seeded[0]["deltas"]["campaign_seed"] == engine.campaign_seed


def test_bootstrap_world_protagonist_in_venue(tmp_path):
    """After bootstrap, protagonist entity is located in a real venue (not starting_location)."""
    from loop.bootstrap import bootstrap_world
    from app.engine import _PROTAGONIST_ID
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    events = list(engine.store.iter_events())
    # Find last entity_moved for protagonist
    moves = [e for e in events if e["type"] == "entity_moved"
             and e["deltas"]["who"] == _PROTAGONIST_ID]
    assert moves, "Protagonist has no entity_moved event"
    last_loc = moves[-1]["deltas"]["to"]
    # Should be a venue_* (not starting_location which new_game uses)
    assert last_loc.startswith("venue_"), (
        f"Protagonist placed at '{last_loc}', expected a venue_* from local_map"
    )


# ---------------------------------------------------------------------------
# Fix #6b: gen_protagonist tests
# ---------------------------------------------------------------------------

def _base_local_map_for_protagonist():
    """Minimal local_map dict for gen_protagonist tests."""
    return {
        "start_town": "town_0",
        "venues": ["venue_0", "venue_1"],
        "l2": [{"id": "town_0", "kind": "settlement", "name": "起始镇"}],
    }


def test_gen_protagonist_returns_four_fields():
    """gen_protagonist returns an authored dict with name/origin/goal/objective."""
    from engine.oracle import Oracle, scene_seed
    frame = _base_frame()
    local_map = _base_local_map_for_protagonist()
    reply = json.dumps({
        "name": "沈云舟",
        "origin": "出身江南小镇的落魄秀才，父亲死于一场离奇大火。",
        "goal": "查明父亲死因，为家族翻案",
        "objective": "前往碎石镇寻找据说见过那场大火的老掌柜",
    })
    p = ScriptedProvider([reply])
    evs, authored = gen_protagonist(p, Oracle(42), frame, local_map)
    assert authored["name"] == "沈云舟"
    assert authored["origin"].strip()
    assert authored["goal"].strip()
    assert authored["objective"].strip()
    assert evs == []  # gen_protagonist emits no events itself


def test_gen_protagonist_stub_fallback_no_provider():
    """With provider=None, stub values are non-empty and function never raises."""
    from engine.oracle import Oracle
    frame = _base_frame()
    local_map = _base_local_map_for_protagonist()
    evs, authored = gen_protagonist(None, Oracle(7), frame, local_map)
    for field in ("name", "origin", "goal", "objective"):
        assert isinstance(authored.get(field), str), f"stub field {field!r} not a str"
        assert authored[field].strip(), f"stub field {field!r} is empty"
    assert evs == []


def test_gen_protagonist_never_raises_on_bad_llm():
    """gen_protagonist does not raise even when the LLM returns garbage."""
    from engine.oracle import Oracle

    class GarbageProvider:
        def supports_tools(self): return False
        def complete_messages(self, messages): return "{not valid json!!!"
        def complete(self, system, user, **kw): return "{not valid json!!!"

    frame = _base_frame()
    local_map = _base_local_map_for_protagonist()
    evs, authored = gen_protagonist(GarbageProvider(), Oracle(1), frame, local_map)
    for field in ("name", "origin", "goal", "objective"):
        assert authored[field].strip(), f"fallback field {field!r} is empty after bad LLM"


def test_gen_protagonist_stub_fallback_is_deterministic():
    """Stub fallback with same seed produces identical output across two calls."""
    from engine.oracle import Oracle
    frame = _base_frame()
    local_map = _base_local_map_for_protagonist()
    _, a1 = gen_protagonist(None, Oracle(55), frame, local_map)
    _, a2 = gen_protagonist(None, Oracle(55), frame, local_map)
    assert a1 == a2


# ---------------------------------------------------------------------------
# Fix #6b: authored protagonist integration tests (bootstrap_world)
# ---------------------------------------------------------------------------

def test_bootstrap_world_protagonist_character_created_authored(tmp_path):
    """character_created for protagonist uses AUTHORED sketch (origin) and goal, not generic stubs."""
    from loop.bootstrap import bootstrap_world
    from app.engine import _PROTAGONIST_ID
    engine = _make_t9_engine(tmp_path)
    result = bootstrap_world(engine, "东方武侠")
    events = list(engine.store.iter_events())
    char_ev = next(
        (e for e in events
         if e["type"] == "character_created" and e["deltas"]["id"] == _PROTAGONIST_ID),
        None
    )
    assert char_ev is not None, "No character_created for protagonist"
    # Authored values must NOT be the generic placeholder strings
    assert char_ev["deltas"]["sketch"] != "一位踏上旅途的冒险者", (
        "sketch is still generic stub — authored origin not used"
    )
    assert char_ev["deltas"]["goal"] != "探索这个世界", (
        "goal is still generic stub — authored goal not used"
    )
    # Must match what the scripted provider returned
    assert char_ev["deltas"]["sketch"] == "测试主角_0的身世背景，出身于一个普通家庭。"
    assert char_ev["deltas"]["goal"] == "测试主角_0的核心目标"


def test_bootstrap_world_protagonist_name_fact_asserted(tmp_path):
    """bootstrap_world emits a fact_asserted for protagonist name with secrecy='public'."""
    from loop.bootstrap import bootstrap_world
    from app.engine import _PROTAGONIST_ID
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    events = list(engine.store.iter_events())
    name_facts = [
        e for e in events
        if e["type"] == "fact_asserted"
        and e["deltas"]["subject"] == _PROTAGONIST_ID
        and e["deltas"]["predicate"] == "真名"
        and e["deltas"].get("secrecy") == "public"
    ]
    assert len(name_facts) == 1, f"Expected 1 protagonist 真名 fact, got {len(name_facts)}"
    assert name_facts[0]["deltas"]["value"] == "测试主角_0"


def test_bootstrap_world_protagonist_objective_fact_asserted(tmp_path):
    """bootstrap_world emits a fact_asserted for protagonist 目标 with secrecy='public'."""
    from loop.bootstrap import bootstrap_world
    from app.engine import _PROTAGONIST_ID
    engine = _make_t9_engine(tmp_path)
    bootstrap_world(engine, "东方武侠")
    events = list(engine.store.iter_events())
    obj_facts = [
        e for e in events
        if e["type"] == "fact_asserted"
        and e["deltas"]["subject"] == _PROTAGONIST_ID
        and e["deltas"]["predicate"] == "目标"
        and e["deltas"].get("secrecy") == "public"
    ]
    assert len(obj_facts) == 1, f"Expected 1 protagonist 目标 fact, got {len(obj_facts)}"
    assert obj_facts[0]["deltas"]["value"] == "测试主角_0的当前任务"


def test_bootstrap_world_summary_has_protagonist_fields(tmp_path):
    """bootstrap_world summary contains protagonist_name/origin/goal/objective."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    result = bootstrap_world(engine, "东方武侠")
    summary = result["summary"]
    for key in ("protagonist_name", "protagonist_origin", "protagonist_goal", "objective"):
        assert key in summary, f"summary missing key: {key}"
        assert isinstance(summary[key], str) and summary[key].strip(), (
            f"summary[{key!r}] is empty"
        )
    assert summary["protagonist_name"] == "测试主角_0"
    assert summary["objective"] == "测试主角_0的当前任务"


def test_bootstrap_world_reroll_step_preserves_protagonist_authored(tmp_path):
    """reroll_step preserves protagonist_authored from prev_result in new result._state."""
    from loop.bootstrap import bootstrap_world, reroll_step
    from app.engine import build_engine
    campaign_dir = tmp_path / _T9_CAMPAIGN_NAME

    engine = build_engine(campaign_dir, provider=_make_t9_scripted_provider(attempt=0))
    result1 = bootstrap_world(engine, "东方武侠")

    venues = [f"venue_{i}" for i in range(_T9_N_VENUES)]
    campaign_threads = json.dumps({
        "lines": [
            {
                "about": f"新暗线{i}", "description": f"新描述{i}",
                "trigger": f"新触发{i}", "secret": f"新真相{i}",
                "l3_anchor": venues[i % len(venues)],
                "stages": [{"hint": f"新阶段{i}"}],
            }
            for i in range(_T9_N_THREADS)
        ]
    })
    prot_threads = json.dumps({
        "lines": [
            {
                "about": f"新主角线{i}", "description": f"新主角描述{i}",
                "trigger": f"新主角触发{i}", "secret": f"新主角真相{i}",
                "l3_anchor": venues[0],
                "stages": [{"hint": f"新主角阶段{i}"}],
            }
            for i in range(_T9_N_P)
        ]
    })
    engine.provider = ScriptedProvider([campaign_threads, prot_threads, "新开场。"])
    result2 = reroll_step(engine, result1, "threads")

    # protagonist_authored must persist in new state
    pa = result2["_state"].get("protagonist_authored")
    assert pa is not None, "protagonist_authored missing from reroll_step result._state"
    for field in ("name", "origin", "goal", "objective"):
        assert pa[field].strip(), f"protagonist_authored[{field!r}] empty after reroll_step"
    # summary must also carry protagonist fields
    summary = result2["summary"]
    for key in ("protagonist_name", "protagonist_origin", "protagonist_goal", "objective"):
        assert key in summary and summary[key].strip(), f"summary missing {key!r} after reroll_step"


# ---------------------------------------------------------------------------
# Fix #1: progress callback tests
# ---------------------------------------------------------------------------

def test_bootstrap_world_progress_called_once_per_step(tmp_path):
    """progress callback is invoked exactly 8 times (one per generation step)."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    calls = []
    bootstrap_world(engine, "东方武侠", progress=lambda idx, total, label: calls.append((idx, total, label)))
    assert len(calls) == 8, f"Expected 8 progress calls, got {len(calls)}: {calls}"


def test_bootstrap_world_progress_increasing_indices(tmp_path):
    """progress indices increase monotonically from 1 to total."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    calls = []
    bootstrap_world(engine, "东方武侠", progress=lambda idx, total, label: calls.append((idx, total, label)))
    indices = [c[0] for c in calls]
    assert indices == sorted(indices), f"Progress indices not sorted: {indices}"
    assert indices[0] >= 1, "First index must be >= 1"
    total = calls[0][1]
    assert total > 0, "total must be > 0"
    for idx, t, label in calls:
        assert t == total, f"total changed mid-bootstrap: {t} != {total}"


def test_bootstrap_world_progress_labels_non_empty(tmp_path):
    """All progress labels are non-empty strings."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    calls = []
    bootstrap_world(engine, "东方武侠", progress=lambda idx, total, label: calls.append((idx, total, label)))
    for idx, total, label in calls:
        assert isinstance(label, str) and label.strip(), (
            f"progress label empty at index {idx}"
        )


def test_bootstrap_world_progress_none_is_clean_noop(tmp_path):
    """Default progress=None runs without error (existing caller contract preserved)."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)
    # Must not raise; result must be valid
    result = bootstrap_world(engine, "东方武侠")
    assert "summary" in result


def test_bootstrap_world_progress_exception_does_not_abort(tmp_path):
    """A progress callback that raises must not abort genesis."""
    from loop.bootstrap import bootstrap_world
    engine = _make_t9_engine(tmp_path)

    def bad_progress(idx, total, label):
        raise RuntimeError("simulated progress failure")

    result = bootstrap_world(engine, "东方武侠", progress=bad_progress)
    assert "summary" in result
    assert result["summary"].get("world_name"), "genesis aborted due to bad progress callback"
