"""Tests for NarrativeSystem (P2 — recency-tiered recap)."""
from __future__ import annotations

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
import systems.narrative as nmod
from systems.narrative import NarrativeSystem


def _reg():
    return Registry().register(NarrativeSystem())


def _narr(scene, text, day=1, turn=1):
    return kernel_event("narration_recorded", day=day, scene=scene,
                        summary="narration", deltas={"scene": scene, "text": text},
                        turn=turn)

def _summ(scene, summary, day=1, turn=2):
    return kernel_event("scene_summarized", day=day, scene=scene,
                        summary="scene summary",
                        deltas={"scene": scene, "summary": summary}, turn=turn)


def test_owns_events_no_section():
    s = NarrativeSystem()
    assert s.name == "narrative"
    assert s.event_types() == {
        "narration_recorded", "scene_summarized", "recap_recompressed"}
    assert s.commit_sections() == set()          # harness-authored
    assert s.requires() == set()


def test_constants_present():
    assert nmod.RECAP_RAW_SCENES == 2
    assert nmod.RECAP_SUMMARY_FANOUT == 6


def test_empty_state_shape():
    assert NarrativeSystem().empty_state() == {
        "scenes": [], "super_summary": None, "summarized_through_index": 0}


def test_narration_recorded_buckets_by_scene():
    world = project(_reg(), [
        _narr("s1", "第一段。", turn=1),
        _narr("s1", "第一段续。", turn=2),
        _narr("s2", "第二场。", turn=3),
    ])
    scenes = world["systems"]["narrative"]["scenes"]
    assert [b["scene"] for b in scenes] == ["s1", "s2"]
    assert scenes[0]["raw"] == ["第一段。", "第一段续。"]
    assert scenes[1]["raw"] == ["第二场。"]


def test_scene_summarized_fills_summary():
    world = project(_reg(), [
        _narr("s1", "原文", turn=1),
        _summ("s1", "s1 摘要", turn=2),
    ])
    b = world["systems"]["narrative"]["scenes"][0]
    assert b["summary"] == "s1 摘要"


def test_recompress_folds_into_super_summary():
    world = project(_reg(), [
        kernel_event("recap_recompressed", day=2, scene="s9", summary="super",
                     deltas={"super_summary": "远古往事总览",
                             "summarized_through_index": 6}, turn=9),
    ])
    ns = world["systems"]["narrative"]
    assert ns["super_summary"] == "远古往事总览"
    assert ns["summarized_through_index"] == 6


# ---------------------------------------------------------------------------
# Task 6 — inject + aged_out_scene tests
# ---------------------------------------------------------------------------

def test_inject_renders_recent_raw_only():
    # RECAP_RAW_SCENES=2 → only the last 2 buckets' raw appear verbatim
    world = project(_reg(), [
        _narr("s1", "最老原文", turn=1),
        _summ("s1", "s1摘要", turn=2),
        _narr("s2", "中间原文", turn=3),
        _narr("s3", "最近原文", turn=4),
    ])
    frag = NarrativeSystem().inject({"id": "s3"}, world)
    assert frag is not None and frag.layer == "scene"
    assert "最近原文" in frag.text and "中间原文" in frag.text
    assert "最老原文" not in frag.text          # aged out of the raw window

def test_inject_empty_returns_none():
    assert NarrativeSystem().inject({"id": "s1"}, project(_reg(), [])) is None

def test_aged_out_scene_detects_window_overflow():
    world = project(_reg(), [
        _narr("s1", "a", turn=1),
        _narr("s2", "b", turn=2),
        _narr("s3", "c", turn=3),     # now 3 buckets, window=2 → s1 aged out
    ])
    ns = world["systems"]["narrative"]
    assert nmod.aged_out_scene(ns) == "s1"     # oldest unsummarized beyond window

def test_aged_out_scene_none_when_within_window():
    world = project(_reg(), [_narr("s1", "a", turn=1), _narr("s2", "b", turn=2)])
    assert nmod.aged_out_scene(world["systems"]["narrative"]) is None
