"""Only an escaping watched provider failure is normalized at genesis."""
import pickle

import pytest

from app.engine import build_engine
from kernel.events import kernel_event
from kernel.projection import project
from llm.generation_guard import collect_failures
from loop.bootstrap import GenesisError, _atomic_genesis


class Provider:
    def __init__(self, error, *, offline=False):
        self.error = error
        self.is_offline = offline

    def complete_messages(self, *args, **kwargs):
        raise self.error


def seed_event(name):
    return kernel_event('entity_created', day=1, scene='genesis', turn=0,
                        summary='fixture', deltas={'id': name, 'etype': 'Person'})


def snapshot(engine):
    return (list(engine.store.iter_events(include_retracted=True)),
            engine.store.revision, pickle.dumps(engine.world))


@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('cascade', [False, True])
def test_escaping_transport_error_preserves_cause_and_all_original_state(tmp_path, populated, cascade):
    error = ConnectionError('diagnostic transport failure')
    engine = build_engine(tmp_path / 'world', provider=Provider(error))
    if cascade:
        engine.cascade_provider = Provider(error)
    if populated:
        engine.store.append(seed_event('original'))
        engine.world = project(engine.registry, engine.store.iter_events())
    before = snapshot(engine)
    original_world = engine.world

    @_atomic_genesis
    def fail(staged):
        staged.store.append(seed_event('unpublished'))
        provider = staged.cascade_provider if cascade else staged.provider
        provider.complete_messages([])

    with pytest.raises(GenesisError) as caught:
        fail(engine)
    assert caught.value.__cause__ is error
    assert 'complete_messages' in str(caught.value)
    assert snapshot(engine) == before
    assert engine.world is original_world
    engine.store.close()


@pytest.mark.parametrize('error', [KeyboardInterrupt(), SystemExit(2), GeneratorExit()])
def test_control_flow_is_never_normalized(tmp_path, error):
    engine = build_engine(tmp_path / 'world', provider=Provider(error))
    before = snapshot(engine)

    @_atomic_genesis
    def fail(staged):
        staged.store.append(seed_event('unpublished'))
        staged.provider.complete_messages([])

    with pytest.raises(type(error)) as caught:
        fail(engine)
    assert caught.value is error
    assert snapshot(engine) == before
    engine.store.close()


@pytest.mark.parametrize('recovered_failure', [False, True])
def test_host_bug_after_recovered_failure_keeps_original_type(tmp_path, recovered_failure):
    engine = build_engine(tmp_path / 'world', provider=Provider(ConnectionError('earlier')))
    bug = TypeError('host programming error')
    before = snapshot(engine)

    @_atomic_genesis
    def fail(staged):
        staged.store.append(seed_event('unpublished'))
        if recovered_failure:
            try:
                staged.provider.complete_messages([])
            except ConnectionError:
                pass
        raise bug

    with pytest.raises(TypeError) as caught:
        fail(engine)
    assert caught.value is bug
    assert snapshot(engine) == before
    engine.store.close()


def test_offline_exception_and_recovered_fallback_behavior_are_unchanged(tmp_path):
    error = ConnectionError('offline fixture')
    engine = build_engine(tmp_path / 'world', provider=Provider(error, offline=True))

    @_atomic_genesis
    def fail(staged):
        staged.provider.complete_messages([])

    with pytest.raises(ConnectionError) as caught:
        fail(engine)
    assert caught.value is error
    assert engine.store.revision == 0

    @_atomic_genesis
    def fallback(staged):
        try:
            staged.provider.complete_messages([])
        except ConnectionError:
            pass
        staged.store.append(seed_event('fallback'))
        return 'offline result'

    assert fallback(engine) == 'offline result'
    assert engine.store.revision == 1
    assert len(list(engine.store.iter_events())) == 1
    engine.store.close()


def test_collector_restores_outer_context_after_failure(tmp_path):
    error = ConnectionError('inner')
    engine = build_engine(tmp_path / 'world', provider=Provider(error))

    @_atomic_genesis
    def fail(staged):
        staged.provider.complete_messages([])

    with collect_failures() as outer:
        with pytest.raises(GenesisError):
            fail(engine)
        assert outer == []
    engine.store.close()


def test_recovered_real_failure_still_prevents_fallback_publication(tmp_path):
    engine = build_engine(tmp_path / 'world', provider=Provider(ConnectionError('real failure')))
    before = snapshot(engine)

    @_atomic_genesis
    def fallback(staged):
        try:
            staged.provider.complete_messages([])
        except ConnectionError:
            pass
        staged.store.append(seed_event('must-not-publish'))

    with pytest.raises(GenesisError):
        fallback(engine)
    assert snapshot(engine) == before
    engine.store.close()


def test_publish_error_is_not_misreported_as_generation_failure(tmp_path, monkeypatch):
    from engine.store import EventBatch
    engine = build_engine(tmp_path / 'world', provider=Provider(ConnectionError('unused')))
    error = OSError('storage publish failure')
    before = snapshot(engine)

    def fail_publish(self, **kwargs):
        raise error

    monkeypatch.setattr(EventBatch, 'publish', fail_publish)

    @_atomic_genesis
    def generate(staged):
        staged.store.append(seed_event('generated'))

    with pytest.raises(OSError) as caught:
        generate(engine)
    assert caught.value is error
    assert snapshot(engine) == before
    engine.store.close()
