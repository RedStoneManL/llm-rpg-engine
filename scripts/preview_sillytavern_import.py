"""Offline, read-only preview for SillyTavern import sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loop.import_sillytavern import preview_sillytavern


def _load(path: str | None):
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读预览 SillyTavern 导入结构；不调用模型或写入 campaign。")
    parser.add_argument("--world-book")
    parser.add_argument("--card")
    parser.add_argument("--card-as", choices=("protagonist", "npc"), default="protagonist")
    args = parser.parse_args(argv)
    if not args.world_book and not args.card:
        parser.error("至少提供 --world-book 或 --card")
    try:
        result = preview_sillytavern(world_book=_load(args.world_book), character_card=_load(args.card), card_as=args.card_as)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"无法读取导入文件：{exc}")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
