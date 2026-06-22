"""tests/loop/test_lore_disclosure_B.py — Task 2: B-mode station-push fragment.

Build a world:
  qingshi_town (L2) contains market (L3) and tavern (L3)
  other_town   (L2) contains bazaar  (L3)
  protagonist hero at market

Lore lines:
  line_a  l3_anchor=market,  anchor=qingshi_town  advanced stage 0 → has beat+clue
  line_b  l3_anchor=market,  anchor=qingshi_town  advanced stage 0 → has beat+clue
  line_c  l3_anchor=tavern,  anchor=qingshi_town  advanced stage 0 → has beat+clue
  line_d  l3_anchor=bazaar,  anchor=other_town    advanced stage 0 → has beat+clue

Assertions:
  station_push_fragment(...) for hero@market:
    - contains line_a and line_b beat/clue text  (L1 — exact venue)
    - contains line_c description text           (L0 — same town, different venue)
    - does NOT contain line_c beat or clue
    - does NOT contain line_d description or clue (different town)
  station_push_fragment(...) when hero has no lore in range → None
"""
import pytest

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.lore import LoreSystem
from systems.place import PlaceSystem
from loop.lore_disclosure import station_push_fragment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(PlaceSystem())
    r.register(LoreSystem())
    return r


def _lore_created(lid, about, description, trigger, l3_anchor, anchor,
                  stages, day=1, scene="s1", turn=1):
    return kernel_event("lore_created", day=day, scene=scene, summary=f"暗线:{lid}",
                        deltas={
                            "id": lid,
                            "complexity": "medium",
                            "about": about,
                            "secret": f"{lid}的秘密",
                            "anchor": anchor,
                            "description": description,
                            "trigger": trigger,
                            "l3_anchor": l3_anchor,
                            "stages": stages,
                            "threshold": 100,
                        }, turn=turn)


def _lore_advanced(lid, stage_idx, hint, day=1, scene="s1", turn=2):
    return kernel_event("lore_advanced", day=day, scene=scene, summary=f"推进:{lid}",
                        deltas={"id": lid, "stage_idx": stage_idx, "hint": hint},
                        turn=turn)


# ---------------------------------------------------------------------------
# Fixture: world + scene
# ---------------------------------------------------------------------------

def _build_world():
    """Build the full fixture world and return (registry, world, scene)."""
    r = _reg()
    events = [
        # --- Places ---
        # L2 towns
        kernel_event("place_created", day=1, scene="s1", summary="qingshi_town",
                     deltas={"id": "qingshi_town", "level": 2, "kind": "settlement"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="other_town",
                     deltas={"id": "other_town", "level": 2, "kind": "settlement"}, turn=0),
        # L3 venues under qingshi_town
        kernel_event("place_created", day=1, scene="s1", summary="market",
                     deltas={"id": "market", "level": 3, "kind": "venue",
                             "parent": "qingshi_town"}, turn=0),
        kernel_event("place_created", day=1, scene="s1", summary="tavern",
                     deltas={"id": "tavern", "level": 3, "kind": "venue",
                             "parent": "qingshi_town"}, turn=0),
        # L3 venue under other_town
        kernel_event("place_created", day=1, scene="s1", summary="bazaar",
                     deltas={"id": "bazaar", "level": 3, "kind": "venue",
                             "parent": "other_town"}, turn=0),
        # adjacency links (needed for place_linked consistency — not required for disclosure)
        kernel_event("place_linked", day=1, scene="s1", summary="link",
                     deltas={"a": "market", "b": "tavern"}, turn=0),
        # --- Protagonist entity (via ontology direct add via a character_created-like path)
        # PlaceSystem+OntologySystem handle entity_moved; we only need the entity in the graph.
        # OntologySystem's entity_created event adds the entity.
        kernel_event("entity_created", day=1, scene="s1", summary="hero",
                     deltas={"id": "hero", "etype": "Character", "tier": "tracked"}, turn=0),
        # Move hero to market
        kernel_event("entity_moved", day=1, scene="s1", summary="hero→market",
                     deltas={"who": "hero", "to": "market"}, turn=0),

        # --- Lore lines ---
        # line_a: at market
        _lore_created("line_a", about="line_a事件",
                      description="line_a的描述：集市的低语",
                      trigger="玩家打听集市往事",
                      l3_anchor="market", anchor="qingshi_town",
                      stages=[{"hint": "line_a_beat_stage0"}, {"hint": "line_a_beat_stage1"}]),
        # line_b: also at market
        _lore_created("line_b", about="line_b事件",
                      description="line_b的描述：集市的谣言",
                      trigger="玩家询问货商",
                      l3_anchor="market", anchor="qingshi_town",
                      stages=[{"hint": "line_b_beat_stage0"}, {"hint": "line_b_beat_stage1"}],
                      turn=3),
        # line_c: at tavern (same town, different venue)
        _lore_created("line_c", about="line_c事件",
                      description="line_c的描述：酒馆传言",
                      trigger="玩家在酒馆喝酒",
                      l3_anchor="tavern", anchor="qingshi_town",
                      stages=[{"hint": "line_c_beat_stage0_SHOULD_NOT_APPEAR"}],
                      turn=4),
        # line_d: at bazaar in other_town (out of town — must not appear)
        _lore_created("line_d", about="line_d事件",
                      description="line_d的描述：他乡集市的秘密",
                      trigger="玩家在他乡集市打听",
                      l3_anchor="bazaar", anchor="other_town",
                      stages=[{"hint": "line_d_beat_stage0_MUST_NOT_APPEAR"}],
                      turn=5),

        # Advance each line to stage 0 so they have a beat and a clue
        _lore_advanced("line_a", stage_idx=0, hint="line_a已知线索：有人目击奇怪车队", turn=10),
        _lore_advanced("line_b", stage_idx=0, hint="line_b已知线索：货单出现异常", turn=11),
        _lore_advanced("line_c", stage_idx=0, hint="line_c已知线索：酒馆老板欲言又止", turn=12),
        _lore_advanced("line_d", stage_idx=0, hint="line_d已知线索：他乡的陌生线索", turn=13),
    ]
    w = project(r, events)
    scene = {"protagonist": "hero", "day": 1, "id": "s1", "location": "market"}
    return r, w, scene


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStationPushFragment:

    def test_market_lines_l1_content_present(self):
        """The two market lines' beat/clue text appear in the fragment."""
        r, w, scene = _build_world()
        result = station_push_fragment(r, w, scene)
        assert result is not None, "expected a non-None fragment"
        # beat (stage hint) and latest_clue should both be in L1 content
        assert "line_a_beat_stage0" in result
        assert "line_a已知线索：有人目击奇怪车队" in result
        assert "line_b_beat_stage0" in result
        assert "line_b已知线索：货单出现异常" in result

    def test_tavern_line_l0_description_present(self):
        """The tavern line's description appears (L0 index — same town, different venue)."""
        r, w, scene = _build_world()
        result = station_push_fragment(r, w, scene)
        assert result is not None
        assert "line_c的描述：酒馆传言" in result

    def test_tavern_line_beat_absent(self):
        """The tavern line's beat/clue text must NOT appear (only L0 for out-of-venue lines)."""
        r, w, scene = _build_world()
        result = station_push_fragment(r, w, scene)
        assert result is not None
        assert "line_c_beat_stage0_SHOULD_NOT_APPEAR" not in result
        assert "line_c已知线索：酒馆老板欲言又止" not in result

    def test_other_town_line_absent(self):
        """The other_town bazaar line must not appear at all."""
        r, w, scene = _build_world()
        result = station_push_fragment(r, w, scene)
        assert result is not None
        assert "line_d的描述：他乡集市的秘密" not in result
        assert "line_d_beat_stage0_MUST_NOT_APPEAR" not in result
        assert "line_d已知线索：他乡的陌生线索" not in result

    def test_returns_none_when_no_lore_in_range(self):
        """When protagonist is at a venue with no lines in its town, return None."""
        # Build a world where bazaar is in other_town, hero has no lore nearby
        r = _reg()
        events = [
            kernel_event("place_created", day=1, scene="s1", summary="lone_town",
                         deltas={"id": "lone_town", "level": 2, "kind": "settlement"}, turn=0),
            kernel_event("place_created", day=1, scene="s1", summary="lone_venue",
                         deltas={"id": "lone_venue", "level": 3, "kind": "venue",
                                 "parent": "lone_town"}, turn=0),
            kernel_event("entity_created", day=1, scene="s1", summary="hero2",
                         deltas={"id": "hero2", "etype": "Character", "tier": "tracked"}, turn=0),
            kernel_event("entity_moved", day=1, scene="s1", summary="hero2→lone_venue",
                         deltas={"who": "hero2", "to": "lone_venue"}, turn=0),
        ]
        w = project(r, events)
        scene = {"protagonist": "hero2", "day": 1, "id": "s1", "location": "lone_venue"}
        result = station_push_fragment(r, w, scene)
        assert result is None

    def test_returns_none_when_protagonist_has_no_location(self):
        """When the protagonist has no located_in, return None."""
        r = _reg()
        events = [
            kernel_event("entity_created", day=1, scene="s1", summary="wanderer",
                         deltas={"id": "wanderer", "etype": "Character", "tier": "tracked"}, turn=0),
        ]
        w = project(r, events)
        scene = {"protagonist": "wanderer", "day": 1, "id": "s1", "location": "nowhere"}
        result = station_push_fragment(r, w, scene)
        assert result is None

    def test_inactive_lines_excluded(self):
        """Lines with status != 'active' must be excluded."""
        r = _reg()
        events = [
            kernel_event("place_created", day=1, scene="s1", summary="t",
                         deltas={"id": "t2", "level": 2, "kind": "settlement"}, turn=0),
            kernel_event("place_created", day=1, scene="s1", summary="v",
                         deltas={"id": "v2", "level": 3, "kind": "venue", "parent": "t2"}, turn=0),
            kernel_event("entity_created", day=1, scene="s1", summary="h3",
                         deltas={"id": "h3", "etype": "Character", "tier": "tracked"}, turn=0),
            kernel_event("entity_moved", day=1, scene="s1", summary="h3→v2",
                         deltas={"who": "h3", "to": "v2"}, turn=0),
            _lore_created("resolved_line", about="已解决",
                          description="resolved_description",
                          trigger="never",
                          l3_anchor="v2", anchor="t2",
                          stages=[{"hint": "resolved_beat"}], turn=1),
            _lore_advanced("resolved_line", stage_idx=0, hint="resolved_clue", turn=2),
        ]
        w = project(r, events)
        # Manually flip status to resolved (direct mutation of world slice for this test)
        w["systems"]["lore"]["lines"]["resolved_line"]["status"] = "resolved"
        scene = {"protagonist": "h3", "day": 1, "id": "s1", "location": "v2"}
        result = station_push_fragment(r, w, scene)
        assert result is None or "resolved_description" not in (result or "")
