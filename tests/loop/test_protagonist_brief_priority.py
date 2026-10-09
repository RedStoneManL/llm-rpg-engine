"""Offline regressions for player-brief precedence in real protagonist generation."""

import copy
import inspect
import json

import pytest

from engine.oracle import Oracle, scene_seed
from loop.genesis.cast import gen_protagonist


# Recorded responses test the host/prompt contract, not a live model's obedience.
_AUTHORED = {
    "name": "模型姓名",
    "origin": "模型填写的出身。",
    "goal": "模型填写的目标",
    "objective": "模型填写的当前行动",
}
_STUB = {
    "name": "无名旅者",
    "origin": "来历不明的旅人，只知道自己踏上了这条路。",
    "goal": "找到属于自己的答案",
    "objective": "在起始小镇打听线索，寻找下一步的方向",
}
_EXPECTED_SEEDS = [
    "庙祝或低阶神职人员",
    "一桩牵连到自己的命案",
    "养着一只通人性的小动物",
]
_NEXT_RANDOM = 0.8517991030702642


class _ScriptedProvider:
    """Offline responses plus snapshots of the actual complete_messages calls."""

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def complete_messages(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response, ensure_ascii=False) if isinstance(response, dict) else response


class _RecordingOracle(Oracle):
    def __init__(self):
        super().__init__(scene_seed(
            _SOURCE_FIXTURE["campaign_seed"], "genesis:protagonist", _SOURCE_FIXTURE["attempt"]
        ))
        self.random_calls = 0
        self.draws = []

    def random(self):
        self.random_calls += 1
        return super().random()

    def draw(self, entries):
        selected = super().draw(entries)
        self.draws.append(selected["name"])
        return selected


def _requirements(messages):
    user = messages[1]["content"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert user.startswith("【玩家已确认的设定与主角预设·优先约束】\n")
    return json.loads(user.splitlines()[1])


@pytest.fixture
def source_fixture():
    # A self-contained copy of the original recorded input, requiring no local
    # reproduction directory, network connection, credentials, or API access.
    return copy.deepcopy(_SOURCE_FIXTURE)


def test_original_fixture_reaches_real_generator_without_changing_seed_draws(source_fixture):
    provider = _ScriptedProvider(_AUTHORED)
    oracle = _RecordingOracle()
    before = copy.deepcopy(source_fixture)
    events, authored = gen_protagonist(
        provider, oracle, source_fixture["frame"], source_fixture["local_map"],
        provided=source_fixture["provided"], flavor=source_fixture["flavor"],
    )
    assert events == []
    assert authored == _AUTHORED  # Scripted response, not a model-quality claim.
    assert source_fixture == before
    assert len(provider.calls) == 1
    messages = provider.calls[0]
    assert _requirements(messages) == {
        "resolved_player_premise": source_fixture["player_pitch"],
        "provided_protagonist": {},
    }
    system, user = (message["content"] for message in messages)
    assert "玩家已确认的设定和非空预设主角字段优先于随机种子" in system
    assert "随机种子只是可选灵感" in system
    assert "不是已发生的剧情、必须成立的身世或强制任务" in system
    assert "冲突的种子可以舍弃或改写，不必全部采用" in system
    assert "不得用世界生成结果或随机种子覆盖" in user
    seed_heading = "【主角随机种子·低优先级可选灵感"
    assert user.index("resolved_player_premise") < user.index(seed_heading)
    assert "冲突时舍弃，不是既定事实" in user
    for seed in _EXPECTED_SEEDS:
        assert seed in user  # No reroll, filtering, or rewriting the Oracle output.
    assert "祈山百工镇" in user and "槐荫井坪" in user and "百家修补铺" in user
    assert oracle.random_calls == 1
    assert oracle.draws == _EXPECTED_SEEDS
    assert oracle.random() == _NEXT_RANDOM


def test_resolved_premise_is_json_encoded_verbatim():
    premise = '  外地修补匠说：“当天办完。”\n只帮邻里，保留"引号"和\\路径。  '
    provider = _ScriptedProvider(_AUTHORED)
    gen_protagonist(provider, _RecordingOracle(), {"genre": premise}, {})
    assert _requirements(provider.calls[0])["resolved_player_premise"] == premise


@pytest.mark.parametrize("field", ["name", "origin", "goal", "objective"])
def test_partial_known_fields_reach_prompt_and_win_host_overlay(field, source_fixture):
    value = '玩家明确的' + field + '：保留"引号"\n与第二行。'
    provided = {field: value}
    before = copy.deepcopy(provided)
    provider = _ScriptedProvider(_AUTHORED)
    events, authored = gen_protagonist(
        provider, _RecordingOracle(), source_fixture["frame"], source_fixture["local_map"],
        provided=provided,
    )
    assert _requirements(provider.calls[0])["provided_protagonist"] == provided
    assert events == []
    assert authored == {**_AUTHORED, field: value}
    assert provided == before


def test_multiple_partial_fields_retain_prompt_text_and_existing_trim_semantics():
    provided = {"name": "  林一  ", "origin": " 暂住镇上的外地修补匠。\n", "goal": " ",
                "objective": None, "unrecognized": "UNKNOWN_KEY_MUST_NOT_LEAK"}
    provider = _ScriptedProvider(_AUTHORED)
    _, authored = gen_protagonist(provider, _RecordingOracle(), {}, {}, provided=provided)
    assert _requirements(provider.calls[0])["provided_protagonist"] == {
        "name": provided["name"], "origin": provided["origin"],
    }
    assert authored == {**_AUTHORED, "name": "林一", "origin": "暂住镇上的外地修补匠。"}
    assert "UNKNOWN_KEY_MUST_NOT_LEAK" not in provider.calls[0][1]["content"]
    assert set(authored) == set(_AUTHORED)


@pytest.mark.parametrize("empty", [None, "", " \n\t ", 0, False, [], {}])
def test_empty_or_nonstring_provided_fields_are_not_player_constraints(empty):
    provider = _ScriptedProvider(_AUTHORED)
    provided = {field: copy.deepcopy(empty) for field in _AUTHORED}
    _, authored = gen_protagonist(provider, _RecordingOracle(), {}, {}, provided=provided)
    assert _requirements(provider.calls[0])["provided_protagonist"] == {}
    assert authored == _AUTHORED


@pytest.mark.parametrize("frame", [{}, {"genre": None}, {"genre": ""}, {"genre": " \n\t"},
                                  {"genre": 42}, {"genre": False}, {"genre": []}, {"genre": {}}])
def test_missing_empty_or_nonstring_brief_uses_empty_compatibility_value(frame):
    provider = _ScriptedProvider(_AUTHORED)
    events, authored = gen_protagonist(provider, _RecordingOracle(), frame, {})
    assert _requirements(provider.calls[0]) == {
        "resolved_player_premise": "", "provided_protagonist": {},
    }
    assert events == [] and authored == _AUTHORED
    user = provider.calls[0][1]["content"]
    assert "世界名称：未名之地" in user
    assert "世界基调：冒险" in user
    assert "核心冲突：未知冲突" in user
    assert "起始小镇：town_0，起始场所：起始场所" in user


def test_omitted_none_and_empty_provided_keep_default_classic_behavior(source_fixture):
    runs = []
    for kwargs in ({}, {"provided": None}, {"provided": {}}, {"provided": {}, "flavor": "classic"}):
        provider = _ScriptedProvider(_AUTHORED)
        oracle = _RecordingOracle()
        result = gen_protagonist(provider, oracle, source_fixture["frame"], source_fixture["local_map"],
                                 **kwargs)
        runs.append((result, provider.calls, oracle.draws, oracle.random()))
    assert all(run == runs[0] for run in runs)
    assert runs[0][2:] == (_EXPECTED_SEEDS, _NEXT_RANDOM)


def test_fully_provided_skips_llm_and_three_seed_draws_but_keeps_entry_random():
    provided = {field: "  玩家指定" + field + "  " for field in _AUTHORED}
    provided["ignored"] = "not an authored field"
    provider = _ScriptedProvider()
    oracle = _RecordingOracle()
    control = Oracle(oracle.seed)
    control.random()
    events, authored = gen_protagonist(provider, oracle, {}, {}, provided=provided)
    assert events == []
    assert authored == {field: provided[field].strip() for field in _AUTHORED}
    assert provider.calls == []
    assert oracle.draws == []
    assert oracle.random_calls == 1
    assert oracle.random() == control.random()


@pytest.mark.parametrize("failure", ["no_provider", "exception", "invalid_json", "missing_fields"])
def test_failure_fallback_remains_deterministic_and_keeps_provided_fields(failure, source_fixture):
    results = []
    for _ in range(2):
        if failure == "no_provider":
            provider = None
        elif failure == "exception":
            provider = _ScriptedProvider(RuntimeError("offline scripted failure"))
        elif failure == "invalid_json":
            provider = _ScriptedProvider("not JSON", "not JSON", "not JSON")
        else:
            provider = _ScriptedProvider({"name": "少字段"}, {"name": "少字段"}, {"name": "少字段"})
        oracle = _RecordingOracle()
        events, authored = gen_protagonist(
            provider, oracle, source_fixture["frame"], source_fixture["local_map"],
            provided={"origin": "外地修补匠，只解决当天的小事。"},
        )
        assert events == []
        assert authored == {**_STUB, "origin": "外地修补匠，只解决当天的小事。"}
        assert oracle.random_calls == 1 and oracle.draws == _EXPECTED_SEEDS
        assert oracle.random() == _NEXT_RANDOM
        if provider is not None:
            assert len(provider.calls) == (1 if failure == "exception" else 3)
            assert _requirements(provider.calls[0])["resolved_player_premise"] == source_fixture["player_pitch"]
        results.append((events, authored))
    assert results[0] == results[1]


def test_structured_repairs_retain_original_brief_and_priority(source_fixture):
    malformed = {"name": "模型姓名"}
    provider = _ScriptedProvider(malformed, {**_AUTHORED, "objective": " "}, _AUTHORED)
    oracle = _RecordingOracle()
    provided = {"origin": "暂住镇上的外地修补匠。", "goal": "帮邻里解决当天的小事"}
    events, authored = gen_protagonist(
        provider, oracle, source_fixture["frame"], source_fixture["local_map"], provided=provided,
    )
    assert events == [] and authored == {**_AUTHORED, **provided}
    assert len(provider.calls) == 3
    original = provider.calls[0]
    for index, messages in enumerate(provider.calls):
        assert messages[:2] == original
        assert len(messages) == 2 + 2 * index
        assert _requirements(messages) == {
            "resolved_player_premise": source_fixture["player_pitch"],
            "provided_protagonist": provided,
        }
        if index:
            assert messages[-2]["role"] == "assistant"
            assert messages[-1]["role"] == "user"
            assert '"objective"' in messages[-1]["content"]
    assert oracle.random_calls == 1 and oracle.draws == _EXPECTED_SEEDS
    assert oracle.random() == _NEXT_RANDOM


@pytest.mark.parametrize("scenario", ["blueprint", "blueprint_and_session_zero", "session_zero"])
def test_canonical_blueprint_and_session_zero_reach_bootstrap_without_raw_pitch_override(
    scenario, tmp_path, monkeypatch,
):
    from app.engine import build_engine, resolve_genesis_spec
    from llm.provider import FakeLLMProvider
    import loop.bootstrap as bootstrap

    raw_pitch = "RAW_PITCH_SUPERSEDED：海盗争夺天下，复仇进行十年。"
    canonical = "已确认：内陆小镇的外地修补匠，当天完成一件邻里小事。"
    session_name = "玩家在零幕确认的名字"
    blueprint_path = None
    interactive = scenario != "blueprint"
    if scenario != "session_zero":
        blueprint_path = tmp_path / "genesis.json"
        blueprint_path.write_text(json.dumps({"world_premise": {"genre": canonical}},
                                             ensure_ascii=False), encoding="utf-8")
    prompts = []
    spec = resolve_genesis_spec(
        None, pitch="" if scenario == "session_zero" else raw_pitch,
        blueprint_path=blueprint_path, interactive=interactive,
        inputs=[canonical, session_name] if scenario == "session_zero" else [session_name],
        out=prompts.append,
    )
    assert spec["world_premise"]["genre"] == canonical
    if interactive:
        assert spec["protagonist"]["name"] == session_name
        assert len(prompts) == (2 if scenario == "session_zero" else 1)
    captured = _ScriptedProvider(_AUTHORED)

    def capture_protagonist(provider, oracle, frame, local_map, **kwargs):
        # Keep real bootstrap/frame/spec wiring and real protagonist generation;
        # substitute only this step's external LLM dependency with an offline fake.
        return gen_protagonist(captured, oracle, frame, local_map, **kwargs)

    monkeypatch.setattr(bootstrap, "gen_protagonist", capture_protagonist)
    engine = build_engine(tmp_path / "campaign", provider=FakeLLMProvider())
    result = bootstrap.bootstrap_world(engine, raw_pitch, spec=spec)
    assert result["_state"]["frame"]["genre"] == canonical
    assert len(captured.calls) == 1
    assert _requirements(captured.calls[0]) == {
        "resolved_player_premise": canonical,
        "provided_protagonist": {"name": session_name} if interactive else {},
    }
    assert raw_pitch not in captured.calls[0][1]["content"]
    assert result["summary"]["protagonist_name"] == (session_name if interactive else _AUTHORED["name"])


def test_generator_public_signature_is_unchanged():
    parameters = list(inspect.signature(gen_protagonist).parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "provider", "oracle", "frame", "local_map", "provided", "flavor",
    ]
    assert all(parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters[:4])
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters[:4])
    assert all(parameter.kind == inspect.Parameter.KEYWORD_ONLY for parameter in parameters[4:])
    assert parameters[4].default is None
    assert parameters[5].default == "classic"


# Original 2026-10-09 reproduction, campaign seed 67905055246112.
_SOURCE_FIXTURE = json.loads(r'''
{
  "campaign_seed": 67905055246112,
  "attempt": 0,
  "flavor": "classic",
  "frame": {
    "genre": "我想在一座靠山的内陆小镇里，体验朴实、有人情味、带一点轻微悬念的日常故事。主角是暂住镇上的外地修补匠，愿意靠观察和手艺帮人解决一件当天能办完的小事。",
    "tone": "治愈",
    "world_name": "祈山百工镇",
    "central_conflict": "这座靠山的内陆小镇，世代由各手艺师门按学徒、出师、掌门的等级传授技艺，并向山神祈求护佑炉火、木器与泉水的微小奇迹。如今镇民想开办不问师承的共用修补铺，老匠人却担心旧规矩和祈祷的诚意一并被丢下。新旧办法的分歧落在每日的锅碗门窗上，也藏着彼此未说出口的好意。暂住镇上的外地修补匠恰逢公用井的摇柄一夜松脱：有人疑心新铺的人动过手脚，有人认定是山神不悦；而几道不起眼的磨痕，或许能让他凭观察和手艺在晚饭前修好水井，也解开邻里间的小误会。",
    "n_factions": 5,
    "n_regions": 5,
    "magic_system": "神授奇迹（向神祇祈求而得的力量）",
    "power_ladder": "学派或师门等级（技艺传承的高低）",
    "world_tension": "新旧秩序交替，变革与守旧的拉锯"
  },
  "local_map": {
    "start_town": "town_0",
    "venues": [
      "venue_0",
      "venue_1"
    ],
    "venue_names": {
      "venue_0": "槐荫井坪",
      "venue_1": "百家修补铺"
    },
    "l2": [
      {
        "id": "town_0",
        "kind": "settlement",
        "name": "祈山百工镇"
      },
      {
        "id": "l2_0",
        "kind": "settlement",
        "name": "竹溪村"
      },
      {
        "id": "l2_1",
        "kind": "wilderness",
        "name": "祈山泉林"
      }
    ]
  },
  "provided": null,
  "player_pitch": "我想在一座靠山的内陆小镇里，体验朴实、有人情味、带一点轻微悬念的日常故事。主角是暂住镇上的外地修补匠，愿意靠观察和手艺帮人解决一件当天能办完的小事。"
}
''')
