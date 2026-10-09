"""Author JSON contract: unusable output cannot publish a fallback/no-op turn."""
import copy
import json
from pathlib import Path

import pytest

from engine import settings
from kernel.events import kernel_event, open_store
from kernel.projection import project
from kernel.registry import Registry
from tests.scripted_provider import StrictScriptedProvider
from loop.strategy import AuthorOutputError, AuthorStrategy
from loop.turn import REQUIRED_SECTIONS, TurnRejected, produce_turn, run_turn
from systems.character import CharacterSystem
from systems.narrative import NarrativeSystem
from systems.object import ObjectSystem
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.time import TimeSystem


def _proposal(narration="你把伞交到同行者手里。", **sections):
    return dict(narration=narration, moves=[], places=[], cast=[], facts=[],
                clock=[dict(advance=False, days=0, bands=0, reason="当面交接")],
                **sections)


def _handover(source="hero"):
    return [dict(op="transfer", item="umbrella", **{"from": source}, to="companion")]


class RecordingProvider(StrictScriptedProvider):
    """Finite phase scripts; ``requests`` retains author-only budget accounting."""
    instances = []

    def __init__(self, responses, *, audits=(), reconciliations=()):
        from loop.strategy import _system_prompt
        from loop.semantic_commit import extraction_system_prompt
        self._responses = [json.dumps(raw, ensure_ascii=False)
                           if isinstance(raw, dict) else raw for raw in responses]

        def payload(request):
            messages = request['messages']
            if len(messages) != 2 or messages[-1].get('role') != 'user':
                return None
            try:
                value = json.loads(messages[-1]['content'])
            except (TypeError, ValueError):
                return None
            return value if isinstance(value, dict) else None

        def extraction(request):
            data = payload(request)
            return data is not None and set(data) == {
                'candidate_prose', 'player_input', 'pov_packet'} and request['messages'][0] == {
                    'role': 'system', 'content': extraction_system_prompt(data['pov_packet'])}

        def reconciliation(request):
            data = payload(request)
            return (data is not None and data.get('scope') == 'primary_turn_before_background_hooks'
                    and {'before', 'after', 'transitions', 'actor_id'} <= set(data)
                    and request['messages'][0]['content'].startswith(
                        '你负责根据已校验的本回合可见结果写最终正文，不负责决定或修改事件。'))

        super().__init__(routes={
            'author': lambda request: request['messages'][0].get('role') == 'system'
                and request['messages'][0].get('content') in {
                    _system_prompt(actor_id='hero'), 'sys'},  # explicit warm-cache fixture
            'semantic_extraction': extraction,
            'narration_reconciliation': reconciliation,
        }, scripts={'author': self._responses, 'semantic_extraction': list(audits),
                    'narration_reconciliation': list(reconciliations)})
        self.instances.append(self)

    @property
    def requests(self):
        return [call['messages'] for call in self.calls if call['phase'] == 'author']


def _fixture_extraction(request, *, noop=False):
    """Explicit complete readings of just two known fixture texts, never arbitrary prose."""
    from loop.semantic_commit import VERSION
    payload = json.loads(request['messages'][-1]['content'])
    expected = "  你暂时停下。\n\n同行者静静等待。\n" if noop else "你把伞交到同行者手里。"
    assert payload['candidate_prose'] == expected
    spans = payload['pov_packet']['narration_spans']
    assert len(spans) == (2 if noop else 1)
    claims = []
    if noop:
        for i, (who, scope) in enumerate((('hero', 'local_motion'), ('companion', 'canonical_transition'))):
            assert spans[i]['text'].strip() == ('你暂时停下。' if i == 0 else '同行者静静等待。')
            claims.append({'id': f'waiting_{i}', 'span_id': spans[i]['id'],
                'quote': spans[i]['text'], 'occurrence': 0, 'kind': 'location', 'scope': scope,
                'binding_reason': 'An already located actor stops locally; the named companion waits in the current inn.',
                'mode': 'current', 'moment': 'after', 'transition_index': None,
                'refs': {'who': who, 'place': 'inn'}, 'present': True})
    else:
        quote = '你把伞交到同行者手里。'
        assert spans[0]['text'] == quote
        for cid, kind, refs, extra in (
            ('handoff', 'transfer', {'item': 'umbrella', 'from': 'hero', 'to': 'companion'}, {}),
            ('present_together', 'co_presence', {'a': 'hero', 'b': 'companion'}, {'together': True}),
        ):
            claims.append({'id': cid, 'span_id': spans[0]['id'], 'quote': quote,
                'occurrence': 0, 'kind': kind, 'scope': 'canonical_transition',
                'binding_reason': 'The exact sentence asserts the umbrella handoff to the physically present companion.',
                'mode': 'completed' if kind == 'transfer' else 'current',
                'moment': 'unknown' if kind == 'transfer' else 'after',
                'transition_index': None, 'refs': refs, **extra})
    return {'version': VERSION, 'claims': claims, 'scene_state_support': [],
        'coverage': {'complete': True, 'spans': [{
            'span_id': span['id'], 'claim_ids': [c['id'] for c in claims if c['span_id'] == span['id']],
            'status': 'checked', 'no_critical_claims': noop and span['id'] == spans[0]['id'],
            'context_span_ids': [], 'context_complete': True} for span in spans]}}


def _handoff_audit(request):
    return _fixture_extraction(request)


def _noop_audit(request):
    return _fixture_extraction(request, noop=True)

@pytest.fixture
def game(tmp_path, monkeypatch):
    RecordingProvider.instances = []
    for name in ("digest_fleet", "run_director", "run_cascade", "run_catchup",
                 "run_lore", "run_density", "_run_demote_on_leave"):
        monkeypatch.setattr("loop.turn." + name, lambda *args, **kwargs: [])
    registry = Registry()
    for system in (OntologySystem(), PlaceSystem(), CharacterSystem(),
                   ObjectSystem(), TimeSystem(), NarrativeSystem()):
        registry.register(system)
    store = open_store(str(tmp_path / "events.db"), str(tmp_path / "events.jsonl"),
                       allowed_types=registry.event_types())
    declarations = [
        ("entity_created", dict(id="hero", etype="Person")),
        ("entity_created", dict(id="companion", etype="Person")),
        ("entity_created", dict(id="inn", etype="Place", attrs={"level": 3, "kind": "venue"})),
        ("object_created", dict(id="umbrella")),
        ("item_transferred", dict(item="umbrella", to="hero")),
        ("entity_moved", dict(who="hero", to="inn")),
        ("entity_moved", dict(who="companion", to="inn")),
        ("fact_asserted", dict(subject="companion", predicate="name", value="同行者")),
    ]
    store.append_many([kernel_event(kind, day=1, scene="inn", turn=0,
                                   summary="contract fixture", deltas=deltas)
                       for kind, deltas in declarations])
    world = project(registry, store.iter_events())
    world["_revision"] = store.revision
    scene = dict(protagonist="hero", present=["hero", "companion"], day=1,
                 location="inn", id="inn")
    try:
        yield registry, store, world, scene
    finally:
        try:
            for provider in RecordingProvider.instances:
                provider.assert_consumed()
        finally:
            store.close()


def _run(game, provider, strategy=None, max_repairs=3):
    registry, store, world, scene = game
    result = run_turn(registry, store, world, scene, "把雨伞交给同行者", provider=provider,
                    strategy=strategy or AuthorStrategy(), max_repairs=max_repairs,
                    required_sections=REQUIRED_SECTIONS)
    assert provider.consumed['semantic_extraction'] == 1
    assert any(row['kind'] == 'semantic_audit' and row['passed']
               for row in result.commit.semantic_audit_log)
    return result


def _produce(game, provider, strategy=None, max_repairs=3):
    registry, _, world, scene = game
    return produce_turn(registry, world, scene, "把雨伞交给同行者", provider=provider,
                        strategy=strategy or AuthorStrategy(), max_repairs=max_repairs,
                        required_sections=REQUIRED_SECTIONS)


@pytest.mark.parametrize("narration", ['  他说："等一下"。\n路径 \\ 雨伞 ☂  ',
                                     '{"narration":"这段 JSON 本身也是故事原文"}'])
def test_history_json_envelope_preserves_exact_text_without_old_effects(game, narration):
    settings.set_conversation_mode("multiturn")
    registry, _, world, scene = game
    strategy = AuthorStrategy()
    # A warm pre-change cache is supported without rewriting it or guessing
    # whether narration that happens to resemble JSON is already an envelope.
    strategy._thread = [dict(role="system", content="sys"),
                        dict(role="user", content="[player] 旧动作"),
                        dict(role="assistant", content=narration)]
    old_history = copy.deepcopy(strategy._thread)
    provider = RecordingProvider([_proposal(items=_handover()), _proposal()])
    commit = strategy.produce(registry, world, scene, "原始动作\n不改写", provider=provider)
    assert json.loads(provider.requests[0][2]["content"]) == {"narration": narration}
    assert strategy._thread == old_history
    strategy.commit_to_thread(commit.narration)
    assert strategy._thread[-2]["content"] == "[player] 原始动作\n不改写"
    assert strategy._thread[-1]["content"] == commit.narration
    strategy.produce(registry, world, scene, "下一步", provider=provider)
    assistants = [json.loads(m["content"]) for m in provider.requests[1]
                  if m["role"] == "assistant"]
    assert assistants == [{"narration": narration}, {"narration": commit.narration}]
    assert all(set(m) == {"narration"} for m in assistants)


def test_committed_history_keeps_eight_exchanges():
    strategy = AuthorStrategy()
    strategy._thread = [dict(role="system", content="sys")]
    for i in range(12):
        strategy._pending_user = "latest context"
        strategy._pending_action = f"动作 {i}"
        strategy.commit_to_thread(f"结果 {i}")
    assert len(strategy._thread) == 17
    assert strategy._thread[1]["content"] == "[player] 动作 4"
    assert strategy._thread[-1]["content"] == "结果 11"


INVALID_OUTPUTS = [
    " \n\t ", "原始散文不能代替结构化回合", "null", "42", '"string"',
    '[{"narration":"数组不能被截取成对象"}]',
    '{"narration":"不完整" "facts":[{"value":"PRIVATE_MARKER"}]}',
    {}, {"narration": ""}, {"narration": " \n "}, {"narration": None},
    {"narration": 12}, {"narration": False}, {"narration": {}},
    {"narration": []}, {"narration": [" ", ""]}, {"narration": ["段落", None]},
]


@pytest.mark.parametrize("invalid", INVALID_OUTPUTS)
def test_unusable_whole_output_recovers_complete_turn_with_optional_items(game, invalid):
    settings.set_conversation_mode("multiturn")
    provider = RecordingProvider([invalid, _proposal(items=_handover())], audits=[_handoff_audit])
    strategy = AuthorStrategy()
    result = _run(game, provider, strategy, max_repairs=1)
    assert result.repair_attempts == 1 and result.dropped_sections == []
    assert len(provider.requests) == 2
    assert result.commit.sections["items"] == _handover()
    assert result.world["systems"]["ontology"].neighbors("umbrella", "held_by", 1) == ["companion"]
    prompt = provider.requests[1][-1]["content"]
    assert "完整 TurnCommit" in prompt and "items" in prompt
    assert prompt.endswith("[player] 把雨伞交给同行者")
    rejected_raw = provider._responses[0]
    assert any(m.get("content") == rejected_raw for m in strategy._messages)
    assert not any(m.get("content") == rejected_raw for m in provider.requests[1])
    assert result.narration == "你把伞交到同行者手里。"
    assert strategy._thread[-1]["content"] == result.narration


@pytest.mark.parametrize("invalid", INVALID_OUTPUTS)
def test_strict_author_failure_is_explicit_and_never_salvaged(game, invalid):
    registry, _, world, scene = game
    provider = RecordingProvider([invalid])
    strategy = AuthorStrategy()
    with pytest.raises(AuthorOutputError) as caught:
        strategy.produce(registry, world, scene, "动作", provider=provider)
    assert caught.value.code in {"invalid_json", "non_object_json", "missing_narration", "invalid_narration"}
    assert "PRIVATE_MARKER" not in str(caught.value)
    assert len(provider.requests) == 1
    assert strategy._messages[-1]["content"] == provider._responses[0]


@pytest.mark.parametrize("compaction_due", [False, True])
def test_exhaustion_rejects_without_event_world_or_cache_advance(game, compaction_due):
    settings.set_conversation_mode("multiturn")
    _, store, world, _ = game
    strategy = AuthorStrategy()
    strategy._thread = [dict(role="system", content="sys"),
                        dict(role="user", content="[player] 旧动作"),
                        dict(role="assistant", content="已发布的旧故事")]
    strategy._compaction_due = compaction_due
    state = copy.deepcopy(strategy.__dict__)
    events = list(store.iter_events())
    revision = store.revision
    mirror = Path(store.jsonl_path).read_bytes()
    graph, meta = copy.deepcopy(world["systems"]["ontology"].__dict__), copy.deepcopy(world["meta"])
    provider = RecordingProvider(['{"facts":[{"value":"PRIVATE_MARKER"}]}'] * 3)
    with pytest.raises(TurnRejected) as caught:
        _run(game, provider, strategy, max_repairs=2)
    assert len(provider.requests) == 3
    assert "PRIVATE_MARKER" not in str(caught.value)
    assert list(store.iter_events()) == events and store.revision == revision
    assert Path(store.jsonl_path).read_bytes() == mirror
    assert world["systems"]["ontology"].__dict__ == graph and world["meta"] == meta
    assert strategy.__dict__ == state


def test_zero_budget_rejects_unusable_output_without_a_hidden_call(game):
    provider = RecordingProvider([" "])
    with pytest.raises(TurnRejected):
        _produce(game, provider, max_repairs=0)
    assert len(provider.requests) == 1


@pytest.mark.parametrize("budget", [1, 2])
def test_whole_and_modular_repairs_share_budget_and_validate_item_provenance(game, budget):
    responses = [" ", _proposal(items=_handover("companion")), {"items": _handover()}]
    provider = RecordingProvider(responses[:1 + budget],
        audits=[_handoff_audit] if budget == 2 else [],
        reconciliations=[{'narration': '你把伞交到同行者手里。'}] if budget == 2 else [])
    if budget == 1:
        with pytest.raises(TurnRejected, match="items"):
            _run(game, provider, max_repairs=budget)
    else:
        result = _run(game, provider, max_repairs=budget)
        assert result.repair_attempts == 2
        assert result.narration == "你把伞交到同行者手里。"
        assert result.world["systems"]["ontology"].neighbors("umbrella", "held_by", 1) == ["companion"]
        assert "只重新输出这些段 [items]" in provider.requests[2][-1]["content"]
    assert len(provider.requests) == 1 + budget


@pytest.mark.parametrize("guard", ["references", "resources", "narration"])
def test_whole_recovery_runs_all_existing_guards(game, guard):
    _, _, world, scene = game
    recovered = _proposal()
    if guard == "references":
        recovered["facts"] = [dict(subject="ghost_404", predicate="mood", value="calm")]
        expected = "facts"
    elif guard == "resources":
        scene["_resolved_values"] = {("hero", "coins"): 7}
        recovered["facts"] = [dict(subject="hero", predicate="coins", value=100)]
        expected = "facts"
    else:
        recovered["narration"] = "这是上一回合已发布的长故事。" * 20
        world["systems"]["narrative"]["scenes"] = [dict(scene="inn", raw=[recovered["narration"]])]
        expected = "narration"
    _, attempts, dropped = _produce(game, RecordingProvider([" ", recovered]), max_repairs=1)
    assert attempts == 1 and expected in dropped


def test_valid_intentional_noop_and_paragraph_compatibility_need_no_retry(game):
    paragraphs = ["  你暂时停下。", "同行者静静等待。\n"]
    provider = RecordingProvider([_proposal(narration=paragraphs)], audits=[_noop_audit])
    result = _run(game, provider, max_repairs=0)
    assert result.narration == "\n\n".join(paragraphs)
    assert result.repair_attempts == 0 and len(provider.requests) == 1
    assert result.world["systems"]["ontology"].neighbors("umbrella", "held_by", 1) == ["hero"]


def test_tool_final_failure_uses_shared_budget_and_retains_tool_groups(game):
    class ToolProvider(RecordingProvider):
        tool_calls = 0

        def supports_tools(self):
            return True

        def complete_with_tools(self, messages, tools, tool_executor, **kwargs):
            self.tool_calls += 1
            messages.extend([
                dict(role="assistant", content=None, tool_calls=[dict(id="query_1", type="function",
                     function=dict(name="map_query", arguments='{"q":"inn"}'))]),
                dict(role="tool", tool_call_id="query_1", content='{"places":[]}'),
            ])
            return "UNUSABLE_TOOL_FINAL"

    provider = ToolProvider([_proposal(items=_handover())], audits=[_handoff_audit])
    strategy = AuthorStrategy()
    result = _run(game, provider, strategy, max_repairs=1)
    assert result.repair_attempts == 1 and provider.tool_calls == 1
    assert len(provider.requests) == 1
    request = provider.requests[0]
    tool_reply = next(i for i, m in enumerate(request) if m["role"] == "tool")
    assert request[tool_reply - 1]["tool_calls"][0]["id"] == request[tool_reply]["tool_call_id"]
    assert not any(m.get("content") == "UNUSABLE_TOOL_FINAL" for m in request)
    assert any(m.get("content") == "UNUSABLE_TOOL_FINAL" for m in strategy._messages)

    no_budget = ToolProvider([])
    with pytest.raises(TurnRejected):
        _produce(game, no_budget, max_repairs=0)
    assert no_budget.tool_calls == 1 and no_budget.requests == []


def test_recovered_author_cannot_publish_when_separate_audit_is_malformed(game):
    _, store, world, _ = game
    before = list(store.iter_events(include_retracted=True))
    revision = store.revision
    mirror = Path(store.jsonl_path).read_bytes()
    provider = RecordingProvider([" ", _proposal(items=_handover())],
                                 audits=[{"narration": "PRIVATE_AUDIT_MARKER"}])
    with pytest.raises(TurnRejected, match="Invalid semantic response fields") as caught:
        _run(game, provider, max_repairs=1)
    assert "PRIVATE_AUDIT_MARKER" not in str(caught.value)
    assert provider.consumed == {'author': 2, 'semantic_extraction': 1,
                                 'narration_reconciliation': 0}
    assert list(store.iter_events(include_retracted=True)) == before
    assert store.revision == revision and Path(store.jsonl_path).read_bytes() == mirror
    assert world['systems']['ontology'].neighbors('umbrella', 'held_by', 1) == ['hero']


def test_repaired_structure_cannot_skip_invalid_narration_reconciliation(game):
    _, store, world, _ = game
    before = list(store.iter_events(include_retracted=True))
    revision = store.revision
    provider = RecordingProvider([" ", _proposal(items=_handover("companion")),
                                  {"items": _handover()}],
        reconciliations=[{"narration": "PRIVATE_RECONCILE_MARKER", "items": []}])
    with pytest.raises(TurnRejected, match="Unable to reconcile") as caught:
        _run(game, provider, max_repairs=2)
    assert "PRIVATE_RECONCILE_MARKER" not in str(caught.value)
    assert provider.consumed == {'author': 3, 'semantic_extraction': 0,
                                 'narration_reconciliation': 1}
    assert list(store.iter_events(include_retracted=True)) == before
    assert store.revision == revision
    assert world['systems']['ontology'].neighbors('umbrella', 'held_by', 1) == ['hero']
