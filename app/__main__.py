"""app.__main__ — CLI entry point for the RPG engine.

Usage:
    python -m app --campaign DIR [--provider fake|openai|zhipu|anthropic]
                  [--model MODEL] [--base-url URL] [--compare]
                  [--pitch TEXT]

Pitch source priority (first non-empty wins):
    1. --pitch <text>          CLI flag
    2. RPG_BOOTSTRAP_PITCH     environment variable
    3. First line read from stdin (only when running interactively, no flag)
    4. Empty string ""         (bootstrap falls back to oracle-rolled genre)

Accepts optional inputs/out/provider params for testability (inputs defaults to sys.stdin).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Iterable

from engine.log import get_logger, configure_logging

log = get_logger("app.main")


def main(
    argv: list[str] | None = None,
    *,
    inputs: Iterable[str] | None = None,
    out: Callable = print,
    provider=None,
) -> None:
    """CLI entry point.

    Args:
        argv:     Argument list (default sys.argv[1:]).
        inputs:   Input iterable for testing (default sys.stdin).
        out:      Output function for testing (default print).
        provider: Injected provider (overrides --provider flag in tests).
    """
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="RPG Engine — 跑团命令行",
    )
    parser.add_argument(
        "--campaign", required=True,
        help="Path to the campaign directory (created if absent)",
    )
    parser.add_argument(
        "--provider", default="fake",
        choices=["fake", "openai", "zhipu", "anthropic"],
        help="LLM provider (default: fake)",
    )
    parser.add_argument(
        "--model", default=None,
        help="Model name for the chosen provider",
    )
    parser.add_argument(
        "--base-url", default=None, dest="base_url",
        help="Custom base URL for the provider API",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Start in compare mode (甲/乙 dual-strategy)",
    )
    parser.add_argument(
        "--transcript", default=None,
        help="Path for the per-turn JSONL transcript (default: <campaign>/transcript.jsonl)",
    )
    parser.add_argument(
        "--max-tokens", default=None, type=int, dest="max_tokens",
        help="Per-call OUTPUT-token cap (default: provider default — Zhipu 32768). "
             "Caps output only, not the context window; raise if a turn truncates.",
    )
    parser.add_argument(
        "--max-repairs", default=6, type=int, dest="max_repairs",
        help="Validation-repair rounds per turn before dropping bad sections. "
             "The strict gate bounces malformed output back to the LLM until it passes.",
    )
    parser.add_argument(
        "--pitch", default=None, dest="pitch",
        help="World background / theme keywords passed to the bootstrap pipeline "
             "(e.g. '东方武侠', '克苏鲁恐怖'). "
             "Alt source: env var RPG_BOOTSTRAP_PITCH. "
             "If neither is provided, bootstrap uses its own oracle-rolled genre.",
    )
    args = parser.parse_args(argv)
    configure_logging()  # honor RPG_DEBUG / RPG_LOG_LEVEL so process logs appear

    from pathlib import Path
    from app.engine import build_engine, new_game
    from app.play import play_loop

    campaign_dir = Path(args.campaign)

    # Resolve provider — injected provider wins over CLI flag (for tests)
    if provider is None:
        from llm.provider import make_provider
        provider = make_provider(
            args.provider,
            model=args.model,
            base_url=args.base_url,
            max_tokens=args.max_tokens,
        )

    log.debug("main: campaign=%s provider=%s", campaign_dir, type(provider).__name__)

    engine = build_engine(campaign_dir, provider=provider)

    # Track whether we are in interactive (real stdin) or scripted (injected) mode.
    # This determines whether we prompt the user for a pitch string.
    _interactive = inputs is None

    # Resolve input source; wrap in an iterator for the reroll loop.
    if inputs is None:
        inputs = sys.stdin
    inputs_iter = iter(inputs)

    # Seed a new game if the store is empty
    events = list(engine.store.iter_events())
    if not events:
        # ---------------------------------------------------------------
        # Resolve pitch: --pitch flag → RPG_BOOTSTRAP_PITCH env →
        #   (if interactive TTY) one line read from stdin → default ""
        # ---------------------------------------------------------------
        pitch: str = ""
        if args.pitch is not None:
            # Explicit --pitch flag wins over everything
            pitch = args.pitch
        else:
            env_pitch = os.environ.get("RPG_BOOTSTRAP_PITCH", "")
            if env_pitch:
                pitch = env_pitch
            elif _interactive:
                # Only prompt + consume a stdin line when running interactively
                # (i.e. inputs was None → real sys.stdin). Injected inputs are for
                # the reroll loop, not for pitch reading.
                out("[新游戏] 请输入世界背景关键词（可留空直接回车）：")
                try:
                    raw = next(inputs_iter, None)
                    if raw is not None:
                        pitch = raw.rstrip("\n")
                except StopIteration:
                    pitch = ""

        log.debug("main: new game pitch=%r", pitch)
        out("[新游戏] 正在生成世界，请稍候…")
        result = new_game(engine, pitch)

        # Print world summary so the player can review it
        summary = result.get("summary", {})
        out("[世界摘要]")
        out(f"  世界名称：{summary.get('world_name', '?')}")
        out(f"  基调：{summary.get('tone', '?')}  核心冲突：{summary.get('central_conflict', '?')}")
        out(f"  大区域数：{summary.get('n_regions', '?')}  势力数：{summary.get('n_factions', '?')}")
        out(f"  NPC 数：{summary.get('n_npcs', '?')}  暗线数：{summary.get('n_lore', '?')}")
        if summary.get("narration_excerpt"):
            out(f"  开场摘录：{summary['narration_excerpt']}")

        # ---------------------------------------------------------------
        # Reroll loop — thin, behind the same injected inputs/out seams
        # Commands:
        #   reroll              → bootstrap.reroll_all (whole genesis)
        #   reroll factions     → bootstrap.reroll_step('factions')
        #   reroll npcs         → bootstrap.reroll_step('npcs')
        #   reroll threads      → bootstrap.reroll_step('threads')
        #   <empty> / 开始 / start → break into play_loop
        #
        # Calls are made via the module object (not bound names) so that
        # monkeypatching loop.bootstrap.reroll_all / reroll_step in tests
        # is intercepted correctly.
        # ---------------------------------------------------------------
        import loop.bootstrap as _bootstrap_mod

        _LEAF_STEPS = {"factions", "npcs", "threads"}
        _BREAK_TOKENS = {"", "开始", "start"}

        out("[提示] 输入 'reroll' 重掷全局，'reroll factions/npcs/threads' 重掷指定步骤，"
            "或直接按回车 / 输入 '开始' / 'start' 进入游戏。")

        for line in inputs_iter:
            line = line.rstrip("\n")
            stripped = line.strip().lower()

            if stripped in _BREAK_TOKENS:
                break

            if stripped == "reroll":
                out("[重掷] 正在重新生成整个世界…")
                result = _bootstrap_mod.reroll_all(engine, result)
                summary = result.get("summary", {})
                out("[新世界摘要]")
                out(f"  世界名称：{summary.get('world_name', '?')}")
                out(f"  基调：{summary.get('tone', '?')}  核心冲突：{summary.get('central_conflict', '?')}")
                out(f"  大区域数：{summary.get('n_regions', '?')}  势力数：{summary.get('n_factions', '?')}")
                out(f"  NPC 数：{summary.get('n_npcs', '?')}  暗线数：{summary.get('n_lore', '?')}")
                if summary.get("narration_excerpt"):
                    out(f"  开场摘录：{summary['narration_excerpt']}")
                out("[提示] 输入 'reroll' 再次重掷，或 '开始' / 'start' / 回车进入游戏。")
                continue

            if stripped.startswith("reroll "):
                step_name = stripped[len("reroll "):].strip()
                if step_name in _LEAF_STEPS:
                    out(f"[重掷] 正在重掷 {step_name}…")
                    result = _bootstrap_mod.reroll_step(engine, result, step_name)
                    summary = result.get("summary", {})
                    out(f"[新{step_name}摘要]")
                    out(f"  世界名称：{summary.get('world_name', '?')}")
                    out(f"  基调：{summary.get('tone', '?')}  核心冲突：{summary.get('central_conflict', '?')}")
                    out(f"  大区域数：{summary.get('n_regions', '?')}  势力数：{summary.get('n_factions', '?')}")
                    out(f"  NPC 数：{summary.get('n_npcs', '?')}  暗线数：{summary.get('n_lore', '?')}")
                    if summary.get("narration_excerpt"):
                        out(f"  开场摘录：{summary['narration_excerpt']}")
                    out("[提示] 输入 'reroll' 再次重掷，或 '开始' / 'start' / 回车进入游戏。")
                else:
                    out(f"[未知重掷步骤] '{step_name}' — 可用：factions / npcs / threads")
                continue

            # Unknown command in reroll loop — treat as break (start game)
            out(f"[提示] 未识别指令 '{line}' — 进入游戏。")
            break

        out("[新游戏] 世界已就绪。输入行动描述开始游戏，/help 查看指令。")
    else:
        out(f"[载入存档] 已读取 {len(events)} 条事件。")

    transcript_path = Path(args.transcript) if args.transcript else (campaign_dir / "transcript.jsonl")
    out(f"[transcript] 逐回合记录写入 {transcript_path}")
    play_loop(engine, inputs=inputs_iter, out=out, compare=args.compare,
              transcript_path=transcript_path, max_repairs=args.max_repairs)


if __name__ == "__main__":
    main()
