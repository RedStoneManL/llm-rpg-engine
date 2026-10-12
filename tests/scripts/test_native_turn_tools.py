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
from context.native_authoring import native_authoring_context, native_authoring_policy_errors
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
    assert set(schema["optional_sections"]) == {"entities", "relations", "items", "knowledge", "quests", "world"}
    assert "required exactly once" in schema["action_adjudication"]["clock"]
    assert "not a time conversion" in schema["action_adjudication"]["travel"]
    assert "reported:<topic>" in schema["action_adjudication"]["information"]
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


def test_native_author_can_create_a_public_sourced_report_without_overwriting_world(tmp_path):
    campaign = _native_campaign(tmp_path)
    proposal = tmp_path / "sourced-report.json"
    value = "我亲眼看见北岸的渡船在晨雾最浓时靠岸等候。"
    proposal.write_text(json.dumps({
        "narration": "集市的鱼贩岑岚指着河面，说自己清晨亲眼见过渡船在雾里靠岸等候。",
        "moves": [
            {"who": "protagonist", "to": "stonebridge_market"},
            {"who": "market_fishmonger_cenlan", "to": "stonebridge_market"},
        ],
        "places": [], "links": [],
        "cast": [{
            "id": "market_fishmonger_cenlan", "op": "create", "name": "岑岚",
            "sketch": "石桥集市的鱼贩，晨间在河边摆摊", "goal": "趁雾散前卖完鲜鱼",
        }],
        "facts": [{
            "subject": "market_fishmonger_cenlan", "predicate": "reported:ferry_morning",
            "value": value, "secrecy": "public",
        }],
        "knowledge": [{
            "op": "told", "knower": "protagonist",
            "fact_key": "market_fishmonger_cenlan.reported:ferry_morning",
            "value": value, "via": "market_fishmonger_cenlan",
        }],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "短程步行和一段问话仍在晨间"}],
    }, ensure_ascii=False), encoding="utf-8")

    accepted = subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal), "去集市打听渡口",
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    assert json.loads(accepted.stdout)["revision"] > 0

    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        graph = engine.world["systems"]["ontology"]
        assert graph.get_entity("market_fishmonger_cenlan").etype == "Person"
        assert graph.value_at("market_fishmonger_cenlan", "reported:ferry_morning", 1) == value
        # This is a report by a named source, not an asserted ferry-world status.
        assert graph.value_at("stonebridge_market", "status", 1) is None
    finally:
        engine.store.close()


def test_native_policy_rejects_existing_fact_overwrite_and_unsourced_report(tmp_path):
    campaign = _native_campaign(tmp_path)
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        scene = _build_scene(engine)
        overwrite = {
            "narration": "你断定渡口恢复通行。", "moves": [], "places": [], "links": [], "cast": [],
            "facts": [{"subject": "stonebridge_market", "predicate": "status", "value": "恢复通行", "secrecy": "public"}],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "没有时段变化"}],
        }
        unsourced = {
            "narration": "一名陌生人随口说渡口恢复通行。", "moves": [], "places": [], "links": [],
            "cast": [{"id": "market_voice", "op": "create", "sketch": "集市商贩", "goal": "叫卖"}],
            "facts": [{"subject": "market_voice", "predicate": "reported:ferry", "value": "恢复通行", "secrecy": "public"}],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "没有时段变化"}],
        }
        from kernel.turncommit import TurnCommit
        assert native_authoring_policy_errors(engine.world, scene, TurnCommit.from_dict(overwrite))
        assert native_authoring_policy_errors(engine.world, scene, TurnCommit.from_dict(unsourced))
    finally:
        engine.store.close()
