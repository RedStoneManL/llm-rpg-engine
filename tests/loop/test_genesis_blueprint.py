import json
import pytest
from loop.genesis_blueprint import load_blueprint, BlueprintError


def test_load_json_blueprint(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({
        "world_premise": {"genre": "日式西幻"},
        "protagonist": {"name": "凛", "origin": "流浪剑士"},
        "factions": [{"name": "教会"}],
    }), encoding="utf-8")
    spec = load_blueprint(p)
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"
    assert spec["factions"] == [{"name": "教会"}]


def test_load_yaml_blueprint(tmp_path):
    pytest.importorskip("yaml")
    p = tmp_path / "g.yaml"
    p.write_text(
        "world_premise:\n  genre: 日式西幻\nprotagonist:\n  name: 凛\n",
        encoding="utf-8")
    spec = load_blueprint(p)
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert spec["protagonist"]["name"] == "凛"


def test_missing_file_raises(tmp_path):
    with pytest.raises(BlueprintError):
        load_blueprint(tmp_path / "nope.json")


def test_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(BlueprintError):
        load_blueprint(p)


def test_non_object_top_level_raises(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(BlueprintError):
        load_blueprint(p)
