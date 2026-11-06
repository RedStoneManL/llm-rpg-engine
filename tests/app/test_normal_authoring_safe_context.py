"""Real normal-entry coverage for the shared safe authoring projection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import _build_scene, play_loop
from engine.store import RevisionConflict
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy, HybridStrategy, _NARRATE_PROMPT, _SYSTEM_PROMPT_HYBRID
from loop.turn import TurnRejected, run_turn
from loop.compare import run_compare
from systems.knowledge import player_statement_fact_key


PUBLIC_WORDS = "PUBLIC_NORMAL_AUTHOR_RECALL"
PRIVATE_SENTINEL = "PRIVATE_NORMAL_AUTHORING_SENTINEL"


def _move_proposal() -> dict:
    return {
        "narration": "你沿公开道路抵达集市。",
        "moves": [{"who": "protagonist", "to": "stonebridge_market"}],
        "places": [], "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "短程步行"}],
    }


def _original_failure_candidate() -> dict:
    fixture = Path(__file__).parents[1] / "fixtures" / "ordinary_gui_transcript_laundering_candidate.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _engine_with_safe_arrival(tmp_path, provider) -> object:
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    fact_key = player_statement_fact_key("protagonist", PUBLIC_WORDS)
    engine.store.append_many([
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="public witness", deltas={
            "id": "market_witness", "sketch": "公开集市见证人", "goal": "整理摊位", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="witness at market", deltas={
            "who": "market_witness", "to": "stonebridge_market",
        }),
        kernel_event("entity_created", day=1, scene="s1", turn=0, summary="hidden arrival person", deltas={
            "id": "hidden_arrival_person", "etype": "Person", "tier": "tracked",
            "attrs": {"visibility": "hidden", "sketch": PRIVATE_SENTINEL},
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="hidden at market", deltas={
            "who": "hidden_arrival_person", "to": "stonebridge_market",
        }),
        kernel_event("knowledge_set", day=1, scene="s1", turn=0, summary="witness heard exact public words", deltas={
            "knower": "market_witness", "fact_key": fact_key, "value": PUBLIC_WORDS,
            "via": "protagonist", "source_kind": "player_statement",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    return engine


def _prompt(provider: FakeLLMProvider) -> str:
    prompts = [user for _, user in provider.calls if "[player]" in user]
    assert prompts
    return prompts[0]


def test_cli_author_strategy_receives_only_safe_conditional_arrival_material(tmp_path):
    provider = FakeLLMProvider(json_responses=[_move_proposal()])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    out: list[str] = []
    try:
        play_loop(engine, inputs=["去集市看看", "/quit"], out=out.append, max_repairs=0)
        prompt = _prompt(provider)
        assert "【条件化抵达材料】" in prompt
        assert "market_witness" in prompt and PUBLIC_WORDS not in prompt
        assert "若本回合 moves 同时使 protagonist 合法抵达 stonebridge_market" in prompt
        assert any("宿主已明确签发的 heard_player" in sys for sys, _ in provider.calls)
        assert PRIVATE_SENTINEL not in prompt and "hidden_arrival_person" not in prompt
        assert engine.world["systems"]["ontology"].neighbors("protagonist", "located_in", 1) == ["stonebridge_market"]
    finally:
        engine.store.close()


def test_normal_gui_author_strategy_uses_same_safe_projection_without_serializing_snapshot(tmp_path):
    provider = FakeLLMProvider(json_responses=[_move_proposal()])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    try:
        initial = dashboard.state()
        response = dashboard.action(player_input="去集市看看", action_id="normal-safe-context",
                                    expected_revision=initial["revision"])
        prompt = _prompt(provider)
        assert response["state"]["scene"]["location"]["id"] == "stonebridge_market"
        assert "【条件化抵达材料】" in prompt and PUBLIC_WORDS not in prompt
        assert PRIVATE_SENTINEL not in prompt and "hidden_arrival_person" not in prompt
        # Once arrival really committed, the same recipient can recall the words.
        from context.assembler import safe_authoring_handoff
        reached = safe_authoring_handoff(engine.world, _build_scene(engine), trusted_events=list(engine.store.iter_events()))
        assert PUBLIC_WORDS in reached
        metadata = engine.store.get_action_result_metadata("normal-safe-context")
        assert "_trusted_authoring_events" not in json.dumps(metadata, ensure_ascii=False)
    finally:
        engine.store.close()


def test_normal_gui_hybrid_strategy_uses_publishable_player_contract(tmp_path):
    """The alternate normal strategy must not be prompted as a GM/director.

    This is the full player-controller path, with the local fake standing in
    for both of HybridStrategy's provider calls.  It verifies a normal move
    still publishes, while the structural call receives the same redacted
    context and the ordinary-player rather than omniscient contract.
    """
    provider = FakeLLMProvider(
        responses=["你沿公开道路抵达集市，晨雾在脚边散开。"],
        json_responses=[_move_proposal()],
    )
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=HybridStrategy(), demo=False)
    try:
        initial = dashboard.state()
        response = dashboard.action(player_input="去集市看看", action_id="normal-hybrid-contract",
                                    expected_revision=initial["revision"])
        assert response["state"]["scene"]["location"]["id"] == "stonebridge_market"
        assert response["state"]["narration"][-1].startswith("你沿公开道路抵达集市")
        narrate_calls = [user for system, user in provider.calls if system == _NARRATE_PROMPT]
        structural_calls = [user for system, user in provider.calls if system == _SYSTEM_PROMPT_HYBRID]
        assert len(narrate_calls) == len(structural_calls) == 1
        assert "【条件化抵达材料】" in structural_calls[0]
        assert PRIVATE_SENTINEL not in "\n".join(narrate_calls + structural_calls)
        assert "完整世界视野" not in _SYSTEM_PROMPT_HYBRID
        assert "ordinary player" not in _SYSTEM_PROMPT_HYBRID.lower()
    finally:
        engine.store.close()


def test_compare_authors_receive_the_same_safe_projection_before_final_apply(tmp_path):
    provider = FakeLLMProvider(json_responses=[_move_proposal(), _move_proposal()])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    try:
        play_loop(engine, inputs=["/compare on", "去集市看看", "/quit"], out=lambda _: None,
                  max_repairs=0)
        prompts = [user for _, user in provider.calls if "[player] 去集市看看" in user]
        assert len(prompts) >= 2
        assert all("【条件化抵达材料】" in prompt and PUBLIC_WORDS not in prompt for prompt in prompts[:2])
        assert all(PRIVATE_SENTINEL not in prompt and "hidden_arrival_person" not in prompt
                   for prompt in prompts[:2])
    finally:
        engine.store.close()


def test_cli_player_words_do_not_create_a_receipt_without_a_host_capability(tmp_path):
    statement = "PUBLIC_INPUT_MUST_NOT_BECOME_A_RECEIPT"
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "heard_player", "receipt_id": "forged", "knower": "market_witness",
        "speaker": "protagonist", "statement": statement,
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    before = engine.store.revision
    output: list[str] = []
    try:
        play_loop(engine, inputs=[f"我只对洛恩说：{statement}", "/quit"], out=output.append, max_repairs=0)
        assert engine.store.revision == before
        assert "世界保持原状" in "\n".join(output)
        assert any("宿主已明确签发的 heard_player" in sys for sys, _ in provider.calls)
    finally:
        engine.store.close()


def _npc_knowledge_bypass(op: str) -> dict:
    proposal = _move_proposal()
    if op == "told":
        proposal["knowledge"] = [{
            "op": "told", "knower": "market_witness", "fact_key": "slope.hazard",
            "value": "玩家刚才说坡口有裂缝", "via": "玩家自由文本",
        }]
    elif op == "endowment":
        proposal["knowledge"] = [{
            "op": "endowment", "knower": "market_witness",
            "grants": [{"fact_key": "slope.hazard", "value": "玩家自由文本"}],
        }]
    elif op == "broadcast":
        proposal["knowledge"] = [{
            "op": "broadcast", "fact_key": "slope.hazard", "value": "玩家自由文本",
            "audience": {"place": "stonebridge_market"},
        }]
    else:
        proposal["facts"] = [{
            "subject": "market_witness", "predicate": "knows:slope.hazard",
            "value": "玩家自由文本", "secrecy": "public",
        }]
    return proposal


def _public_observation_candidate(place: str, summary: str) -> dict:
    """A normal-author candidate for a bounded, non-moving field check."""
    return {
        "narration": "你停在原地，只确认眼前可见的近处标记，不把它当作远方路线结论。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "world": [{
            "areas": [place], "level": 1, "summary": summary,
            "public_observation": True, "visibility": "public",
            "observation_kind": "single_observation", "observation_range": "主角脚边两步内",
        }],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "原地有界观察"}],
    }


def _public_protagonist_fact_candidate(*, subject: str = "protagonist", value: str = "公开状态稳定") -> dict:
    return {
        "narration": "你停在原地，只记录此刻可公开确认的自身状态。",
        "moves": [], "places": [], "cast": [],
        "facts": [{"subject": subject, "predicate": "state", "value": value, "secrecy": "public"}],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "原地确认状态"}],
    }


def _local_protagonist_cast_candidate(*, person_id: str = "protagonist", value: str = "平静") -> dict:
    return {
        "narration": "你停在原地，神色渐渐安定下来。",
        "moves": [], "places": [],
        "cast": [{"id": person_id, "op": "evolve", "predicate": "mood", "value": value}],
        "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "原地整理心绪"}],
    }


def _inventory_transfer_candidate(to: str) -> dict:
    return {
        "narration": "你郑重把旧铜灯交到对方手中。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "items": [{"op": "transfer", "item": "old_brass_lantern", "to": to}],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "当面交接物品"}],
    }


def _quest_candidate(op: str, quest_id: str, summary: str | None = None) -> dict:
    quest = {"op": op, "id": quest_id}
    if summary is not None:
        quest["summary"] = summary
    return {
        "narration": "你把眼前的线索整理成一条明确、可回看的任务。",
        "moves": [], "places": [], "cast": [], "facts": [], "quests": [quest],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "整理当前线索"}],
    }


def _faction_member_candidate(person: str, faction: str) -> dict:
    return {
        "narration": "远方势力的成员名册在此刻被改写。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "factions": [{"op": "member", "person": person, "faction": faction, "rank": "内环"}],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "无关的远方组织变更"}],
    }


def _remote_topology_candidate() -> dict:
    return {
        "narration": "远方两处旧地点之间忽然多出一条秘密捷径。",
        "moves": [], "places": [], "cast": [], "facts": [],
        "links": [{"a": "stonebridge_market", "b": "north_bank_old_tower", "visibility": "public"}],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "越权改写远方地图"}],
    }


def _public_frontier_candidate() -> dict:
    return {
        "narration": "你在客栈门外发现一处公开的小石阶，并沿着新近看清的短径走到那里。",
        "moves": [{"who": "protagonist", "to": "inn_stone_step"}],
        "places": [{"id": "inn_stone_step", "level": 1, "kind": "venue",
                    "seed": "客栈门外的小石阶", "visibility": "public"}],
        "links": [{"a": "stonebridge_inn", "b": "inn_stone_step", "visibility": "public"}],
        "cast": [], "facts": [],
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "门外短程观察"}],
    }


def _ordinary_demo_dashboard(tmp_path, candidate: dict) -> PlayerDashboard:
    provider = FakeLLMProvider(json_responses=[candidate])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    return PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False, max_repairs=0)


def _add_public_local_recipient(dashboard: PlayerDashboard, person_id: str = "inn_guest") -> None:
    """Give the current scene one public recipient without changing the action under test."""
    scene = _build_scene(dashboard.engine)
    dashboard.engine.store.append_many([
        kernel_event("character_created", day=scene["day"], scene=scene["id"], turn=0,
                     summary="public local recipient", deltas={
                         "id": person_id, "sketch": "客栈里的公开旅人", "goal": "等候商队", "tier": "tracked",
                     }),
        kernel_event("entity_moved", day=scene["day"], scene=scene["id"], turn=0,
                     summary="recipient at current location", deltas={
                         "who": person_id, "to": scene["location"],
                     }),
    ], preflight=lambda history: project(dashboard.engine.registry, history))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision


def _add_local_active_quest(dashboard: PlayerDashboard, quest_id: str = "inn_delivery") -> None:
    """Seed a host-owned, public task at the current location for normal play."""
    scene = _build_scene(dashboard.engine)
    dashboard.engine.store.append_many([
        kernel_event("quest_opened", day=scene["day"], scene=scene["id"], turn=0,
                     summary="public local quest", deltas={
                         "id": quest_id, "summary": "把给客栈的补给交到柜台", "anchor": scene["location"],
                     }),
    ], preflight=lambda history: project(dashboard.engine.registry, history))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision


class _InputKeyedAuthorProvider(FakeLLMProvider):
    """Offline AuthorStrategy fixture that ignores unrelated backstage calls."""

    def __init__(self, by_player_input: dict[str, dict]):
        super().__init__()
        self._by_player_input = by_player_input

    def complete_messages(self, messages, *, model=None, max_tokens=None):
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        self.calls.append((system, user))
        for player_input, proposal in self._by_player_input.items():
            if f"[player] {player_input}" in user:
                return json.dumps(proposal, ensure_ascii=False)
        # Backstage density/recap calls receive an inert local JSON object.
        return "{}"


def test_normal_gui_rejects_remote_hidden_world_observation_without_writes(tmp_path):
    """The redacted prompt alone must not be the only remote-world boundary."""
    dashboard = _ordinary_demo_dashboard(
        tmp_path,
        _public_observation_candidate("north_bank_old_tower", "REMOTE_WORLD_OBSERVATION_SENTINEL"),
    )
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="world observation lies outside"):
            dashboard.action(player_input="原地观察", action_id="remote-world-observation",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-world-observation") is None
        assert len(dashboard.engine.provider.calls) == 1
        assert "REMOTE_WORLD_OBSERVATION_SENTINEL" not in json.dumps(
            list(dashboard.engine.store.iter_events()), ensure_ascii=False,
        )
    finally:
        dashboard.engine.store.close()


def test_normal_gui_final_payload_cannot_add_remote_world_observation(tmp_path, monkeypatch):
    """A materializer cannot bypass the candidate-level local-observation rule."""
    dashboard = _ordinary_demo_dashboard(
        tmp_path,
        _public_observation_candidate("stonebridge_inn", "脚边石阶有浅浅湿痕。"),
    )
    owner = dashboard.engine.registry.owner_of_section("world")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("world_change", day=day, scene=scene, turn=turn,
            summary="REMOTE_FINAL_WORLD_SENTINEL", deltas={
                "place": "north_bank_old_tower", "level": 1,
                "summary": "REMOTE_FINAL_WORLD_SENTINEL", "source": "author_world",
                "visibility": "public", "public_observation": True,
                "observation_kind": "single_observation", "observation_range": "远处不可见地点",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="final publish violates the player world observation boundary"):
            dashboard.action(player_input="原地观察", action_id="remote-final-world-observation",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-final-world-observation") is None
        assert "REMOTE_FINAL_WORLD_SENTINEL" not in json.dumps(
            list(dashboard.engine.store.iter_events()), ensure_ascii=False,
        )
    finally:
        dashboard.engine.store.close()


def test_normal_gui_persists_local_public_observation_and_mode_bound_recovery(tmp_path):
    campaign = tmp_path / "campaign"
    candidate = _public_observation_candidate("stonebridge_inn", "脚边石阶有浅浅湿痕。")
    dashboard = _ordinary_demo_dashboard(tmp_path, candidate)
    initial = dashboard.state()
    try:
        first = dashboard.action(player_input="留在门前确认湿痕", action_id="local-world-observation",
                                 expected_revision=initial["revision"])
        assert first["duplicate"] is False
        events = [event for event in dashboard.engine.store.iter_events()
                  if event.get("type") == "world_change" and event.get("turn") == first["state"]["turn"]]
        assert len(events) == 1
        assert events[0]["deltas"] == {
            "observer": "protagonist",
            "place": "stonebridge_inn", "level": 1, "summary": "脚边石阶有浅浅湿痕。",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "single_observation", "observation_range": "主角脚边两步内",
        }
        metadata = dashboard.engine.store.get_action_result_metadata("local-world-observation")
        assert metadata["authority_modes"] == {
            "ordinary_player_knowledge_authority": True,
            "ordinary_player_world_authority": True,
            "ordinary_player_fact_authority": True,
            "ordinary_player_entity_authority": True,
            "ordinary_player_faction_authority": True,
                "ordinary_player_place_authority": True,
                "ordinary_player_item_authority": True,
                "ordinary_player_quest_authority": True,
                "host_discovery_reveal_only": False,
        }
        calls_after_first = len(dashboard.engine.provider.calls)
        duplicate = dashboard.action(player_input="留在门前确认湿痕", action_id="local-world-observation",
                                     expected_revision=initial["revision"])
        assert duplicate["duplicate"] is True
        assert len(dashboard.engine.provider.calls) == calls_after_first
    finally:
        dashboard.engine.store.close()

    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        observations = [event for event in reopened.store.iter_events()
                        if event.get("type") == "world_change" and event.get("deltas", {}).get("summary") == "脚边石阶有浅浅湿痕。"]
        assert len(observations) == 1
    finally:
        reopened.store.close()


def test_world_observation_authority_does_not_depend_on_travel_mode(tmp_path):
    """Shared callers cannot turn off the local-observation boundary with travel=False."""
    provider = FakeLLMProvider(json_responses=[
        _public_observation_candidate("north_bank_old_tower", "REMOTE_TRAVEL_FALSE_SENTINEL"),
    ])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    before = engine.store.revision
    try:
        with pytest.raises(TurnRejected, match="world observation lies outside"):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "原地观察",
                strategy=AuthorStrategy(), provider=provider, action_id="world-without-travel",
                expected_revision=before, enforce_player_travel=False,
                enforce_player_knowledge_authority=False,
                enforce_player_world_authority=True,
            )
        assert engine.store.revision == before
    finally:
        engine.store.close()


def test_normal_gui_rejects_remote_hidden_fact_without_writes(tmp_path):
    dashboard = _ordinary_demo_dashboard(
        tmp_path,
        _public_protagonist_fact_candidate(
            subject="north_bank_old_tower", value="REMOTE_FACT_SENTINEL",
        ),
    )
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="player fact lies outside"):
            dashboard.action(player_input="原地观察", action_id="remote-hidden-fact",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-hidden-fact") is None
        assert len(dashboard.engine.provider.calls) == 1
        assert "REMOTE_FACT_SENTINEL" not in json.dumps(
            list(dashboard.engine.store.iter_events()), ensure_ascii=False,
        )
    finally:
        dashboard.engine.store.close()


def test_normal_gui_final_payload_cannot_add_remote_fact(tmp_path, monkeypatch):
    dashboard = _ordinary_demo_dashboard(tmp_path, _public_protagonist_fact_candidate())
    owner = dashboard.engine.registry.owner_of_section("facts")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("fact_asserted", day=day, scene=scene, turn=turn,
            summary="REMOTE_FINAL_FACT_SENTINEL", deltas={
                "subject": "north_bank_old_tower", "predicate": "condition",
                "value": "REMOTE_FINAL_FACT_SENTINEL", "secrecy": "public",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="final publish violates the player fact authority boundary"):
            dashboard.action(player_input="原地确认", action_id="remote-final-fact",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-final-fact") is None
    finally:
        dashboard.engine.store.close()


def test_normal_gui_persists_public_protagonist_fact_and_recovers_without_generation(tmp_path):
    campaign = tmp_path / "campaign"
    dashboard = _ordinary_demo_dashboard(
        tmp_path, _public_protagonist_fact_candidate(value="本回合确认的公开状态"),
    )
    initial = dashboard.state()
    try:
        first = dashboard.action(player_input="原地确认状态", action_id="local-public-self-fact",
                                 expected_revision=initial["revision"])
        assert first["duplicate"] is False
        assert dashboard.engine.world["systems"]["ontology"].value_at(
            "protagonist", "state", 1,
        ) == "本回合确认的公开状态"
        metadata = dashboard.engine.store.get_action_result_metadata("local-public-self-fact")
        assert metadata["authority_modes"]["ordinary_player_fact_authority"] is True
        calls_after_first = len(dashboard.engine.provider.calls)
        duplicate = dashboard.action(player_input="原地确认状态", action_id="local-public-self-fact",
                                     expected_revision=initial["revision"])
        assert duplicate["duplicate"] is True
        assert len(dashboard.engine.provider.calls) == calls_after_first
    finally:
        dashboard.engine.store.close()

    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    dashboard = PlayerDashboard(reopened, strategy=AuthorStrategy(), demo=False, max_repairs=0)
    try:
        duplicate = dashboard.action(player_input="原地确认状态", action_id="local-public-self-fact",
                                     expected_revision=initial["revision"])
        assert duplicate["duplicate"] is True
        assert reopened.world["systems"]["ontology"].value_at(
            "protagonist", "state", 1,
        ) == "本回合确认的公开状态"
        assert not reopened.provider.calls
    finally:
        reopened.store.close()


def test_fact_authority_does_not_depend_on_travel_and_binds_recovery_mode(tmp_path):
    campaign = tmp_path / "campaign"
    candidate = _public_protagonist_fact_candidate(value="模式绑定的公开状态")
    provider = FakeLLMProvider(json_responses=[candidate])
    engine = build_engine(campaign, provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    base_revision = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("fact-authority conflict must reject before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "原地确认",
            strategy=AuthorStrategy(), provider=provider, action_id="fact-mode-bound-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_knowledge_authority=False,
            enforce_player_world_authority=False,
            enforce_player_fact_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "原地确认",
                strategy=MustNotRun(), provider=provider, action_id="fact-mode-bound-action",
                expected_revision=base_revision, enforce_player_travel=False,
                enforce_player_knowledge_authority=False,
                enforce_player_world_authority=False,
                enforce_player_fact_authority=True,
            )
        assert engine.store.revision == base_revision + 1
    finally:
        engine.store.close()


def test_normal_gui_rejects_remote_hidden_character_evolution_without_writes(tmp_path):
    dashboard = _ordinary_demo_dashboard(
        tmp_path,
        _local_protagonist_cast_candidate(person_id="tower_warden", value="REMOTE_CAST_SENTINEL"),
    )
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="player entity change lies outside"):
            dashboard.action(player_input="原地观察", action_id="remote-hidden-cast",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-hidden-cast") is None
        assert len(dashboard.engine.provider.calls) == 1
    finally:
        dashboard.engine.store.close()


def test_normal_gui_final_payload_cannot_add_remote_character_evolution(tmp_path, monkeypatch):
    dashboard = _ordinary_demo_dashboard(tmp_path, _local_protagonist_cast_candidate())
    owner = dashboard.engine.registry.owner_of_section("cast")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("character_evolved", day=day, scene=scene, turn=turn,
            summary="REMOTE_FINAL_CAST_SENTINEL", deltas={
                "id": "tower_warden", "predicate": "mood", "value": "REMOTE_FINAL_CAST_SENTINEL",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="final publish violates the player entity authority boundary"):
            dashboard.action(player_input="原地整理心绪", action_id="remote-final-cast",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-final-cast") is None
    finally:
        dashboard.engine.store.close()


def test_normal_gui_rejects_generic_relation_to_hidden_entity_without_writes(tmp_path):
    candidate = _public_protagonist_fact_candidate()
    candidate["relations"] = [{"src": "protagonist", "rel": "observes", "dst": "tower_warden"}]
    dashboard = _ordinary_demo_dashboard(tmp_path, candidate)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="player entity change lies outside"):
            dashboard.action(player_input="原地观察", action_id="hidden-generic-relation",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("hidden-generic-relation") is None
    finally:
        dashboard.engine.store.close()


def test_normal_gui_persists_local_cast_and_public_new_character(tmp_path):
    dashboard = _ordinary_demo_dashboard(tmp_path, _local_protagonist_cast_candidate(value="安定"))
    initial = dashboard.state()
    try:
        result = dashboard.action(player_input="原地整理心绪", action_id="local-cast-evolution",
                                  expected_revision=initial["revision"])
        assert result["duplicate"] is False
        assert dashboard.engine.world["systems"]["ontology"].value_at("protagonist", "mood", 1) == "安定"
    finally:
        dashboard.engine.store.close()

    candidate = _public_protagonist_fact_candidate()
    candidate["cast"] = [{"id": "new_public_witness", "op": "create", "sketch": "公开的路边见证人", "goal": "寻找失物"}]
    dashboard = _ordinary_demo_dashboard(tmp_path / "new", candidate)
    initial = dashboard.state()
    try:
        result = dashboard.action(player_input="向路人问好", action_id="new-public-character",
                                  expected_revision=initial["revision"])
        assert result["duplicate"] is False
        assert dashboard.engine.world["systems"]["ontology"].get_entity("new_public_witness") is not None
    finally:
        dashboard.engine.store.close()


def test_entity_authority_binds_recovery_mode_before_generation(tmp_path):
    provider = FakeLLMProvider(json_responses=[_local_protagonist_cast_candidate()])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    base_revision = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("entity-authority conflict must reject before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "原地整理心绪",
            strategy=AuthorStrategy(), provider=provider, action_id="entity-mode-bound-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_entity_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "原地整理心绪",
                strategy=MustNotRun(), provider=provider, action_id="entity-mode-bound-action",
                expected_revision=base_revision, enforce_player_travel=False,
                enforce_player_entity_authority=True,
            )
        assert engine.store.revision == base_revision + 1
    finally:
        engine.store.close()


def test_normal_gui_rejects_remote_faction_membership_without_writes(tmp_path):
    dashboard = _ordinary_demo_dashboard(
        tmp_path,
        _faction_member_candidate("tower_warden", "remote_secret_order"),
    )
    scene = _build_scene(dashboard.engine)
    dashboard.engine.store.append_many([
        kernel_event("entity_created", day=scene["day"], scene=scene["id"], turn=0,
                     summary="remote hidden person", deltas={
                         "id": "tower_warden", "etype": "Person", "tier": "tracked",
                         "attrs": {"visibility": "hidden"},
                     }),
        kernel_event("faction_created", day=scene["day"], scene=scene["id"], turn=0,
                     summary="remote secret faction", deltas={
                         "op": "faction", "id": "remote_secret_order", "tier": "tracked",
                     }),
    ], preflight=lambda history: project(dashboard.engine.registry, history))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="player faction change lies outside"):
            dashboard.action(player_input="改写远方成员名册", action_id="remote-faction-member",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-faction-member") is None
        assert len(dashboard.engine.provider.calls) == 1
        assert "remote_secret_order" not in json.dumps(
            [event for event in dashboard.engine.store.iter_events() if event.get("turn") != 0],
            ensure_ascii=False,
        )
    finally:
        dashboard.engine.store.close()


def test_normal_gui_final_payload_cannot_add_faction_membership(tmp_path, monkeypatch):
    dashboard = _ordinary_demo_dashboard(tmp_path, _public_protagonist_fact_candidate())
    owner = dashboard.engine.registry.owner_of_section("facts")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("member_changed", day=day, scene=scene, turn=turn,
            summary="REMOTE_FINAL_FACTION_SENTINEL", deltas={
                "person": "tower_warden", "faction": "remote_secret_order", "rank": "内环",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="final publish violates the player faction authority boundary"):
            dashboard.action(player_input="原地确认", action_id="remote-final-faction",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-final-faction") is None
    finally:
        dashboard.engine.store.close()


def test_faction_authority_binds_recovery_mode_before_generation(tmp_path):
    provider = FakeLLMProvider(json_responses=[_public_protagonist_fact_candidate()])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    base_revision = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("faction-authority conflict must reject before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "原地确认",
            strategy=AuthorStrategy(), provider=provider, action_id="faction-mode-bound-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_faction_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "原地确认",
                strategy=MustNotRun(), provider=provider, action_id="faction-mode-bound-action",
                expected_revision=base_revision, enforce_player_travel=False,
                enforce_player_faction_authority=True,
            )
        assert engine.store.revision == base_revision + 1
    finally:
        engine.store.close()


def test_normal_gui_rejects_existing_topology_rewrite_without_writes(tmp_path):
    dashboard = _ordinary_demo_dashboard(tmp_path, _remote_topology_candidate())
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="player place change lies outside"):
            dashboard.action(player_input="替远方旧塔开一条新路", action_id="remote-topology-rewrite",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-topology-rewrite") is None
        assert len(dashboard.engine.provider.calls) == 1
    finally:
        dashboard.engine.store.close()


def test_normal_gui_final_payload_cannot_move_existing_remote_person(tmp_path, monkeypatch):
    dashboard = _ordinary_demo_dashboard(tmp_path, _public_protagonist_fact_candidate())
    owner = dashboard.engine.registry.owner_of_section("facts")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("entity_moved", day=day, scene=scene, turn=turn,
            summary="REMOTE_PERSON_MOVE_SENTINEL", deltas={
                "who": "tower_warden", "to": "stonebridge_market",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="final publish violates the player place authority boundary"):
            dashboard.action(player_input="原地确认", action_id="remote-person-move",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-person-move") is None
    finally:
        dashboard.engine.store.close()


def test_normal_gui_can_create_one_public_frontier_and_move_to_it(tmp_path):
    dashboard = _ordinary_demo_dashboard(tmp_path, _public_frontier_candidate())
    initial = dashboard.state()
    try:
        result = dashboard.action(player_input="查看客栈门外的小石阶", action_id="public-frontier",
                                  expected_revision=initial["revision"])
        assert result["duplicate"] is False
        graph = dashboard.engine.world["systems"]["ontology"]
        assert graph.get_entity("inn_stone_step").attrs["visibility"] == "public"
        assert graph.neighbors("protagonist", "located_in", 1) == ["inn_stone_step"]
        assert "inn_stone_step" in graph.neighbors("stonebridge_inn", "adjacent_to", 1)
    finally:
        dashboard.engine.store.close()


def test_normal_gui_rejects_inventory_transfer_to_remote_hidden_person_without_writes(tmp_path):
    dashboard = _ordinary_demo_dashboard(tmp_path, _inventory_transfer_candidate("tower_warden"))
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="player item change lies outside"):
            dashboard.action(player_input="把旧铜灯交给远方的人", action_id="remote-item-transfer",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-item-transfer") is None
        assert dashboard.engine.world["systems"]["ontology"].neighbors(
            "old_brass_lantern", "held_by", 1,
        ) == ["protagonist"]
    finally:
        dashboard.engine.store.close()


def test_normal_gui_final_payload_cannot_transfer_inventory_to_remote_person(tmp_path, monkeypatch):
    dashboard = _ordinary_demo_dashboard(tmp_path, _inventory_transfer_candidate("inn_guest"))
    _add_public_local_recipient(dashboard)
    owner = dashboard.engine.registry.owner_of_section("items")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("item_transferred", day=day, scene=scene, turn=turn,
            summary="REMOTE_FINAL_ITEM_SENTINEL", deltas={
                "op": "transfer", "item": "old_brass_lantern", "to": "tower_warden",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="final publish violates the player item authority boundary"):
            dashboard.action(player_input="把旧铜灯交给旅人", action_id="remote-final-item-transfer",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-final-item-transfer") is None
        assert dashboard.engine.world["systems"]["ontology"].neighbors(
            "old_brass_lantern", "held_by", 1,
        ) == ["protagonist"]
    finally:
        dashboard.engine.store.close()


def test_normal_gui_persists_local_inventory_transfer_and_recovers_without_generation(tmp_path):
    campaign = tmp_path / "campaign"
    dashboard = _ordinary_demo_dashboard(tmp_path, _inventory_transfer_candidate("inn_guest"))
    _add_public_local_recipient(dashboard)
    initial = dashboard.state()
    try:
        assert {entry["id"] for entry in initial["speech_recipients"]} == {"inn_guest"}
        first = dashboard.action(player_input="把旧铜灯交给旅人", action_id="local-inventory-transfer",
                                 expected_revision=initial["revision"])
        assert first["duplicate"] is False
        assert dashboard.engine.world["systems"]["ontology"].neighbors(
            "old_brass_lantern", "held_by", 1,
        ) == ["inn_guest"]
        assert first["state"]["inventory"] == []
        metadata = dashboard.engine.store.get_action_result_metadata("local-inventory-transfer")
        assert metadata["authority_modes"]["ordinary_player_item_authority"] is True
        calls_after_first = len(dashboard.engine.provider.calls)
        duplicate = dashboard.action(player_input="把旧铜灯交给旅人", action_id="local-inventory-transfer",
                                     expected_revision=initial["revision"])
        assert duplicate["duplicate"] is True
        assert len(dashboard.engine.provider.calls) == calls_after_first
    finally:
        dashboard.engine.store.close()

    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    dashboard = PlayerDashboard(reopened, strategy=AuthorStrategy(), demo=False, max_repairs=0)
    try:
        duplicate = dashboard.action(player_input="把旧铜灯交给旅人", action_id="local-inventory-transfer",
                                     expected_revision=initial["revision"])
        assert duplicate["duplicate"] is True
        assert reopened.world["systems"]["ontology"].neighbors(
            "old_brass_lantern", "held_by", 1,
        ) == ["inn_guest"]
        assert not reopened.provider.calls
    finally:
        reopened.store.close()


def test_item_authority_binds_recovery_mode_before_generation(tmp_path):
    provider = FakeLLMProvider(json_responses=[_inventory_transfer_candidate("protagonist")])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    base_revision = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("item-authority conflict must reject before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "整理背包",
            strategy=AuthorStrategy(), provider=provider, action_id="item-mode-bound-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_item_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "整理背包",
                strategy=MustNotRun(), provider=provider, action_id="item-mode-bound-action",
                expected_revision=base_revision, enforce_player_travel=False,
                enforce_player_item_authority=True,
            )
        assert engine.store.revision == base_revision + 1
    finally:
        engine.store.close()


def test_normal_gui_rejects_remote_dark_quest_surface_without_writes(tmp_path):
    candidate = _quest_candidate("surface", "remote_dark_tower")
    dashboard = _ordinary_demo_dashboard(tmp_path, candidate)
    dashboard.engine.store.append_many([
        kernel_event("lore_created", day=1, scene="s1", turn=0, summary="remote dark lore", deltas={
            "id": "remote_dark_tower", "complexity": "simple", "about": "REMOTE_QUEST_SENTINEL",
            "secret": "REMOTE_QUEST_SECRET", "anchor": "north_bank_old_tower",
            "l3_anchor": "north_bank_old_tower", "stages": [], "threshold": 50,
            "description": "REMOTE_QUEST_SENTINEL", "trigger": "远端触发",
        }),
    ], preflight=lambda history: project(dashboard.engine.registry, history))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="player quest change lies outside"):
            dashboard.action(player_input="原地观察", action_id="remote-quest-surface",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-quest-surface") is None
        assert dashboard.engine.world["systems"]["lore"]["lines"]["remote_dark_tower"]["state"] == "暗"
        assert "REMOTE_QUEST_SENTINEL" not in _prompt(dashboard.engine.provider)
    finally:
        dashboard.engine.store.close()


def test_normal_gui_final_payload_cannot_advance_remote_quest(tmp_path, monkeypatch):
    dashboard = _ordinary_demo_dashboard(tmp_path, _quest_candidate("open", "player_note", "整理客栈线索"))
    _add_local_active_quest(dashboard)
    dashboard.engine.store.append_many([
        kernel_event("quest_opened", day=1, scene="s1", turn=0, summary="remote quest", deltas={
            "id": "remote_tower_task", "summary": "远端旧塔任务", "anchor": "north_bank_old_tower",
        }),
    ], preflight=lambda history: project(dashboard.engine.registry, history))
    dashboard.engine.world = project(dashboard.engine.registry, dashboard.engine.store.iter_events())
    dashboard.engine.world["_revision"] = dashboard.engine.store.revision
    owner = dashboard.engine.registry.owner_of_section("quests")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("quest_advanced", day=day, scene=scene, turn=turn,
            summary="REMOTE_FINAL_QUEST_SENTINEL", deltas={
                "id": "remote_tower_task", "summary": "REMOTE_FINAL_QUEST_SENTINEL",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = dashboard.state()["revision"]
    try:
        with pytest.raises(TurnRejected, match="final publish violates the player quest authority boundary"):
            dashboard.action(player_input="记录新的本地线索", action_id="remote-final-quest",
                             expected_revision=before)
        assert dashboard.engine.store.revision == before
        assert dashboard.engine.store.get_action_receipt("remote-final-quest") is None
        assert dashboard.engine.world["systems"]["lore"]["lines"]["remote_tower_task"]["summary"] == "远端旧塔任务"
    finally:
        dashboard.engine.store.close()


def test_normal_gui_opens_and_advances_local_quest_with_recovery(tmp_path):
    campaign = tmp_path / "campaign"
    provider = _InputKeyedAuthorProvider({
        "我决定记下这条集市补给线索": _quest_candidate(
            "open", "player_market_note", "在客栈核对集市的补给线索",
        ),
        "把补给交到客栈柜台": _quest_candidate(
            "advance", "inn_delivery", "补给已经交到客栈柜台",
        ),
    })
    engine = build_engine(campaign, provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False, max_repairs=0)
    _add_local_active_quest(dashboard)
    opening = dashboard.state()
    try:
        opened = dashboard.action(player_input="我决定记下这条集市补给线索", action_id="open-public-quest",
                                  expected_revision=opening["revision"])
        assert opened["duplicate"] is False
        assert {entry["id"] for entry in opened["state"]["objectives"]["active_quests"]} >= {
            "inn_delivery", "player_market_note",
        }
        advanced = dashboard.action(player_input="把补给交到客栈柜台", action_id="advance-local-quest",
                                    expected_revision=opened["revision"])
        assert advanced["duplicate"] is False
        assert advanced["state"]["objectives"]["active_quests"] == [
            {"id": "inn_delivery", "summary": "补给已经交到客栈柜台"},
            {"id": "player_market_note", "summary": "在客栈核对集市的补给线索"},
        ]
        metadata = dashboard.engine.store.get_action_result_metadata("advance-local-quest")
        assert metadata["authority_modes"]["ordinary_player_quest_authority"] is True
        calls_after_first = len(provider.calls)
        duplicate = dashboard.action(player_input="把补给交到客栈柜台", action_id="advance-local-quest",
                                     expected_revision=opened["revision"])
        assert duplicate["duplicate"] is True
        assert len(provider.calls) == calls_after_first
    finally:
        dashboard.engine.store.close()

    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    dashboard = PlayerDashboard(reopened, strategy=AuthorStrategy(), demo=False, max_repairs=0)
    try:
        duplicate = dashboard.action(player_input="把补给交到客栈柜台", action_id="advance-local-quest",
                                     expected_revision=opened["revision"])
        assert duplicate["duplicate"] is True
        assert reopened.world["systems"]["lore"]["lines"]["inn_delivery"]["summary"] == "补给已经交到客栈柜台"
        assert not reopened.provider.calls
    finally:
        reopened.store.close()


def test_quest_authority_binds_recovery_mode_before_generation(tmp_path):
    provider = FakeLLMProvider(json_responses=[_quest_candidate("open", "mode_bound_quest", "模式绑定任务")])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    base_revision = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("quest-authority conflict must reject before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "记下任务",
            strategy=AuthorStrategy(), provider=provider, action_id="quest-mode-bound-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_quest_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "记下任务",
                strategy=MustNotRun(), provider=provider, action_id="quest-mode-bound-action",
                expected_revision=base_revision, enforce_player_travel=False,
                enforce_player_quest_authority=True,
            )
        assert engine.store.revision == base_revision + 1
    finally:
        engine.store.close()


def test_action_recovery_cannot_cross_into_world_observation_authority_mode(tmp_path):
    candidate = _public_observation_candidate("stonebridge_inn", "旧模式的本地观察。")
    provider = FakeLLMProvider(json_responses=[candidate])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    scene = _build_scene(engine)
    base_revision = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("world-authority mode conflict must reject before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "原地观察",
            strategy=AuthorStrategy(), provider=provider, action_id="world-mode-bound-action",
            expected_revision=base_revision, enforce_player_travel=True,
            enforce_player_knowledge_authority=True,
            enforce_player_world_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "原地观察",
                strategy=MustNotRun(), provider=provider, action_id="world-mode-bound-action",
                expected_revision=base_revision, enforce_player_travel=True,
                enforce_player_knowledge_authority=True,
                enforce_player_world_authority=True,
            )
        assert len(provider.calls) >= 1
    finally:
        engine.store.close()


@pytest.mark.parametrize("op", ["told", "endowment", "broadcast", "knows_fact"])
def test_normal_gui_rejects_npc_knowledge_aliases_without_receipt_before_repair(tmp_path, op):
    provider = FakeLLMProvider(json_responses=[_npc_knowledge_bypass(op)])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    before = engine.store.revision
    try:
        with pytest.raises(TurnRejected, match="knowledge lacks a valid host communication receipt"):
            dashboard.action(player_input="回集市告诉见证人坡口情况", action_id=f"npc-{op}",
                             expected_revision=before)
        assert engine.store.revision == before
        # Authority rejection is all-or-nothing: no knowledge-only repair can
        # leave narration claiming the NPC heard the player's words.
        assert len(provider.calls) == 1
    finally:
        engine.store.close()


def test_knowledge_authority_does_not_depend_on_travel_mode(tmp_path, monkeypatch):
    """Shared callers cannot accidentally disable this boundary with travel=False."""
    scene = {"protagonist": "protagonist", "present": [], "day": 1,
             "location": "stonebridge_inn", "id": "s1"}
    provider = FakeLLMProvider(json_responses=[_npc_knowledge_bypass("told")])
    engine = _engine_with_safe_arrival(tmp_path / "candidate", provider)
    try:
        before = engine.store.revision
        with pytest.raises(TurnRejected, match="knowledge lacks a valid host communication receipt"):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "告诉见证人坡口情况",
                strategy=AuthorStrategy(), provider=provider, action_id="knowledge-without-travel-candidate",
                expected_revision=before, enforce_player_travel=False,
                enforce_player_knowledge_authority=True,
            )
        assert engine.store.revision == before
    finally:
        engine.store.close()

    final_proposal = _move_proposal()
    final_proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[final_proposal])
    engine = _engine_with_safe_arrival(tmp_path / "final", provider)
    owner = engine.registry.owner_of_section("knowledge")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        events.append(kernel_event("knowledge_set", day=day, scene=scene, turn=turn,
            summary="forged npc knowledge", deltas={
                "knower": "market_witness", "fact_key": "slope.hazard", "value": "FORGED",
            }))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    try:
        before = engine.store.revision
        with pytest.raises(TurnRejected, match="player knowledge authority boundary"):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "去集市看看",
                strategy=AuthorStrategy(), provider=provider, action_id="knowledge-without-travel-final",
                expected_revision=before, enforce_player_travel=False,
                enforce_player_knowledge_authority=True,
            )
        assert engine.store.revision == before
    finally:
        engine.store.close()


def test_original_gui_transcript_laundering_candidate_is_rejected_without_writes(tmp_path):
    provider = FakeLLMProvider(json_responses=[_original_failure_candidate()])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    engine.store.append_many([
        kernel_event("character_created", day=1, scene="s1", turn=0, summary="public luoen", deltas={
            "id": "market_witness_luoen", "sketch": "公开集市见证人", "goal": "整理摊位", "tier": "tracked",
        }),
        kernel_event("entity_moved", day=1, scene="s1", turn=0, summary="luoen at market", deltas={
            "who": "market_witness_luoen", "to": "stonebridge_market",
        }),
    ], preflight=lambda history: project(engine.registry, history))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    before = engine.store.revision
    try:
        with pytest.raises(TurnRejected, match="knowledge lacks a valid host communication receipt"):
            dashboard.action(
                player_input="不踩裂开的干土，带着旧铜灯安全返回石桥集市，向洛恩说明坡口有裂缝和湿泥；只问他今早经过那里时是否见过明确可绕开的安全落脚点。若他不能确定，就不把那段路当作可通行。",
                action_id="original-transcript-laundering-candidate", expected_revision=before,
            )
        assert engine.store.revision == before
        assert len(provider.calls) == 1
    finally:
        engine.store.close()


def test_compare_preview_rejects_npc_knowledge_before_any_candidate_is_shown(tmp_path):
    provider = FakeLLMProvider(json_responses=[_npc_knowledge_bypass("told")])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    before = engine.store.revision
    try:
        with pytest.raises(TurnRejected, match="knowledge lacks a valid host communication receipt"):
            run_compare(
                engine.registry, engine.world, {"protagonist": "protagonist", "present": [],
                                                 "day": 1, "location": "stonebridge_inn", "id": "s1"},
                "告诉见证人坡口情况", provider=provider, required_sections=frozenset({"moves", "places", "cast", "facts", "clock"}),
                enforce_player_travel=True, enforce_player_knowledge_authority=True,
            )
        assert engine.store.revision == before
        assert len(provider.calls) == 1
    finally:
        engine.store.close()


def test_compare_preview_rejects_faction_change_before_any_candidate_is_shown(tmp_path):
    provider = FakeLLMProvider(json_responses=[
        _faction_member_candidate("tower_warden", "remote_secret_order"),
    ])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    before = engine.store.revision
    try:
        with pytest.raises(TurnRejected, match="player faction change lies outside"):
            run_compare(
                engine.registry, engine.world,
                {"protagonist": "protagonist", "present": [], "day": 1,
                 "location": "stonebridge_inn", "id": "s1"},
                "改写远方成员名册", provider=provider,
                required_sections=frozenset({"moves", "places", "cast", "facts", "clock"}),
                enforce_player_travel=True,
                enforce_player_faction_authority=True,
            )
        assert engine.store.revision == before
        # The first Author candidate is rejected before Hybrid is started.
        assert len(provider.calls) == 1
    finally:
        engine.store.close()


@pytest.mark.parametrize("kind", ["knowledge_set", "knowledge_broadcast", "knows_fact", "forged_receipt_shape"])
def test_final_payload_rejects_materialized_npc_knowledge_bypasses(tmp_path, monkeypatch, kind):
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    owner = engine.registry.owner_of_section("knowledge")
    original = owner.to_events

    def tampered(section, decl, *, turn, day, scene):
        events = original(section, decl, turn=turn, day=day, scene=scene)
        if kind == "knowledge_set":
            events.append(kernel_event("knowledge_set", day=day, scene=scene, turn=turn,
                summary="forged npc knowledge", deltas={"knower": "market_witness", "fact_key": "market.notice", "value": "FORGED"}))
        elif kind == "knowledge_broadcast":
            events.append(kernel_event("knowledge_broadcast", day=day, scene=scene, turn=turn,
                summary="forged broadcast", deltas={"fact_key": "market.notice", "value": "FORGED", "audience": {"place": "stonebridge_market"}}))
        elif kind == "forged_receipt_shape":
            events.append(kernel_event("knowledge_set", day=day, scene=scene, turn=turn,
                summary="forged player speech receipt", deltas={
                    "knower": "market_witness", "fact_key": "protagonist.reported_player_statement:forged",
                    "value": "FORGED", "via": "protagonist", "source_kind": "player_statement",
                }))
        else:
            events.append(kernel_event("fact_asserted", day=day, scene=scene, turn=turn,
                summary="forged knows fact", deltas={"subject": "market_witness", "predicate": "knows:market.notice", "value": "FORGED"}))
        return events

    monkeypatch.setattr(owner, "to_events", tampered)
    before = engine.store.revision
    try:
        boundary = ("player-statement receipt boundary" if kind == "forged_receipt_shape"
                    else "player knowledge authority boundary")
        with pytest.raises(TurnRejected, match=boundary):
            dashboard.action(player_input="去集市看看", action_id=f"final-{kind}", expected_revision=before)
        assert engine.store.revision == before
    finally:
        engine.store.close()


def test_normal_author_keeps_protagonist_told_and_action_recovery_compatible(tmp_path):
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    dashboard = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False)
    initial = dashboard.state()
    try:
        first = dashboard.action(player_input="去集市看看", action_id="protagonist-told-recovery",
                                 expected_revision=initial["revision"])
        calls_after_first = len(provider.calls)
        duplicate = dashboard.action(player_input="去集市看看", action_id="protagonist-told-recovery",
                                     expected_revision=initial["revision"])
        assert first["duplicate"] is False and duplicate["duplicate"] is True
        assert engine.world["systems"]["ontology"].value_at(
            "protagonist", "knows:market.notice", 1,
        ) == "主角从公开告示得知集市仍开放"
        # The first turn may run deterministic background hooks; recovery must
        # not ask the author/provider to generate the same action again.
        assert calls_after_first >= 1
        assert len(provider.calls) == calls_after_first
    finally:
        engine.store.close()


def test_action_recovery_cannot_cross_into_ordinary_knowledge_authority_mode(tmp_path):
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    scene = {"protagonist": "protagonist", "present": [], "day": 1,
             "location": "stonebridge_inn", "id": "s1"}
    before = engine.store.revision

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("mismatched authorization mode must fail before generation")

    try:
        run_turn(
            engine.registry, engine.store, engine.world, scene, "查看告示",
            strategy=AuthorStrategy(), provider=provider, action_id="mode-bound-action",
            expected_revision=before, enforce_player_travel=True,
            enforce_player_knowledge_authority=False,
        )
        with pytest.raises(RevisionConflict):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "查看告示",
                strategy=MustNotRun(), provider=provider, action_id="mode-bound-action",
                expected_revision=before, enforce_player_travel=True,
                enforce_player_knowledge_authority=True,
            )
        assert len(provider.calls) >= 1
    finally:
        engine.store.close()


def test_pre_mode_safe_action_recovers_read_only_under_ordinary_authority(tmp_path):
    proposal = _move_proposal()
    proposal["knowledge"] = [{
        "op": "told", "knower": "protagonist", "fact_key": "market.notice",
        "value": "主角从公开告示得知集市仍开放", "via": "公开告示",
    }]
    provider = FakeLLMProvider(json_responses=[proposal])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    scene = {"protagonist": "protagonist", "present": [], "day": 1,
             "location": "stonebridge_inn", "id": "s1"}

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("verified legacy recovery must not generate")

    try:
        base_revision = engine.store.revision
        first = run_turn(
            engine.registry, engine.store, engine.world, scene, "查看告示",
            strategy=AuthorStrategy(), provider=provider, action_id="pre-mode-safe-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_knowledge_authority=False,
        )
        row = engine.store._conn.execute(
            "SELECT metadata FROM action_result_metadata WHERE action_id=?", ("pre-mode-safe-action",),
        ).fetchone()
        metadata = json.loads(row["metadata"])
        metadata.pop("authority_modes")  # faithfully model a pre-mode sidecar
        engine.store._conn.execute(
            "UPDATE action_result_metadata SET metadata=? WHERE action_id=?",
            (json.dumps(metadata, sort_keys=True, ensure_ascii=False), "pre-mode-safe-action"),
        )
        engine.store._conn.commit()
        before = (engine.store.revision, engine.store.jsonl_path.read_bytes(),
                  engine.store.get_action_result_metadata("pre-mode-safe-action"))
        duplicate = run_turn(
            engine.registry, engine.store, engine.world, scene, "查看告示",
            strategy=MustNotRun(), provider=FakeLLMProvider(), action_id="pre-mode-safe-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_knowledge_authority=True,
        )
        assert duplicate.receipt["duplicate"] is True and duplicate.events == first.events
        assert (engine.store.revision, engine.store.jsonl_path.read_bytes(),
                engine.store.get_action_result_metadata("pre-mode-safe-action")) == before
    finally:
        engine.store.close()


def test_pre_mode_laundered_npc_knowledge_remains_a_zero_write_conflict(tmp_path):
    provider = FakeLLMProvider(json_responses=[_npc_knowledge_bypass("told")])
    engine = _engine_with_safe_arrival(tmp_path, provider)
    scene = {"protagonist": "protagonist", "present": [], "day": 1,
             "location": "stonebridge_inn", "id": "s1"}

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("unsafe legacy result must not generate")

    try:
        base_revision = engine.store.revision
        run_turn(
            engine.registry, engine.store, engine.world, scene, "告诉见证人坡口情况",
            strategy=AuthorStrategy(), provider=provider, action_id="pre-mode-laundered-action",
            expected_revision=base_revision, enforce_player_travel=False,
            enforce_player_knowledge_authority=False,
        )
        row = engine.store._conn.execute(
            "SELECT metadata FROM action_result_metadata WHERE action_id=?", ("pre-mode-laundered-action",),
        ).fetchone()
        metadata = json.loads(row["metadata"])
        metadata.pop("authority_modes")
        engine.store._conn.execute(
            "UPDATE action_result_metadata SET metadata=? WHERE action_id=?",
            (json.dumps(metadata, sort_keys=True, ensure_ascii=False), "pre-mode-laundered-action"),
        )
        engine.store._conn.commit()
        before = (engine.store.revision, engine.store.jsonl_path.read_bytes(),
                  engine.store.get_action_result_metadata("pre-mode-laundered-action"))
        with pytest.raises(RevisionConflict, match="cannot be safely recovered"):
            run_turn(
                engine.registry, engine.store, engine.world, scene, "告诉见证人坡口情况",
                strategy=MustNotRun(), provider=FakeLLMProvider(), action_id="pre-mode-laundered-action",
                expected_revision=base_revision, enforce_player_travel=False,
                enforce_player_knowledge_authority=True,
            )
        assert (engine.store.revision, engine.store.jsonl_path.read_bytes(),
                engine.store.get_action_result_metadata("pre-mode-laundered-action")) == before
    finally:
        engine.store.close()
