"""Regression cases for genesis, replayable variety and long-scene memory."""
import shutil
import pytest

from app.engine import build_engine
from kernel.events import kernel_event
from kernel.projection import project
from loop.bootstrap import bootstrap_world, reroll_all, GenesisError
from loop.variation import prepare_variation
from loop.fleet import digest_fleet
from llm.provider import FakeLLMProvider
from context.assembler import assemble_context


def ev(kind, **data):
    return kernel_event(kind, day=1, scene='room', turn=0, summary='test', deltas=data)


class FailingRealProvider:
    """Not an offline fixture: verifies the production failure path."""
    def supports_tools(self): return False
    def complete(self, *a, **kw): raise ConnectionError('transport failed')
    def complete_messages(self, *a, **kw): raise ConnectionError('transport failed')


@pytest.mark.parametrize('reroll', [False, True])
def test_failed_real_genesis_leaves_original_save_intact(tmp_path, reroll):
    e = build_engine(tmp_path/'world', provider=FailingRealProvider())
    if reroll:
        e.store.append_many([ev('campaign_seeded', campaign_seed=42, flavor='isekai'),
                             ev('entity_created', id='survivor', etype='Person')])
        e.world = project(e.registry, e.store.iter_events())
    before = list(e.store.iter_events(include_retracted=True))
    revision = e.store.revision
    with pytest.raises(GenesisError):
        if reroll:
            reroll_all(e, {'_state':{'pitch':'冒险','attempts':{}}})
        else:
            bootstrap_world(e, '冒险')
    assert list(e.store.iter_events(include_retracted=True)) == before
    assert e.store.revision == revision
    e.store.close()


@pytest.mark.parametrize('flavor', ['classic','isekai'])
def test_variation_replays_and_avoids_recent_repetition(tmp_path, flavor):
    e = build_engine(tmp_path/'variety')
    e.store.append(ev('campaign_seeded', campaign_seed=1234, flavor=flavor))
    samples = []
    for turn in range(1, 25):
        world = project(e.registry, e.store.iter_events())
        scene = {'day':1, 'location':'room', 'id':'room'}
        a = prepare_variation(e.registry, world, scene, turn)
        b = prepare_variation(e.registry, world, scene, turn)
        assert a['deltas'] == b['deltas']
        assert a['deltas']['id'] not in samples[-2:]
        samples.append(a['deltas']['id'])
        e.store.append(a)
    assert len(set(samples)) >= 5
    e.store.retract_from_turn(24)
    retry = prepare_variation(e.registry, project(e.registry,e.store.iter_events()), scene, 24)
    assert retry['deltas'] == a['deltas']
    e.store.close()


def test_campaign_seed_survives_directory_rename(tmp_path):
    e = build_engine(tmp_path/'before')
    e.store.append(ev('campaign_seeded', campaign_seed=99, flavor='isekai'))
    e.store.close()
    shutil.copytree(tmp_path/'before', tmp_path/'after')
    reopened = build_engine(tmp_path/'after')
    assert reopened.campaign_seed == 99
    assert reopened.world['meta']['flavor'] == 'isekai'
    reopened.store.close()


def test_long_single_scene_is_chunked_summarized_and_raw_context_bounded(tmp_path):
    e = build_engine(tmp_path/'memory')
    provider = FakeLLMProvider(json_responses=[{'summary':'保留了约定和人物关系'}]*100)
    for i in range(60):
        e.store.append(ev('narration_recorded', scene='room', text=f'RAW_{i:03d}_' + '甲'*500))
        e.world = project(e.registry,e.store.iter_events())
        digest_fleet(e.registry,e.store,[],e.world,provider=provider,recap_provider=provider)
    e.world = project(e.registry,e.store.iter_events())
    ns = e.world['systems']['narrative']
    assert len(ns['scenes']) == 10
    assert all(len(b['raw']) <= 6 for b in ns['scenes'])
    assert ns['summarized_through_index'] == 6
    assert ns['super_summary']
    assert len([x for x in e.store.iter_events() if x['type']=='recap_recompressed']) == 1
    context = assemble_context(e.registry,e.world,{'day':1,'location':'room'})
    assert 'RAW_000_' not in context and 'RAW_059_' in context
    assert context.count('RAW_') == 12
    assert len(context) < 8000
    # The verbatim archive remains intact; only the prompt is compressed.
    assert len([x for x in e.store.iter_events() if x['type']=='narration_recorded']) == 60
    e.store.close()


def test_pack_fact_rules_reject_overdraw_and_immutable_rewrite(tmp_path):
    e = build_engine(tmp_path/'rules')
    e.store.append_many([
        ev('entity_created', id='pilot', etype='Person', attrs={'fact_rules':{
            'oxygen':{'type':'integer','min':0,'max':100},
            'callsign':{'type':'string','immutable':True}}}),
        ev('fact_asserted', subject='pilot', predicate='oxygen', value=50),
        ev('fact_asserted', subject='pilot', predicate='callsign', value='Alpha'),
    ])
    before = list(e.store.iter_events())
    for pred, val in [('oxygen',-1),('oxygen','full'),('oxygen',101),('callsign','Beta')]:
        with pytest.raises(ValueError):
            e.store.append(ev('fact_asserted',subject='pilot',predicate=pred,value=val))
        assert list(e.store.iter_events()) == before
    e.store.append(ev('fact_asserted',subject='pilot',predicate='oxygen',value=40))
    e.store.close()
