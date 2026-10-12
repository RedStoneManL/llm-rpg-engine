#!/usr/bin/env python3
"""Run a fixed, no-network CLI tour of save, recap-pending and /memory status.

The campaign lives only in a temporary directory.  This is an experience/demo
fixture, not a production game launcher and it never calls a real provider.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# Keep the documented ``python scripts/...`` form independent of an editable
# install: Python otherwise puts only ``scripts/`` on ``sys.path``.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# This standalone fixture fixes its own embedding choice, rather than relying
# on a caller's RPG_EMBEDDER setting.  It does not alter production defaults.
os.environ["RPG_EMBEDDER"] = "none"

# The fixture intentionally makes a recap provider fail.  Keep its internal
# diagnostic out of the human-facing walkthrough; the CLI status line is the
# behavior being demonstrated.
os.environ.setdefault("RPG_LOG_LEVEL", "CRITICAL")

from app.__main__ import main
from app.engine import build_engine
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import FakeLLMProvider


class DemoProvider(FakeLLMProvider):
    def __init__(self):
        super().__init__(json_responses=[{
            "moves": [], "places": [], "cast": [], "facts": [],
            "narration": "公开叙事：你沿着雨后的街道继续前行。",
            "clock": [{"advance": True, "days": 1, "bands": 0,
                       "reason": "离线演示推进一天"}],
        }])

    def complete_messages(self, messages, **kwargs):
        system = next((message.get("content", "") for message in messages
                       if message.get("role") == "system"), "")
        if "剧情摘要员" in system:
            # Deliberately exercise the safe UI status.  The exception text is
            # intentionally not printed by the player-facing CLI.
            raise RuntimeError("offline recap intentionally unavailable")
        return super().complete_messages(messages, **kwargs)


def _seed(campaign: Path) -> None:
    engine = build_engine(campaign, provider=FakeLLMProvider())
    try:
        # A small normal persisted world avoids turning this status tour into a
        # noisy genesis-provider demonstration.
        engine.store.append_many([
            kernel_event("entity_created", day=1, scene="seed", summary="主角",
                         deltas={"id": "protagonist", "etype": "Person", "tier": "tracked"}, turn=0),
            kernel_event("entity_created", day=1, scene="seed", summary="酒馆",
                         deltas={"id": "inn", "etype": "Place", "tier": "tracked",
                                 "attrs": {"level": 3, "kind": "venue", "seed": "inn"}}, turn=0),
            kernel_event("relation_added", day=1, scene="seed", summary="主角在酒馆",
                         deltas={"src": "protagonist", "rel": "located_in", "dst": "inn"}, turn=0),
        ])
        engine.world = project(engine.registry, engine.store.iter_events())
    finally:
        engine.store.close()


def _run(campaign: Path, label: str, args: list[str], inputs: list[str]) -> None:
    print(f"\n===== {label} =====")
    main(["--campaign", str(campaign), *args], inputs=inputs,
         out=print, provider=DemoProvider())


def main_demo() -> None:
    with TemporaryDirectory(prefix="llm-rpg-cli-demo-") as directory:
        campaign = Path(directory) / "campaign"
        _seed(campaign)
        _run(campaign, "默认启动：私有实验关闭", [], ["/memory", "/quit"])
        _run(campaign, "显式实验：三个已保存回合、摘要暂未生成、查看并撤销记忆",
             ["--offline-private-memory"],
             ["第一天前进", "第二天前进", "第三天前进", "/memory", "/undo", "/quit"])
        _run(campaign, "重开续玩：撤销后的持久状态", ["--offline-private-memory"],
             ["/memory", "/quit"])


if __name__ == "__main__":
    main_demo()
