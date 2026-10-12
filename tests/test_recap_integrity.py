"""RCV-04 — recap is a replayable public narrative aid, never fact authority.

These tests exercise the real ``run_turn`` → ``digest_fleet`` path.  The
foreground strategy is deterministic, but recap providers and persistence are
the production implementations: no synthetic narrative events are injected.
"""
from __future__ import annotations

import copy
import json
import logging

import pytest

from app.__main__ import _print_resume_recap
from app.engine import build_engine
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from loop.turn import REQUIRED_SECTIONS, run_turn
from systems.interpretive_memory import (
    OfflineInterpretationGenerator,
    public_event_history,
)


PRIVATE_SENTINEL = "PRIVATE_INTERPRETATION_MUST_NOT_REACH_RECAP"


class PreparedStrategy:
    """A foreground-only strategy so recap is the only provider call here."""

    def __init__(self, narration: str, coins: int):
        self.narration = narration
        self.coins = coins

    def produce(self, *args, **kwargs):
        return TurnCommit(self.narration, {
            "moves": [], "places": [], "cast": [],
            "facts": [{"subject": "hero", "predicate": "coins",
                       "value": self.coins, "secrecy": "public"}],
            "clock": [{"advance": False, "days": 0, "bands": 0,
                       "reason": "片刻"}],
        })


class RaisingRecapProvider:
    """Offline probe that fails only when the real summary call reaches it."""

    def __init__(self):
        self.calls = []

    def complete_messages(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        raise RuntimeError("PRIVATE_RECAP_EXCEPTION_BODY")


class FixedRecapProvider:
    def __init__(self, summary: str):
        self.summary = summary
        self.calls = []

    def complete_messages(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return json.dumps({"summary": self.summary}, ensure_ascii=False)


def _disable_unrelated_backstage(monkeypatch):
    """Leave digest_fleet real; isolate its recap boundary from other hooks."""
    import loop.turn as turns
    for name in ("run_director", "run_cascade", "run_catchup", "run_lore",
                 "run_density", "_run_demote_on_leave"):
        monkeypatch.setattr(turns, name, lambda *args, **kwargs: [])


def _seed_engine(tmp_path):
    engine = build_engine(tmp_path / "campaign", enable_interpretive_memory=True)
    engine.store.append_many([
        kernel_event("entity_created", day=1, scene="seed", summary="hero",
                     deltas={"id": "hero", "etype": "Person", "tier": "tracked"}, turn=0),
        kernel_event("entity_created", day=1, scene="seed", summary="inn",
                     deltas={"id": "inn", "etype": "Place", "tier": "tracked",
                             "attrs": {"level": 3, "kind": "venue", "seed": "inn"}}, turn=0),
        kernel_event("relation_added", day=1, scene="seed", summary="hero at inn",
                     deltas={"src": "hero", "rel": "located_in", "dst": "inn"}, turn=0),
        kernel_event("fact_asserted", day=1, scene="seed", summary="coins",
                     deltas={"subject": "hero", "predicate": "coins", "value": 10,
                             "secrecy": "public"}, turn=0),
    ])
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision
    return engine


def _scene(scene_id):
    return {"id": scene_id, "protagonist": "hero", "present": [],
            "day": 1, "location": "inn"}


def _play(engine, scene_id, narration, coins, provider, action_id):
    result = run_turn(
        engine.registry, engine.store, engine.world, _scene(scene_id), "继续前进",
        strategy=PreparedStrategy(narration, coins), provider=provider,
        required_sections=REQUIRED_SECTIONS, action_id=action_id,
        memory_role="hero", memory_generator=OfflineInterpretationGenerator(PRIVATE_SENTINEL),
    )
    engine.world = result.world
    return result


def _private_texts(world):
    return [item["text"] for item in
            world["systems"]["interpretive_memory"]["by_actor"].get("hero", [])]


def _public_resume(engine):
    lines = []
    _print_resume_recap(engine, lines.append)
    return "\n".join(lines)


def test_summary_failure_rolls_back_only_summary_and_replay_stays_consistent(tmp_path, monkeypatch, caplog):
    _disable_unrelated_backstage(monkeypatch)
    caplog.set_level(logging.ERROR)
    engine = _seed_engine(tmp_path)
    try:
        # Three distinct scenes age s1 out.  The first two actions establish a
        # nonempty fact state and real private records before the failing recap.
        _play(engine, "s1", "PUBLIC_S1_NARRATION", 11, RaisingRecapProvider(), "recap-fail-1")
        _play(engine, "s2", "PUBLIC_S2_NARRATION", 12, RaisingRecapProvider(), "recap-fail-2")
        pre_action_world = engine.world
        before_events = list(engine.store.iter_events(include_retracted=True))
        before_revision = engine.store.revision
        failing = RaisingRecapProvider()

        result = _play(engine, "s3", "PUBLIC_S3_FOREGROUND_SURVIVES", 13,
                       failing, "recap-fail-3")

        assert failing.calls, "the real summary provider boundary must have been reached"
        assert engine.store.revision == before_revision + 1
        history = list(engine.store.iter_events(include_retracted=True))
        assert history[:len(before_events)] == before_events
        assert not [event for event in result.events if event["type"] == "scene_summarized"]
        assert engine.world["systems"]["ontology"].value_at("hero", "coins", 1) == 13
        assert _private_texts(engine.world) == [PRIVATE_SENTINEL] * 3

        receipt = engine.store.get_action_receipt("recap-fail-3")
        metadata = engine.store.get_action_result_metadata("recap-fail-3")
        assert receipt["revision"] == result.receipt["revision"] == engine.store.revision
        assert metadata["kind"] == "turn_result"
        assert metadata["result"]["events"] == result.events
        assert all(event["turn"] == result.events[0]["turn"] for event in result.events)

        rendered = _public_resume(engine)
        assert "PUBLIC_S3_FOREGROUND_SURVIVES" in rendered
        assert PRIVATE_SENTINEL not in rendered
        assert all(PRIVATE_SENTINEL not in str(event) for event in public_event_history(history))
        assert caplog.records
        assert "PRIVATE_RECAP_EXCEPTION_BODY" not in caplog.text

        class MustNotGenerate:
            def produce(self, *args, **kwargs):
                pytest.fail("completed pending recap action retried generation")

        recovered = run_turn(
            engine.registry, engine.store, pre_action_world, _scene("s3"), "继续前进",
            strategy=MustNotGenerate(), provider=FixedRecapProvider("must not run"),
            required_sections=REQUIRED_SECTIONS, action_id="recap-fail-3",
            memory_role="hero", memory_generator=OfflineInterpretationGenerator(PRIVATE_SENTINEL),
        )
        assert recovered.receipt["duplicate"]
        assert recovered.recap_status == result.recap_status == "pending"

        db_path, jsonl_path = engine.store.db_path, engine.store.jsonl_path
        engine.store.close()
        reopened = build_engine(tmp_path / "campaign", enable_interpretive_memory=True)
        try:
            assert reopened.store.db_path == db_path and reopened.store.jsonl_path == jsonl_path
            assert reopened.store.revision == receipt["revision"]
            assert reopened.world["systems"]["ontology"].value_at("hero", "coins", 1) == 13
            assert _private_texts(reopened.world) == [PRIVATE_SENTINEL] * 3
            assert not any(event["type"] == "scene_summarized"
                           for event in reopened.store.iter_events())
            assert PRIVATE_SENTINEL not in _public_resume(reopened)
        finally:
            reopened.store.close()
    except Exception:
        if engine.store._conn:
            engine.store.close()
        raise


def test_reflection_failure_before_recap_does_not_claim_recap_pending(tmp_path, monkeypatch, caplog):
    """Only digest's actual recap attempt may report ``pending``."""
    _disable_unrelated_backstage(monkeypatch)
    caplog.set_level(logging.ERROR)
    engine = _seed_engine(tmp_path)
    try:
        # Seed an aged candidate without forcing reflection on its first two
        # foreground actions.  The third action's reflection now fails before
        # digest reaches Phase 3 recap maintenance.
        _play(engine, "s1", "PUBLIC_S1", 11, FixedRecapProvider("unused"), "reflect-1")
        _play(engine, "s2", "PUBLIC_S2", 12, FixedRecapProvider("unused"), "reflect-2")
        monkeypatch.setattr("loop.fleet.importance_mod.score", lambda *args, **kwargs: 100)
        reflection_calls = []

        def broken_reflection(*args, **kwargs):
            reflection_calls.append(1)
            raise RuntimeError("PRIVATE_REFLECTION_EXCEPTION_BODY")

        monkeypatch.setattr("loop.fleet.reflection_mod.reflect", broken_reflection)
        recap = RaisingRecapProvider()
        result = _play(engine, "s3", "PUBLIC_S3", 13, recap, "reflect-3")

        assert reflection_calls
        assert recap.calls == []
        assert result.recap_status == "not_due"
        assert not any(event["type"] == "scene_summarized" for event in result.events)
        assert "PRIVATE_REFLECTION_EXCEPTION_BODY" not in caplog.text
    finally:
        engine.store.close()


def test_summary_is_public_replayable_narrative_not_fact_authority(tmp_path, monkeypatch):
    _disable_unrelated_backstage(monkeypatch)
    engine = _seed_engine(tmp_path)
    try:
        # The deliberately false number proves a summary is not applied as a
        # fact.  It is retained only as public narrative recap text.
        _play(engine, "s1", "PUBLIC_S1_FOR_SUMMARY", 11, FixedRecapProvider("unused"), "recap-ok-1")
        _play(engine, "s2", "PUBLIC_S2_FOR_SUMMARY", 12, FixedRecapProvider("unused"), "recap-ok-2")
        provider = FixedRecapProvider("SUMMARY_FALSE_CLAIM_COINS_999")
        pre_action_world = engine.world
        result = _play(engine, "s3", "PUBLIC_S3_CURRENT", 13, provider, "recap-ok-3")

        summaries = [event for event in result.events if event["type"] == "scene_summarized"]
        assert len(summaries) == 1
        assert summaries[0]["deltas"] == {
            "scene": "s1", "summary": "SUMMARY_FALSE_CLAIM_COINS_999"}
        assert provider.calls
        recap_prompt = provider.calls[0]
        rendered_prompt = "\n".join(message.get("content", "") for message in recap_prompt)
        assert "PUBLIC_S1_FOR_SUMMARY" in rendered_prompt
        assert PRIVATE_SENTINEL not in rendered_prompt

        # Authoritative projection comes from fact_asserted, not scene_summarized.
        assert engine.world["systems"]["ontology"].value_at("hero", "coins", 1) == 13
        narrative = engine.world["systems"]["narrative"]
        assert narrative["scenes"][0]["summary"] == "SUMMARY_FALSE_CLAIM_COINS_999"
        assert _private_texts(engine.world) == [PRIVATE_SENTINEL] * 3

        receipt = engine.store.get_action_receipt("recap-ok-3")
        assert receipt["revision"] == result.receipt["revision"] == engine.store.revision
        assert engine.store.get_action_result_metadata("recap-ok-3")["kind"] == "turn_result"
        rendered = _public_resume(engine)
        assert "SUMMARY_FALSE_CLAIM_COINS_999" in rendered
        assert PRIVATE_SENTINEL not in rendered
        assert all(PRIVATE_SENTINEL not in str(event)
                   for event in public_event_history(engine.store.iter_events(include_retracted=True)))

        class MustNotGenerate:
            def produce(self, *args, **kwargs):
                pytest.fail("completed action retried generation instead of restoring status")

        recovered = run_turn(
            engine.registry, engine.store, pre_action_world, _scene("s3"), "继续前进",
            strategy=MustNotGenerate(), provider=FixedRecapProvider("must not run"),
            required_sections=REQUIRED_SECTIONS, action_id="recap-ok-3",
            memory_role="hero", memory_generator=OfflineInterpretationGenerator(PRIVATE_SENTINEL),
        )
        assert recovered.receipt["duplicate"]
        assert recovered.recap_status == result.recap_status == "saved"

        engine.store.close()
        reopened = build_engine(tmp_path / "campaign", enable_interpretive_memory=True)
        try:
            assert reopened.store.revision == receipt["revision"]
            assert reopened.world["systems"]["ontology"].value_at("hero", "coins", 1) == 13
            assert (reopened.world["systems"]["narrative"]["scenes"][0]["summary"]
                    == "SUMMARY_FALSE_CLAIM_COINS_999")
            assert _private_texts(reopened.world) == [PRIVATE_SENTINEL] * 3
            assert "SUMMARY_FALSE_CLAIM_COINS_999" in _public_resume(reopened)
            assert PRIVATE_SENTINEL not in _public_resume(reopened)
        finally:
            reopened.store.close()
    except Exception:
        if engine.store._conn:
            engine.store.close()
        raise
