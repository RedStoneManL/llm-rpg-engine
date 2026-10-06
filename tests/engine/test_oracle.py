from engine.oracle import load_table, load_pack_manifest


def test_load_table_base_fallback(tmp_path, monkeypatch):
    import engine.oracle as o
    root = tmp_path / "oracles"
    (root / "flavorX").mkdir(parents=True)
    (root / "classic").mkdir(parents=True)
    (root / "flavorX" / "only_here.json").write_text('[{"name":"X"}]', encoding="utf-8")
    (root / "classic" / "fallback_me.json").write_text('[{"name":"C"}]', encoding="utf-8")
    monkeypatch.setattr(o, "_ORACLE_DIR", root)
    # present in the flavor dir -> flavor wins
    assert load_table("only_here", "flavorX", base="classic") == [{"name": "X"}]
    # absent in flavor -> falls back to base=classic (NOT default)
    assert load_table("fallback_me", "flavorX", base="classic") == [{"name": "C"}]


def test_load_table_default_base_unchanged():
    # existing callers (no base) still resolve via default/
    assert isinstance(load_table("event_types"), list)  # lives in data/oracles/default/


def test_load_pack_manifest_classic_has_voice_and_tone():
    m = load_pack_manifest("classic")
    assert m["name"]
    assert m["voice"]                     # classic voice = the old literary directive
    assert set(m["tone"]) >= {"bright", "dark", "hints_bright", "hints_dark"}


def test_load_pack_manifest_missing_returns_empty():
    assert load_pack_manifest("no_such_flavor") == {}
