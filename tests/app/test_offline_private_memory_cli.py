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

    # Now run a second real player turn after BOTH non-empty sentinels exist.
    # This is the actual prompt-isolation boundary: the provider must receive
    # fresh public input/context, but neither pre-existing private body.
    second_provider = _turn_provider()
    reopened, _ = _run_main(
        campaign, ['--offline-private-memory'],
        ['PUBLIC_SECOND_TURN_CONTROL：继续沿街观察', '/memory', '/quit'], second_provider,
    )
    reopened_text = '\n'.join(reopened)
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT in reopened_text
    assert 'NPC_PRIVATE_BODY' not in reopened_text
    assert second_provider.calls
    second_prompt_text = '\n'.join(system + user for system, user in second_provider.calls)
    assert 'PUBLIC_SECOND_TURN_CONTROL' in second_prompt_text
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT not in second_prompt_text
    assert 'NPC_PRIVATE_BODY' not in second_prompt_text
    # The second real turn added a second protagonist record, so the subsequent
    # undo can prove that its complete turn (including memory) is retracted.
    assert reopened_text.count('[memory] 第 ') == 2

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

    # Undo retracts the second complete turn, leaving only the first record;
    # rewinding turn 1 then retracts that remaining record too.  The count
    # assertions make the identical fixed private text distinguishable.
    undone, _ = _run_main(campaign, ['--offline-private-memory'], ['/undo', '/memory', '/quit'], _turn_provider())
    undone_text = '\n'.join(undone)
    assert undone_text.count('[memory] 第 ') == 1
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT in undone_text

    rewound, _ = _run_main(campaign, ['--offline-private-memory'], ['/rewind 1', '/memory', '/quit'], _turn_provider())
    rewound_text = '\n'.join(rewound)
    assert '[memory] 无' in rewound_text
    assert rewound_text.count('[memory] 第 ') == 0
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT not in rewound_text


def test_debug_trace_keeps_offline_private_memory_out_of_operator_records(tmp_path, monkeypatch):
    """Debug is an operator-only raw prompt sink, not another /memory outlet.

    The trace intentionally retains public player input and full provider
    prompt/output for diagnosis.  This checks the narrower but essential
    boundary: enabling it must not make the opt-in protagonist interpretation
    enter those raw diagnostic records.
    """
    campaign = tmp_path / "private-memory-debug-trace"
    _seed_campaign(campaign)
    monkeypatch.delenv("RPG_DEBUG_TRACE", raising=False)
    import kernel.observability as observability
    observability._DEBUG_TRACER = None

    main(
        ["--campaign", str(campaign), "--offline-private-memory", "--debug"],
        inputs=["PUBLIC_TRACE_CONTROL：在酒馆观察雨声", "/quit"],
        out=lambda _line: None,
        provider=_turn_provider(),
    )

    trace_path = campaign / "trace.jsonl"
    assert trace_path.exists()
    reopened = build_engine(campaign, provider=_turn_provider(), enable_interpretive_memory=True)
    try:
        from systems.interpretive_memory import records_for_actor
        assert [row["text"] for row in records_for_actor(reopened.world, "protagonist")] == [
            OFFLINE_INTERPRETIVE_MEMORY_TEXT,
        ]
    finally:
        reopened.store.close()
    raw_trace = trace_path.read_text(encoding="utf-8")
    assert "PUBLIC_TRACE_CONTROL" in raw_trace
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT not in raw_trace
    # Exercise the actual diagnostic viewer's full-record route as well: it
    # must only reveal what the local trace already contains.
    from app import trace
    records = trace.load(str(trace_path))
    rendered = []
    import io
    for record in records:
        target = io.StringIO()
        trace.show_record(record, out=target)
        rendered.append(target.getvalue())
    assert OFFLINE_INTERPRETIVE_MEMORY_TEXT not in "".join(rendered)

    # Keep the process-global trace singleton from leaking into a later test.
    observability._DEBUG_TRACER = None
