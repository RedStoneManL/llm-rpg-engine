import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests" / "fixtures"
SCRIPT = ROOT / "scripts" / "dry_run_genesis_import.py"


def test_dry_run_is_offline_and_reports_untranslated_worldbook(tmp_path):
    campaign = tmp_path / "campaign"
    run = subprocess.run([sys.executable, str(SCRIPT), "--card", str(FIX / "st_card_v2.json"),
                          "--world-book", str(FIX / "st_worldbook.json")], text=True,
                         capture_output=True, check=True, cwd=ROOT)
    result = json.loads(run.stdout)
    assert result["parts"] == ["protagonist"]
    assert result["world_book_note"]
    assert not campaign.exists()


def test_dry_run_rejects_missing_sources():
    run = subprocess.run([sys.executable, str(SCRIPT)], text=True, capture_output=True, cwd=ROOT)
    assert run.returncode == 2
    assert "至少提供一个创世来源" in run.stderr


def test_dry_run_blueprint_overrides_card_without_provider_or_campaign(tmp_path):
    from app.engine import resolve_genesis_spec

    blueprint = tmp_path / "blueprint.json"
    blueprint.write_text(json.dumps({"protagonist": {"name": "蓝图主角"},
                                    "world_premise": {"genre": "测试"}}), encoding="utf-8")
    run = subprocess.run([sys.executable, str(SCRIPT), "--card", str(FIX / "st_card_v2.json"),
                          "--genesis", str(blueprint)], text=True, capture_output=True,
                         check=True, cwd=ROOT)
    result = json.loads(run.stdout)
    assert result["parts"] == ["protagonist", "world_premise"]
    assert result["missing_required"] == []
    # The dry-run uses the same provider-free resolver as real genesis.  Check
    # the resolved value, rather than inferring precedence from the part list.
    spec = resolve_genesis_spec(None, card_path=FIX / "st_card_v2.json",
                                blueprint_path=blueprint)
    assert spec["protagonist"]["name"] == "蓝图主角"
    assert not (tmp_path / "campaign").exists()


def test_dry_run_bad_source_fails_without_creating_campaign(tmp_path):
    bad = tmp_path / "bad-card.json"
    campaign = tmp_path / "campaign"
    bad.write_text("{not valid json", encoding="utf-8")

    run = subprocess.run([sys.executable, str(SCRIPT), "--card", str(bad)], text=True,
                         capture_output=True, cwd=ROOT)

    assert run.returncode == 2
    assert "无法读取创世来源" in run.stderr
    assert not campaign.exists()
