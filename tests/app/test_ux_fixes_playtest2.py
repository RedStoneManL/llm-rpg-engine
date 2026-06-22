"""Tests for playtest-feedback-2026-06-22 UX fixes #1 and #2.

Fix #1 -- progress indicator:
  - A fresh game (empty store) prints "[i/total] 正在生成<label>..." lines via out
    before the intro block.
  - Progress lines have the form "[N/M] 正在生成..." where N and M are integers.

Fix #2 -- rich INTRO block:
  - The intro block printed after bootstrap contains:
      * protagonist name
      * protagonist origin (身世)
      * protagonist goal
      * the start-region name (from _state["regions_summary"])
      * the start town name (from _state["local_map"]["l2"])
      * the objective (starting quest)
      * the central_conflict / world backdrop
      * the narration_excerpt
      * counts footer (regions/factions/NPC/lore)
  - The reroll re-print (after "reroll" command) also shows the rich INTRO block
    (world name must appear in the re-print).
"""
from __future__ import annotations

import json
import re

import pytest
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# ScriptedProvider (local copy — no inter-test-file imports)
# ---------------------------------------------------------------------------

class ScriptedProvider:
    """Returns canned strings in order; repeats the last one if exhausted."""
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
# Fixed campaign / canned replies (same constants as test_integration.py)
# ---------------------------------------------------------------------------

_CAMPAIGN_NAME = "bootstrap_fixed_name"
_N_FACTIONS = 5
_N_REGIONS = 5
_N_VENUES = 2
_N_NPCS = 3
_N_THREADS = 3
_N_P = 1

_WORLD_NAME = "进度测试世界"
_CENTRAL_CONFLICT = "进度测试冲突"
_TOWN_NAME = "进度测试镇"
_REGION_NAMES = [f"进度地域{i}" for i in range(_N_REGIONS)]
_PROT_NAME = "青云"
_PROT_ORIGIN = "流落边疆的江湖侠客"
_PROT_GOAL = "查明家族覆灭真相"
_OBJECTIVE = "前往镇中老掌柜处打探消息"
_OPENING = "你踏入进度测试镇，四周尘土飞扬，命运的齿轮开始转动。"


def _make_scripted_replies(attempt: int = 0) -> list:
    venues = [f"venue_{i}" for i in range(_N_VENUES)]

    frame_reply = json.dumps({
        "world_name": f"{_WORLD_NAME}_{attempt}",
        "central_conflict": f"{_CENTRAL_CONFLICT}_{attempt}",
    })
    regions_reply = json.dumps({
        "regions": [
            {"name": f"{_REGION_NAMES[i]}_{attempt}", "terrain": ["山地", "荒漠", "森林", "水乡", "平原"][i],
             "seed": f"地域{i}描述"}
            for i in range(_N_REGIONS)
        ]
    })
    local_map_reply = json.dumps({
        "town": {"name": f"{_TOWN_NAME}_{attempt}", "seed": "古老集镇"},
        "venues": [{"name": f"场所{i}_{attempt}", "seed": f"场所{i}描述"} for i in range(_N_VENUES)],
        "neighbors": [{"name": f"荒野_{attempt}", "seed": "危险地带"}],
    })
    protagonist_reply = json.dumps({
        "name": f"{_PROT_NAME}_{attempt}",
        "origin": f"{_PROT_ORIGIN}_{attempt}",
        "goal": f"{_PROT_GOAL}_{attempt}",
        "objective": f"{_OBJECTIVE}_{attempt}",
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
                "description": f"暗线{i}描述",
                "trigger": f"触发{i}",
                "secret": f"真相{i}",
                "l3_anchor": venues[i % len(venues)],
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
                "l3_anchor": venues[0],
                "stages": [{"hint": "提示"}],
            }
        ]
    })
    opening_reply = f"{_OPENING}_{attempt}"

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
# Fix #1: progress indicator tests
# ---------------------------------------------------------------------------

class TestProgressIndicator:
    """Progress lines [i/total] 正在生成<label>... must appear during fresh game."""

    def test_progress_lines_printed_during_fresh_game(self, tmp_path):
        """At least one [N/M] 正在生成... line must appear in output for a fresh game."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        # At least one progress line
        progress_lines = [
            line for line in output
            if re.match(r"\[\d+/\d+\]", line) and "正在生成" in line
        ]
        assert len(progress_lines) >= 1, (
            f"Expected at least one progress line matching [N/M] 正在生成...; "
            f"got output:\n{combined}"
        )

    def test_progress_lines_count_and_format(self, tmp_path):
        """Progress lines must follow [N/M] format with N incrementing up to M."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        progress_lines = [
            line for line in output
            if re.match(r"\[\d+/\d+\]", line) and "正在生成" in line
        ]
        assert len(progress_lines) >= 2, (
            f"Expected at least 2 progress lines; got {progress_lines!r}"
        )
        for line in progress_lines:
            m = re.match(r"\[(\d+)/(\d+)\]", line)
            assert m is not None, f"Line does not match [N/M] format: {line!r}"
            n, total = int(m.group(1)), int(m.group(2))
            assert total > 0, f"Total steps must be > 0; got {total} in {line!r}"
            assert 1 <= n <= total, f"Step {n} out of range [1, {total}] in {line!r}"

    def test_progress_lines_appear_before_intro_block(self, tmp_path):
        """Progress lines must appear before the INTRO / world-name block."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        prog_idx = next(
            (i for i, line in enumerate(output)
             if re.match(r"\[\d+/\d+\]", line) and "正在生成" in line),
            None,
        )
        world_idx = next(
            (i for i, line in enumerate(output)
             if f"{_WORLD_NAME}_0" in line),
            None,
        )
        assert prog_idx is not None, "No progress line found in output"
        assert world_idx is not None, f"World name '{_WORLD_NAME}_0' not found in output"
        assert prog_idx < world_idx, (
            f"Progress line (idx={prog_idx}) must appear before world name (idx={world_idx})"
        )

    def test_no_progress_lines_on_existing_store(self, tmp_path):
        """A second run (existing store) must NOT emit progress lines."""
        # First run to populate the store
        _run_main(tmp_path, inputs=["开始"], provider=_make_provider(0))
        # Second run: store already has events
        output2 = _run_main(tmp_path, inputs=["/quit"], provider=_make_provider(0))
        progress_lines = [
            line for line in output2
            if re.match(r"\[\d+/\d+\]", line) and "正在生成" in line
        ]
        assert len(progress_lines) == 0, (
            f"Second run must not emit progress lines; got {progress_lines!r}"
        )


# ---------------------------------------------------------------------------
# Fix #2: rich INTRO block tests
# ---------------------------------------------------------------------------

class TestRichIntroBlock:
    """The INTRO block must contain key protagonist, location, and world info."""

    def test_intro_contains_protagonist_name(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_PROT_NAME}_0" in combined, (
            f"Protagonist name '{_PROT_NAME}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_protagonist_origin(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_PROT_ORIGIN}_0" in combined, (
            f"Protagonist origin '{_PROT_ORIGIN}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_protagonist_goal(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_PROT_GOAL}_0" in combined, (
            f"Protagonist goal '{_PROT_GOAL}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_start_region_name(self, tmp_path):
        """The start region name (region_0's name) must appear in the intro."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        # _REGION_NAMES[0] is "进度地域0", attempt=0 → "进度地域0_0"
        assert f"{_REGION_NAMES[0]}_0" in combined, (
            f"Start region name '{_REGION_NAMES[0]}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_start_town_name(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_TOWN_NAME}_0" in combined, (
            f"Start town name '{_TOWN_NAME}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_objective(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_OBJECTIVE}_0" in combined, (
            f"Objective '{_OBJECTIVE}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_world_name(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_WORLD_NAME}_0" in combined, (
            f"World name '{_WORLD_NAME}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_central_conflict(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_CENTRAL_CONFLICT}_0" in combined, (
            f"Central conflict '{_CENTRAL_CONFLICT}_0' not found in intro output:\n{combined}"
        )

    def test_intro_contains_narration_excerpt(self, tmp_path):
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        # opening reply is _OPENING + "_0"
        assert _OPENING in combined, (
            f"Narration excerpt not found in intro output:\n{combined}"
        )

    def test_intro_contains_counts_footer(self, tmp_path):
        """The counts footer must mention regions / factions / NPC / lore counts."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        # We expect numbers for all four counts to appear somewhere near each other
        # Just check the combined text mentions the relevant Chinese count labels
        assert "大区域" in combined or "区域数" in combined or "n_regions" in combined or (
            str(_N_REGIONS) in combined
        ), f"Region count not found in output:\n{combined}"
        assert "势力" in combined, f"Faction count label not found:\n{combined}"
        assert "NPC" in combined, f"NPC count label not found:\n{combined}"

    def test_reroll_reprints_rich_block_with_new_world_name(self, tmp_path):
        """After 'reroll', the re-print must show the new world name (attempt=1)."""
        replies = _make_scripted_replies(attempt=0) + _make_scripted_replies(attempt=1)
        output = _run_main(
            tmp_path,
            inputs=["reroll", "开始", "/quit"],
            provider=ScriptedProvider(replies),
        )
        combined = "\n".join(output)
        assert f"{_WORLD_NAME}_0" in combined, "Initial world name missing before reroll"
        assert f"{_WORLD_NAME}_1" in combined, (
            f"Post-reroll world name '{_WORLD_NAME}_1' not found in output:\n{combined}"
        )

    def test_reroll_reprints_protagonist_name(self, tmp_path):
        """After 'reroll', the re-print must show the new protagonist name."""
        replies = _make_scripted_replies(attempt=0) + _make_scripted_replies(attempt=1)
        output = _run_main(
            tmp_path,
            inputs=["reroll", "开始", "/quit"],
            provider=ScriptedProvider(replies),
        )
        combined = "\n".join(output)
        # Both protagonist names (attempt 0 and 1) should appear
        assert f"{_PROT_NAME}_0" in combined, f"Initial protagonist name missing:\n{combined}"
        assert f"{_PROT_NAME}_1" in combined, (
            f"Post-reroll protagonist name missing:\n{combined}"
        )

    def test_intro_block_appears_before_reroll_prompt(self, tmp_path):
        """The intro block must appear BEFORE the reroll prompt line."""
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        world_idx = next(
            (i for i, line in enumerate(output) if f"{_WORLD_NAME}_0" in line), None
        )
        prompt_idx = next(
            (i for i, line in enumerate(output) if "reroll" in line and "提示" in line), None
        )
        assert world_idx is not None, "World name not found in output"
        assert prompt_idx is not None, "Reroll prompt not found in output"
        assert world_idx < prompt_idx, (
            f"Intro block (idx={world_idx}) must appear before reroll prompt (idx={prompt_idx})"
        )

    def test_existing_test_world_summary_assertion_still_holds(self, tmp_path):
        """test_main_first_run_prints_summary: world_name must appear in output (compat check)."""
        # This is the same assertion that test_integration.py:test_main_first_run_prints_summary
        # uses -- just confirming the new INTRO block still satisfies it.
        output = _run_main(tmp_path, inputs=["开始", "/quit"])
        combined = "\n".join(output)
        assert f"{_WORLD_NAME}_0" in combined, (
            f"Expected world_name in output (compat with test_integration), got: {combined!r}"
        )
