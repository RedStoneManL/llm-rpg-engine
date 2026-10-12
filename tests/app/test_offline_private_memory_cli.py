"""End-to-end coverage for the deliberately narrow offline memory experiment."""
from __future__ import annotations

import gc
import json

from app.__main__ import main
from app.engine import OFFLINE_INTERPRETIVE_MEMORY_TEXT, build_engine, new_game
from kernel.events import kernel_event
from llm.provider import FakeLLMProvider


def _turn_provider() -> FakeLLMProvider:
    return FakeLLMProvider(json_responses=[{
        'moves': [], 'places': [], 'cast': [], 'facts': [],
        'narration': '公开叙事：主角在酒馆里听见了雨声。',
        'clock': [{'advance': False, 'days': 0, 'bands': 0, 'reason': '仍在此刻'}],
    }])


def _seed_campaign(path) -> None:
    """Create a real playable save before exercising the CLI resume path."""
    engine = build_engine(path, provider=_turn_provider())
    try:
        new_game(engine)
    finally:
        engine.store.close()


def _run_main(path, args, inputs, provider=None) -> tuple[list[str], FakeLLMProvider | None]:
    out: list[str] = []
    main(['--campaign', str(path), *args], inputs=inputs, out=out.append, provider=provider)
    # main owns a local Engine.  Make the test's next real reopen independent
    # of CPython's implementation-specific collection timing.
    gc.collect()
    return out, provider


def test_private_memory_experiment_is_explicit_and_replayable_without_public_leaks(tmp_path):
    campaign = tmp_path / 'private-memory-cli'
    _seed_campaign(campaign)

    # Default CLI construction neither registers the private owner nor offers
    # a read outlet.  This is a real resume invocation, not a dispatch unit test.
    disabled, _ = _run_main(campaign, [], ['/memory', '/quit'], _turn_provider())
    assert '未启用' in '\n'.join(disabled)
    default_engine = build_engine(campaign, provider=_turn_provider())
    try:
        assert default_engine.registry.owner_of_event('character_interpretation_recorded') is None
    finally:
        default_engine.store.close()

    # The explicit flag registers the owner before replay, routes one real turn
    # through run_turn, and renders the non-empty fixed local record only via
    # /memory.  The foreground provider never receives the private text.
    provider = _turn_provider()
    enabled, _ = _run_main(
        campaign, ['--offline-private-memory'],
        ['听酒馆里的动静', '/memory', '/recall 离线实验解释', '/quit'], provider,
    )
    rendered = '\n'.join(enabled)
    assert '离线私有记忆实验已启用' in rendered
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT in rendered
    assert '[recall] 无' in rendered
    assert all(OFFLINE_INTERPRETIVE_MEMORY_TEXT not in system + user
               for system, user in provider.calls)

    # Add a different actor's private row through the enabled host setup.  The
    # CLI has no role argument, so /memory must keep it hidden from protagonist.
    privileged = build_engine(campaign, provider=_turn_provider(), enable_interpretive_memory=True)
    try:
        privileged.store.append(kernel_event(
            'character_interpretation_recorded', day=1, scene='inn',
            summary='private character interpretation',
            deltas={'actor': 'npc', 'text': 'NPC_PRIVATE_BODY', 'memory_id': 'npc-private'},
            turn=0,
        ))
    finally:
        privileged.store.close()

    reopened, _ = _run_main(campaign, ['--offline-private-memory'], ['/memory', '/quit'], _turn_provider())
    reopened_text = '\n'.join(reopened)
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT in reopened_text
    assert 'NPC_PRIVATE_BODY' not in reopened_text

    # A normal reopen still filters private rows from its recap/public DTO and
    # leaves /memory disabled.  This checks the process boundary, not merely an
    # in-memory projection from the previous invocation.
    public_engine = build_engine(campaign, provider=_turn_provider())
    try:
        public_text = json.dumps(public_engine.public_history(), ensure_ascii=False)
        assert OFFLINE_INTERPRETIVE_MEMORY_TEXT not in public_text
        assert 'NPC_PRIVATE_BODY' not in public_text
    finally:
        public_engine.store.close()
    normal_reopen, _ = _run_main(campaign, [], ['/recall 离线实验解释', '/memory', '/quit'], _turn_provider())
    normal_text = '\n'.join(normal_reopen)
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT not in normal_text
    assert 'NPC_PRIVATE_BODY' not in normal_text
    assert '未启用' in normal_text

    # Undo is a whole-turn retraction: reopening with the experiment enabled
    # afterwards must not revive the protagonist's retracted record.
    undone, _ = _run_main(campaign, ['--offline-private-memory'], ['/undo', '/memory', '/quit'], _turn_provider())
    undone_text = '\n'.join(undone)
    assert '[memory] 无' in undone_text
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT not in undone_text
