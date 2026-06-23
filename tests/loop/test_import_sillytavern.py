import json
from pathlib import Path
from llm.provider import FakeLLMProvider
from loop.import_sillytavern import convert_sillytavern

FIX = Path(__file__).parent.parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_card_becomes_protagonist_offline_stub():
    # provider=None -> structural extraction only, never raises
    spec = convert_sillytavern(None, character_card=_load("st_card_v2.json"))
    assert spec["protagonist"]["name"] == "凛"
    assert "流浪" in spec["protagonist"]["origin"]


def test_card_as_npc_routes_to_npcs():
    spec = convert_sillytavern(None, character_card=_load("st_card_v2.json"), card_as="npc")
    assert "protagonist" not in spec
    assert any("凛" in (n.get("sketch", "") + n.get("goal", "")) or n.get("name") == "凛"
               for n in spec["npcs"])


def test_worldbook_llm_translation_shape():
    # scripted provider returns a spec-shaped translation
    prov = FakeLLMProvider(json_responses=[{
        "world_premise": {"genre": "日式西幻", "central_conflict": "教会与魔物对峙"},
        "factions": [{"name": "光之教会"}, {"name": "盗贼公会"}],
    }])
    spec = convert_sillytavern(prov, world_book=_load("st_worldbook.json"))
    assert spec["world_premise"]["genre"] == "日式西幻"
    assert [f["name"] for f in spec["factions"]] == ["光之教会", "盗贼公会"]


def test_no_inputs_returns_empty():
    assert convert_sillytavern(None) == {}
