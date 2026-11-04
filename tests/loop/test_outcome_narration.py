"""Playable results: repair the outcome before composing the visible story."""
import asyncio
import copy

import pytest

from app.engine import build_engine, rewind
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy, HybridStrategy
from loop.turn import TurnRejected, run_turn


class RecordingProvider(FakeLLMProvider):
    def __init__(self, *, proposals, prose):
        super().__init__(json_responses=proposals, responses=prose)
        self.stages = []

    def complete_messages(self, messages, **kwargs):
        self.stages.append(('structure', copy.deepcopy(messages)))
        return super().complete_messages(messages, **kwargs)

    def complete(self, system, user, **kwargs):
        self.stages.append(('narration', user))
        return super().complete(system, user, **kwargs)


@pytest.fixture
def game(tmp_path, monkeypatch):
    # Isolate foreground ordering; real store/projection/controller remain live.
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup',
                 'run_lore', 'run_density', '_run_demote_on_leave'):
        monkeypatch.setattr('loop.turn.' + name, lambda *a, **kw: [])
    engine = build_engine(tmp_path / 'campaign', embedder=None)
    _seed_demo_campaign(engine)
    yield engine
    engine.store.close()


def proposal(**changes):
    return dict(narration='尚未确定的草稿', moves=[], places=[], cast=[], facts=[],
                clock=[dict(advance=False, days=0, bands=0, reason='短暂交谈')], **changes)


def test_hybrid_validates_structure_then_writes_once_through_normal_gui(game):
    plan = proposal()
    plan['moves'] = [{'who': 'protagonist', 'to': 'stonebridge_market'}]
    provider = RecordingProvider(proposals=[plan], prose=['你走进集市。摊主抬头，等你开口。'])
    game.provider = provider
    dashboard = PlayerDashboard(game, strategy=HybridStrategy(), demo=False)
    result = dashboard.action(player_input='去集市', action_id='outcome-first',
                              expected_revision=game.store.revision)
    assert [stage for stage, _ in provider.stages] == ['structure', 'narration']
    final_prompt = provider.stages[-1][1]
    assert '【本回合已通过校验的结果】' in final_prompt
    assert 'stonebridge_market' in final_prompt
    assert '尚未确定的草稿' not in final_prompt
    assert result['state']['narration'][-1] == '你走进集市。摊主抬头，等你开口。'
    assert result['state']['scene']['location']['id'] == 'stonebridge_market'


@pytest.mark.parametrize('strategy_type', [AuthorStrategy, HybridStrategy])
def test_changed_structure_replaces_old_success_prose(game, strategy_type):
    plan = proposal()
    plan.update(narration='幽灵把宝石交给了你。',
                facts=[dict(subject='missing_ghost', predicate='gift', value='宝石')])
    provider = RecordingProvider(proposals=[plan, {'facts': []}],
                                 prose=['门后空无一人。你没有得到宝石，只有走廊传来的脚步声。'])
    answer = run_turn(game.registry, game.store, game.world, _build_scene(game),
                      '敲门看看', strategy=strategy_type(), provider=provider)
    assert [stage for stage, _ in provider.stages] == ['structure', 'structure', 'narration']
    prompt = provider.stages[-1][1]
    assert 'missing_ghost' not in prompt and '幽灵把宝石交给了你' not in prompt
    assert '"facts": []' in prompt
    assert answer.narration.startswith('门后空无一人')
    assert answer.commit.sections['facts'] == []
    saved = [e['deltas']['text'] for e in answer.events if e['type'] == 'narration_recorded']
    assert saved == [answer.narration]


def test_unchanged_author_keeps_single_call(game):
    provider = RecordingProvider(proposals=[proposal()], prose=[])
    run_turn(game.registry, game.store, game.world, _build_scene(game), '等一会',
             strategy=AuthorStrategy(), provider=provider)
    assert [stage for stage, _ in provider.stages] == ['structure']


def test_bad_structure_never_spends_a_narration_call(game):
    plan = proposal()
    plan['facts'] = [{'subject': 'missing', 'predicate': 'x', 'value': 'y'}]
    provider = RecordingProvider(proposals=[plan], prose=['不该生成的正文'])
    before = list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '敲门',
                 strategy=HybridStrategy(), provider=provider, max_repairs=0)
    assert [stage for stage, _ in provider.stages] == ['structure']
    assert list(game.store.iter_events()) == before


def test_hybrid_repairs_unreachable_move_before_any_prose(game):
    game.store.append(kernel_event('place_created', day=1, scene='s1', turn=0,
        summary='remote place', deltas={'id': 'unreachable_destination', 'level': 2,
        'kind': 'settlement', 'seed': '远处的城镇', 'visibility': 'public'}))
    game.world = project(game.registry, game.store.iter_events())
    plan = proposal()
    plan['moves'] = [{'who': 'protagonist', 'to': 'unreachable_destination'}]
    provider = RecordingProvider(proposals=[plan, {'moves': []}], prose=['你还在桥头，先向路人打听路线。'])
    scene = _build_scene(game)
    answer = run_turn(game.registry, game.store, game.world, scene, '往远处走',
        strategy=HybridStrategy(), provider=provider, enforce_player_travel=True)
    assert [kind for kind, _ in provider.stages] == ['structure', 'structure', 'narration']
    accepted = provider.stages[-1][1].split('【本回合已通过校验的结果】', 1)[1]
    assert 'unreachable_destination' not in accepted
    assert answer.commit.sections['moves'] == []
    assert answer.world['systems']['ontology'].neighbors('protagonist', 'located_in', 1) == [scene['location']]


def test_repaired_author_commits_only_final_text_to_multiturn_thread(game):
    from engine import settings
    settings.set_conversation_mode('multiturn')
    plan = proposal()
    plan['facts'] = [{'subject': 'missing', 'predicate': 'gift', 'value': '宝石'}]
    provider = RecordingProvider(proposals=[plan, {'facts': []}], prose=['门后空无一人。'])
    strategy = AuthorStrategy()
    run_turn(game.registry, game.store, game.world, _build_scene(game), '敲门',
             strategy=strategy, provider=provider)
    assert strategy._thread[-1] == {'role': 'assistant', 'content': '门后空无一人。'}
    assert '尚未确定的草稿' not in str(strategy._thread)


def test_prose_hook_cannot_mutate_the_accepted_effects(game):
    class Mutating(HybridStrategy):
        def finalize_narration(self, commit, **kwargs):
            commit.sections['moves'] = [{'who': 'protagonist', 'to': 'stonebridge_market'}]
            return '你在原地看了看。'
    provider = RecordingProvider(proposals=[proposal()], prose=[])
    scene = _build_scene(game)
    answer = run_turn(game.registry, game.store, game.world, scene, '观察',
                     strategy=Mutating(), provider=provider)
    assert answer.commit.sections['moves'] == []
    assert answer.world['systems']['ontology'].neighbors('protagonist', 'located_in', 1) == [scene['location']]


def test_resource_refusal_is_available_to_final_writer_after_fact_repair(game):
    game.store.append(kernel_event('resources_configured', day=1, scene='s1', turn=0,
        summary='starting purse', deltas={'subject': 'protagonist', 'resources': {
            'coins': {'initial': 8, 'type': 'integer', 'min': 0}}}))
    game.world = project(game.registry, game.store.iter_events())
    plan = proposal()
    plan.update(narration='你买下货物，把五枚找零装进口袋。',
                facts=[{'subject': 'protagonist', 'predicate': 'coins', 'value': 5, 'secrecy': 'public'}])
    provider = RecordingProvider(proposals=[{'op': 'spend', 'resource': 'coins', 'amount': 20}, plan, {'facts': []}],
                                 prose=['钱不够。商人把货物放回货架，等着你的答复。'])
    answer = run_turn(game.registry, game.store, game.world, _build_scene(game), '花20金币买货',
                     strategy=AuthorStrategy(), provider=provider, enforce_player_fact_authority=True)
    prompt = provider.stages[-1][1]
    assert 'insufficient' in prompt and '"coins": 8' in prompt
    assert '你买下货物' not in prompt
    assert answer.narration.startswith('钱不够')
    assert answer.world['systems']['ontology'].value_at('protagonist', 'coins', 1) == 8


@pytest.mark.parametrize('failure', [ConnectionError, asyncio.CancelledError])
def test_final_prose_failure_never_applies_or_debits(game, failure):
    game.store.append(kernel_event('resources_configured', day=1, scene='s1', turn=0,
        summary='starting purse', deltas={'subject': 'protagonist', 'resources': {
            'coins': {'initial': 8, 'type': 'integer', 'min': 0}}}))
    game.world = project(game.registry, game.store.iter_events())
    class Failing(RecordingProvider):
        def complete(self, *args, **kwargs):
            raise failure('final prose unavailable')
    provider = Failing(proposals=[{'op': 'spend', 'resource': 'coins', 'amount': 3}, proposal()], prose=[])
    before = list(game.store.iter_events())
    with pytest.raises(failure):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '付3金币',
                 strategy=HybridStrategy(), provider=provider)
    assert list(game.store.iter_events()) == before
    assert project(game.registry, game.store.iter_events())['systems']['ontology'].value_at(
        'protagonist', 'coins', 1) == 8


def test_final_prose_retry_does_not_repeat_resource_resolution(game):
    game.store.append(kernel_event('resources_configured', day=1, scene='s1', turn=0,
        summary='starting purse', deltas={'subject': 'protagonist', 'resources': {
            'coins': {'initial': 8, 'type': 'integer', 'min': 0}}}))
    game.world = project(game.registry, game.store.iter_events())
    provider = RecordingProvider(proposals=[{'op': 'spend', 'resource': 'coins', 'amount': 3}, proposal()],
                                 prose=['', '你付出三枚金币，还剩五枚。摊主向你点了点头。'])
    scene = _build_scene(game)
    revision = game.store.revision
    answer = run_turn(game.registry, game.store, game.world, scene, '付3金币',
        strategy=HybridStrategy(), provider=provider, action_id='pay-once', expected_revision=revision)
    assert answer.world['systems']['ontology'].value_at('protagonist', 'coins', 1) == 5
    assert sum(e['type'] == 'resources_resolved' for e in answer.events) == 1
    prompts = [payload for kind, payload in provider.stages if kind == 'narration']
    assert len(prompts) == 2 and all('"coins": 5' in p for p in prompts)
    # Duplicate and cold reopening recover the exact final text, never reroll it.
    game.store.close()
    reopened = build_engine(game.store.jsonl_path.parent, embedder=None)
    try:
        duplicate_provider = RecordingProvider(proposals=[], prose=[])
        replay = run_turn(reopened.registry, reopened.store, reopened.world, scene, '付3金币',
            strategy=HybridStrategy(), provider=duplicate_provider, action_id='pay-once', expected_revision=revision)
        assert replay.narration == answer.narration
        assert duplicate_provider.stages == []
        reopened.world = replay.world
        rewind(reopened, answer.events[0]['turn'])
        assert reopened.world['systems']['ontology'].value_at('protagonist', 'coins', 1) == 8
    finally:
        reopened.store.close()


@pytest.mark.parametrize('text', ['', '{"narration":"raw commit","facts":[]}'])
def test_exhausted_prose_retry_aborts_without_publishing(game, text):
    provider = RecordingProvider(proposals=[proposal()], prose=[text, text])
    before = list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry, game.store, game.world, _build_scene(game), '等待',
                 strategy=HybridStrategy(), provider=provider)
    assert list(game.store.iter_events()) == before
    assert [stage for stage, _ in provider.stages] == ['structure', 'narration', 'narration']
