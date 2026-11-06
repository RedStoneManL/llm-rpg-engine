"""RCV-04 — recap is a replayable public narrative aid, never fact authority.

Foreground integrity cases exercise the real ``run_turn`` → ``digest_fleet``
path with deterministic providers and production persistence. Backlog scheduler
cases explicitly seed synthetic narration and bound summaries to isolate retries.
"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import logging

import pytest

from app.__main__ import _print_resume_recap
from app.engine import build_engine
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from loop.turn import REQUIRED_SECTIONS, run_turn
from context.assembler import assemble_context
from app.engine import rewind
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


def _seed_summary_backlog(engine, *, summaries=7, raw=2, day=1):
    """Persist bound summaries, with no fabricated index-only coverage."""
    from systems.narrative import summary_source_ref
    for i in range(summaries + raw):
        scene = f'backlog-{i}'
        engine.store.append(kernel_event(
            'narration_recorded', day=day, scene=scene, turn=i + 1,
            summary='public source', deltas={'scene': scene, 'text': f'PUBLIC_BACKLOG_RAW_{i}'}))
        if i < summaries:
            ns = project(engine.registry, engine.store.iter_events())['systems']['narrative']
            engine.store.append(kernel_event(
                'scene_summarized', day=day, scene=scene, turn=i + 1,
                summary='bound source', deltas={'scene': scene, 'summary': f'PUBLIC_BACKLOG_SUMMARY_{i}',
                                              'source': summary_source_ref(ns['scenes'][-1])}))
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world['_revision'] = engine.store.revision


def test_recompress_existing_backlog_retries_after_reopen_with_bounded_work(tmp_path):
    from loop.fleet import digest_fleet
    engine = _seed_engine(tmp_path)
    try:
        _seed_summary_backlog(engine, summaries=13, day=4)
        revision, mirror = engine.store.revision, engine.store.jsonl_path.read_bytes()
        failing = RaisingRecapProvider()
        result = digest_fleet(engine.registry, engine.store, [], engine.world,
                              provider=failing, recap_provider=failing)
        assert result.recap_status == 'pending'
        assert len(failing.calls) == 1
        assert result == []
        assert engine.store.revision == revision
        assert engine.store.jsonl_path.read_bytes() == mirror
        engine.store.close()
        engine = build_engine(tmp_path / 'campaign', enable_interpretive_memory=True)
        provider = FixedRecapProvider('PUBLIC_RETRY_OVERVIEW')
        # Each invocation does only one recap job, even with two batches waiting.
        for frontier in (6, 12):
            calls = len(provider.calls)
            result = digest_fleet(engine.registry, engine.store, [], engine.world,
                                  provider=provider, recap_provider=provider)
            assert result.recap_status == 'saved'
            assert [e['type'] for e in result] == ['recap_recompressed']
            assert len(provider.calls) == calls + 1
            assert result[0]['day'] == 4
            assert result[0]['scene'] == 'backlog-14'
            assert result[0]['deltas']['summarized_through_index'] == frontier
            engine.world = project(engine.registry, engine.store.iter_events())
        revision, mirror, calls = engine.store.revision, engine.store.jsonl_path.read_bytes(), len(provider.calls)
        result = digest_fleet(engine.registry, engine.store, [], engine.world,
                              provider=provider, recap_provider=provider)
        assert result == [] and result.recap_status == 'not_due'
        assert len(provider.calls) == calls
        assert engine.store.revision == revision
        assert engine.store.jsonl_path.read_bytes() == mirror
    finally:
        engine.store.close()


@pytest.mark.parametrize('failed_job', ['scene', 'recap'])
def test_independent_recap_jobs_keep_partial_success_and_pending_status(tmp_path, monkeypatch, caplog, failed_job):
    _disable_unrelated_backstage(monkeypatch)
    caplog.set_level(logging.ERROR)
    engine = _seed_engine(tmp_path)

    class PartialProvider(FixedRecapProvider):
        def complete_messages(self, messages, **kwargs):
            self.calls.append(copy.deepcopy(messages))
            job = 'recap' if '多场景' in messages[0]['content'] else 'scene'
            if job == failed_job:
                raise RuntimeError('PRIVATE_MAINTENANCE_EXCEPTION')
            return json.dumps({'summary': f'PUBLIC_{job}_SUCCESS'})

    try:
        _seed_summary_backlog(engine, raw=3)
        provider = PartialProvider('unused')
        pre_action_world = engine.world
        result = _play(engine, 'backlog-9', 'PUBLIC_NEXT_ACTION', 20, provider, 'partial-job')
        assert len(provider.calls) == 2
        assert result.recap_status == 'pending'
        types = [e['type'] for e in result.events]
        assert ('scene_summarized' in types) == (failed_job != 'scene')
        assert ('recap_recompressed' in types) == (failed_job != 'recap')
        assert engine.world['systems']['ontology'].value_at('hero', 'coins', 1) == 20
        ns = engine.world['systems']['narrative']
        assert (ns['scenes'][7]['summary'] is None) == (failed_job == 'scene')
        assert ns['summarized_through_index'] == (0 if failed_job == 'recap' else 6)
        context = assemble_context(engine.registry, engine.world, _scene('backlog-9'))
        if failed_job == 'scene':
            assert 'PUBLIC_BACKLOG_RAW_7' in context
        assert PRIVATE_SENTINEL not in str(provider.calls)
        assert 'PRIVATE_MAINTENANCE_EXCEPTION' not in caplog.text
        expected = copy.deepcopy(ns)
        revision, mirror = engine.store.revision, engine.store.jsonl_path.read_bytes()
        engine.store.close()
        engine = build_engine(tmp_path / 'campaign', enable_interpretive_memory=True)
        assert engine.world['systems']['narrative'] == expected
        guard = RaisingRecapProvider()
        duplicate = run_turn(
            engine.registry, engine.store, pre_action_world, _scene('backlog-9'), '继续前进',
            strategy=PreparedStrategy('PUBLIC_NEXT_ACTION', 20), provider=guard,
            required_sections=REQUIRED_SECTIONS, action_id='partial-job',
            memory_role='hero', memory_generator=OfflineInterpretationGenerator(PRIVATE_SENTINEL))
        assert duplicate.receipt['duplicate'] and duplicate.recap_status == 'pending'
        assert guard.calls == []
        assert engine.store.revision == revision
        assert engine.store.jsonl_path.read_bytes() == mirror
    finally:
        engine.store.close()


def test_malformed_jobs_have_separate_bounded_repair_budgets(tmp_path, monkeypatch):
    _disable_unrelated_backstage(monkeypatch)
    engine = _seed_engine(tmp_path)

    class MalformedProvider(FixedRecapProvider):
        def complete_messages(self, messages, **kwargs):
            self.calls.append(copy.deepcopy(messages))
            return '{"wrong": "no summary"}'

    try:
        _seed_summary_backlog(engine, raw=3)
        provider = MalformedProvider('unused')
        result = _play(engine, 'backlog-9', 'PUBLIC_NEXT_ACTION', 20, provider, 'bad-jobs')
        assert len(provider.calls) == 4  # two jobs, one repair each; no drain loop
        assert [len(m) for m in provider.calls] == [2, 4, 2, 4]
        assert result.recap_status == 'pending'
        assert not any(e['type'] in {'scene_summarized', 'recap_recompressed'} for e in result.events)
        assert engine.store.get_action_receipt('bad-jobs') is not None
    finally:
        engine.store.close()


@pytest.mark.parametrize('cancel_job', ['scene', 'recap'])
def test_cancellation_in_either_independent_job_aborts_entire_turn(tmp_path, monkeypatch, cancel_job):
    _disable_unrelated_backstage(monkeypatch)
    engine = _seed_engine(tmp_path)

    class CancellingProvider(FixedRecapProvider):
        def complete_messages(self, messages, **kwargs):
            self.calls.append(copy.deepcopy(messages))
            job = 'recap' if '多场景' in messages[0]['content'] else 'scene'
            if job == cancel_job:
                raise asyncio.CancelledError()
            return '{"summary": "PUBLIC_SCENE_SAVED_BEFORE_CANCEL"}'

    try:
        _seed_summary_backlog(engine, raw=3)
        before = list(engine.store.iter_events(include_retracted=True))
        revision, mirror = engine.store.revision, engine.store.jsonl_path.read_bytes()
        with pytest.raises(asyncio.CancelledError):
            _play(engine, 'backlog-9', 'PUBLIC_CANCELLED_ACTION', 20,
                  CancellingProvider('unused'), 'cancel-jobs')
        assert list(engine.store.iter_events(include_retracted=True)) == before
        assert engine.store.revision == revision
        assert engine.store.jsonl_path.read_bytes() == mirror
        assert engine.store.get_action_receipt('cancel-jobs') is None
        engine.store.close()
        engine = build_engine(tmp_path / 'campaign', enable_interpretive_memory=True)
        assert list(engine.store.iter_events(include_retracted=True)) == before
    finally:
        engine.store.close()


def test_invalid_recap_staging_does_not_discard_saved_scene_summary(tmp_path, monkeypatch):
    import loop.fleet as fleet
    _disable_unrelated_backstage(monkeypatch)
    engine = _seed_engine(tmp_path)
    original = fleet.kernel_event

    def corrupt_recap(*args, **kwargs):
        event = original(*args, **kwargs)
        if event['type'] == 'recap_recompressed':
            event['deltas']['source']['previous'] = {'event_id': 'nonexistent'}
        return event

    try:
        _seed_summary_backlog(engine, raw=3)
        monkeypatch.setattr(fleet, 'kernel_event', corrupt_recap)
        result = _play(engine, 'backlog-9', 'PUBLIC_VALID_ACTION', 20,
                       FixedRecapProvider('PUBLIC_VALID_SUMMARY'), 'bad-recap-staging')
        assert result.recap_status == 'pending'
        assert any(e['type'] == 'scene_summarized' for e in result.events)
        assert not any(e['type'] == 'recap_recompressed' for e in result.events)
        assert engine.world['systems']['narrative']['scenes'][7]['summary'] == 'PUBLIC_VALID_SUMMARY'
        assert engine.world['systems']['narrative']['summarized_through_index'] == 0
    finally:
        engine.store.close()


def test_recompression_does_not_skip_unsummarized_source_gap(tmp_path):
    """Mixed/legacy histories can contain a hole before saved summaries."""
    from loop.fleet import digest_fleet
    engine = _seed_engine(tmp_path)
    try:
        for i in range(11):
            engine.store.append(kernel_event(
                'narration_recorded', day=1, scene=f's{i}', summary='public', turn=i + 1,
                deltas={'scene': f's{i}', 'text': f'ORIGINAL_{i}'}))
            if 2 <= i <= 8:
                engine.store.append(kernel_event(
                    'scene_summarized', day=1, scene=f's{i}', summary='legacy', turn=i + 1,
                    deltas={'scene': f's{i}', 'summary': f'SUMMARY_{i}'}))
        engine.world = project(engine.registry, engine.store.iter_events())
        provider = FixedRecapProvider('SUMMARY_OF_FIRST')
        result = digest_fleet(engine.registry, engine.store, [], engine.world,
                              provider=provider, recap_provider=provider)
        assert [ev['type'] for ev in result] == ['scene_summarized']
        assert len(provider.calls) == 1
        world = project(engine.registry, engine.store.iter_events())
        ns = world['systems']['narrative']
        assert ns['summarized_through_index'] == 0
        assert ns['scenes'][1]['summary'] is None
        assert 'ORIGINAL_1' in assemble_context(engine.registry, world, _scene('s10'))
    finally:
        engine.store.close()


@pytest.mark.parametrize("same_scene", [False, True])
def test_failed_recap_keeps_unsummarized_history_until_saved_and_replays(tmp_path, monkeypatch, same_scene):
    """Actual turns retain promises across failed compression and branch rewind."""
    _disable_unrelated_backstage(monkeypatch)
    engine = _seed_engine(tmp_path)
    count = 14 if same_scene else 4
    try:
        failing = RaisingRecapProvider()
        for i in range(count):
            _play(engine, "room" if same_scene else f"s{i}",
                  f"PUBLIC_PROMISE_{i:03d}_END", 10 + i, failing, f"promise-{i}")
        assert failing.calls
        context = assemble_context(engine.registry, engine.world, _scene("room"))
        for i in range(count):
            assert context.count(f"PUBLIC_PROMISE_{i:03d}_END") == 1
        assert PRIVATE_SENTINEL not in context
        before_revision = engine.store.revision
        before_jsonl = engine.store.jsonl_path.read_bytes()
        engine.store.close()
        engine = build_engine(tmp_path / "campaign", enable_interpretive_memory=True)
        assert assemble_context(engine.registry, engine.world, _scene("room")) == context
        assert engine.store.revision == before_revision
        assert engine.store.jsonl_path.read_bytes() == before_jsonl

        result = _play(engine, "room" if same_scene else "recovery", "PUBLIC_RECOVERY_TURN", 40,
                       FixedRecapProvider("PUBLIC_SAVED_PROMISE_SUMMARY"), "promise-recovery")
        summary = next(ev for ev in result.events if ev['type'] == 'scene_summarized')
        source = summary['deltas']['source']
        history_by_id = {ev['id']: ev for ev in engine.store.iter_events()}
        originals = [history_by_id[eid] for eid in source['event_ids']]
        assert source['version'] == 1 and source['bucket_id'] == originals[0]['id']
        assert len(originals) == (6 if same_scene else 1)
        assert all(ev['type'] == 'narration_recorded' for ev in originals)
        assert originals[0]['deltas']['text'] == 'PUBLIC_PROMISE_000_END'
        pairs = [[ev['id'], ev['deltas']['text']] for ev in originals]
        assert source['sha256'] == hashlib.sha256(json.dumps(
            pairs, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        recovered = assemble_context(engine.registry, engine.world, _scene("room"))
        assert "PUBLIC_SAVED_PROMISE_SUMMARY" in recovered
        assert "PUBLIC_PROMISE_000_END" not in recovered
        assert f"PUBLIC_PROMISE_{count-1:03d}_END" in recovered
        assert PRIVATE_SENTINEL not in recovered

        # Verify the durable source, not just the in-memory batch. Invalid new
        # provenance must fail before any event/revision/receipt/mirror write.
        engine.store.close()
        engine = build_engine(tmp_path / "campaign", enable_interpretive_memory=True)
        assert engine.world['systems']['narrative']['scenes'][0]['summary_source'] == source
        assert assemble_context(engine.registry, engine.world, _scene('room')) == recovered
        before = list(engine.store.iter_events(include_retracted=True))
        revision, mirror = engine.store.revision, engine.store.jsonl_path.read_bytes()
        bad = copy.deepcopy(summary)
        bad['id'] = 'ev_bad_summary_source'
        bad['deltas']['source']['bucket_id'] = 'ev_missing'
        with pytest.raises(ValueError, match='summary source'):
            engine.store.append_many([bad], action_id='invalid-summary-source')
        assert list(engine.store.iter_events(include_retracted=True)) == before
        assert engine.store.revision == revision
        assert engine.store.jsonl_path.read_bytes() == mirror
        assert engine.store.get_action_receipt('invalid-summary-source') is None

        rewind(engine, result.events[0]["turn"])
        assert assemble_context(engine.registry, engine.world, _scene("room")) == context
        engine.store.close()
        engine = build_engine(tmp_path / "campaign", enable_interpretive_memory=True)
        assert assemble_context(engine.registry, engine.world, _scene("room")) == context
    finally:
        engine.store.close()


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


def test_reflection_failure_does_not_suppress_due_recap(tmp_path, monkeypatch, caplog):
    """Only digest's actual recap attempt may report ``pending``."""
    _disable_unrelated_backstage(monkeypatch)
    caplog.set_level(logging.ERROR)
    engine = _seed_engine(tmp_path)
    try:
        # Seed an aged candidate without forcing reflection on its first two
        # foreground actions.  The third action's reflection now fails before
        # the independent recap operation is attempted.
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
        assert recap.calls
        assert result.recap_status == "pending"
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
        from systems.narrative import summary_source_ref
        assert summaries[0]["deltas"] == {
            "scene": "s1", "summary": "SUMMARY_FALSE_CLAIM_COINS_999",
            'source': summary_source_ref(pre_action_world['systems']['narrative']['scenes'][0])}
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


def test_recursive_recap_binds_real_turn_sources_and_cold_duplicate(tmp_path, monkeypatch):
    """Default thresholds, actual run_turn batches, two generations of recaps."""
    _disable_unrelated_backstage(monkeypatch)

    class RecapProvider(FixedRecapProvider):
        def complete_messages(self, messages, **kwargs):
            self.calls.append(copy.deepcopy(messages))
            # Extra model provenance must never replace host-created references.
            return json.dumps({'summary': f'PUBLIC_SUMMARY_{len(self.calls)}',
                               'source': {'event_id': 'MODEL_FORGED_SOURCE'}})

    engine = _seed_engine(tmp_path)
    provider = RecapProvider('unused')
    try:
        for i in range(15):
            pre_action_world = engine.world
            result = _play(engine, f's{i}', f'PUBLIC_RAW_{i}', 10 + i,
                           provider, f'chain-{i}')
        history = list(engine.store.iter_events())
        by_id = {e['id']: e for e in history}
        recaps = [e for e in history if e['type'] == 'recap_recompressed']
        assert len(recaps) == 2
        assert [e['deltas']['summarized_through_index'] for e in recaps] == [6, 12]
        assert recaps[0]['deltas']['source']['previous'] is None
        assert recaps[1]['deltas']['source']['previous']['event_id'] == recaps[0]['id']
        assert PRIVATE_SENTINEL not in str(provider.calls)
        assert 'MODEL_FORGED_SOURCE' not in str(recaps)
        for recap in recaps:
            source = recap['deltas']['source']
            # Rebuild from the exact durable prefix, not the staging projection.
            prefix = history[:history.index(recap)]
            from systems.narrative import recap_source_ref
            ns = project(engine.registry, prefix)['systems']['narrative']
            assert source == recap_source_ref(ns, source['through_index'])
            for reference in source['summaries']:
                summary = by_id[reference['event_id']]
                assert summary['type'] == 'scene_summarized'
                assert reference['source_status'] == 'bound'
                assert reference['bucket_id'] == summary['deltas']['source']['bucket_id']
                assert all(by_id[eid]['type'] == 'narration_recorded'
                           for eid in summary['deltas']['source']['event_ids'])
            # The prompt actually sent contains every referenced summary and predecessor.
            text = recap['deltas']['super_summary']
            call = provider.calls[int(text.removeprefix('PUBLIC_SUMMARY_')) - 1]
            prompt = '\n'.join(m['content'] for m in call)
            for reference in source['summaries']:
                assert by_id[reference['event_id']]['deltas']['summary'] in prompt
            if source['previous']:
                assert recaps[0]['deltas']['super_summary'] in prompt

        context = assemble_context(engine.registry, engine.world, _scene('s14'))
        assert PRIVATE_SENTINEL not in context
        expected = copy.deepcopy(engine.world['systems']['narrative'])
        engine.store.close()
        engine = build_engine(tmp_path / 'campaign', enable_interpretive_memory=True)
        assert engine.world['systems']['narrative'] == expected
        before = list(engine.store.iter_events(include_retracted=True))
        revision, mirror = engine.store.revision, engine.store.jsonl_path.read_bytes()
        guard = RaisingRecapProvider()
        recovered = run_turn(
            engine.registry, engine.store, pre_action_world, _scene('s14'), '继续前进',
            strategy=PreparedStrategy('PUBLIC_RAW_14', 24), provider=guard,
            required_sections=REQUIRED_SECTIONS, action_id='chain-14',
            memory_role='hero', memory_generator=OfflineInterpretationGenerator(PRIVATE_SENTINEL))
        assert recovered.receipt['duplicate']
        assert recovered.events == result.events
        assert guard.calls == []
        assert list(engine.store.iter_events(include_retracted=True)) == before
        assert engine.store.revision == revision
        assert engine.store.jsonl_path.read_bytes() == mirror
        rewind(engine, 15)
        assert engine.world['systems']['narrative']['super_summary_event_id'] == recaps[0]['id']
        engine.store.close()
        engine = build_engine(tmp_path / 'campaign', enable_interpretive_memory=True)
        assert engine.world['systems']['narrative']['super_summary_event_id'] == recaps[0]['id']
    finally:
        engine.store.close()
