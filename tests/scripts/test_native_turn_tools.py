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
from systems.knowledge import player_statement_fact_key


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
    assert authoring["host_authorized_routes"] == []
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


def test_native_heard_player_is_exact_local_and_cannot_overwrite_knowledge(tmp_path):
    campaign = _native_campaign(tmp_path)
    player_input = "我亲眼看见客栈门口的石阶有裂缝。"
    statement = player_input
    proposal = {
        "narration": "你把亲眼所见的石阶裂缝告诉玛拉。",
        "moves": [], "places": [], "links": [], "cast": [], "facts": [],
        "knowledge": [{
            "op": "heard_player", "knower": "innkeeper_mara",
            "speaker": "protagonist", "statement": statement,
        }],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "同场说明没有推进时段"}],
    }
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        engine.store.append_many([
            kernel_event("entity_created", day=1, scene="s1", turn=0, summary="隐藏旁听者", deltas={
                "id": "hidden_listener", "etype": "Person", "tier": "tracked",
                "attrs": {"sketch": "HEARD_PLAYER_PRIVATE_SENTINEL", "visibility": "hidden"},
            }),
            kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="隐藏旁听者在客栈", deltas={
                "who": "hidden_listener", "to": "stonebridge_inn",
            }),
        ], preflight=lambda history: project(engine.registry, history))
        engine.world = project(engine.registry, engine.store.iter_events())
        scene = _build_scene(engine)
        context = native_authoring_context(engine.world, scene, player_input=player_input)
        receipt = context["communication_receipts"]
        assert receipt[0]["statement"] == player_input
        assert receipt[0]["recipients"] == [{"id": "innkeeper_mara", "when": {"kind": "current_scene"}}]
        assert "HEARD_PLAYER_PRIVATE_SENTINEL" not in repr(context)

        from kernel.turncommit import TurnCommit
        commit = TurnCommit.from_dict(proposal)
        assert native_authoring_policy_errors(
            engine.world, scene, commit, player_input=player_input,
        ) == []
        altered = TurnCommit.from_dict({
            **proposal,
            "knowledge": [{**proposal["knowledge"][0], "statement": "作者改写的句子"}],
        })
        assert native_authoring_policy_errors(engine.world, scene, altered, player_input=player_input)
        remote = TurnCommit.from_dict({
            **proposal,
            "knowledge": [{**proposal["knowledge"][0], "knower": "protagonist"}],
        })
        assert native_authoring_policy_errors(engine.world, scene, remote, player_input=player_input)
    finally:
        engine.store.close()

    proposal_path = tmp_path / "heard-player.json"
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal_path), player_input,
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        graph = reopened.world["systems"]["ontology"]
        key = player_statement_fact_key("protagonist", statement)
        assert graph.value_at("innkeeper_mara", f"knows:{key}", 1) == statement
        assert graph.value_at("stonebridge_inn", "status", 1) is None
        assert native_authoring_policy_errors(
            reopened.world, _build_scene(reopened), TurnCommit.from_dict(proposal),
            player_input=player_input,
        )
    finally:
        reopened.store.close()


def test_legal_arrival_context_is_conditional_and_hides_destination_secrets(tmp_path):
    """A one-turn public exit can have an arrival scene without leaking host cast."""
    campaign = _native_campaign(tmp_path)
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        engine.store.append_many([
            kernel_event("character_created", day=1, scene="s1", turn=0, summary="洛恩在集市", deltas={
                "id": "market_witness_luoen", "sketch": "集市边整理货绳的洛恩", "goal": "收好摊位", "tier": "mentioned",
            }),
            kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="洛恩在集市", deltas={
                "who": "market_witness_luoen", "to": "stonebridge_market",
            }),
            kernel_event("entity_created", day=1, scene="s1", turn=0, summary="隐藏的人", deltas={
                "id": "market_hidden_contact", "etype": "Person", "tier": "tracked",
                "attrs": {"sketch": "ARRIVAL_PRIVATE_SENTINEL", "visibility": "hidden"},
            }),
            kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="隐藏的人在集市", deltas={
                "who": "market_hidden_contact", "to": "stonebridge_market",
            }),
        ], preflight=lambda history: project(engine.registry, history))
        engine.world = project(engine.registry, engine.store.iter_events())
        scene = _build_scene(engine)
        context = native_authoring_context(engine.world, scene, player_input="去集市看看洛恩")
        assert [person["id"] for person in context["present_people"]] == ["innkeeper_mara"]
        arrival = context["conditional_arrivals"]
        assert len(arrival) == 1
        assert arrival[0]["move"] == {"who": "protagonist", "to": "stonebridge_market"}
        assert arrival[0]["location"]["id"] == "stonebridge_market"
        assert [person["id"] for person in arrival[0]["present_people"]] == ["market_witness_luoen"]
        assert "ARRIVAL_PRIVATE_SENTINEL" not in repr(context)

        from kernel.turncommit import TurnCommit
        with_arrival = TurnCommit.from_dict({
            "narration": "你抵达集市，洛恩正在整理货绳；他只说自己仍在这里。",
            "moves": [{"who": "protagonist", "to": "stonebridge_market"}],
            "places": [], "links": [], "cast": [],
            "facts": [{"subject": "market_witness_luoen", "predicate": "reported:market_presence",
                       "value": "我仍在集市边整理货绳。", "secrecy": "public"}],
            "knowledge": [{"op": "told", "knower": "protagonist",
                           "fact_key": "market_witness_luoen.reported:market_presence",
                           "value": "我仍在集市边整理货绳。", "via": "market_witness_luoen"}],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "短程回到集市"}],
        })
        assert native_authoring_policy_errors(engine.world, scene, with_arrival) == []

        without_move = TurnCommit.from_dict({
            **with_arrival.to_dict(), "moves": [],
        })
        assert native_authoring_policy_errors(engine.world, scene, without_move)

        statement = "我在布棚外空地看见一根系棚绳的木桩。"
        arrival_receipt = TurnCommit.from_dict({
            "narration": "你抵达集市，把自己的观察告诉洛恩。",
            "moves": [{"who": "protagonist", "to": "stonebridge_market"}],
            "places": [], "links": [], "cast": [], "facts": [],
            "knowledge": [{
                "op": "heard_player", "knower": "market_witness_luoen",
                "speaker": "protagonist", "statement": statement,
            }],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "短程回到集市"}],
        })
        assert native_authoring_policy_errors(
            engine.world, scene, arrival_receipt, player_input=statement,
        ) == []
        assert native_authoring_policy_errors(
            engine.world, scene,
            TurnCommit.from_dict({**arrival_receipt.to_dict(), "moves": []}),
            player_input=statement,
        )
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
        # Character creation defaults to ``mentioned`` rather than tracked.
        # The next native brief must nevertheless retain this co-located,
        # public source and its statement as source-bound evidence.
        follow_up = native_authoring_context(engine.world, _build_scene(engine))
        reporter = next(person for person in follow_up["present_people"]
                        if person["id"] == "market_fishmonger_cenlan")
        assert reporter["speakable_evidence"] == [{
            "fact_key": "market_fishmonger_cenlan.reported:ferry_morning",
            "value": value,
            "source": "present_npc_persisted_report",
            "confidence": "self_reported",
        }]
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


def test_visible_existing_source_can_append_new_report_but_cannot_overwrite_it(tmp_path):
    """Natural follow-up questions extend provenance without rewriting history."""
    campaign = _native_campaign(tmp_path)
    proposal = tmp_path / "mara-follow-up.json"
    first_value = "玛拉自述她清晨离开客栈门口时，小路仍通向石桥集市；她没有亲自到过渡口。"
    proposal.write_text(json.dumps({
        "narration": "你追问玛拉离开客栈时看见的路况；她只说明自己的观察范围。",
        "moves": [], "places": [], "links": [], "cast": [],
        "facts": [{
            "subject": "innkeeper_mara", "predicate": "reported:market_route_observation",
            "value": first_value, "secrecy": "public",
        }],
        "knowledge": [{
            "op": "told", "knower": "protagonist",
            "fact_key": "innkeeper_mara.reported:market_route_observation",
            "value": first_value, "via": "innkeeper_mara",
        }],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "同场追问仍在晨间"}],
    }, ensure_ascii=False), encoding="utf-8")

    accepted = subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal), "询问玛拉看见的路况",
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    assert json.loads(accepted.stdout)["revision"] > 0

    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        graph = engine.world["systems"]["ontology"]
        assert graph.value_at("innkeeper_mara", "reported:market_route_observation", 1) == first_value
        assert graph.value_at("protagonist", "knows:innkeeper_mara.reported:market_route_observation", 1) == first_value

        from kernel.turncommit import TurnCommit
        overwrite = TurnCommit.from_dict({
            "narration": "你把玛拉的话改成已经确认的道路状态。",
            "moves": [], "places": [], "links": [], "cast": [],
            "facts": [{
                "subject": "innkeeper_mara", "predicate": "reported:market_route_observation",
                "value": "玛拉确认渡口已经恢复通行。", "secrecy": "public",
            }],
            "knowledge": [{
                "op": "told", "knower": "protagonist",
                "fact_key": "innkeeper_mara.reported:market_route_observation",
                "value": "玛拉确认渡口已经恢复通行。", "via": "innkeeper_mara",
            }],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试"}],
        })
        assert native_authoring_policy_errors(engine.world, _build_scene(engine), overwrite)
        assert graph.value_at("stonebridge_market", "status", 1) is None
    finally:
        engine.store.close()


def test_native_host_route_authorization_is_explicit_and_separate_from_direction_report(tmp_path):
    """Only a host option exposes and authorizes an existing hidden route."""
    campaign = _native_campaign(tmp_path)
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        engine.store.append_many([
            kernel_event("place_created", day=1, scene="s1", turn=0, summary="运河栈桥", deltas={
                "id": "canal_landing", "level": 2, "kind": "venue", "seed": "运河边的栈桥",
                "visibility": "undiscovered",
            }),
            kernel_event("place_linked", day=1, scene="s1", turn=0, summary="客栈至运河栈桥", deltas={
                "a": "stonebridge_inn", "b": "canal_landing", "visibility": "undiscovered",
            }),
        ], preflight=lambda history: project(engine.registry, history))
        engine.world = project(engine.registry, engine.store.iter_events())
    finally:
        engine.store.close()

    ordinary = tmp_path / "ordinary-brief.json"
    subprocess.run([
        sys.executable, str(EXPORTER), str(campaign), "--goal", "先问路，不预设目的地。",
        "--output", str(ordinary),
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    assert "canal_landing" not in ordinary.read_text(encoding="utf-8")
    forbidden = subprocess.run([
        sys.executable, str(EXPORTER), str(campaign), "--goal", "尝试暴露隐藏地点。",
        "--host-authorize-route", "north_bank_old_tower", "--output", str(tmp_path / "forbidden.json"),
    ], cwd=tmp_path, env=_offline_env(), capture_output=True, text=True)
    assert forbidden.returncode != 0
    assert "north_bank_old_tower" not in forbidden.stdout

    authorized = tmp_path / "authorized-brief.json"
    subprocess.run([
        sys.executable, str(EXPORTER), str(campaign), "--goal", "宿主已授权的路线可供选择。",
        "--host-authorize-route", "canal_landing", "--output", str(authorized),
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    brief = json.loads(authorized.read_text(encoding="utf-8"))
    assert brief["player_context"]["available_exits"] == [{
        "id": "stonebridge_market", "label": "石桥集市", "kind": "settlement",
    }]
    assert brief["authoring_context"]["host_authorized_routes"] == [{
        "from": "stonebridge_inn", "to": "canal_landing",
        "place_discovery_key": "canal_landing.discovered",
        "route_discovery_key": "route:canal_landing|stonebridge_inn.discovered",
    }]

    proposal = tmp_path / "authorized-route.json"
    proposal.write_text(json.dumps({
        "narration": "你按宿主已批准的线索前往运河栈桥。",
        "moves": [{"who": "protagonist", "to": "canal_landing"}],
        "places": [], "links": [], "cast": [], "facts": [],
        "knowledge": [
            {"op": "discover_place", "knower": "protagonist", "place": "canal_landing"},
            {"op": "discover_route", "knower": "protagonist", "a": "stonebridge_inn", "b": "canal_landing"},
        ],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "短程前往仍在晨间"}],
    }, ensure_ascii=False), encoding="utf-8")
    before = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    revision = before.store.revision
    before.store.close()
    rejected = subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal), "前往运河栈桥",
    ], cwd=tmp_path, env=_offline_env(), capture_output=True, text=True)
    assert rejected.returncode != 0
    after_reject = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    assert after_reject.store.revision == revision
    after_reject.store.close()

    accepted = subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal), "前往运河栈桥",
        "--host-authorize-route", "canal_landing",
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    assert json.loads(accepted.stdout)["revision"] > revision
    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        graph = reopened.world["systems"]["ontology"]
        assert graph.neighbors("protagonist", "located_in", 1) == ["canal_landing"]
        assert graph.value_at("protagonist", "knows:canal_landing.discovered", 1) is True
        assert graph.value_at("protagonist", "knows:route:canal_landing|stonebridge_inn.discovered", 1) is True
    finally:
        reopened.store.close()


def test_native_author_can_create_a_fresh_public_frontier_place_but_not_touch_hidden_topology(tmp_path):
    """Fresh public creation remains possible without relabeling a hidden route."""
    campaign = _native_campaign(tmp_path)
    proposal = tmp_path / "fresh-frontier.json"
    proposal.write_text(json.dumps({
        "narration": "你离开客栈，走到新近发现的公开石阶平台；这里不是任何既有地点的别名。",
        "moves": [{"who": "protagonist", "to": "courtyard_steps"}],
        "places": [{
            "id": "courtyard_steps", "level": 2, "kind": "venue",
            "label": "石阶平台", "seed": "客栈外新开辟的一段公开石阶",
        }],
        "links": [{"a": "stonebridge_inn", "b": "courtyard_steps", "travel_cost": 1}],
        "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "短程移动仍在晨间"}],
    }, ensure_ascii=False), encoding="utf-8")

    accepted = subprocess.run([
        sys.executable, str(IMPORTER), str(campaign), str(proposal), "到门外的石阶平台看看",
    ], cwd=tmp_path, env=_offline_env(), check=True, capture_output=True, text=True)
    assert json.loads(accepted.stdout)["revision"] > 0
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        graph = engine.world["systems"]["ontology"]
        assert graph.neighbors("protagonist", "located_in", 1) == ["courtyard_steps"]
        assert graph.get_entity("courtyard_steps").attrs.get("visibility") is None
        assert "courtyard_steps" in graph.neighbors("stonebridge_inn", "adjacent_to", 1)

        from kernel.turncommit import TurnCommit
        hidden_touch = TurnCommit.from_dict({
            "narration": "你给新地点接上一条未知旧路。", "moves": [],
            "places": [{"id": "fresh_marker", "level": 2, "kind": "venue", "seed": "新标记"}],
            "links": [
                {"a": "courtyard_steps", "b": "fresh_marker"},
                {"a": "fresh_marker", "b": "north_bank_old_tower"},
            ],
            "cast": [], "facts": [],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试"}],
        })
        assert native_authoring_policy_errors(engine.world, _build_scene(engine), hidden_touch)
        duplicate_frontier = TurnCommit.from_dict({
            "narration": "你重复登记同一条新路。", "moves": [],
            "places": [{"id": "duplicate_marker", "level": 2, "kind": "venue", "seed": "重复标记"}],
            "links": [
                {"a": "courtyard_steps", "b": "duplicate_marker"},
                {"a": "courtyard_steps", "b": "duplicate_marker"},
            ],
            "cast": [], "facts": [],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "测试"}],
        })
        assert native_authoring_policy_errors(engine.world, _build_scene(engine), duplicate_frontier)
    finally:
        engine.store.close()
