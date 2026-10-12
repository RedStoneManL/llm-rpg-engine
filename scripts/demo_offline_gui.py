#!/usr/bin/env python3
"""Launch the disposable, no-network player-dashboard demonstration."""
from __future__ import annotations

import argparse
import tempfile

from app.gui import main as gui_main


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动临时离线 RPG 玩家仪表盘演示")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="llm-rpg-gui-demo-") as campaign:
        print("这是临时离线演示；退出后 campaign 会被删除。", flush=True)
        return gui_main(["--campaign", campaign, "--demo", "--port", str(args.port)])


if __name__ == "__main__":
    raise SystemExit(main())
