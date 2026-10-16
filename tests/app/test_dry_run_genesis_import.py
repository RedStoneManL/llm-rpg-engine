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
