import json
from llm.provider import FakeLLMProvider


def _provider():
    return FakeLLMProvider(json_responses=[{
        "narration": "你站在晨曦镇的街道上。",
        "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "未推进"}],
    }])


def test_main_genesis_file_drives_protagonist(tmp_path):
    from app import __main__ as M
    bp = tmp_path / "g.json"
    bp.write_text(json.dumps({
        "world_premise": {"genre": "日式西幻"},
        "protagonist": {"name": "凛", "origin": "流浪剑士"},
    }), encoding="utf-8")
    out = []
    M.main(
        ["--campaign", str(tmp_path / "camp"), "--genesis", str(bp)],
        inputs=iter(["", "/quit"]),     # empty -> start game; then quit
        out=out.append, provider=_provider())
    combined = "\n".join(out)
    assert "凛" in combined              # authored protagonist surfaced in intro


def test_main_import_card_drives_protagonist(tmp_path):
    from app import __main__ as M
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"spec": "chara_card_v2", "data": {
        "name": "凛", "description": "流浪剑士"}}), encoding="utf-8")
    out = []
    M.main(
        ["--campaign", str(tmp_path / "camp"), "--import-card", str(card)],
        inputs=iter(["", "/quit"]), out=out.append, provider=_provider())
    assert "凛" in "\n".join(out)


def test_main_bad_genesis_file_aborts_cleanly(tmp_path):
    # A missing --genesis file aborts with a friendly message, not a traceback.
    from app import __main__ as M
    out = []
    M.main(
        ["--campaign", str(tmp_path / "camp"), "--genesis", str(tmp_path / "nope.json")],
        inputs=iter(["/quit"]), out=out.append, provider=_provider())
    assert "开局错误" in "\n".join(out)
