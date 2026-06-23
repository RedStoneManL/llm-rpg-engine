"""#R5 — a JSON parse failure must NEVER dump the raw model output (which carries
the structured commit + secrecy='secret' facts) into the player-facing narration.
"""
from loop.strategy import _salvage_narration, _data_or_safe, _PARSE_FAIL_NARRATION
from llm.provider import _parse_json_object


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


def test_parse_json_object_salvages_trailing_comma():
    # reasoning models often emit a trailing comma; recover instead of failing.
    assert _parse_json_object('{"narration":"x","clock":[],}') == {"narration": "x", "clock": []}
    assert _parse_json_object('{"a":[1,2,],}') == {"a": [1, 2]}
