from loop.genesis_spec import normalize, merge, missing_required


def test_normalize_drops_empty_fields_and_parts():
    spec = normalize({
        "world_premise": {"genre": "日式西幻", "tone": "  ", "world_name": ""},
        "protagonist": {},
        "factions": [{"name": "教会"}, {"name": "  "}, {"motivation": "x"}],
        "opening": "   ",
    })
    assert spec["world_premise"] == {"genre": "日式西幻"}
    assert "protagonist" not in spec          # all-empty part dropped
    assert spec["factions"] == [{"name": "教会"}]  # nameless/blank dropped
    assert "opening" not in spec


def test_normalize_none_and_garbage():
    assert normalize(None) == {}
    assert normalize("nope") == {}
    assert normalize({"unknown_key": 1}) == {}


def test_merge_scalar_overlay_wins_when_nonempty():
    base = normalize({"world_premise": {"genre": "a", "tone": "暗黑"}})
    overlay = normalize({"world_premise": {"genre": "b", "world_name": "X"}})
    out = merge(base, overlay)
    assert out["world_premise"] == {"genre": "b", "tone": "暗黑", "world_name": "X"}


def test_merge_name_list_augments_and_dedups():
    base = normalize({"factions": [{"name": "教会", "motivation": "m1"}]})
    overlay = normalize({"factions": [{"name": "教会", "motivation": "dup"},
                                       {"name": "盗贼公会"}]})
    out = merge(base, overlay)
    names = [f["name"] for f in out["factions"]]
    assert names == ["教会", "盗贼公会"]        # base kept, dup dropped, new appended


def test_merge_npcs_concat_no_dedup():
    base = normalize({"npcs": [{"sketch": "老者"}]})
    overlay = normalize({"npcs": [{"sketch": "老者"}]})
    out = merge(base, overlay)
    assert len(out["npcs"]) == 2               # concat, no dedup


def test_merge_local_map_town_and_venues():
    base = normalize({"local_map": {"town": {"name": "起点镇"},
                                     "venues": [{"name": "酒馆"}]}})
    overlay = normalize({"local_map": {"town": {"seed": "雾气弥漫"},
                                        "venues": [{"name": "铁铺"}]}})
    out = merge(base, overlay)
    assert out["local_map"]["town"] == {"name": "起点镇", "seed": "雾气弥漫"}
    assert [v["name"] for v in out["local_map"]["venues"]] == ["酒馆", "铁铺"]


def test_missing_required():
    assert set(missing_required({})) == {"world_premise", "protagonist"}
    assert missing_required(normalize({
        "world_premise": {"genre": "x"},
        "protagonist": {"name": "凛"},
    })) == []
    assert missing_required(normalize({
        "world_premise": {"tone": "x"},          # genre missing
        "protagonist": {"name": "凛"},
    })) == ["world_premise"]
