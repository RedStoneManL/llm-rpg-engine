#!/usr/bin/env python3
"""Verify the checked-in handoff; optionally restore its synthetic campaign.

Standard library only. No provider, model call, network, or turn execution.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "docs" / "handoff"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_snapshot(path: Path, meta: dict) -> dict:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Snapshot integrity check failed: {integrity}")
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        rows = {table: db.execute(
            'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"'
        ).fetchone()[0] for table in tables}
        if rows != meta["table_rows"]:
            raise ValueError("Snapshot table inventory differs from the archive metadata")
        # Exercise the actual FTS virtual table, not merely its shadow tables.
        db.execute("SELECT COUNT(*) FROM play_history_fts WHERE play_history_fts MATCH ?",
                   ("青竹",)).fetchone()
        return {
            "integrity_check": integrity,
            "events": rows["events"],
            "active_events": db.execute("SELECT COUNT(*) FROM events WHERE retracted=0").fetchone()[0],
            "action_receipts": rows["action_receipts"],
            "player_inputs": rows["player_inputs"],
            "revision": db.execute("SELECT revision FROM store_revision").fetchone()[0],
            "active_max_turn": db.execute("SELECT MAX(turn) FROM events WHERE retracted=0").fetchone()[0],
            "fts_query_executed": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-campaign", type=Path,
                        help="New directory ending in terra-live-campaign; refuses existing destinations")
    args = parser.parse_args()
    manifest = json.loads((HANDOFF / "manifest.json").read_text(encoding="utf-8"))
    checked = 0
    for name, expected in manifest["files"].items():
        path = ROOT / name
        if not path.is_file() or sha256(path.read_bytes()) != expected:
            raise ValueError(f"Missing or changed handoff file: {name}")
        checked += 1
    subprocess.run(["git", "merge-base", "--is-ancestor", manifest["runtime_base_commit"], "HEAD"],
                   cwd=ROOT, check=True)
    # Applicability is all that is claimed for the WIP; never apply it here.
    subprocess.run(["git", "apply", "--check",
                    str(HANDOFF / "wip" / "director-novelty-UNVALIDATED.patch")],
                   cwd=ROOT, check=True)
    meta = json.loads((HANDOFF / "artifacts" / "terra-live-campaign-snapshot.json").read_text(encoding="utf-8"))
    archive = (HANDOFF / "artifacts" / "terra-live-campaign-2026-11-11.sqlite3.gz").read_bytes()
    if sha256(archive) != meta["archive_sha256"]:
        raise ValueError("Archive digest mismatch")
    payload = gzip.decompress(archive)
    if sha256(payload) != meta["sqlite_sha256"]:
        raise ValueError("SQLite payload digest mismatch")
    with tempfile.TemporaryDirectory(prefix="rpg-handoff-verify-") as temporary:
        db_path = Path(temporary) / "events.db"
        db_path.write_bytes(payload)
        snapshot = inspect_snapshot(db_path, meta)
    result = {"checked_files": checked, "runtime_base_commit": manifest["runtime_base_commit"],
              "wip_applicable_not_applied": True, "snapshot": snapshot,
              "model_calls": 0, "runtime_changes": False}
    if args.restore_campaign:
        destination = args.restore_campaign.resolve()
        if destination.name != meta["campaign_basename"]:
            raise ValueError("Preserve basename terra-live-campaign: the legacy save's seed depends on it")
        destination.mkdir(parents=True, exist_ok=False)
        with (destination / "events.db").open("xb") as stream:
            stream.write(payload)
        # Only generate the mirror on the newly created copy. SQLite stays the authority.
        sys.path.insert(0, str(ROOT))
        from engine.store import EventStore
        with EventStore(destination / "events.db", destination / "events.jsonl") as store:
            store.sync_jsonl()
        restored = inspect_snapshot(destination / "events.db", meta)
        if restored != snapshot:
            raise ValueError("Restored snapshot differs after mirror reconstruction")
        result["restored_campaign"] = str(destination)
        result["mirror_rebuilt"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
