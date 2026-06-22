"""app.play — play_loop + OOC command dispatch.

play_loop(engine, inputs, *, out=print, strategy=None)
    Iterates over an input iterable (e.g. sys.stdin or a list for tests).
    Lines starting with '/' are dispatched as OOC commands.
    All other lines run a turn via run_turn (or run_compare when toggled).

OOC commands:
    /quit          — stop the loop
    /recall <q>    — fan-out recall and print hits (no turn)
    /compare on    — switch to run_compare mode
    /compare off   — switch back to run_turn mode
    /help          — print command list

The play_loop is a plain function (not a generator) so tests can pass a
list for inputs and a collector for out= without any async/generator magic.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable, Any

from kernel.observability import get_tracer
from kernel.recall import recall as kernel_recall
from loop.turn import run_turn, apply_turn, advanced_day, REQUIRED_SECTIONS
from loop.compare import run_compare
from loop.strategy import AuthorStrategy
from app.engine import rewind as _rewind, last_turn as _last_turn
from engine.log import get_logger

log = get_logger("app.play")

_HELP_TEXT = """\
OOC 指令 (以 / 开头):
  /quit           — 退出游戏
  /recall <关键词>  — 在记忆中搜索
  /compare on|off — 开/关 甲丙策略对比模式
  /rewind <N>     — 回退到第 N 回合之前（撤回 N 及之后的所有事件）
  /undo           — 撤销最后一回合（别名：/oops, //veto）
  //retcon <N>    — 同 /rewind <N>（OOC 别名）
  //veto          — 同 /undo（OOC 别名）
  /help           — 显示此帮助
"""


def _build_scene(engine) -> dict:
    """Construct a scene dict from the current world state.

    protagonist: first tracked Person in the graph (fallback 'protagonist').
    location:    derived from g.neighbors(protagonist, 'located_in', day) first result;
                 falls back to meta['scene'] if the protagonist has no located_in edge.
    present:     every OTHER tracked Person whose current located_in place equals
                 the protagonist's place.  If the protagonist has no location, present=[].
    """
    world = engine.world
    meta = world.get("meta", {})
    g = world.get("systems", {}).get("ontology")

    # Find protagonist (first tracked Person)
    protagonist_id = "protagonist"
    if g:
        for eid, e in g.entities.items():
            if e.etype == "Person" and e.tier == "tracked":
                protagonist_id = eid
                break

    day = meta.get("day") or 1
    scene_id = meta.get("scene") or "scene"

    # Derive protagonist's current location from the bitemporal graph
    protagonist_place: str | None = None
    if g:
        locs = g.neighbors(protagonist_id, "located_in", day)
        protagonist_place = locs[0] if locs else None

    # location: graph-derived if available, else meta scene fallback
    location = protagonist_place if protagonist_place is not None else None

    # present: co-located tracked Persons only; empty if protagonist has no place
    present: list[str] = []
    if g and protagonist_place is not None:
        for eid, e in g.entities.items():
            if e.etype == "Person" and e.tier == "tracked" and eid != protagonist_id:
                npc_locs = g.neighbors(eid, "located_in", day)
                if npc_locs and npc_locs[0] == protagonist_place:
                    present.append(eid)

    return {
        "protagonist": protagonist_id,
        "present": present,
        "day": day,
        "id": scene_id,
        "location": location,
    }


def dispatch_ooc(cmd: str, engine, *, out: Callable, compare_mode: list) -> bool:
    """Dispatch an OOC command. Returns True if the loop should stop.

    compare_mode is a mutable list[bool] used as a flag so the caller can
    detect mode changes without a nonlocal variable.

    Handles both single-slash (/foo) and double-slash (//foo) forms.
    After stripping the first '/', the remaining may start with '/' for the
    double-slash aliases (//retcon, //veto → /retcon, /veto).
    """
    rest = cmd[1:].strip()  # strip the leading '/'

    # Handle double-slash forms: //retcon <n> and //veto
    # After stripping the first '/', rest starts with '/' → double-slash form.
    if rest.startswith("/"):
        inner = rest[1:].strip()  # strip the second '/'
        inner_parts = inner.split(None, 1)
        inner_verb = inner_parts[0].lower() if inner_parts else ""
        inner_arg = inner_parts[1] if len(inner_parts) > 1 else ""

        if inner_verb == "retcon":
            # //retcon <n> is an alias for /rewind <n>
            rest = f"rewind {inner_arg}".strip()
        elif inner_verb == "veto":
            # //veto is an alias for /undo
            rest = "undo"
        elif inner_verb == "steer":
            # //steer: placeholder for v1 — inform the user it's not yet implemented
            out("[//steer] 方向引导功能将在后续版本实装，本回合已记录您的意图。")
            return False
        else:
            out(f"[未知指令] //{inner_verb} — 输入 /help 查看可用指令")
            return False

    parts = rest.split(None, 1)
    verb = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if verb == "quit":
        out("[游戏结束]")
        return True

    elif verb == "recall":
        q = arg.strip()
        if not q:
            out("[recall] 请提供关键词，例如：/recall 桥")
            return False
        hits = kernel_recall(engine.registry, q, engine.world)
        if hits:
            for h in hits[:5]:
                out(f"[recall] {h.text}")
        else:
            out("[recall] 无")
        return False

    elif verb == "compare":
        flag = arg.strip().lower()
        if flag == "on":
            compare_mode[0] = True
            out("[对比模式已开启] 下次行动将运行甲丙双策略")
        elif flag == "off":
            compare_mode[0] = False
            out("[对比模式已关闭]")
        else:
            out(f"[compare] 用法：/compare on|off")
        return False

    elif verb == "rewind":
        turn_str = arg.strip()
        try:
            turn_n = int(turn_str)
        except (ValueError, TypeError):
            out(f"[倒带] 用法：/rewind <回合数>  例如：/rewind 3")
            return False
        result = _rewind(engine, turn_n)
        out(f"[倒带] 已回退到第 {turn_n} 回合之前，撤回 {result['retracted']} 个事件。")
        return False

    elif verb in ("undo", "oops"):
        t = _last_turn(engine)
        result = _rewind(engine, t)
        out(f"[倒带] 已撤销第 {t} 回合，撤回 {result['retracted']} 个事件。")
        return False

    elif verb == "help":
        out(_HELP_TEXT)
        return False

    else:
        out(f"[未知指令] /{verb} — 输入 /help 查看可用指令")
        return False


def _write_transcript(path, record: dict) -> None:
    """Append one JSON record (a turn) to the transcript file, if a path is set."""
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        log.exception("transcript write failed")


def _candidate_record(commit, attempts, dropped) -> dict:
    return {
        "narration": commit.narration,
        "sections": commit.sections,
        "reasons": commit.reasons,
        "repair_attempts": attempts,
        "dropped": list(dropped or []),
    }


def play_loop(
    engine,
    inputs: Iterable[str],
    *,
    out: Callable = print,
    strategy=None,
    compare: bool = False,
    transcript_path=None,
    max_repairs: int = 6,
    required_sections: frozenset = REQUIRED_SECTIONS,
) -> None:
    """REPL play loop.

    Args:
        engine:          Engine instance (from build_engine).
        inputs:          Iterable of input lines (stdin lines or scripted list).
        out:             Output function (default print; pass a collector in tests).
        strategy:        Override the turn strategy (default AuthorStrategy).
        compare:         Start in compare mode (甲/乙 dual strategy).
        transcript_path: If set, append one JSON record per turn (both candidates
                         in compare mode) for later review/evaluation.
        max_repairs:     Validation-repair rounds per turn before dropping bad
                         sections — the strict gate bounces malformed output back
                         to the LLM until it passes or the cap is hit.
    """
    if strategy is None:
        strategy = AuthorStrategy()

    compare_mode = [bool(compare)]  # mutable flag
    turn_no = 0
    prev_scene = None  # track previous turn's scene for catch-up enter-scope detection

    for line in inputs:
        line = line.rstrip("\n")
        if not line:
            continue

        if line.startswith("/"):
            stop = dispatch_ooc(line, engine, out=out, compare_mode=compare_mode)
            if stop:
                break
            continue

        # Normal player input — run a turn
        scene = _build_scene(engine)
        player_input = line
        turn_no += 1

        get_tracer().event("player_input", text=player_input, turn=turn_no)

        try:
            if compare_mode[0]:
                # run_compare: produce 甲+丙 on the same snapshot; show both, apply 甲.
                results = run_compare(
                    engine.registry,
                    engine.world,
                    scene,
                    player_input,
                    provider=engine.provider,
                    embedder=engine.embedder,
                    max_repairs=max_repairs,
                    required_sections=required_sections,
                )
                out("[对比模式]")
                rec = {"turn": turn_no, "input": player_input, "mode": "compare"}
                for label, (commit, attempts, dropped) in results.items():
                    out(f"[{label}策略] {commit.narration}")
                    if dropped:
                        out(f"  (丢弃段落: {dropped})")
                    rec[label] = _candidate_record(commit, attempts, dropped)
                rec["applied"] = "甲"
                _write_transcript(transcript_path, rec)
                # Apply the 甲 result by default (callers can override).
                # Use advanced_day (same logic as run_turn) so the compare path
                # stamps events at the post-clock-advance day, not the frozen
                # pre-turn scene day — fixes the "frozen time in compare mode" bug.
                jia_commit, _, _ = results["甲"]
                new_world = apply_turn(
                    engine.registry, engine.store, jia_commit,
                    day=advanced_day(engine.world, jia_commit), scene=scene["id"],
                )
                engine.world = new_world
            else:
                result = run_turn(
                    engine.registry,
                    engine.store,
                    engine.world,
                    scene,
                    player_input,
                    strategy=strategy,
                    provider=engine.provider,
                    embedder=engine.embedder,
                    max_repairs=max_repairs,
                    required_sections=required_sections,
                    cascade_provider=engine.cascade_provider,
                    catchup_provider=engine.cascade_provider,
                    prev_scene=prev_scene,
                )
                engine.world = result.world
                prev_scene = scene  # update for next turn's enter-scope detection
                out(result.narration)
                if result.dropped_sections:
                    out(f"[提示：{len(result.dropped_sections)}个段落因验证失败已丢弃]")
                _write_transcript(transcript_path, {
                    "turn": turn_no, "input": player_input, "mode": "single",
                    **_candidate_record(result.commit, result.repair_attempts,
                                        result.dropped_sections),
                })

        except Exception as exc:
            log.exception("play_loop: turn error: %s", exc)
            out(f"[错误] {exc}")
            _write_transcript(transcript_path, {
                "turn": turn_no, "input": player_input, "error": str(exc)})
