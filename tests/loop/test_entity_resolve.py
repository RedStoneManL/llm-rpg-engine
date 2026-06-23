"""#R7 A' / Phase 1 — pre-validate augment: resolve name-refs in moves/places/
links/materialize to entity ids; mint+inject cast/places creates (+真名 fact +
first-seen breadcrumb + tier) for unknown names; dedup; never raise; byte-identical
when all refs are already valid ids."""
from facts.graph import FactGraph
from kernel.turncommit import TurnCommit
from loop.entity_resolve import augment_unresolved_refs


def _world(g):
    return {"systems": {"ontology": g}}


def _commit(**sections):
    return TurnCommit(narration="x", sections=dict(sections))


def _name(g):
    pass


def test_existing_id_refs_unchanged_byte_identical():
    g = FactGraph()
    g.add_entity("protagonist", "Person")
    g.add_entity("venue_0", "Place")
    c = _commit(moves=[{"who": "protagonist", "to": "venue_0"}])
    minted = augment_unresolved_refs(c, _world(g), scene="venue_0", day=1)
    assert minted == []
    assert c.sections["moves"] == [{"who": "protagonist", "to": "venue_0"}]
    assert "cast" not in c.sections and "facts" not in c.sections


def test_unknown_person_name_mints_cast_and_zhenming():
    g = FactGraph()
    g.add_entity("protagonist", "Person")
    g.add_entity("venue_0", "Place")
    c = _commit(moves=[{"who": "卡恩", "to": "venue_0"}])
    minted = augment_unresolved_refs(c, _world(g), scene="酒馆", day=5)
    assert len(minted) == 1
    pid = c.sections["moves"][0]["who"]
    assert pid.startswith("npc_auto")
    assert c.sections["moves"][0]["to"] == "venue_0"      # existing place unchanged
    cast = [x for x in c.sections["cast"] if x["id"] == pid]
    assert cast and cast[0]["op"] == "create" and cast[0]["tier"] == "mentioned"
    assert cast[0]["sketch"] and cast[0]["goal"]          # cast validate needs both
    assert "首次现身" in cast[0]["sketch"] and "酒馆" in cast[0]["sketch"]
    zm = [x for x in c.sections["facts"]
          if x["subject"] == pid and x["predicate"] == "真名"]
    assert zm and zm[0]["value"] == "卡恩"


def test_unknown_place_name_mints_places():
    g = FactGraph()
    g.add_entity("protagonist", "Person")
    c = _commit(moves=[{"who": "protagonist", "to": "后厨"}])
    minted = augment_unresolved_refs(c, _world(g), scene="酒馆", day=2)
    lid = c.sections["moves"][0]["to"]
    assert lid.startswith("place_auto")
    p = [x for x in c.sections["places"] if x["id"] == lid]
    assert p and p[0]["kind"] == "venue" and p[0]["level"] == 3 and p[0]["seed"] == "后厨"


def test_resolve_by_zhenming_no_remint():
    g = FactGraph()
    g.add_entity("protagonist", "Person")
    g.add_entity("npc_3", "Person")
    g.assert_fact("npc_3", "真名", "老柯", day=1, turn=0, source_event="t")
    g.add_entity("venue_0", "Place")
    c = _commit(moves=[{"who": "老柯", "to": "venue_0"}])
    minted = augment_unresolved_refs(c, _world(g), scene="x", day=3)
    assert minted == []                                   # resolved, not minted
    assert c.sections["moves"][0]["who"] == "npc_3"


def test_resolve_to_this_turn_cast_name():
    g = FactGraph()
    g.add_entity("protagonist", "Person")
    g.add_entity("venue_0", "Place")
    c = _commit(
        cast=[{"id": "npc_kahn", "op": "create", "name": "卡恩",
               "sketch": "情报贩子", "goal": "卖密信"}],
        moves=[{"who": "卡恩", "to": "venue_0"}],
    )
    minted = augment_unresolved_refs(c, _world(g), scene="x", day=1)
    assert minted == []                                   # matched the cast's name
    assert c.sections["moves"][0]["who"] == "npc_kahn"


def test_dedup_within_turn():
    g = FactGraph()
    g.add_entity("protagonist", "Person")
    g.add_entity("venue_0", "Place")
    c = _commit(moves=[{"who": "卡恩", "to": "venue_0"},
                       {"who": "卡恩", "to": "venue_0"}])
    minted = augment_unresolved_refs(c, _world(g), scene="x", day=1)
    assert len(minted) == 1
    assert c.sections["moves"][0]["who"] == c.sections["moves"][1]["who"]


def test_no_ontology_or_junk_never_raises():
    c = _commit(moves=[{"who": "x"}])
    assert augment_unresolved_refs(c, {}, scene="x", day=1) == []
    assert augment_unresolved_refs(c, {"systems": {}}, scene="x", day=1) == []
    g = FactGraph()
    c2 = _commit(moves="not a list", cast=[None, 42], links=[{"a": "卡"}])
    augment_unresolved_refs(c2, _world(g), scene="x", day=1)  # must not raise


def test_produce_turn_autocreates_new_named_npc(tmp_path):
    # End-to-end through produce_turn: a move to a brand-new NPC name is NOT
    # dropped — the augment mints+injects the entity so the commit validates.
    from app.engine import build_engine, new_game
    from loop.turn import produce_turn
    from llm.provider import FakeLLMProvider

    eng = build_engine(tmp_path / "c", provider=FakeLLMProvider())
    new_game(eng)

    class _Stub:
        def produce(self, registry, world, scene, player_input, *,
                    provider, embedder=None, repair=None):
            return TurnCommit(narration="一个叫卡恩的人走了进来。",
                              sections={"moves": [{"who": "卡恩", "to": "town_0"}]})

        def repair_sections(self, failing, errors, *, provider):
            raise NotImplementedError

    scene = {"protagonist": "protagonist", "day": 1, "location": "town_0"}
    commit, attempts, dropped = produce_turn(
        eng.registry, eng.world, scene, "看看谁来了",
        strategy=_Stub(), provider=eng.provider)

    assert "moves" not in (dropped or [])                     # move survived
    cast_ids = [c["id"] for c in commit.sections.get("cast", [])
                if isinstance(c, dict) and c.get("op") == "create"]
    assert any(i.startswith("npc_auto") for i in cast_ids)    # 卡恩 minted
    zm = [f for f in commit.sections.get("facts", [])
          if f.get("predicate") == "真名" and f.get("value") == "卡恩"]
    assert len(zm) == 1
    assert commit.sections["moves"][0]["who"].startswith("npc_auto")
