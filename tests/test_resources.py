import pytest
from app.engine import build_engine, rewind
from engine.oracle import load_pack_manifest
from app.gui import _INDEX, _JS, build_player_state
from app.play import _build_scene
from app.play import _print_turn_status
from app.player_resources import player_resource_outcome
from engine.store import RevisionConflict
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider
from loop.resources import resolve_resource_intent, resource_intent_errors
from loop.turn import run_turn, TurnRejected
from loop.strategy import AuthorStrategy
from loop.bootstrap import GenesisError, bootstrap_world
from systems.resources import ResourceConfigurationError, validate_resource_configuration


@pytest.fixture
def game(tmp_path,monkeypatch):
    e=build_engine(tmp_path/'resources')
    for name in ['digest_fleet','run_director','run_cascade','run_catchup','run_lore','run_density','_run_demote_on_leave']:
        monkeypatch.setattr('loop.turn.'+name,lambda *a,**kw:[])
    data=[('entity_created',{'id':'hero','etype':'Person','tier':'tracked'}),
          ('resources_configured',{'subject':'hero','resources':{'oxygen':{'initial':12,'type':'integer','min':0}}})]
    e.store.append_many([kernel_event(t,day=1,scene='room',turn=0,summary='setup',deltas=d) for t,d in data])
    e.world=project(e.registry,e.store.iter_events())
    yield e
    e.store.close()


@pytest.mark.parametrize('amount,expected,status',[(3,9,'spent'),(100,12,'insufficient')])
def test_resolve_before_narrating_and_undo_whole_action(game,amount,expected,status):
    p=FakeLLMProvider(json_responses=[{'op':'spend','resource':'oxygen','amount':amount}, {'narration':'阀门回到原位。'}])
    answer=run_turn(game.registry,game.store,game.world,_build_scene(game),'使用氧气',provider=p,strategy=AuthorStrategy())
    assert answer.world['systems']['ontology'].value_at('hero','oxygen',1)==expected
    assert answer.world['systems']['resources']['last_resolution']['outcome']==status
    assert len({e['turn'] for e in answer.events})==1
    game.world=answer.world
    rewind(game,answer.events[0]['turn'])
    assert game.world['systems']['ontology'].value_at('hero','oxygen',1)==12


def test_narrator_cannot_double_debit_or_debit_a_failed_purchase(game):
    p=FakeLLMProvider(json_responses=[{'op':'spend','resource':'oxygen','amount':100},
        {'narration':'我花掉三单位。','facts':[{'subject':'hero','predicate':'oxygen','value':9}]}])
    before=list(game.store.iter_events())
    with pytest.raises(TurnRejected):
        run_turn(game.registry,game.store,game.world,_build_scene(game),'使用100单位氧气',
                 provider=p,strategy=AuthorStrategy(),max_repairs=0)
    assert list(game.store.iter_events())==before


def test_prose_failure_does_not_charge_prepared_cost(game):
    class FailsAfterIntent(FakeLLMProvider):
        def complete_messages(self,*args,**kwargs):
            if self.calls: raise ConnectionError('narration failed')
            return super().complete_messages(*args,**kwargs)
    p=FailsAfterIntent(json_responses=[{'op':'spend','resource':'oxygen','amount':3}])
    before=list(game.store.iter_events())
    with pytest.raises(ConnectionError):
        run_turn(game.registry,game.store,game.world,_build_scene(game),'使用3单位氧气',provider=p,strategy=AuthorStrategy())
    assert list(game.store.iter_events())==before


def test_explicit_wait_is_repaired_to_absolute_target(game):
    p=FakeLLMProvider(json_responses=[{'op':'none','wait_until':{'day':2,'band':1}},
        {'narration':'你休息到约定的时间。','clock':[{'advance':False,'days':0,'bands':0,'reason':'wrong'}]},
        {'clock':[{'advance':True,'days':1,'bands':1,'reason':'次日中午'}]}])
    answer=run_turn(game.registry,game.store,game.world,_build_scene(game),'等到明天中午',
        provider=p,strategy=AuthorStrategy())
    assert (answer.world['meta']['day'],answer.world['meta']['band'])==(2,1)
    assert answer.repair_attempts==1


def test_pure_resource_resolver_preserves_other_balances_and_numeric_boundaries():
    resources = {"coins": 5, "fuel": 1.5}
    rules = {
        "coins": {"resource": True, "type": "integer", "min": 0},
        "fuel": {"resource": True, "type": "number", "min": 0.0},
    }
    event, expected, prompt = resolve_resource_intent(
        {"op": "spend", "resource": "coins", "amount": 5, "wait_until": None},
        resources, rules, subject="hero", day=1, band=0, scene="room", turn=1,
    )
    assert event["deltas"]["outcome"] == "spent"
    assert event["deltas"]["before"] == resources
    assert event["deltas"]["after"] == {"coins": 0, "fuel": 1.5}
    assert expected == {("hero", "coins"): 0, ("hero", "fuel"): 1.5}
    assert "spent" in prompt and "insufficient" in prompt

    event, expected, _ = resolve_resource_intent(
        {"op": "spend", "resource": "fuel", "amount": 1.5000001, "wait_until": None},
        resources, rules, subject="hero", day=1, band=0, scene="room", turn=1,
    )
    assert event["deltas"]["outcome"] == "insufficient"
    assert event["deltas"]["after"] == resources
    assert expected == {("hero", "coins"): 5, ("hero", "fuel"): 1.5}

    zero, _, _ = resolve_resource_intent(
        {"op": "spend", "resource": "coins", "amount": 0, "wait_until": None},
        resources, rules, subject="hero", day=1, band=0, scene="room", turn=1,
    )
    assert zero["deltas"]["outcome"] == "spent"
    assert resource_intent_errors(
        {"op": "spend", "resource": "coins", "amount": 0.5, "wait_until": None},
        resources, rules, day=1, band=0,
    ) == ["amount must be an integer for this resource"]


def test_resource_action_id_cold_retry_restores_resolution_without_reparsing(game):
    start_world = game.world
    start_scene = _build_scene(game)
    base_revision = game.store.revision
    first = run_turn(
        game.registry, game.store, start_world, start_scene, "使用3单位氧气",
        provider=FakeLLMProvider(json_responses=[
            {"op": "spend", "resource": "oxygen", "amount": 3, "wait_until": None},
            {"narration": "阀门回到原位。"},
        ]),
        strategy=AuthorStrategy(), action_id="resource-response-loss", expected_revision=base_revision,
    )
    assert any(event["type"] == "resources_resolved" for event in first.events)
    campaign = game.store.db_path.parent
    game.store.close()
    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)

    class MustNotRun:
        def produce(self, *args, **kwargs):
            raise AssertionError("duplicate resource action reached strategy")

    class MustNotParse:
        def complete_messages(self, *args, **kwargs):
            raise AssertionError("duplicate resource action reparsed intent")

    try:
        before_events = list(reopened.store.iter_events(include_retracted=True))
        before_revision = reopened.store.revision
        before_jsonl = reopened.store.jsonl_path.read_bytes()
        retry = run_turn(
            reopened.registry, reopened.store, start_world, start_scene, "使用3单位氧气",
            provider=MustNotParse(), strategy=MustNotRun(),
            action_id="resource-response-loss", expected_revision=base_revision,
        )
        assert retry.receipt["duplicate"] is True
        assert retry.events == first.events
        assert retry.world["systems"]["ontology"].value_at("hero", "oxygen", 1) == 9
        assert list(reopened.store.iter_events(include_retracted=True)) == before_events
        assert reopened.store.revision == before_revision
        assert reopened.store.jsonl_path.read_bytes() == before_jsonl
    finally:
        reopened.store.close()


def test_stale_resource_turn_rejects_before_intent_parse_or_write(game):
    other = build_engine(game.store.db_path.parent, provider=FakeLLMProvider(), embedder=None)
    base_revision = game.store.revision
    try:
        first = run_turn(
            game.registry, game.store, game.world, _build_scene(game), "使用3单位氧气",
            provider=FakeLLMProvider(json_responses=[
                {"op": "spend", "resource": "oxygen", "amount": 3, "wait_until": None},
                {"narration": "阀门回到原位。"},
            ]),
            strategy=AuthorStrategy(), action_id="resource-fresh", expected_revision=base_revision,
        )
        game.world = first.world
        before_events = list(other.store.iter_events(include_retracted=True))
        before_revision = other.store.revision
        before_jsonl = other.store.jsonl_path.read_bytes()

        class MustNotParse:
            def complete_messages(self, *args, **kwargs):
                raise AssertionError("stale resource action parsed intent")

        with pytest.raises(RevisionConflict, match="refresh the world"):
            run_turn(
                other.registry, other.store, other.world, _build_scene(other), "使用3单位氧气",
                provider=MustNotParse(), strategy=AuthorStrategy(),
                action_id="resource-stale", expected_revision=base_revision,
            )
        assert list(other.store.iter_events(include_retracted=True)) == before_events
        assert other.store.revision == before_revision
        assert other.store.jsonl_path.read_bytes() == before_jsonl
    finally:
        other.store.close()


@pytest.mark.parametrize(
    ("amount", "outcome", "balance", "verdict"),
    [(3, "spent", 9, "已扣除"), (100, "insufficient", 12, "余额不足，未扣除")],
)
def test_player_resource_outcome_is_canonical_replayable_and_shared_by_cli_gui(
        game, amount, outcome, balance, verdict):
    """Player surfaces report the stored adjudication, not narrator prose."""
    provider = FakeLLMProvider(json_responses=[
        {"op": "spend", "resource": "oxygen", "amount": amount},
        {"narration": "不以正文作为余额结论。"},
    ])
    result = run_turn(
        game.registry, game.store, game.world, _build_scene(game), "使用氧气",
        provider=provider, strategy=AuthorStrategy(), action_id=f"resource-{amount}",
    )
    game.world = result.world
    expected = {
        "turn": 1,
        "outcome": outcome,
        "resource": "oxygen",
        "requested_amount": amount,
        "balance": balance,
    }
    resolution_event = next(event for event in result.events if event["type"] == "resources_resolved")
    assert "turn" not in resolution_event["deltas"]
    assert player_resource_outcome(game.world, "hero") == expected
    assert build_player_state(game)["resource_outcome"] == expected
    assert 'id="resource-outcome"' in _INDEX
    assert "renderResourceOutcome(s.resource_outcome)" in _JS

    output = []
    _print_turn_status(result, output.append, protagonist="hero")
    assert any(line.startswith("[资源]") and verdict in line and "oxygen" in line for line in output)

    campaign = game.store.db_path.parent
    game.store.close()
    reopened = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        assert player_resource_outcome(reopened.world, "hero") == expected
        before_rewind = list(reopened.store.iter_events(include_retracted=True))
        rewind(reopened, 1)
        assert player_resource_outcome(reopened.world, "hero") is None
        assert any(event.get("retracted") for event in reopened.store.iter_events(include_retracted=True))
        assert len(list(reopened.store.iter_events(include_retracted=True))) == len(before_rewind)
    finally:
        reopened.store.close()


def test_player_resource_outcome_rejects_nonpublic_or_malformed_resolution():
    world = {"systems": {"resources": {"last_resolution": {
        "subject": "other", "outcome": "spent", "resource": "coins",
        "requested_amount": 1, "after": {"coins": 9}, "turn": 1,
    }}}}
    assert player_resource_outcome(world, "hero") is None
    world["systems"]["resources"]["last_resolution"] = {
        "subject": "hero", "outcome": "spent", "resource": "coins",
        "requested_amount": float("nan"), "after": {"coins": 9}, "turn": 1,
    }
    assert player_resource_outcome(world, "hero") is None
    world["systems"]["resources"]["last_resolution"] = {
        "subject": "hero", "outcome": "none", "resource": None,
        "requested_amount": 0, "after": {}, "turn": 1,
    }
    assert player_resource_outcome(world, "hero") is None


@pytest.mark.parametrize("resources", [
    None,
    [],
    {},
    {"coins": []},
    {"coins": {"initial": 1, "type": "integer"}},
    {"coins": {"initial": True, "type": "integer", "min": 0}},
    {"coins": {"initial": float("nan"), "type": "number", "min": 0}},
    {"coins": {"initial": 1, "type": "decimal", "min": 0}},
    {"coins": {"initial": 1, "type": "integer", "min": 0.0}},
    {"coins": {"initial": 1, "type": "integer", "min": 2}},
    {"coins": {"initial": 1, "type": "integer", "min": 0, "max": 0}},
    {"coins": {"initial": 1, "type": "integer", "min": 0, "extra": True}},
])
def test_resource_configuration_rejects_malformed_numeric_schema(resources):
    with pytest.raises(ResourceConfigurationError):
        validate_resource_configuration(resources)


def test_resource_configuration_accepts_existing_pack_shape_and_replays(tmp_path):
    engine = build_engine(tmp_path / "resource-replay", embedder=None)
    resources = {
        "coins": {"initial": 12, "type": "integer", "min": 0, "label": "随身钱币"},
        "fuel": {"initial": 4.5, "type": "number", "min": 0.0, "max": 8.0, "label": "燃料"},
    }
    try:
        engine.store.append_many([
            kernel_event("entity_created", day=1, scene="s1", turn=0,
                         summary="hero", deltas={"id": "hero", "etype": "Person", "tier": "tracked"}),
            kernel_event("resources_configured", day=1, scene="s1", turn=0,
                         summary="resources", deltas={"subject": "hero", "resources": resources}),
        ], preflight=lambda history: project(engine.registry, history))
        engine.world = project(engine.registry, engine.store.iter_events())
        hero = engine.world["systems"]["ontology"].get_entity("hero")
        assert hero.attrs["fact_rules"]["coins"] == {"type": "integer", "min": 0,
                                                        "label": "随身钱币", "resource": True}
        assert engine.world["systems"]["ontology"].value_at("hero", "fuel", 1) == 4.5
        campaign = engine.store.db_path.parent
        engine.store.close()
        reopened = build_engine(campaign, embedder=None)
        try:
            hero = reopened.world["systems"]["ontology"].get_entity("hero")
            assert hero.attrs["fact_rules"]["fuel"] == {"type": "number", "min": 0.0,
                                                           "max": 8.0, "label": "燃料", "resource": True}
            assert reopened.world["systems"]["ontology"].value_at("hero", "coins", 1) == 12
        finally:
            reopened.store.close()
    finally:
        if engine.store._conn is not None:
            engine.store.close()


@pytest.mark.parametrize("flavor", ["classic", "isekai"])
def test_shipped_resource_pack_shape_is_schema_compatible(flavor):
    resources = load_pack_manifest(flavor)["resources"]
    assert validate_resource_configuration(resources) == resources


def test_invalid_opt_in_resource_pack_blocks_genesis_before_any_publish(tmp_path, monkeypatch):
    engine = build_engine(tmp_path / "bad-resource-pack", provider=FakeLLMProvider(), embedder=None)
    before_events = list(engine.store.iter_events(include_retracted=True))
    before_revision = engine.store.revision
    before_jsonl = engine.store.jsonl_path.read_bytes() if engine.store.jsonl_path.exists() else None
    monkeypatch.setenv("RPG_RESOURCE_RULES", "1")
    monkeypatch.setattr("loop.bootstrap.load_pack_manifest", lambda _flavor: {
        "resources": {"coins": {"initial": float("inf"), "type": "number", "min": 0}},
    })
    try:
        with pytest.raises(GenesisError, match="invalid opt-in resource configuration"):
            bootstrap_world(engine, "不应开始")
        assert engine.provider.calls == []
        assert list(engine.store.iter_events(include_retracted=True)) == before_events
        assert engine.store.revision == before_revision
        after_jsonl = engine.store.jsonl_path.read_bytes() if engine.store.jsonl_path.exists() else None
        assert after_jsonl == before_jsonl
    finally:
        engine.store.close()
