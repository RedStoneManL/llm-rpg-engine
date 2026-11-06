"""DM facts, individual beliefs, player thoughts and heard words have distinct sources."""
import pytest

from app.engine import build_engine
from app.gui import _seed_demo_campaign
from app.play import _build_scene
from context.native_authoring import native_authoring_context, native_authoring_policy_errors
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit


KEY = 'stonebridge_market.ferry'


@pytest.fixture
def campaign(tmp_path):
    engine = build_engine(tmp_path, embedder=None)
    _seed_demo_campaign(engine)
    def add(kind, **delta):
        engine.store.append(kernel_event(kind, day=1, turn=0, scene='s1', summary=kind, deltas=delta))
    for pid, location in [('mara','stonebridge_inn'), ('lens','stonebridge_inn'), ('painter','stonebridge_market')]:
        add('character_created', id=pid, sketch='过客'+pid, goal='等人', tier='tracked')
        add('entity_moved', who=pid, to=location)
    add('fact_asserted', subject='stonebridge_market', predicate='ferry', value='实际从西岸出发', secrecy='public')
    add('fact_asserted', subject='stonebridge_market', predicate='cause', value='仅DM知道的黑栓', secrecy='secret')
    for pid, belief in [('mara','应从东岸出发'), ('lens','听说停航'), ('painter','听说去南湾')]:
        add('knowledge_set', knower=pid, fact_key=KEY, value=belief)
    add('knowledge_set', knower='mara', fact_key='stonebridge_market.cause', value='仅DM知道的黑栓')
    engine.world = project(engine.registry, engine.store.iter_events())
    try:
        yield engine
    finally:
        engine.store.close()


def test_current_and_arriving_people_keep_their_own_beliefs(campaign):
    brief = native_authoring_context(campaign.world, _build_scene(campaign))
    here = {person['id']: person['speakable_evidence'] for person in brief['present_people']}
    there = {p['id']: p['speakable_evidence'] for arrival in brief['conditional_arrivals'] for p in arrival['present_people']}
    assert [e['value'] for e in here['mara']] == ['应从东岸出发']
    assert [e['value'] for e in here['lens']] == ['听说停航']
    assert [e['value'] for e in there['painter']] == ['听说去南湾']
    assert 'painter' not in here and '仅DM知道的黑栓' not in str(brief)


def test_restatement_cannot_overwrite_truth_or_borrow_another_speakers_belief(campaign):
    world, scene = campaign.world, _build_scene(campaign)
    def policy(value, speaker, *, fact_value=None, move=False):
        sections = {'knowledge':[{'op':'told','knower':'protagonist','fact_key':KEY,'value':value,'via':speaker}]}
        if move:
            sections['moves'] = [{'who':'protagonist','to':'stonebridge_market'}]
        if fact_value is not None:
            sections['facts'] = [{'subject':'stonebridge_market','predicate':'ferry','value':fact_value,'secrecy':'public'}]
        return native_authoring_policy_errors(world, scene, TurnCommit(sections=sections))
    assert not policy('应从东岸出发','mara')
    assert not policy('听说停航','lens')
    assert policy('应从东岸出发','lens')
    assert policy('实际从西岸出发','mara')
    assert policy('应从东岸出发','mara',fact_value='应从东岸出发')
    assert policy('听说去南湾','painter') and not policy('听说去南湾','painter',move=True)


def test_private_belief_record_is_not_speakable_even_for_public_topic(campaign):
    graph = campaign.world['systems']['ontology']
    graph.assert_fact('lens','knows:'+KEY,'个人隐念-只向主持人提供',day=1,turn=2,source_event='synthetic',secrecy='restricted')
    brief = native_authoring_context(campaign.world,_build_scene(campaign))
    lens = next(p for p in brief['present_people'] if p['id']=='lens')
    assert not lens['speakable_evidence']


def test_dm_tool_and_player_author_receive_different_material(campaign):
    from llm.tools import build_tool_registry
    scene = _build_scene(campaign)
    dm = build_tool_registry(campaign.registry, campaign.world, scene, dm=True)
    ordinary = build_tool_registry(campaign.registry, campaign.world, scene)
    assert '仅DM知道的黑栓' in str(dm.execute('dm_world_query', {'q':'stonebridge_market'}))
    assert '仅DM知道的黑栓' not in str(ordinary.execute('dm_world_query', {'q':'stonebridge_market'}))
    stale = {**scene, 'present': scene['present'] + ['painter']}
    from context.native_authoring import host_player_statement_receipt
    with pytest.raises(ValueError):
        host_player_statement_receipt(campaign.world, stale, recipient='painter', statement='仅说给你听')


@pytest.mark.parametrize('wrong_stage', ['proposal', 'published'])
def test_ordinary_turn_cannot_substitute_a_different_witness(campaign, monkeypatch, wrong_stage):
    from llm.provider import FakeLLMProvider
    from loop.strategy import AuthorStrategy
    from loop.turn import TurnRejected, run_turn
    for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density'):
        monkeypatch.setattr('loop.turn.' + name, lambda *a, **kw: [])
    before = list(campaign.store.iter_events())
    row = {'op':'told','knower':'protagonist','fact_key':KEY,'value':'应从东岸出发',
           'via':'lens' if wrong_stage == 'proposal' else 'mara'}
    if wrong_stage == 'published':
        owner = campaign.registry.owner_of_section('knowledge')
        original = owner.to_events
        def substitute(*args, **kwargs):
            events = original(*args, **kwargs)
            events[0]['deltas']['via'] = 'lens'
            return events
        monkeypatch.setattr(owner, 'to_events', substitute)
    provider = FakeLLMProvider(json_responses=[{'narration':'你打听渡口。', 'moves':[], 'places':[], 'cast':[],
          'facts':[], 'knowledge':[row], 'clock':[{'advance':False,'reason':'交谈'}]}])
    with pytest.raises(TurnRejected):
        run_turn(campaign.registry,campaign.store,campaign.world,_build_scene(campaign),'打听渡口',
                 strategy=AuthorStrategy(),provider=provider,max_repairs=0,enforce_player_knowledge_authority=True)
    assert list(campaign.store.iter_events()) == before


def test_boolean_belief_stays_boolean_and_cannot_be_replaced_with_one(campaign):
    graph = campaign.world['systems']['ontology']
    graph.assert_fact('stonebridge_market','open',True,day=1,turn=1,source_event='world',secrecy='public')
    graph.assert_fact('mara','knows:stonebridge_market.open',False,day=1,turn=2,source_event='witness')
    scene = _build_scene(campaign)
    brief = native_authoring_context(campaign.world,scene)
    evidence = next(p for p in brief['present_people'] if p['id']=='mara')['speakable_evidence']
    assert next(e for e in evidence if e['fact_key'].endswith('.open'))['value'] is False
    for value, allowed in [(False,True), (0,False), (True,False)]:
        commit = TurnCommit(sections={'knowledge':[{'op':'told','knower':'protagonist',
                            'fact_key':'stonebridge_market.open','value':value,'via':'mara'}]})
        assert bool(native_authoring_policy_errors(campaign.world,scene,commit)) is not allowed


def test_normal_handoff_keeps_private_words_with_present_and_addressed_recipient(campaign):
    from context.assembler import safe_authoring_handoff
    from systems.knowledge import player_statement_fact_key
    world, scene = campaign.world, _build_scene(campaign)
    words = {'mara':'仅交玛拉的暗号-藤壶','lens':'仅交林思的暗号-画眉','painter':'远处画师的暗号-乌桕'}
    for pid, private in words.items():
        campaign.store.append(kernel_event('knowledge_set', day=1, turn=1, scene='s', summary='明确私语',
            deltas={'knower':pid,'fact_key':player_statement_fact_key('protagonist',private),'value':private,
                    'via':'protagonist','source_kind':'player_statement'}))
    events = list(campaign.store.iter_events())
    world = project(campaign.registry,events)
    handoff = safe_authoring_handoff(world,scene,trusted_events=events)
    assert words['mara'] in handoff and words['lens'] in handoff and words['painter'] not in handoff
    from context.native_authoring import host_player_statement_receipt
    receipt = host_player_statement_receipt(world, scene, recipient='lens', statement='您如何看渡口？')
    direct = safe_authoring_handoff(world,scene,trusted_events=events,player_statement_receipts=[receipt])
    assert words['lens'] in direct and words['mara'] not in direct and words['painter'] not in direct


def test_world_change_and_actor_notification_are_separate_across_reopen_and_undo(campaign):
    def record(kind, turn, **delta):
        campaign.store.append(kernel_event(kind, day=2, turn=turn, scene='s1', summary=kind, deltas=delta))
    def statements(engine):
        brief = native_authoring_context(engine.world, _build_scene(engine))
        return {p['id']: e['value'] for p in brief['present_people'] for e in p['speakable_evidence']
                if e['fact_key'] == KEY}
    record('fact_asserted', 1, subject='stonebridge_market', predicate='ferry', value='北岸复航', secrecy='public')
    campaign.world = project(campaign.registry, campaign.store.iter_events())
    assert statements(campaign) == {'mara':'应从东岸出发', 'lens':'听说停航'}
    record('knowledge_set', 2, knower='mara', fact_key=KEY, value='北岸复航', via='当面收到的新班次单')
    folder = campaign.store.jsonl_path.parent
    campaign.store.close()
    reopened = build_engine(folder, embedder=None)
    try:
        assert statements(reopened) == {'mara':'北岸复航','lens':'听说停航'}
        reopened.store.retract_from_turn(2)
        reopened.world = project(reopened.registry, reopened.store.iter_events())
        assert statements(reopened) == {'mara':'应从东岸出发','lens':'听说停航'}
        assert reopened.world['systems']['ontology'].value_at('stonebridge_market','ferry',2) == '北岸复航'
    finally:
        reopened.store.close()
