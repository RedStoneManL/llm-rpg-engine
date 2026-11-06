import json
from pathlib import Path

import pytest
from llm.provider import FakeLLMProvider
from loop.import_sillytavern import convert_sillytavern, preview_sillytavern

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


def test_offline_preview_counts_worldbook_without_provider_or_source_text():
    preview = preview_sillytavern(world_book=_load("st_worldbook.json"), character_card=_load("st_card_v2.json"))
    assert preview["world_book_entries"] > 0
    assert preview["card_fields"] == ["name", "origin"]
    assert any("需要 provider" in issue for issue in preview["issues"])
    assert "world_book_entries" in preview
    # The pure preview API has no provider parameter and cannot create a campaign.
    assert "provider" not in preview


def test_offline_preview_reports_malformed_sources():
    preview = preview_sillytavern(world_book={"entries": []}, character_card={})
    assert preview["world_book_entries"] == 0
    assert len(preview["issues"]) == 2


def test_offline_preview_rejects_unsupported_card_role():
    with pytest.raises(ValueError, match="card_as"):
        preview_sillytavern(card_as="gm")


def test_disabled_book_entries_never_reach_conversion_and_enabled_keywords_do():
    book = {"entries":{
        "disabled":{"disable":True, "key":["暗语甲"], "content":"停用的秘密甲"},
        "enabled":{"key":["铃响"], "content":"门户会打开"}}}
    assert preview_sillytavern(world_book=book)["world_book_entries"] == 1
    provider = FakeLLMProvider(json_responses=[{"world_premise":{"tone":"夜访"}}])
    convert_sillytavern(provider, world_book=book)
    prompt = "\n".join(user for _, user in provider.calls)
    assert "停用的秘密甲" not in prompt and "暗语甲" not in prompt
    assert "铃响" in prompt and "门户会打开" in prompt
