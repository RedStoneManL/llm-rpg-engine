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
import itertools
import os
import sys
from typing import Callable, Iterable

from engine.log import get_logger, configure_logging

log = get_logger("app.main")


def _print_intro(result: dict, out: Callable, *, header: str = "[世界摘要]") -> None:
    """Print the rich INTRO block from a bootstrap_world result dict.

    Covers:
      - 主角：name — origin；目标：goal
      - 当前所在：start region name + start town name + venue ids
      - 世界背景：world_name (tone) — central_conflict
      - 当前目标：objective (starting quest)
      - 开场：narration_excerpt
      - Counts footer: regions / factions / NPC / lore

    Args:
        result: dict returned by bootstrap_world / reroll_all / reroll_step.
        out:    Output callable (injected seam).
        header: Section header prefix (default "[世界摘要]", overridable for reroll).
    """
    summary = result.get("summary", {})
    state = result.get("_state", {})

    # -----------------------------------------------------------------------
    # Protagonist info
    # -----------------------------------------------------------------------
    prot_name = summary.get("protagonist_name", "?")
    prot_origin = summary.get("protagonist_origin", "?")
    prot_goal = summary.get("protagonist_goal", "?")
    objective = summary.get("objective", "?")

    # -----------------------------------------------------------------------
    # Location info — L1 region + L2 town + L3 venue ids
    # -----------------------------------------------------------------------
    regions_summary = state.get("regions_summary", {})
    start_region_id = regions_summary.get("start_region", "region_0")
    region_name = "?"
    for r in regions_summary.get("regions", []):
        if r.get("id") == start_region_id:
            region_name = r.get("name", "?")
            break

    local_map = state.get("local_map", {})
    # l2 is a list; town_0 is the first entry
    l2_list = local_map.get("l2", [])
    town_name = "?"
    for entry in l2_list:
        if entry.get("id") == "town_0":
            town_name = entry.get("name", "?")
            break
    venue_ids = local_map.get("venues", [])
    # Resolve venue display names: prefer venue_names map, fall back to id
    venue_names_map = local_map.get("venue_names", {})

    # -----------------------------------------------------------------------
    # World backdrop
    # -----------------------------------------------------------------------
    world_name = summary.get("world_name", "?")
    tone = summary.get("tone", "?")
    central_conflict = summary.get("central_conflict", "?")

    # -----------------------------------------------------------------------
    # Narration
    # -----------------------------------------------------------------------
    narration_excerpt = summary.get("narration_excerpt", "")

    # -----------------------------------------------------------------------
    # Counts
    # -----------------------------------------------------------------------
    n_regions = summary.get("n_regions", "?")
    n_factions = summary.get("n_factions", "?")
    n_npcs = summary.get("n_npcs", "?")
    n_lore = summary.get("n_lore", "?")

    # -----------------------------------------------------------------------
    # Emit the block
    # -----------------------------------------------------------------------
    sep = "-" * 40
    out(sep)
    out(header)
    out(sep)

    # Protagonist
    out(f"【主角】{prot_name} — {prot_origin}（身世）")
    out(f"  长期目标：{prot_goal}")

    # Location — show venue NAMES (not ids)
    venue_display_names = [venue_names_map.get(vid, vid) for vid in venue_ids]
    venues_str = "、".join(venue_display_names) if venue_display_names else "?"
    out(f"【当前所在】{region_name} > {town_name}（场所：{venues_str}）")

    # World backdrop
    out(f"【世界背景】{world_name}（{tone}）")
    out(f"  核心冲突：{central_conflict}")

    # Objective
    out(f"【当前目标】{objective}")

    # Opening narration
    if narration_excerpt:
        out(f"【开场】{narration_excerpt}")

    # Counts footer
    out(f"  [ 大区域数：{n_regions}  势力数：{n_factions}  NPC 数：{n_npcs}  暗线数：{n_lore} ]")
    out(sep)


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
    parser.add_argument(
        "--debug", action="store_true",
        help="record a structured trajectory trace to <campaign>/trace.jsonl for debugging",
    )
    parser.add_argument(
        "--verbosity", default=None, choices=["concise", "medium", "rich"],
        help="Narration verbosity level: concise | medium | rich "
             "(default: env RPG_NARRATION_VERBOSITY, fallback 'medium'). "
             "Controls how much atmosphere vs plot-forward prose the DM produces.",
    )
    args = parser.parse_args(argv)
    configure_logging()  # honor RPG_DEBUG / RPG_LOG_LEVEL so process logs appear

    # Apply --verbosity flag (only when provided; env/default already set at import)
    if args.verbosity is not None:
        from engine import settings as _eng_settings
        _eng_settings.set_verbosity(args.verbosity)

    from pathlib import Path
    from app.engine import build_engine, new_game
    from app.play import play_loop

    campaign_dir = Path(args.campaign)

    # Handle --debug flag: set RPG_DEBUG_TRACE env var and reset tracer singleton
    # so genesis is traced too
    if args.debug and "RPG_DEBUG_TRACE" not in os.environ:
        trace_path = campaign_dir / "trace.jsonl"
        os.environ["RPG_DEBUG_TRACE"] = str(trace_path)
        import kernel.observability as _obs
        # get_tracer() caches one DebugTracer by path; reset so it rebinds to this run's path.
        _obs._DEBUG_TRACER = None
        out(f"[debug] 轨迹 → {trace_path}  (查看: python -m app.trace {trace_path} [--turn N|--phase X|--show SEQ|--tree|--stats])")

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

        # Progress callback — prints [i/total] 正在生成<label>... via out (Fix #1)
        def _progress_cb(step_idx: int, total_steps: int, label: str) -> None:
            out(f"[{step_idx}/{total_steps}] 正在生成{label}…")

        result = new_game(engine, pitch, progress=_progress_cb)

        # Print rich INTRO block so the player can review the new world (Fix #2)
        _print_intro(result, out)

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

        # _first_action holds the player's first non-reroll, non-break line (if any),
        # to be prepended to inputs_iter so play_loop runs it as turn 1.
        _first_action: str | None = None

        for line in inputs_iter:
            line = line.rstrip("\n")
            stripped = line.strip().lower()

            if stripped in _BREAK_TOKENS:
                # Bare break token (empty / 开始 / start) — start game with no forced turn
                break

            if stripped == "reroll":
                out("[重掷] 正在重新生成整个世界…")
                result = _bootstrap_mod.reroll_all(engine, result)
                _print_intro(result, out, header="[新世界]")
                out("[提示] 输入 'reroll' 再次重掷，或 '开始' / 'start' / 回车进入游戏。")
                continue

            if stripped.startswith("reroll "):
                step_name = stripped[len("reroll "):].strip()
                if step_name in _LEAF_STEPS:
                    out(f"[重掷] 正在重掷 {step_name}…")
                    result = _bootstrap_mod.reroll_step(engine, result, step_name)
                    _print_intro(result, out, header=f"[新{step_name}]")
                    out("[提示] 输入 'reroll' 再次重掷，或 '开始' / 'start' / 回车进入游戏。")
                else:
                    out(f"[未知重掷步骤] '{step_name}' — 可用：factions / npcs / threads")
                continue

            # Unknown command / first real player action — break into game AND
            # preserve this line as turn 1 (Feed #11 fix: do NOT discard the line).
            out(f"[提示] 未识别指令 '{line}' — 进入游戏。")
            _first_action = line
            break

        out("[新游戏] 世界已就绪。输入行动描述开始游戏，/help 查看指令。")
        # If the player typed their first action in the reroll loop, prepend it so
        # play_loop receives it as turn 1 rather than losing it.
        if _first_action is not None:
            inputs_iter = itertools.chain([_first_action], inputs_iter)
    else:
        out(f"[载入存档] 已读取 {len(events)} 条事件。")

    transcript_path = Path(args.transcript) if args.transcript else (campaign_dir / "transcript.jsonl")
    out(f"[transcript] 逐回合记录写入 {transcript_path}")
    play_loop(engine, inputs=inputs_iter, out=out, compare=args.compare,
              transcript_path=transcript_path, max_repairs=args.max_repairs)


if __name__ == "__main__":
    main()
