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
from loop.strategy import AuthorStrategy
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
        assert "market_witness" in prompt and PUBLIC_WORDS in prompt
        assert "若本回合 moves 同时使 protagonist 合法抵达 stonebridge_market" in prompt
        assert "自动新建 heard_player" in prompt
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
        assert "【条件化抵达材料】" in prompt and PUBLIC_WORDS in prompt
        assert PRIVATE_SENTINEL not in prompt and "hidden_arrival_person" not in prompt
        metadata = engine.store.get_action_result_metadata("normal-safe-context")
        assert "_trusted_authoring_events" not in json.dumps(metadata, ensure_ascii=False)
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
        assert all("【条件化抵达材料】" in prompt and PUBLIC_WORDS in prompt for prompt in prompts[:2])
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
        assert "自动新建 heard_player" in _prompt(provider)
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


def _ordinary_demo_dashboard(tmp_path, candidate: dict) -> PlayerDashboard:
    provider = FakeLLMProvider(json_responses=[candidate])
    engine = build_engine(tmp_path / "campaign", provider=provider, embedder=None)
    _seed_demo_campaign(engine)
    return PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False, max_repairs=0)


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
            "place": "stonebridge_inn", "level": 1, "summary": "脚边石阶有浅浅湿痕。",
            "source": "author_world", "visibility": "public", "public_observation": True,
            "observation_kind": "single_observation", "observation_range": "主角脚边两步内",
        }
        metadata = dashboard.engine.store.get_action_result_metadata("local-world-observation")
        assert metadata["authority_modes"] == {
            "ordinary_player_knowledge_authority": True,
            "ordinary_player_world_authority": True,
            "ordinary_player_fact_authority": True,
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
