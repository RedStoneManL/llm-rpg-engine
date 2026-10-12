"""Offline executable coverage for the native-author hand-off tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from app.engine import build_engine
from app.gui import _seed_demo_campaign
from app.play import _build_scene
from context.native_authoring import native_authoring_context
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts" / "export_native_turn_context.py"
IMPORTER = ROOT / "scripts" / "import_native_turn.py"


def _native_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "native-campaign"
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    _seed_demo_campaign(engine)
    engine.store.append_many([
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="客栈主人玛拉", deltas={
            "id": "innkeeper_mara", "sketch": "客栈主人玛拉，熟悉河谷来往的旅人", "goal": "让今晨的客人平安离开", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="玛拉在客栈", deltas={
            "who": "innkeeper_mara", "to": "stonebridge_inn",
        }),
        kernel_event("narration_recorded", day=1, scene="s1", turn=0, summary="公开委托", deltas={
            "scene": "s1", "text": "玛拉说渡口今早延误，愿意听你讲述旅途上的发现。",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    engine.store.close()
    return campaign


def _offline_env() -> dict[str, str]:
    # Scripts must ignore these ambient real-provider choices.
    return {**os.environ, "RPG_PROVIDER": "openai", "RPG_EMBEDDER": "fastembed"}


def test_exporter_creates_rich_safe_native_brief_from_real_campaign(tmp_path):
    campaign = _native_campaign(tmp_path)
    output = tmp_path / "native-brief.json"
    subprocess.run([
        sys.executable, str(EXPORTER), str(campaign),
        "--goal", "向玛拉询问渡口延误的缘由，再决定是否前往集市继续调查。",
        "--output", str(output),
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)

    brief = json.loads(output.read_text(encoding="utf-8"))
    context = brief["player_context"]
    assert context["current_location"]["id"] == "stonebridge_inn"
    assert context["protagonist"]["id"] == "protagonist"
    assert context["known_people"] == [{
        "id": "innkeeper_mara", "public_background": "客栈主人玛拉，熟悉河谷来往的旅人",
    }]
    assert context["known_items"] == [{"id": "old_brass_lantern", "label": "旧铜灯"}]
    assert any("渡口今早延误" in line for line in context["known_events"])
    assert context["available_exits"] == [{"id": "stonebridge_market", "label": "石桥集市", "kind": "settlement"}]
    authoring = brief["authoring_context"]
    assert authoring["scene_time"] == {"day": 1, "band": "晨"}
    assert authoring["stateful_inventory"] == [{"id": "old_brass_lantern", "canonical_holder": "protagonist"}]
    assert authoring["present_people"] == [{
        "id": "innkeeper_mara",
        "observable": {"sketch": "客栈主人玛拉，熟悉河谷来往的旅人"},
        "speakable_evidence": [],
        "absence_meaning": "not_supplied_not_npc_unknown",
    }]
    assert brief["turncommit_schema"]["canonical"].startswith("TurnCommit.to_dict")
    schema = brief["turncommit_schema"]
    assert set(schema["optional_sections"]) == {"items", "knowledge", "quests", "world"}
    assert "required exactly once" in schema["action_adjudication"]["clock"]
    assert "not a time conversion" in schema["action_adjudication"]["travel"]
    rendered = json.dumps(brief, ensure_ascii=False)
    assert "GUI_DEMO_PRIVATE_SENTINEL" not in rendered
    assert "north_bank_old_tower" not in rendered


def test_authoring_context_exposes_only_public_attributed_npc_knowledge(tmp_path):
    campaign = _native_campaign(tmp_path)
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        engine.store.append_many([
            kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="公开渡口状态", deltas={
                "subject": "stonebridge_market", "predicate": "status", "value": "因大雾延误", "secrecy": "public",
            }),
            kernel_event("knowledge_set", day=1, scene="s1", turn=0, summary="玛拉得知渡口状态", deltas={
                "knower": "innkeeper_mara", "fact_key": "stonebridge_market.status", "value": "因大雾延误",
            }),
            kernel_event("fact_asserted", day=1, scene="s1", turn=0, summary="隐藏地点公开属性", deltas={
                "subject": "north_bank_old_tower", "predicate": "status", "value": "GUI_DEMO_PRIVATE_SENTINEL", "secrecy": "public",
            }),
            kernel_event("knowledge_set", day=1, scene="s1", turn=0, summary="玛拉秘密知识", deltas={
                "knower": "innkeeper_mara", "fact_key": "north_bank_old_tower.status", "value": "GUI_DEMO_PRIVATE_SENTINEL",
            }),
        ], preflight=lambda history: project(engine.registry, history))
        engine.world = project(engine.registry, engine.store.iter_events())

        context = native_authoring_context(engine.world, _build_scene(engine), player_input="询问渡口")

        mara = context["present_people"][0]
        assert mara["speakable_evidence"] == [{
            "fact_key": "stonebridge_market.status", "value": "因大雾延误",
            "source": "present_npc_declared_knowledge", "confidence": "declared",
        }]
        assert context["current_player_intent"] == "询问渡口"
        assert "GUI_DEMO_PRIVATE_SENTINEL" not in repr(context)
    finally:
        engine.store.close()


def test_importer_uses_canonical_schema_and_rejects_host_authority(tmp_path):
    campaign = _native_campaign(tmp_path)
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps({
        "narration": "你向玛拉询问渡口的消息，并把旧铜灯放在桌边。",
        "moves": [], "places": [], "links": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "交谈尚未推进时间"}],
    }, ensure_ascii=False), encoding="utf-8")

    accepted = subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal), "向玛拉询问渡口",
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    assert json.loads(accepted.stdout)["revision"] > 0

    before = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    revision = before.store.revision
    before.store.close()
    proposal.write_text(json.dumps({
        "narration": "越权", "trusted_discovery_keys": ["place:sealed_gate.discovered"],
    }, ensure_ascii=False), encoding="utf-8")
    rejected = subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal), "越权发现",
    ], cwd=tmp_path, env=_offline_env(), capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "host-only authority" in rejected.stderr
    after = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    assert after.store.revision == revision
    after.store.close()
