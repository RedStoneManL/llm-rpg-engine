import json
from pathlib import Path
import pytest
from llm.provider import FakeLLMProvider


FIXTURES = Path(__file__).parents[1] / "fixtures"


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


@pytest.mark.parametrize("source_flag", ["--genesis", "--import-card", "--import-world-book"])
def test_main_bad_genesis_source_does_not_create_campaign(tmp_path, source_flag):
    """Every explicit source is checked before opening a new SQLite campaign."""
    from app import __main__ as M

    campaign = tmp_path / "campaign"
    out = []
    M.main(
        ["--campaign", str(campaign), source_flag, str(tmp_path / "missing.json")],
        inputs=iter(["/quit"]), out=out.append, provider=_provider())

    assert "开局错误" in "\n".join(out)
    assert not campaign.exists()


def test_main_preview_genesis_is_offline_and_needs_no_campaign(monkeypatch):
    """The standard CLI exposes the dry-run without provider or store startup."""
    from app import __main__ as M

    def must_not_make_provider(*args, **kwargs):
        raise AssertionError("preview must not construct a provider")

    monkeypatch.setattr("llm.provider.make_provider", must_not_make_provider)
    out = []
    M.main(
        ["--preview-genesis", "--import-card", str(FIXTURES / "st_card_v2.json"),
         "--card-as", "npc"],
        out=out.append,
    )

    result = json.loads(out[0])
    assert result["parts"] == ["npcs"]
    assert result["counts"]["npcs"] == 1


def test_main_bad_flavor_does_not_create_campaign(tmp_path):
    """A rejected first-run flavor must not leave an empty save behind."""
    from app import __main__ as M

    campaign = tmp_path / "campaign"
    out = []
    M.main(
        ["--campaign", str(campaign), "--flavor", "missing-pack"],
        inputs=iter(["/quit"]), out=out.append, provider=_provider())

    assert "unknown flavor" in "\n".join(out)
    assert not campaign.exists()


def test_main_saved_campaign_ignores_new_game_flavor_flag(tmp_path):
    """A resume keeps the save's flavor instead of preflighting a new-game flag."""
    from app import __main__ as M
    from app.engine import build_engine, new_game

    campaign = tmp_path / "campaign"
    engine = build_engine(campaign, provider=_provider())
    new_game(engine)
    engine.store.close()

    out = []
    M.main(
        ["--campaign", str(campaign), "--flavor", "missing-pack"],
        inputs=iter(["/quit"]), out=out.append, provider=_provider())

    combined = "\n".join(out)
    assert "[载入存档]" in combined
    assert "unknown flavor" not in combined
