"""#R5 — a JSON parse failure must NEVER dump the raw model output (which carries
the structured commit + secrecy='secret' facts) into the player-facing narration.
"""
from loop.strategy import (
    _salvage_narration, _data_or_safe, _PARSE_FAIL_NARRATION, AuthorStrategy,
)
from llm.provider import _parse_json_object, FakeLLMProvider


_LEAKY_RAW = (
    '好的，这是本回合：\n'
    '{"narration": "你推开门，潮湿的空气扑面而来。", '
    '"facts": [{"subject":"protagonist","predicate":"护身符状态",'
    '"value":"持续发热","secrecy":"secret"}], "clock":[{"advance":false}]}'
)


def test_salvage_narration_extracts_only_narration():
    out = _salvage_narration(_LEAKY_RAW)
    assert out == "你推开门，潮湿的空气扑面而来。"
    assert "secrecy" not in out and "facts" not in out


def test_salvage_handles_escaped_quotes():
    raw = '{"narration": "他说\\"住手\\"，然后退后。", "facts": []}'
    assert _salvage_narration(raw) == '他说"住手"，然后退后。'


def test_salvage_returns_none_when_no_narration():
    assert _salvage_narration('{"facts": [{"secrecy":"secret"}]}') is None
    assert _salvage_narration("") is None
    assert _salvage_narration(None) is None


def test_data_or_safe_valid_json_passthrough():
    data = _data_or_safe('{"narration":"ok","clock":[]}')
    assert data["narration"] == "ok"
    assert data.get("clock") == []


def test_data_or_safe_malformed_salvages_narration_no_leak():
    # missing comma between narration and facts -> invalid JSON; a secret fact in the blob.
    raw = '{"narration": "你环顾四周。" "facts":[{"secrecy":"secret","value":"X"}]}'
    data = _data_or_safe(raw)
    assert data["narration"] == "你环顾四周。"
    assert "facts" not in data                       # structured section not leaked
    assert "secrecy" not in data["narration"]


def test_valid_json_with_prose_prefix_narration_clean():
    # _LEAKY_RAW is valid JSON behind a prose prefix -> parses cleanly; narration
    # is the clean prose, and the structured facts land in the sections (applied
    # as fog-protected events), never inside the player-facing narration.
    data = _data_or_safe(_LEAKY_RAW)
    assert data["narration"] == "你推开门，潮湿的空气扑面而来。"
    assert "secrecy" not in data["narration"]
    assert isinstance(data.get("facts"), list)


def test_data_or_safe_unsalvageable_uses_neutral_fallback():
    data = _data_or_safe("一堆既不能解析也没有 narration 字段的东西 {[}")
    assert data["narration"] == _PARSE_FAIL_NARRATION
    assert "{" not in data["narration"]


def test_data_or_safe_bare_prose_becomes_narration():
    # Reasoning models (esp. after a native tool loop) often drop the JSON envelope
    # and answer in pure prose. Bare prose is NOT the #R5 leak risk — a broken commit
    # blob carrying secret facts always contains '{'. So recover the prose as the
    # turn's narration instead of discarding a good turn to the neutral fallback.
    prose = "你在流浪汉旁边坐下。他没有看你，只把一根锈钉拨到你的杯脚旁。"
    data = _data_or_safe(prose)
    assert data["narration"] == prose
    assert data["narration"] != _PARSE_FAIL_NARRATION
    assert "facts" not in data and "clock" not in data   # no fabricated sections


def test_data_or_safe_prose_with_brace_stays_neutral():
    # Guard the #R5 boundary: anything containing '{' may be a broken commit blob
    # (possibly with secrecy='secret' facts) → must NOT be surfaced as narration.
    blob = '一些散文 {"facts":[{"predicate":"护身符","secrecy":"secret"}]'
    data = _data_or_safe(blob)
    assert data["narration"] == _PARSE_FAIL_NARRATION


def test_parse_json_object_salvages_trailing_comma():
    # reasoning models often emit a trailing comma; recover instead of failing.
    assert _parse_json_object('{"narration":"x","clock":[],}') == {"narration": "x", "clock": []}
    assert _parse_json_object('{"a":[1,2,],}') == {"a": [1, 2]}


# --- JSON re-ask after a bare-prose tool-loop answer ---

def test_reask_json_recovers_structured_commit():
    # tool loop returned prose → _reask_json asks once, model returns a proper JSON
    # commit → use it (structured sections recovered, not just narration).
    s = AuthorStrategy()
    s._messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
    prov = FakeLLMProvider(json_responses=[{"narration": "你坐下了。", "moves": [], "clock": []}])
    out = s._reask_json("你在流浪汉旁边坐下。（散文，没有 JSON）", prov)
    assert _parse_json_object(out) == {"narration": "你在流浪汉旁边坐下。（散文，没有 JSON）", "moves": [], "clock": []}


def test_reask_json_keeps_prose_when_reask_also_fails():
    # if the re-ask STILL isn't JSON, keep the original prose (Fix A salvages it).
    s = AuthorStrategy()
    s._messages = [{"role": "system", "content": "sys"}]
    prov = FakeLLMProvider(responses=["还是散文，依旧没有 JSON"])
    assert s._reask_json("原始那段好散文", prov) == "原始那段好散文"


def test_reask_json_does_not_mutate_working_messages():
    # the re-ask exchange must NOT leak into the persistent working messages.
    s = AuthorStrategy()
    s._messages = [{"role": "system", "content": "sys"}]
    before = list(s._messages)
    prov = FakeLLMProvider(json_responses=[{"narration": "x"}])
    s._reask_json("prose", prov)
    assert s._messages == before
