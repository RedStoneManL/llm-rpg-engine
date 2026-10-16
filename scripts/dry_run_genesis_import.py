"""Read-only genesis-source merge preview; never opens a campaign."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import resolve_genesis_spec
from loop.genesis_spec import missing_required


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="只读预览创世导入合并；不调用模型、bootstrap 或 campaign。")
    p.add_argument("--pitch", default="")
    p.add_argument("--genesis")
    p.add_argument("--world-book")
    p.add_argument("--card")
    p.add_argument("--card-as", choices=("protagonist", "npc"), default="protagonist")
    a = p.parse_args(argv)
    if not any((a.pitch, a.genesis, a.world_book, a.card)):
        p.error("至少提供一个创世来源")
    try:
        spec = resolve_genesis_spec(None, pitch=a.pitch, blueprint_path=a.genesis,
                                    world_book_path=a.world_book, card_path=a.card,
                                    card_as=a.card_as)
    except Exception as exc:
        p.error(f"无法读取创世来源：{exc}")
    summary = {"parts": sorted(spec), "missing_required": missing_required(spec),
               "counts": {k: len(spec.get(k, [])) for k in ("regions", "factions", "npcs", "threads")},
               "world_book_note": "世界书条目在离线 dry-run 中不会翻译；需要 provider。" if a.world_book else None}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
