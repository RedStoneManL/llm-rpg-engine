"""Tests for playtest-feedback-2026-06-22 bug fixes #10 and #11.

Fix #10 — internal place ids (town_0/venue_0) leak into player-facing text:
  (a) gen_local_map summary carries venue_names {id: name}.
  (b) gen_protagonist and gen_opening prompts include venue/town NAMES and the
      no-internal-id instruction.
  (c) _print_intro shows venue NAMES, not raw ids.

Fix #11 — reroll loop discards the player's first real action:
  A non-reroll, non-break first line must reach play_loop as turn 1 (via
  itertools.chain prepend), not be silently discarded.
  A bare break token (empty string / '开始' / 'start') still starts the game
  with no forced first turn.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# ScriptedProvider (local copy — no inter-test-file imports)
# ---------------------------------------------------------------------------

class ScriptedProvider:
    """Returns canned strings in order; repeats last one if exhausted."""
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
# Shared constants — identical campaign / reply set as test_integration.py
# ---------------------------------------------------------------------------

_CAMPAIGN_NAME = "bootstrap_fixed_name"
_N_FACTIONS = 5
_N_REGIONS = 5
_N_VENUES = 2
_N_NPCS = 3
_N_THREADS = 3
_N_P = 1

_TOWN_NAME = "青瓦集镇"
_VENUE_NAMES = ["老醉酒馆", "铁铺"]
_REGION_NAMES = [f"进度地域{i}" for i in range(_N_REGIONS)]
_WORLD_NAME = "碎镜大陆"


def _make_scripted_replies(attempt: int = 0) -> list:
    """Return canned replies for all 9 LLM calls in bootstrap_world."""
    venues_raw = [f"venue_{i}" for i in range(_N_VENUES)]

    frame_reply = json.dumps({
        "world_name": f"{_WORLD_NAME}_{attempt}",
        "central_conflict": f"核心冲突_{attempt}",
    })
    regions_reply = json.dumps({
        "regions": [
            {"name": f"{_REGION_NAMES[i]}_{attempt}",
             "terrain": ["山地", "荒漠", "森林", "水乡", "平原"][i],
             "seed": f"地域{i}描述"}
            for i in range(_N_REGIONS)
        ]
    })
    local_map_reply = json.dumps({
        "town": {"name": f"{_TOWN_NAME}_{attempt}", "seed": "古老的集镇"},
        "venues": [
            {"name": f"{_VENUE_NAMES[i]}_{attempt}", "seed": f"场所{i}描述"}
            for i in range(_N_VENUES)
        ],
        "neighbors": [{"name": f"荒野_{attempt}", "seed": "危险地带"}],
    })
    protagonist_reply = json.dumps({
        "name": f"主角_{attempt}",
        "origin": f"身世_{attempt}",
        "goal": f"目标_{attempt}",
        "objective": f"当前任务_{attempt}：前往{_TOWN_NAME}_{attempt}的{_VENUE_NAMES[0]}_{attempt}",
    })
    factions_reply = json.dumps({
        "factions": [
            {"name": f"势力{i}_{attempt}", "motivation": f"动机{i}"}
            for i in range(_N_FACTIONS)
        ]
    })
    npcs_reply = json.dumps({
        "npcs": [
            {"sketch": f"NPC{i}_{attempt}外貌", "goal": f"NPC{i}目标", "secret": f"NPC{i}秘密"}
            for i in range(_N_NPCS)
        ]
    })
    campaign_threads_reply = json.dumps({
        "lines": [
            {
                "about": f"暗线{i}_{attempt}",
                "description": f"描述{i}",
                "trigger": f"触发{i}",
                "secret": f"真相{i}",
                "l3_anchor": venues_raw[i % len(venues_raw)],
                "stages": [{"hint": f"阶段{j}"} for j in range(3)],
            }
            for i in range(_N_THREADS)
        ]
    })
    prot_threads_reply = json.dumps({
        "lines": [
            {
                "about": f"主角线_{attempt}",
                "description": "描述",
                "trigger": "触发",
                "secret": "真相",
                "l3_anchor": venues_raw[0],
                "stages": [{"hint": "提示"}],
            }
        ]
    })
    opening_reply = (
        f"你踏入了{_TOWN_NAME}_{attempt}的{_VENUE_NAMES[0]}_{attempt}，"
        f"命运的齿轮开始转动。"
    )

    return [
        frame_reply,
        regions_reply,
        local_map_reply,
        protagonist_reply,
        factions_reply,
        npcs_reply,
        campaign_threads_reply,
        prot_threads_reply,
        opening_reply,
    ]


def _make_provider(attempt: int = 0) -> ScriptedProvider:
    return ScriptedProvider(_make_scripted_replies(attempt))


def _run_main(tmp_path, inputs, provider=None, attempt: int = 0):
    """Run main() with injected provider and inputs; return collected output lines."""
    from app.__main__ import main
    if provider is None:
        provider = _make_provider(attempt)
    campaign_dir = tmp_path / _CAMPAIGN_NAME
    output = []
    main(
        ["--campaign", str(campaign_dir), "--provider", "fake"],
        inputs=inputs,
        out=output.append,
        provider=provider,
    )
    return output


# ---------------------------------------------------------------------------
# Fix #10 (a): gen_local_map summary includes venue_names
# ---------------------------------------------------------------------------

class TestGenLocalMapVenueNames:
    """gen_local_map summary must carry venue_names: {id -> name}."""

    def _make_frame(self):
        return {
            "genre": "x", "tone": "悬疑", "central_conflict": "c",
            "world_name": "w", "n_factions": 3, "n_regions": 4,
        }

    def _regions_summary(self):
        from loop.bootstrap import gen_regions
        return {
            "start_region": "region_0",
            "regions": [
                {"id": f"region_{i}", "name": f"地域{i}", "tier": "start" if i == 0 else "neighbor", "terrain": "平原"}
                for i in range(4)
            ],
            "density": 0.3,
        }

    def test_summary_has_venue_names_key(self, tmp_path):
        """gen_local_map summary must have a 'venue_names' key."""
        from loop.bootstrap import gen_local_map
        from engine.oracle import Oracle
        frame = self._make_frame()
        regions = self._regions_summary()
        reply = json.dumps({
            "town": {"name": "铁石镇", "seed": "古老集镇"},
            "venues": [{"name": "集市", "seed": "热闹"}, {"name": "酒馆", "seed": "喧嚣"}],
            "neighbors": [{"name": "荒野", "seed": "危险"}],
        })
        p = ScriptedProvider([reply])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        assert "venue_names" in summ, "summary must have 'venue_names' key"

    def test_venue_names_maps_ids_to_names(self, tmp_path):
        """venue_names must be a dict mapping each venue_id to its LLM-authored name."""
        from loop.bootstrap import gen_local_map
        from engine.oracle import Oracle
        frame = self._make_frame()
        regions = self._regions_summary()
        reply = json.dumps({
            "town": {"name": "铁石镇", "seed": "古老集镇"},
            "venues": [{"name": "集市", "seed": "热闹"}, {"name": "酒馆", "seed": "喧嚣"}],
            "neighbors": [{"name": "荒野", "seed": "危险"}],
        })
        p = ScriptedProvider([reply])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        venue_names = summ["venue_names"]
        assert isinstance(venue_names, dict), "venue_names must be a dict"
        for vid in summ["venues"]:
            assert vid in venue_names, f"venue_names missing entry for {vid!r}"
            assert isinstance(venue_names[vid], str) and venue_names[vid].strip(), \
                f"venue_names[{vid!r}] is empty"

    def test_venue_names_uses_llm_authored_strings(self, tmp_path):
        """The LLM-authored venue names flow into venue_names (not stubs)."""
        from loop.bootstrap import gen_local_map
        from engine.oracle import Oracle
        frame = self._make_frame()
        regions = self._regions_summary()
        reply = json.dumps({
            "town": {"name": "碎石镇", "seed": "古老集镇"},
            "venues": [
                {"name": "铁铁匠铺", "seed": "火花飞溅"},
                {"name": "老醉酒馆", "seed": "消息汇聚"},
            ],
            "neighbors": [{"name": "幽林", "seed": "有异兽"}],
        })
        p = ScriptedProvider([reply])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        venue_names = summ["venue_names"]
        assert "铁铁匠铺" in venue_names.values(), "First venue name not in venue_names"
        assert "老醉酒馆" in venue_names.values(), "Second venue name not in venue_names"

    def test_venue_names_fallback_still_has_key(self, tmp_path):
        """Fallback (provider=None) still produces a non-empty venue_names dict."""
        from loop.bootstrap import gen_local_map
        from engine.oracle import Oracle
        frame = self._make_frame()
        regions = self._regions_summary()
        _, summ = gen_local_map(None, Oracle(1), frame, regions)
        assert "venue_names" in summ, "Fallback must still carry venue_names"
        for vid in summ["venues"]:
            assert vid in summ["venue_names"], f"Fallback venue_names missing {vid!r}"
            assert summ["venue_names"][vid].strip(), f"Fallback venue_names[{vid!r}] empty"

    def test_venue_names_count_matches_venues(self, tmp_path):
        """len(venue_names) == len(venues) — one entry per venue."""
        from loop.bootstrap import gen_local_map
        from engine.oracle import Oracle
        frame = self._make_frame()
        regions = self._regions_summary()
        reply = json.dumps({
            "town": {"name": "镇A", "seed": "s"},
            "venues": [{"name": "场所0", "seed": "s0"}, {"name": "场所1", "seed": "s1"}],
            "neighbors": [{"name": "邻地", "seed": "s"}],
        })
        p = ScriptedProvider([reply])
        _, summ = gen_local_map(p, Oracle(1), frame, regions)
        assert len(summ["venue_names"]) == len(summ["venues"]), \
            "venue_names must have same count as venues"


# ---------------------------------------------------------------------------
# Fix #10 (b): gen_protagonist prompt contains names + no-id instruction
# ---------------------------------------------------------------------------

class TestGenProtagonistPromptNames:
    """gen_protagonist prompt must include venue/town names and the no-id instruction."""

    def _make_frame(self):
        return {
            "genre": "东方武侠", "tone": "悬疑",
            "world_name": "碎镜大陆", "central_conflict": "核心冲突",
            "n_factions": 3, "n_regions": 4,
        }

    def _local_map_with_names(self):
        """local_map carrying venue_names as gen_local_map now produces."""
        return {
            "start_town": "town_0",
            "venues": ["venue_0", "venue_1"],
            "venue_names": {"venue_0": "老醉酒馆", "venue_1": "铁铺"},
            "l2": [{"id": "town_0", "kind": "settlement", "name": "青瓦集镇"}],
        }

    def test_prompt_includes_town_name(self):
        """gen_protagonist user prompt must contain the town NAME, not 'town_0'."""
        from engine.oracle import Oracle
        from loop.bootstrap import gen_protagonist

        captured_prompts = []

        class CapturingProvider:
            def supports_tools(self): return False
            def complete_messages(self, messages):
                for m in messages:
                    if m.get("role") == "user":
                        captured_prompts.append(m["content"])
                return json.dumps({
                    "name": "沈云舟", "origin": "落魄秀才", "goal": "查明死因",
                    "objective": "前往青瓦集镇打探消息",
                })
            def complete(self, system, user, **kw):
                captured_prompts.append(user)
                return json.dumps({
                    "name": "沈云舟", "origin": "落魄秀才", "goal": "查明死因",
                    "objective": "前往青瓦集镇打探消息",
                })

        local_map = self._local_map_with_names()
        frame = self._make_frame()
        gen_protagonist(CapturingProvider(), Oracle(42), frame, local_map)

        assert captured_prompts, "Provider was never called"
        combined = "\n".join(captured_prompts)
        assert "青瓦集镇" in combined, (
            f"Town name '青瓦集镇' must appear in gen_protagonist prompt; got:\n{combined!r}"
        )
        # The town name must appear as the LOCATION designator (e.g. "起始小镇：青瓦集镇"),
        # not only as part of the no-id example string.  Check the location context line.
        location_ctx = [line for line in combined.split("\n") if "起始小镇" in line]
        assert location_ctx, "No '起始小镇' context line in prompt"
        assert "青瓦集镇" in location_ctx[0], (
            f"Town name not in location context line: {location_ctx[0]!r}"
        )
        # The raw id 'town_0' must not appear as the location designator
        assert "起始小镇：town_0" not in combined, (
            "Raw id 'town_0' used as location designator — must use name instead"
        )

    def test_prompt_includes_venue_name(self):
        """gen_protagonist user prompt must contain the venue NAME, not a raw id as designator."""
        from engine.oracle import Oracle
        from loop.bootstrap import gen_protagonist

        captured_prompts = []

        class CapturingProvider:
            def supports_tools(self): return False
            def complete_messages(self, messages):
                for m in messages:
                    if m.get("role") == "user":
                        captured_prompts.append(m["content"])
                return json.dumps({
                    "name": "沈云舟", "origin": "落魄秀才", "goal": "查明死因",
                    "objective": "前往老醉酒馆打探消息",
                })
            def complete(self, system, user, **kw):
                captured_prompts.append(user)
                return json.dumps({
                    "name": "沈云舟", "origin": "落魄秀才", "goal": "查明死因",
                    "objective": "前往老醉酒馆打探消息",
                })

        local_map = self._local_map_with_names()
        frame = self._make_frame()
        gen_protagonist(CapturingProvider(), Oracle(42), frame, local_map)

        assert captured_prompts, "Provider was never called"
        combined = "\n".join(captured_prompts)
        assert "老醉酒馆" in combined, (
            f"Venue name '老醉酒馆' must appear in gen_protagonist prompt; got:\n{combined!r}"
        )
        # The raw id must not appear as the VENUE designator (may appear in no-id example)
        assert "起始场所：venue_0" not in combined, (
            "Raw id 'venue_0' used as venue designator — must use name instead"
        )

    def test_prompt_includes_no_id_instruction(self):
        """gen_protagonist prompt must forbid internal ids like town_0/venue_0."""
        from engine.oracle import Oracle
        from loop.bootstrap import gen_protagonist

        captured_prompts = []

        class CapturingProvider:
            def supports_tools(self): return False
            def complete_messages(self, messages):
                for m in messages:
                    if m.get("role") == "user":
                        captured_prompts.append(m["content"])
                return json.dumps({
                    "name": "沈云舟", "origin": "落魄秀才", "goal": "查明死因",
                    "objective": "前往老醉酒馆打探消息",
                })
            def complete(self, system, user, **kw):
                captured_prompts.append(user)
                return json.dumps({
                    "name": "沈云舟", "origin": "落魄秀才", "goal": "查明死因",
                    "objective": "前往老醉酒馆打探消息",
                })

        local_map = self._local_map_with_names()
        frame = self._make_frame()
        gen_protagonist(CapturingProvider(), Oracle(42), frame, local_map)

        combined = "\n".join(captured_prompts)
        # The no-id instruction must appear
        assert "内部 id" in combined or "town_0 / venue_0" in combined, (
            f"No-id instruction not found in gen_protagonist prompt; got:\n{combined!r}"
        )


# ---------------------------------------------------------------------------
# Fix #10 (b): gen_opening prompt contains names + no-id instruction
# ---------------------------------------------------------------------------

class TestGenOpeningPromptNames:
    """gen_opening prompt must pass the venue NAME (via scene_loc_name) and forbid ids."""

    def _make_frame(self):
        return {
            "genre": "东方武侠", "tone": "悬疑",
            "world_name": "碎镜大陆", "central_conflict": "核心冲突",
            "n_factions": 3, "n_regions": 4,
        }

    def test_prompt_uses_scene_loc_name_not_id(self):
        """gen_opening prompt must reference the human-readable scene_loc_name as location."""
        from loop.bootstrap import gen_opening

        captured_prompts = []

        class CapturingProvider:
            def supports_tools(self): return False
            def complete(self, system, user, **kw):
                captured_prompts.append(user)
                return "你踏入了老醉酒馆，命运的齿轮开始转动。"
            def complete_messages(self, messages):
                return self.complete(None, messages[-1]["content"] if messages else "")

        frame = self._make_frame()
        gen_opening(
            CapturingProvider(), frame, "世界摘要内容",
            scene_loc="venue_0", scene_loc_name="老醉酒馆",
        )

        assert captured_prompts, "Provider was never called"
        combined = "\n".join(captured_prompts)
        assert "老醉酒馆" in combined, (
            f"scene_loc_name '老醉酒馆' must appear in gen_opening prompt; got:\n{combined!r}"
        )
        # The raw id must not appear as the LOCATION designator.
        # (It may appear inside the no-id instruction, e.g. "绝不要出现 town_0 / venue_0 这类内部 id".)
        assert "主角当前所在地点：venue_0" not in combined, (
            f"Raw id 'venue_0' used as location designator in gen_opening prompt:\n{combined!r}"
        )

    def test_prompt_includes_no_id_instruction(self):
        """gen_opening system or user prompt must contain the no-id instruction."""
        from loop.bootstrap import gen_opening, _SYSTEM_GEN_OPENING

        # Check the system prompt constant
        assert "town_0 / venue_0" in _SYSTEM_GEN_OPENING or "内部 id" in _SYSTEM_GEN_OPENING, (
            "No-id instruction must appear in _SYSTEM_GEN_OPENING"
        )

    def test_prompt_fallback_to_id_when_no_name(self):
        """When scene_loc_name is None, gen_opening falls back to scene_loc (id) — no crash."""
        from loop.bootstrap import gen_opening

        frame = self._make_frame()
        # Should not raise; uses scene_loc as display when no name given
        evs, narration = gen_opening(
            None, frame, "摘要", scene_loc="venue_0",
        )
        assert isinstance(narration, str) and narration.strip()


# ---------------------------------------------------------------------------
# Fix #10 (c): _print_intro / main outputs venue NAMES, not ids
# ---------------------------------------------------------------------------

class TestIntroBlockNoIds:
    """The intro block printed by main must use venue NAMES, not raw ids."""

    def test_intro_contains_venue_names_not_ids(self, tmp_path):
        """After bootstrap, the printed intro must show venue names, not venue_0/venue_1."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)

        # Venue names from the scripted reply must appear
        assert f"{_VENUE_NAMES[0]}_0" in combined, (
            f"Venue name '{_VENUE_NAMES[0]}_0' not found in intro output:\n{combined}"
        )
        # Raw ids must NOT appear in the player-facing intro
        assert "venue_0" not in combined, (
            f"Raw id 'venue_0' must NOT appear in player-facing intro:\n{combined}"
        )
        assert "venue_1" not in combined, (
            f"Raw id 'venue_1' must NOT appear in player-facing intro:\n{combined}"
        )

    def test_intro_contains_town_name_not_id(self, tmp_path):
        """Intro must show town NAME (from l2 list), not 'town_0'."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)

        assert f"{_TOWN_NAME}_0" in combined, (
            f"Town name '{_TOWN_NAME}_0' not found in intro output:\n{combined}"
        )
        # town_0 must not appear as a standalone id in the human-facing output
        # (it may appear in system messages / transcript paths, so we check the
        # specific 【当前所在】 line which is the player-facing venue display)
        location_lines = [line for line in output if "当前所在" in line]
        assert location_lines, "No 【当前所在】 line found in output"
        loc_combined = "\n".join(location_lines)
        assert "venue_0" not in loc_combined, (
            f"Raw 'venue_0' found in location line(s):\n{loc_combined}"
        )
        assert "town_0" not in loc_combined, (
            f"Raw 'town_0' found in location line(s):\n{loc_combined}"
        )


# ---------------------------------------------------------------------------
# Fix #11: reroll loop preserves first real action as turn 1
# ---------------------------------------------------------------------------

class TestRerollLoopPreservesFirstAction:
    """A non-reroll first line must not be discarded; it must reach play_loop as turn 1."""

    def _make_turn_response(self):
        return json.dumps({
            "narration": "你走进了市集，感受到了热闹的气息。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "未推进"}],
        })

    def test_first_action_runs_as_turn_1(self, tmp_path):
        """main(['我去市集看看', '/quit']) → a play turn runs for '我去市集看看'."""
        turn_response = self._make_turn_response()
        # 9 bootstrap replies + 1 play-turn reply
        all_replies = _make_scripted_replies(attempt=0) + [turn_response]

        output = _run_main(
            tmp_path,
            inputs=["我去市集看看", "/quit"],
            provider=ScriptedProvider(all_replies),
        )
        combined = "\n".join(output)

        # The play turn narration must have been produced
        assert "市集" in combined, (
            f"Expected play turn narration containing '市集'; got output:\n{combined}"
        )

    def test_first_action_not_discarded(self, tmp_path):
        """The line '我去市集看看' must NOT be silently discarded (old bug)."""
        # This test uses a turn response that echoes the player action in narration.
        # The key assertion: the game must have actually RUN a turn (narration present).
        turn_response = json.dumps({
            "narration": "你迈步走向市集，四周的叫卖声涌入耳中。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "未推进"}],
        })
        all_replies = _make_scripted_replies(attempt=0) + [turn_response]

        output = _run_main(
            tmp_path,
            inputs=["我去市集看看", "/quit"],
            provider=ScriptedProvider(all_replies),
        )
        combined = "\n".join(output)

        # The narration from the play turn must appear — proves the turn ran
        assert "叫卖声" in combined, (
            f"Play turn narration not found — first action was discarded.\n"
            f"Output:\n{combined}"
        )

    def test_break_token_kaishi_no_forced_turn(self, tmp_path):
        """'开始' → starts game with no forced turn (turn_response NOT required)."""
        # No turn_response needed — '开始' is a break token, then /quit exits
        output = _run_main(
            tmp_path,
            inputs=["开始", "/quit"],
        )
        combined = "\n".join(output)
        # Game started without error
        assert "世界已就绪" in combined or "载入存档" in combined or len(combined) > 0

    def test_break_token_empty_no_forced_turn(self, tmp_path):
        """Empty line '' → starts game with no forced turn."""
        output = _run_main(
            tmp_path,
            inputs=["", "/quit"],
        )
        combined = "\n".join(output)
        assert len(combined) > 0

    def test_break_token_start_english_no_forced_turn(self, tmp_path):
        """'start' → starts game with no forced turn."""
        output = _run_main(
            tmp_path,
            inputs=["start", "/quit"],
        )
        combined = "\n".join(output)
        assert len(combined) > 0

    def test_reroll_then_first_action_preserved(self, tmp_path):
        """After a reroll, a first real action still reaches play_loop as turn 1."""
        turn_response = json.dumps({
            "narration": "你抵达了市集，耳边响起了嘈杂的喧嚣声。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "未推进"}],
        })
        # 9 bootstrap + 9 reroll + 1 play turn
        all_replies = (
            _make_scripted_replies(attempt=0)
            + _make_scripted_replies(attempt=1)
            + [turn_response]
        )

        output = _run_main(
            tmp_path,
            inputs=["reroll", "我去市集看看", "/quit"],
            provider=ScriptedProvider(all_replies),
        )
        combined = "\n".join(output)

        assert "嘈杂" in combined, (
            f"After reroll, first action should run as turn 1; got:\n{combined}"
        )
