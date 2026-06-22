"""Loop-level tests for fix #10 (venue names in gen_local_map summary / prompts).

These are companion tests to tests/app/test_fix1011.py, which covers the main()
integration path.  Here we test the bootstrap.py functions directly without
running the full CLI.

Fix #10 (a): gen_local_map summary carries venue_names {id: name}.
Fix #10 (b): gen_protagonist and gen_opening prompts include venue/town NAMES
             and the no-id instruction.
"""
from __future__ import annotations

import json

import pytest

from engine.oracle import Oracle
from loop.bootstrap import (
    gen_local_map,
    gen_opening,
    gen_protagonist,
    _build_world_summary,
)


# ---------------------------------------------------------------------------
# ScriptedProvider (local — no cross-file imports)
# ---------------------------------------------------------------------------

class ScriptedProvider:
    """Tiny scripted provider that replays canned replies."""
    def __init__(self, replies):
        self._r = list(replies)
        self.i = 0

    def supports_tools(self):
        return False

    def _next(self):
        r = self._r[self.i] if self.i < len(self._r) else self._r[-1]
        self.i += 1
        return r

    def complete_messages(self, messages):
        return self._next()

    def complete(self, system, user, **kw):
        return self._next()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_frame():
    return {
        "genre": "x", "tone": "悬疑", "central_conflict": "c",
        "world_name": "测试世界", "n_factions": 3, "n_regions": 4,
    }


def _base_regions_summary(n=4):
    return {
        "start_region": "region_0",
        "regions": [
            {"id": f"region_{i}", "name": f"地域{i}",
             "tier": "start" if i == 0 else "neighbor", "terrain": "平原"}
            for i in range(n)
        ],
        "density": 0.3,
    }


def _local_map_reply(n_venues=2, n_neighbors=1, town_name="青瓦集镇",
                     venue_names=None):
    """Return a JSON string for gen_local_map's LLM call."""
    venue_names = venue_names or [f"场所{i}" for i in range(n_venues)]
    return json.dumps({
        "town": {"name": town_name, "seed": "古老集镇"},
        "venues": [{"name": venue_names[i], "seed": f"s{i}"} for i in range(n_venues)],
        "neighbors": [{"name": f"邻地{i}", "seed": "s"} for i in range(n_neighbors)],
    })


# ---------------------------------------------------------------------------
# Tests: gen_local_map — venue_names in summary
# ---------------------------------------------------------------------------

class TestGenLocalMapVenueNamesDirect:
    """Direct tests for the venue_names field in gen_local_map summary."""

    def test_venue_names_key_present(self):
        """Summary must include 'venue_names'."""
        frame = _base_frame()
        regions = _base_regions_summary()
        p = ScriptedProvider([_local_map_reply(n_venues=2, n_neighbors=1,
                                               venue_names=["铁铺", "酒馆"])])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        assert "venue_names" in summ

    def test_venue_names_maps_all_ids(self):
        """Every venue_id in summary['venues'] has an entry in venue_names."""
        frame = _base_frame()
        regions = _base_regions_summary()
        p = ScriptedProvider([_local_map_reply(n_venues=2, n_neighbors=1,
                                               venue_names=["铁铺", "酒馆"])])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        vn = summ["venue_names"]
        for vid in summ["venues"]:
            assert vid in vn, f"venue_names missing {vid!r}"

    def test_venue_names_values_are_authored_names(self):
        """venue_names values must be the LLM-authored names (non-empty strings)."""
        frame = _base_frame()
        regions = _base_regions_summary()
        p = ScriptedProvider([_local_map_reply(
            n_venues=2, n_neighbors=1,
            venue_names=["UNIQUE_铁铺", "UNIQUE_酒馆"],
        )])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        assert "UNIQUE_铁铺" in summ["venue_names"].values()
        assert "UNIQUE_酒馆" in summ["venue_names"].values()

    def test_venue_names_fallback_not_empty(self):
        """Fallback path (provider=None) still populates venue_names."""
        frame = _base_frame()
        regions = _base_regions_summary()
        _, summ = gen_local_map(None, Oracle(1), frame, regions)
        assert "venue_names" in summ
        for vid in summ["venues"]:
            assert summ["venue_names"].get(vid, "").strip(), \
                f"Fallback venue_names[{vid!r}] empty"

    def test_venue_names_no_raw_ids_as_values(self):
        """venue_names values should not be the same as their keys (not id echo)."""
        frame = _base_frame()
        regions = _base_regions_summary()
        p = ScriptedProvider([_local_map_reply(
            n_venues=2, n_neighbors=1,
            venue_names=["集市广场", "破旧酒馆"],
        )])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        for vid, vname in summ["venue_names"].items():
            assert vname != vid, (
                f"venue_names[{vid!r}] == {vid!r} — looks like an id echo, not a name"
            )


# ---------------------------------------------------------------------------
# Tests: gen_protagonist — prompt contains names, not ids
# ---------------------------------------------------------------------------

class TestGenProtagonistPromptNamesLoop:
    """gen_protagonist must receive and embed venue/town NAMES in its LLM prompt."""

    def _local_map_with_names(self, n_venues=2):
        return {
            "start_town": "town_0",
            "venues": [f"venue_{i}" for i in range(n_venues)],
            "venue_names": {f"venue_{i}": f"VENUE_NAME_{i}" for i in range(n_venues)},
            "l2": [{"id": "town_0", "kind": "settlement", "name": "TOWN_NAME"}],
        }

    def _capturing_provider(self, captured):
        """Return a provider that records every user prompt passed to it."""
        class _Cap:
            def supports_tools(self): return False
            def complete_messages(self, messages):
                for m in messages:
                    if m.get("role") == "user":
                        captured.append(m["content"])
                return json.dumps({
                    "name": "A", "origin": "B", "goal": "C",
                    "objective": "D",
                })
            def complete(self, system, user, **kw):
                captured.append(user)
                return json.dumps({
                    "name": "A", "origin": "B", "goal": "C",
                    "objective": "D",
                })
        return _Cap()

    def test_town_name_in_prompt(self):
        """The town's human-readable name must appear in the gen_protagonist prompt."""
        captured = []
        local_map = self._local_map_with_names()
        gen_protagonist(self._capturing_provider(captured), Oracle(1), _base_frame(), local_map)
        combined = "\n".join(captured)
        assert "TOWN_NAME" in combined, (
            f"Town name not found in gen_protagonist prompt:\n{combined!r}"
        )
        # The raw id must not appear as the LOCATION designator
        assert "起始小镇：town_0" not in combined, (
            "Raw id 'town_0' used as location designator — must use name instead"
        )

    def test_venue_name_in_prompt(self):
        """The first venue's human-readable name must appear in the gen_protagonist prompt."""
        captured = []
        local_map = self._local_map_with_names()
        gen_protagonist(self._capturing_provider(captured), Oracle(1), _base_frame(), local_map)
        combined = "\n".join(captured)
        assert "VENUE_NAME_0" in combined, (
            f"Venue name not found in gen_protagonist prompt:\n{combined!r}"
        )
        # The raw id must not appear as the VENUE designator
        assert "起始场所：venue_0" not in combined, (
            "Raw id 'venue_0' used as venue designator — must use name instead"
        )

    def test_no_id_instruction_in_prompt(self):
        """The no-id instruction must appear in the gen_protagonist prompt."""
        captured = []
        local_map = self._local_map_with_names()
        gen_protagonist(self._capturing_provider(captured), Oracle(1), _base_frame(), local_map)
        combined = "\n".join(captured)
        assert "town_0 / venue_0" in combined or "内部 id" in combined, (
            f"No-id instruction not found in gen_protagonist prompt:\n{combined!r}"
        )


# ---------------------------------------------------------------------------
# Tests: gen_opening — scene_loc_name flows into prompt
# ---------------------------------------------------------------------------

class TestGenOpeningSceneLocName:
    """gen_opening must use scene_loc_name in the prompt when provided."""

    def _capturing_provider(self, captured):
        class _Cap:
            def supports_tools(self): return False
            def complete(self, system, user, **kw):
                captured.append({"system": system, "user": user})
                return "开场叙事。"
            def complete_messages(self, messages):
                return "开场叙事。"
        return _Cap()

    def test_scene_loc_name_replaces_id_in_user_prompt(self):
        """When scene_loc_name is given, the user prompt must use it as the location designator."""
        frame = _base_frame()
        captured = []
        gen_opening(
            self._capturing_provider(captured), frame, "世界摘要",
            scene_loc="venue_0", scene_loc_name="老醉酒馆",
        )
        assert captured, "Provider was never called"
        user_prompt = captured[0]["user"]
        assert "老醉酒馆" in user_prompt, (
            f"scene_loc_name not found in gen_opening user prompt:\n{user_prompt!r}"
        )
        # The raw id must not appear as the LOCATION designator.
        # (It may appear inside the no-id instruction, e.g. "绝不要出现 ... venue_0 这类内部 id".)
        assert "主角当前所在地点：venue_0" not in user_prompt, (
            f"Raw id 'venue_0' used as location designator in gen_opening user prompt:\n{user_prompt!r}"
        )

    def test_system_prompt_has_no_id_instruction(self):
        """The system prompt must contain the no-id instruction."""
        from loop.bootstrap import _SYSTEM_GEN_OPENING
        assert "town_0 / venue_0" in _SYSTEM_GEN_OPENING or "内部 id" in _SYSTEM_GEN_OPENING, (
            f"No-id instruction missing from _SYSTEM_GEN_OPENING:\n{_SYSTEM_GEN_OPENING!r}"
        )

    def test_no_scene_loc_name_falls_back_gracefully(self):
        """When scene_loc_name=None, gen_opening falls back to scene_loc (no crash)."""
        frame = _base_frame()
        evs, narration = gen_opening(None, frame, "摘要", scene_loc="venue_0")
        assert isinstance(narration, str) and narration.strip()

    def test_scene_loc_name_none_uses_id_as_fallback(self):
        """When scene_loc_name=None, the id is used as the display string (fallback)."""
        frame = _base_frame()
        captured = []

        class _Cap:
            def supports_tools(self): return False
            def complete(self, system, user, **kw):
                captured.append(user)
                return "开场叙事。"
            def complete_messages(self, messages): return "开场叙事。"

        gen_opening(_Cap(), frame, "摘要", scene_loc="venue_0")
        assert captured
        # When no name given, the id itself appears as fallback display
        assert "venue_0" in captured[0], (
            "When scene_loc_name is None, id should appear as fallback in prompt"
        )


# ---------------------------------------------------------------------------
# Tests: _build_world_summary — uses venue names from venue_names map
# ---------------------------------------------------------------------------

class TestBuildWorldSummaryVenueNames:
    """_build_world_summary must use venue NAMES from venue_names map, not raw ids."""

    def _make_local_map(self, with_names=True):
        local_map = {
            "start_town": "town_0",
            "venues": ["venue_0", "venue_1"],
            "l2": [{"id": "town_0", "kind": "settlement", "name": "青瓦集镇"}],
        }
        if with_names:
            local_map["venue_names"] = {"venue_0": "集市广场", "venue_1": "破旧酒馆"}
        return local_map

    def test_venue_names_used_not_ids(self):
        """_build_world_summary must embed venue names, not raw ids."""
        frame = _base_frame()
        regions = _base_regions_summary()
        local_map = self._make_local_map(with_names=True)
        npcs = {"npcs": [{"id": "npc_0", "role": "商人", "sketch": "神秘旅人"}]}
        threads = {"threads": [{"id": "t0", "type": "阴谋", "complexity": "medium",
                                "anchor": "town_0", "about": "失踪的货商"}]}
        summary = _build_world_summary(frame, regions, local_map, npcs, threads)
        assert "集市广场" in summary, "Venue name '集市广场' must appear in world summary"
        assert "破旧酒馆" in summary, "Venue name '破旧酒馆' must appear in world summary"
        assert "venue_0" not in summary, "Raw id 'venue_0' must not appear in world summary"
        assert "venue_1" not in summary, "Raw id 'venue_1' must not appear in world summary"

    def test_town_name_used_from_l2_list(self):
        """_build_world_summary must use the town NAME from l2, not the raw id."""
        frame = _base_frame()
        regions = _base_regions_summary()
        local_map = self._make_local_map(with_names=True)
        npcs = {"npcs": []}
        threads = {"threads": []}
        summary = _build_world_summary(frame, regions, local_map, npcs, threads)
        assert "青瓦集镇" in summary, "Town name '青瓦集镇' must appear in world summary"
        assert "town_0" not in summary, "Raw id 'town_0' must not appear in world summary"
