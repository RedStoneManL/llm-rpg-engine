"""CodexSystem (I6-P3): world setting text blocks — projection + stable-layer index."""
from kernel.registry import Registry
from kernel.projection import project, empty_world
from kernel.events import kernel_event
from systems.codex import CodexSystem, codex_entries


def _reg():
    r = Registry()
    r.register(CodexSystem())
    return r


def _entry(eid, kind, title, body, day=1):
    return kernel_event("codex_entry_added", day=day, scene="genesis", turn=0,
                        summary=f"codex:{title}",
                        deltas={"id": eid, "kind": kind, "title": title, "body": body})


def test_codex_entry_added_projects():
    w = project(_reg(), iter([_entry("codex_power", "实力等级", "星脉位阶", "从初窥到通脉，共九阶……")]))
    entries = codex_entries(w)
    assert len(entries) == 1
    assert entries[0]["title"] == "星脉位阶" and "九阶" in entries[0]["body"]


def test_codex_index_injected_in_stable_layer():
    w = project(_reg(), iter([
        _entry("c1", "编年史", "碎星编年", "..."),
        _entry("c2", "实力等级", "星脉位阶", "..."),
    ]))
    frag = CodexSystem().inject({}, w)
    assert frag is not None
    assert frag.layer == "stable"
    assert "碎星编年" in frag.text and "星脉位阶" in frag.text
    assert "codex_query" in frag.text


def test_codex_re_add_by_id_replaces():
    w = project(_reg(), iter([
        _entry("c1", "k", "t1", "b1", day=1),
        _entry("c1", "k", "t1b", "b1b", day=2),
    ]))
    entries = codex_entries(w)
    assert len(entries) == 1 and entries[0]["title"] == "t1b"


def test_codex_empty_no_inject():
    assert CodexSystem().inject({}, empty_world(_reg())) is None


def test_codex_query_tool_returns_block_and_index():
    import json
    from llm.tools import build_tool_registry
    w = project(_reg(), iter([
        _entry("c1", "实力等级", "星脉位阶", "由初窥到通脉，共九阶……"),
        _entry("c2", "编年史", "碎星编年", "古时星辰坠地……"),
    ]))
    scene = {"protagonist": "hero", "present": [], "day": 1, "location": "town"}
    reg = build_tool_registry(_reg(), w, scene)
    out = json.loads(reg.execute("codex_query", {"q": "实力等级"}))
    assert "九阶" in " ".join(m.get("body", "") for m in out.get("matches", []))
    idx = json.loads(reg.execute("codex_query", {}))      # empty → index
    assert any(e.get("title") == "星脉位阶" for e in idx.get("index", []))
