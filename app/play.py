"""app.play — play_loop + OOC command dispatch.

play_loop(engine, inputs, *, out=print, strategy=None)
    Iterates over an input iterable (e.g. sys.stdin or a list for tests).
    Lines starting with '/' are dispatched as OOC commands.
    All other lines run a turn via run_turn (or run_compare when toggled).

OOC commands:
    /quit          — stop the loop
    /recall <q>    — fan-out recall and print hits (no turn)
    /memory        — show this protagonist's opt-in offline private interpretations
    /compare on    — switch to run_compare mode
    /compare off   — switch back to run_turn mode
    /help          — print command list

The play_loop is a plain function (not a generator) so tests can pass a
list for inputs and a collector for out= without any async/generator magic.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from typing import Callable, Iterable, Any

from app.player_recall import player_recall
from app.player_objectives import player_objectives
from app.player_character import player_character_facts
from app.player_people import player_present_people
from app.player_observations import player_place_observations
from app.player_resources import player_resource_outcome
from context.access import pov_world
from context.capabilities import scene_capabilities
from engine.store import RevisionConflict
from kernel.clock import band_name

# ---------------------------------------------------------------------------
# #4 — Loading indicator while a turn is generated
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ["⏳ DM 落笔中   ", "⏳ DM 落笔中.  ", "⏳ DM 落笔中.. ", "⏳ DM 落笔中..."]
_SPINNER_INTERVAL = 0.35  # seconds per frame


class _Spinner:
    """Content-free "engine is working" indicator.

    TTY path  — background thread writes animated dots directly to sys.stdout
                using \\r to overwrite the same line.  Cleared on stop().
    Non-TTY   — prints a single plain line via the injected ``out`` callable
                (no thread, deterministic for tests/pipes).
    """

    def __init__(self, out: Callable, *, _force_tty: bool | None = None):
        self._out = out
        self._is_tty = (
            sys.stdout.isatty() if _force_tty is None else _force_tty
        )
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._is_tty:
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            # Non-TTY: single plain line through the injected output channel.
            self._out("（DM 落笔中…）")

    def stop(self) -> None:
        if self._is_tty and self._stop_event is not None:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            # Clear the spinner line so the DM narration starts clean.
            sys.stdout.write("\r" + " " * max(len(f) for f in _SPINNER_FRAMES) + "\r")
            sys.stdout.flush()

    def _run(self) -> None:
        idx = 0
        while not self._stop_event.is_set():
            frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
            sys.stdout.write("\r" + frame)
            sys.stdout.flush()
            idx += 1
            self._stop_event.wait(timeout=_SPINNER_INTERVAL)


# ---------------------------------------------------------------------------
# #5 — Distinguish player vs DM visually
# ---------------------------------------------------------------------------

_ANSI_DIM = "\x1b[2m"
_ANSI_RESET = "\x1b[0m"
# DM header separator
_DM_SEPARATOR_TTY = "─" * 40
_DM_SEPARATOR_PLAIN = "-" * 40


def _echo_player(text: str, out: Callable, *, _force_tty: bool | None = None) -> None:
    """Echo the sanitised player input with a '▶ 你：' prefix.

    TTY  — dim ANSI color so it recedes behind the DM prose.
    Non-TTY — plain ASCII prefix, no color codes.
    """
    is_tty = sys.stdout.isatty() if _force_tty is None else _force_tty
    if is_tty:
        line = f"{_ANSI_DIM}▶ 你：{text}{_ANSI_RESET}"
    else:
        line = f"> 你：{text}"
    out(line)


def _print_dm_narration(narration: str, out: Callable, *, _force_tty: bool | None = None) -> None:
    """Print the DM narration under a clear visual marker.

    TTY  — uses a Unicode separator rule + 【DM】 header.
    Non-TTY — uses plain ASCII dashes + [DM] header (safe for pipes/tests).
    """
    is_tty = sys.stdout.isatty() if _force_tty is None else _force_tty
    if is_tty:
        out(_DM_SEPARATOR_TTY)
        out("【DM】")
    else:
        out(_DM_SEPARATOR_PLAIN)
        out("[DM]")
    out(narration)


def _print_turn_status(result, out: Callable, *, protagonist: str | None = None) -> None:
    """Render durable action status without exposing backstage error details."""
    receipt = getattr(result, "receipt", {}) or {}
    events = getattr(result, "events", []) or []
    turn = next((event.get("turn") for event in events if event.get("turn") is not None), "?")
    revision = receipt.get("revision", "?")
    out(f"[已保存] 第 {turn} 回合，版本 {revision}。")
    recap_status = getattr(result, "recap_status", "not_due")
    if recap_status == "saved":
        out("[摘要] 已更新。")
    elif recap_status == "pending":
        out("[摘要] 暂未生成；本回合已保存。")
    outcome = player_resource_outcome(getattr(result, "world", {}), protagonist)
    if outcome is not None:
        verdict = "已扣除" if outcome["outcome"] == "spent" else "余额不足，未扣除"
        out(f"[资源] 第 {outcome['turn']} 回合：{outcome['resource']} 请求 {outcome['requested_amount']}；"
            f"{verdict}，当前 {outcome['balance']}。")

# ---------------------------------------------------------------------------
# Input sanitizer — strips terminal control/ANSI noise from raw stdin lines.
# ---------------------------------------------------------------------------

# CSI sequences: ESC [ ... final-byte  (params: 0-9 ; ? and intermediates: space-/)
_RE_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Other ESC sequences: ESC <any non-[> char  (SS2/SS3/OSC/DCS/etc.)
_RE_ESC_OTHER = re.compile(r"\x1b[^\x1b]?")
# Remaining stray ESC bytes (bare ESC with nothing after)
_RE_ESC_BARE = re.compile(r"\x1b")
# C0 control characters EXCEPT HT (0x09 / \t): 0x00-0x08, 0x0a-0x1f
# (0x0a = \n is already stripped by rstrip; keep \t for tab-completion pastes)
_RE_C0 = re.compile(r"[\x00-\x08\x0a-\x1f]")


def _sanitize_input(line: str) -> str:
    """Strip ANSI/CSI escape sequences and stray C0 control characters from *line*.

    Returns the cleaned string with leading/trailing whitespace stripped.
    Printable ASCII, CJK characters, and tab (\t) are preserved.

    Examples:
        _sanitize_input("\x1b[B\x1b[A\x1b[B")  -> ""
        _sanitize_input("\x1b[B赶紧进去")        -> "赶紧进去"
        _sanitize_input("我环顾四周")            -> "我环顾四周"
        _sanitize_input("\x02[")               -> ""   (Ctrl-b tmux prefix)
    """
    s = _RE_CSI.sub("", line)
    s = _RE_ESC_OTHER.sub("", s)
    s = _RE_ESC_BARE.sub("", s)
    s = _RE_C0.sub("", s)
    return s.strip()


from kernel.observability import get_tracer
from kernel.projection import project
from loop.turn import run_turn, apply_turn, advanced_day, REQUIRED_SECTIONS
from loop.compare import run_compare
from loop.strategy import AuthorStrategy
from app.engine import (rewind as _rewind, last_turn as _last_turn,
                        last_turn_and_revision as _last_turn_and_revision,
                        projected_revision, reproject_engine_world,
                        refresh_engine_world_if_advanced)
from engine.log import get_logger

log = get_logger("app.play")

_HELP_TEXT = """\
OOC 指令 (以 / 开头):
  /quit                — 退出游戏
  /recall <关键词>       — 在记忆中搜索
  /codex [关键词]        — 浏览公开世界设定
  /journal              — 查看最近公开叙事
  /status               — 查看当前公开位置、同场角色、背包与出口
  /map                  — 查看已知地点与公开路径
  /memory              — 查看当前主角的离线私有解释（实验功能；须启动时显式启用）
  /speak <显示名或 ID> <原话> — 明确对当前公开同场角色说出原话（显示名有空格时加引号；仅该句可记忆）
  /compare on|off      — 开/关 甲丙策略对比模式
  /rewind <N>          — 回退到第 N 回合之前（撤回 N 及之后的所有事件）
  /undo                — 撤销最后一回合（别名：/oops, //veto）
  //retcon <N>         — 同 /rewind <N>（OOC 别名）
  //veto               — 同 /undo（OOC 别名）
  //steer <方向>        — 软转向后续叙事；//steer clear 清除（不改写世界）
  /verbosity [级别]    — 查询或设置叙事详细程度（concise|medium|rich）
  /style [文风]        — 查询或设置叙事文风/笔调（如「日式轻小说」；/style clear 清空）
  /help                — 显示此帮助
"""


def _build_scene(engine) -> dict:
    """Construct a scene dict from the current world state.

    protagonist: canonical 'protagonist' when present, otherwise first tracked Person.
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
    if g and g.get_entity(protagonist_id) is None:
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


def _previous_scene_before_latest_action(engine) -> dict | None:
    """Recover the prior action-start scene after a cold reopen.

    Catch-up is intentionally based on whether a tracked entity entered the
    player's scope since the previous *durable player action*.  That context
    used to be session-only, so reopening could make every stale co-located
    entity look newly arrived.  The receipt range already identifies the last
    fully active publish; replaying the immutable prefix before it recreates
    the same scene without writing, generating, or trusting client data.

    No active receipt (for example a freshly seeded campaign) remains the
    existing first-action behavior and returns ``None``.
    """
    recovered = engine.store.latest_active_action_prefix()
    if recovered is None:
        return None
    _receipt, history = recovered
    if not history:
        return None
    prior_world = project(engine.registry, history)

    class _SceneEngine:
        world = prior_world

    return _build_scene(_SceneEngine())


def _refresh_if_store_advanced(engine, previous_scene: dict | None, strategy) -> dict | None:
    """Catch a peer publish before a long-running CLI reads or authors again.

    The REPL keeps its projected world and author conversation in memory, while
    another local CLI or dashboard may have durably changed the same campaign.
    A regular OOC read has no optimistic write conflict to force a refresh, so
    compare the cached revision before each non-empty input.  This mirrors the
    dashboard's passive synchronization and stays strictly read-only: no event,
    receipt, transcript, or retry is created by observing the peer history.
    """
    if not refresh_engine_world_if_advanced(engine):
        return previous_scene
    if hasattr(strategy, "reset"):
        strategy.reset()
    return _previous_scene_before_latest_action(engine)


def _player_label(graph, entity_id: str) -> str:
    """Return an ID-free player label from an already projected graph."""
    entity = graph.get_entity(entity_id) if graph is not None else None
    if entity is None:
        return ""
    return str(entity.attrs.get("label") or entity.attrs.get("name")
               or entity.attrs.get("sketch") or entity_id)


def _public_speech_recipients(engine, scene: dict) -> list[dict[str, str]]:
    """Return the same safe recipient choices used by the player dashboard."""
    return [{"id": person["id"], "label": person["label"]}
            for person in player_present_people(engine.world, scene)]


def _print_player_status(engine, out: Callable) -> None:
    """Render a compact, read-only CLI orientation panel from protagonist POV."""
    scene = _build_scene(engine)
    protagonist = scene.get("protagonist")
    view = pov_world(engine.world, scene, pov=protagonist, redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    capability = scene_capabilities(view, scene, actor=protagonist)
    day = scene.get("day") or 1
    band = band_name((view.get("meta", {}) or {}).get("band", 0))
    location = next((place for place in capability.places if place.id == capability.location), None)
    out(f"[状态] 第 {day} 天 · {band} · 版本 {projected_revision(engine)}")
    out(f"[状态] 当前位置：{location.label if location else '未确定'}")

    character_facts = player_character_facts(view, protagonist)
    rendered_facts = [f"{fact['name']}={fact['value']}" for fact in character_facts]
    out(f"[状态] 角色状态：{'、'.join(rendered_facts) if rendered_facts else '无公开状态'}")

    people = []
    for person in player_present_people(engine.world, scene):
        details = "、".join(f"{item['name']}：{item['value']}" for item in person["observables"])
        people.append(f"{person['label']}（{details}）" if details else person["label"])
    out(f"[状态] 同场角色：{'、'.join(people) if people else '无'}")

    observations = player_place_observations(engine.world, scene)
    if observations:
        rendered_observations = "；".join(
            f"{_observation_scope(item)}·{item['range']}：{item['summary']}" for item in observations
        )
        out(f"[状态] 近处观察：{rendered_observations}")
    else:
        out("[状态] 近处观察：暂无已保存的公开观察")

    inventory = []
    if graph is not None and isinstance(protagonist, str):
        inventory = [_player_label(graph, item_id) for item_id, entity in graph.entities.items()
                     if entity.etype == "Object" and protagonist in graph.neighbors(item_id, "held_by", day)]
        inventory = [label for label in inventory if label]
    out(f"[状态] 背包：{'、'.join(inventory) if inventory else '空'}")

    costs = {(route.source, route.destination): route.cost for route in capability.routes}
    exits = [f"{place.label}（成本 {costs.get(tuple(sorted((capability.location or '', place.id))), '1')}）"
             for place in capability.exits]
    out(f"[状态] 已知出口：{'、'.join(exits) if exits else '无'}")
    objectives = player_objectives(view, protagonist)
    out(f"[状态] 长期目标：{objectives['long_term_goal'] or '未设定'}")
    active = [item['summary'] for item in objectives['active_quests']]
    out(f"[状态] 公开任务：{'、'.join(active) if active else '无'}")
    completed = [item['summary'] for item in objectives['completed_quests']]
    out(f"[状态] 已结任务：{'、'.join(completed) if completed else '无'}")


def _observation_scope(item: dict[str, Any]) -> str:
    """Label an observation without implying an adjacent place is current."""
    return "这里" if item.get("is_current") is True else f"已知出口：{item.get('place', '未知地点')}"


def _print_player_map(engine, out: Callable) -> None:
    """Render the existing player-POV map contract without a write or lookup."""
    scene = _build_scene(engine)
    protagonist = scene.get("protagonist")
    view = pov_world(engine.world, scene, pov=protagonist, redact_facts=True)
    capability = scene_capabilities(view, scene, actor=protagonist)
    nodes = {place.id: place for place in capability.places}
    if not nodes:
        out("[地图] 暂无已知地点。")
        return
    out("[地图] 已知地点：")
    for place in capability.places:
        marker = "（当前位置）" if place.id == capability.location else ""
        out(f"[地图] {place.label}（{place.kind}）{marker}")
    if not capability.routes:
        out("[地图] 暂无已知路径。")
        return
    out("[地图] 已知路径：")
    for route in capability.routes:
        source = nodes.get(route.source)
        destination = nodes.get(route.destination)
        if source is None or destination is None:
            # Do not fall back to raw ids if an inconsistent graph ever slips
            # through the DTO contract.
            continue
        out(f"[地图] {source.label} ↔ {destination.label}（成本 {route.cost}）")


def _print_public_journal(engine, out: Callable, *, limit: int = 8) -> None:
    """Print recent active player narration from the public-history DTO only."""
    entries: list[tuple[object, str]] = []
    try:
        history = engine.public_history()
    except ValueError:
        # The DTO intentionally rejects an ordinary row that references a
        # private memory id.  Preserve that fail-closed boundary without
        # turning an OOC read into a REPL crash or echoing any stored body.
        out("[journal] 当前公开叙事暂不可用；请检查当前状态。")
        return
    for event in history:
        if event.get("retracted") or event.get("type") != "narration_recorded":
            continue
        deltas = event.get("deltas") if isinstance(event.get("deltas"), dict) else {}
        text = deltas.get("text")
        if isinstance(text, str) and text:
            entries.append((event.get("turn", "?"), text))
    if not entries:
        out("[journal] 暂无公开叙事。")
        return
    for turn, text in entries[-limit:]:
        out(f"[journal] 第 {turn} 回合：{text}")


def _resolve_public_speech_recipient(engine, scene: dict, remainder: str) -> tuple[str, str] | None:
    """Resolve one exact, current public recipient label or id for ``/speak``.

    The terminal status view deliberately exposes player-facing labels, while
    the durable communication receipt must retain the canonical recipient id.
    Accepting a label only when it maps to one public co-located person keeps
    those two normal-player surfaces usable without turning names into a
    discovery query.  Ambiguous labels, hidden people, and stale labels all
    fail closed; the receipt helper remains the authority for co-presence.
    """
    raw = remainder.strip()
    if not raw:
        return None
    public_people = _public_speech_recipients(engine, scene)

    recipient_text: str
    statement: str
    if raw[:1] in {'"', "'"}:
        quote = raw[0]
        end = raw.find(quote, 1)
        if end <= 1:
            return None
        recipient_text = raw[1:end]
        statement = raw[end + 1:].strip()
    else:
        # Display names may themselves contain spaces.  Match only complete
        # aliases at the beginning of the supplied text, taking the longest
        # first so a shorter public label cannot consume a longer one.
        recipient_text = ""
        statement = ""
        aliases = {
            alias
            for person in public_people
            for alias in (person["id"], person["label"])
            if isinstance(alias, str) and alias
        }
        folded = raw.casefold()
        for alias in sorted(aliases, key=len, reverse=True):
            if folded.startswith(alias.casefold()) and len(raw) > len(alias) and raw[len(alias)].isspace():
                recipient_text = raw[:len(alias)]
                statement = raw[len(alias):].strip()
                break
        if not recipient_text:
            return None

    if not statement:
        return None
    matched = {
        person["id"]
        for person in public_people
        if recipient_text.casefold() in {
            person["id"].casefold(), person["label"].casefold(),
        }
    }
    if len(matched) != 1:
        return None
    return next(iter(matched)), statement


def dispatch_ooc(cmd: str, engine, *, out: Callable, compare_mode: list,
                 steering: list[str | None] | None = None) -> bool:
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
            # A soft direction is deliberately a session-only player preference,
            # not a world event or a durable host instruction.  Let the normal
            # branch handle both //steer and the convenient /steer spelling.
            rest = f"steer {inner_arg}".strip()
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
        # The shared player query keeps every player-facing recall adapter on
        # the same POV/redaction boundary.  A guessed hidden id is not a
        # discovery mechanism.
        scene = _build_scene(engine)
        hits = player_recall(engine.registry, engine.world, scene, q)
        if hits:
            for h in hits[:5]:
                out(f"[recall] {h}")
        else:
            out("[recall] 无")
        return False

    elif verb == "codex":
        from systems.codex import codex_entries
        query = arg.strip().casefold()
        entries = [entry for entry in codex_entries(engine.world) if isinstance(entry, dict)]
        if query:
            entries = [entry for entry in entries if query in " ".join(
                str(entry.get(field, "")) for field in ("kind", "title", "body")
            ).casefold()]
        if not entries:
            out("[codex] 没有匹配的公开设定。")
            return False
        for entry in entries[:12]:
            kind = str(entry.get("kind") or "设定")
            title = str(entry.get("title") or entry.get("id") or "未命名")
            body = str(entry.get("body") or "")
            out(f"[codex] [{kind}] {title}：{body}")
        return False

    elif verb == "journal":
        if arg.strip():
            out("[journal] 用法：/journal")
            return False
        _print_public_journal(engine, out)
        return False

    elif verb == "status":
        if arg.strip():
            out("[状态] 用法：/status")
            return False
        _print_player_status(engine, out)
        return False

    elif verb == "map":
        if arg.strip():
            out("[地图] 用法：/map")
            return False
        _print_player_map(engine, out)
        return False

    elif verb == "memory":
        # This is intentionally a host-only, current-protagonist view.  There
        # is no role argument and no fallback to raw store rows, so a player
        # cannot select another character or obtain private text through the
        # normal recall/public-history paths.
        if not getattr(engine, 'interpretive_memory_enabled', False):
            out("[memory] 离线私有记忆实验未启用；启动时传入 --offline-private-memory。")
            return False
        from systems.interpretive_memory import open_memory_read_session
        role = _build_scene(engine)["protagonist"]
        session = open_memory_read_session(engine.world, engine.store, role)
        try:
            handle = session.issue_handle()
            records = session.read(handle, engine.world, engine.store)
        finally:
            session.close()
        out("[memory] 离线实验：仅当前主角的私有解释；不是世界事实。")
        if not records:
            out("[memory] 无")
        else:
            for record in records:
                out(f"[memory] 第 {record.get('turn', '?')} 回合：{record['text']}")
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
        if turn_n < 1:
            out("[倒带] 回合数必须从 1 开始；创世世界不能通过玩家倒带撤销。")
            return False
        result = _rewind(engine, turn_n, expected_revision=engine.store.revision)
        out(f"[倒带] 已回退到第 {turn_n} 回合之前，撤回 {result['retracted']} 个事件。")
        return False

    elif verb in ("undo", "oops"):
        t, revision = _last_turn_and_revision(engine)
        if t < 1:
            out("[倒带] 当前没有可撤销的玩家回合。")
            return False
        result = _rewind(engine, t, expected_revision=revision)
        out(f"[倒带] 已撤销第 {t} 回合，撤回 {result['retracted']} 个事件。")
        return False

    elif verb == "steer":
        if steering is None:
            out("[steer] 此入口未提供会话方向状态。")
            return False
        requested = _sanitize_input(arg)
        if not requested:
            current = steering[0]
            out(f"[steer] 当前软方向：{current or '（未设置）'}")
            return False
        if requested.lower() in {"clear", "off", "none", "清除", "关闭"}:
            steering[0] = None
            out("[steer] 已清除软方向；后续回合只按你的行动和已保存世界推进。")
            return False
        if len(requested) > 500:
            out("[steer] 方向请控制在 500 个字符内。")
            return False
        steering[0] = requested
        out("[steer] 已设置软方向；它只作为后续叙事偏好，不改写世界、路线或权限。")
        return False

    elif verb == "verbosity":
        from engine import settings as _eng_settings
        if not arg:
            # No argument — report current level
            out(f"[verbosity] 当前叙事详细程度：{_eng_settings.get_verbosity()}")
        else:
            level = arg.strip().lower()
            ok = _eng_settings.set_verbosity(level)
            if ok:
                out(f"[verbosity] 叙事详细程度已设为：{level}")
            else:
                out(f"[verbosity] 无效级别 '{level}' — 可用：concise | medium | rich")
        return False

    elif verb == "style":
        from engine import settings as _eng_settings
        if not arg:
            cur = _eng_settings.get_style()
            out(f"[style] 当前叙事文风：{cur or '（中性/默认）'}")
        else:
            s = arg.strip()
            if s.lower() in ("clear", "none", "默认", "中性"):
                s = ""
            _eng_settings.set_style(s)
            out(f"[style] 叙事文风已设为：{s or '（中性/默认）'}")
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
    echo_input: bool = True,
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
    # Session-local, non-event preference.  It is never written to the store,
    # transcript, action receipt, or safe world projection.
    steering: list[str | None] = [None]
    # Transcript and trace rows identify canonical campaign turns, not a
    # counter that happens to start over whenever a terminal process reopens.
    # It is refreshed again immediately before each normal action so rewind,
    # peer publication, and a failed attempt cannot leave the next saved row
    # with a made-up session-local number.
    turn_no = _last_turn(engine)
    # Rebuild the previous durable action-start scene on cold CLI reopen.  It
    # keeps catch-up's enter-scope rule stable across process boundaries.
    prev_scene = _previous_scene_before_latest_action(engine)

    for line in inputs:
        line = line.rstrip("\n")
        if not line:
            continue

        # Strip ANSI/CSI escape sequences and stray C0 control characters.
        # Pure terminal-noise lines (arrows, tmux prefix, etc.) become empty → skip.
        # Mixed lines (e.g. "\x1b[B赶紧进去") → salvaged text runs as a turn.
        cleaned = _sanitize_input(line)
        if not cleaned:
            log.debug("play_loop: skipping pure-noise input %r", line)
            continue
        line = cleaned

        # A peer dashboard/CLI may have committed between two lines of this
        # long-running REPL.  Do this before `/recall`, `/speak`, or normal
        # scene construction so every player-facing read uses one current POV
        # projection and any author cache cannot carry stale history forward.
        prev_scene = _refresh_if_store_advanced(engine, prev_scene, strategy)

        # ``/speak`` is deliberately a turn-producing command, not an OOC
        # parser for ordinary prose.  The user supplies both recipient and
        # exact words; the host then validates current public co-presence and
        # creates the narrow receipt used by the existing publish gateway.
        trusted_player_statement_receipts = ()
        if line.startswith("/speak"):
            command, separator, remainder = line.partition(" ")
            if command.lower() != "/speak":
                separator = ""
            if not separator:
                out("[speak] 用法：/speak <当前公开同场角色显示名或 ID> <逐字原话>")
                continue
            scene_for_receipt = _build_scene(engine)
            parsed_speech = _resolve_public_speech_recipient(engine, scene_for_receipt, remainder)
            if parsed_speech is None:
                out("[speak] 请使用唯一的当前公开同场角色显示名或 ID；含空格的显示名请加引号。")
                continue
            recipient, statement = parsed_speech
            try:
                from context.native_authoring import host_player_statement_receipt
                receipt = host_player_statement_receipt(
                    engine.world, scene_for_receipt, recipient=recipient, statement=statement,
                )
            except ValueError:
                out("[speak] 收件人必须是当前公开同场角色，且原话不能为空。")
                continue
            trusted_player_statement_receipts = (receipt,)
            line = f"我对 {recipient} 说：{statement}"

        if line.startswith("/"):
            revision_before = engine.store.revision
            steer_before = steering[0]
            try:
                stop = dispatch_ooc(line, engine, out=out, compare_mode=compare_mode,
                                    steering=steering)
            except RevisionConflict:
                # `/undo` and `/rewind` are optimistic mutations too.  A peer
                # may publish after this loop refreshed but before SQLite takes
                # the retraction lock; never retract that newer branch.
                reproject_engine_world(engine)
                prev_scene = _previous_scene_before_latest_action(engine)
                if hasattr(strategy, 'reset'):
                    strategy.reset()
                out('[世界状态已更新；未执行撤销，请重新决定是否撤销。]')
                continue
            # The conversational cache contains prior user messages.  Reset it
            # when the soft direction changes so clearing it cannot leave an old
            # steering instruction active merely through model conversation history.
            if steering[0] != steer_before and hasattr(strategy, 'reset'):
                strategy.reset()
            if engine.store.revision != revision_before:
                if hasattr(strategy, 'reset'):
                    strategy.reset()
                # `/undo` and `/rewind` change durable history just like a
                # cold reopen.  Preserve catch-up's entering-scope semantics
                # from the newest surviving player action instead of clearing
                # the transient anchor unconditionally.
                prev_scene = _previous_scene_before_latest_action(engine)
            if stop:
                break
            continue

        # Normal player input — run a turn
        scene = _build_scene(engine)
        if steering[0]:
            scene = {**scene, "_player_steer": steering[0]}
        player_input = line
        turn_no = engine.store.next_turn()

        get_tracer().event("player_input", text=player_input, turn=turn_no)

        # #5 — echo the player's sanitised input with a visual marker.
        # Skipped when an interactive readline prompt ("▶ 你：") already showed it
        # (echo_input=False) — otherwise the line would appear twice.
        if echo_input:
            _echo_player(player_input, out)

        # A turn becomes durable inside run_turn.  Keep this bit separate from
        # presentation so a later stdout/transcript failure never tells the
        # player that an already-published world was rolled back.
        turn_published = False
        try:
            if compare_mode[0]:
                from loop.variation import prepare_variation, variation_fragment
                variation = prepare_variation(engine.registry, engine.world, scene, engine.store.next_turn())
                if variation:
                    scene = {**scene, '_variation_prompt':variation_fragment(variation)}
                # ``run_compare`` produces candidates before ``run_turn``
                # creates its EventBatch.  Give both candidates the same
                # pre-rendered redacted projection that normal AuthorStrategy
                # receives later; no raw EventStore row enters a scene.
                from context.assembler import safe_authoring_handoff
                scene = {
                    **scene,
                    '_safe_authoring_handoff': safe_authoring_handoff(
                        engine.world, scene,
                        trusted_events=tuple(engine.store.iter_events(include_retracted=True)),
                        player_statement_receipts=trusted_player_statement_receipts,
                    ),
                }
                spinner = _Spinner(out)
                spinner.start()
                try:
                    results = run_compare(
                        engine.registry,
                        engine.world,
                        scene,
                        player_input,
                        provider=engine.provider,
                        embedder=engine.embedder,
                        max_repairs=max_repairs,
                        required_sections=required_sections,
                        enforce_player_travel=True,
                        enforce_player_knowledge_authority=True,
                        enforce_player_world_authority=True,
                        enforce_player_fact_authority=True,
                        enforce_player_entity_authority=True,
                        enforce_player_faction_authority=True,
                        enforce_player_place_authority=True,
                        enforce_player_item_authority=True,
                        enforce_player_quest_authority=True,
                        trusted_player_statement_receipts=trusted_player_statement_receipts,
                    )
                finally:
                    spinner.stop()
                jia_commit, _, jia_dropped = results["甲"]
                if jia_dropped:
                    from loop.turn import TurnRejected
                    raise TurnRejected('comparison candidate failed validation')
                # Reuse the prepared candidate through the same complete-action
                # gateway as ordinary play; no second narration model call.
                class PreparedCandidate:
                    def produce(self, *args, **kwargs):
                        return jia_commit
                applied = run_turn(engine.registry, engine.store, engine.world,
                    scene, player_input, strategy=PreparedCandidate(), provider=engine.provider,
                    embedder=engine.embedder, required_sections=required_sections,
                    cascade_provider=engine.cascade_provider, prev_scene=prev_scene,
                    catchup_provider=engine.cascade_provider,
                    memory_role=(scene['protagonist'] if engine.interpretive_memory_enabled else None),
                    memory_generator=engine.interpretive_memory_generator,
                    enforce_player_travel=True,
                    enforce_player_knowledge_authority=True,
                    enforce_player_world_authority=True,
                    enforce_player_fact_authority=True,
                    enforce_player_entity_authority=True,
                    enforce_player_faction_authority=True,
                    enforce_player_place_authority=True,
                    enforce_player_item_authority=True,
                    enforce_player_quest_authority=True,
                    trusted_player_statement_receipts=trusted_player_statement_receipts)
                turn_published = True
                engine.world = applied.world
                prev_scene = scene
                if hasattr(strategy, 'reset'):
                    strategy.reset()
                out("[对比模式]")
                rec = {"turn": turn_no, "revision": applied.receipt["revision"],
                       "action_id": applied.receipt["action_id"],
                       "input": player_input, "mode": "compare"}
                for label, (commit, attempts, dropped) in results.items():
                    # #5 — frame each candidate's narration under its own DM header
                    _print_dm_narration(f"[{label}策略] {commit.narration}", out)
                    if dropped:
                        out(f"  (丢弃段落: {dropped})")
                    rec[label] = _candidate_record(commit, attempts, dropped)
                rec["applied"] = "甲"
                _write_transcript(transcript_path, rec)
                _print_turn_status(applied, out, protagonist=scene.get("protagonist"))
            else:
                spinner = _Spinner(out)
                spinner.start()
                try:
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
                        memory_role=(scene['protagonist'] if engine.interpretive_memory_enabled else None),
                        memory_generator=engine.interpretive_memory_generator,
                        enforce_player_travel=True,
                        enforce_player_knowledge_authority=True,
                        enforce_player_world_authority=True,
                        enforce_player_fact_authority=True,
                        enforce_player_entity_authority=True,
                        enforce_player_faction_authority=True,
                        enforce_player_place_authority=True,
                        enforce_player_item_authority=True,
                        enforce_player_quest_authority=True,
                        trusted_player_statement_receipts=trusted_player_statement_receipts,
                    )
                finally:
                    spinner.stop()
                turn_published = True
                engine.world = result.world
                prev_scene = scene  # update for next turn's enter-scope detection
                # #5 — frame the DM narration under a clear marker
                _print_dm_narration(result.narration, out)
                if result.dropped_sections:
                    # #R7: name the dropped sections (was just a count) so it's clear
                    # WHICH world-changes failed validation and did not take effect.
                    out(f"[提示：{('、'.join(result.dropped_sections))} 段验证失败，"
                        f"本回合这些世界变更未生效]")
                _write_transcript(transcript_path, {
                    "turn": turn_no, "revision": result.receipt["revision"],
                    "action_id": result.receipt["action_id"],
                    "input": player_input, "mode": "single",
                    **_candidate_record(result.commit, result.repair_attempts,
                                        result.dropped_sections),
                })
                _print_turn_status(result, out, protagonist=scene.get("protagonist"))

        except Exception as exc:
            # Provider or extension exceptions may include untrusted/private
            # content.  Keep normal CLI and stderr diagnostics body-free.
            log.error("play_loop: turn failed")
            from loop.turn import TurnRejected
            if isinstance(exc, TurnRejected):
                out('[这一行动尚未完成，世界保持原状。请重试或换一种行动描述。]')
            elif isinstance(exc, RevisionConflict):
                reproject_engine_world(engine)
                # A rejected optimistic turn never becomes the previous action
                # for catch-up.  Another durable action may have won the race,
                # so recover its action-start scene from the same receipt prefix
                # used on cold reopen and OOC rewind instead of retaining this
                # session's stale anchor.
                prev_scene = _previous_scene_before_latest_action(engine)
                if hasattr(strategy, 'reset'):
                    strategy.reset()
                out('[世界状态已更新，请重新尝试这次行动。]')
            else:
                # Both normal CLI output and its player-facing transcript avoid
                # provider errors or private text that an exception happened
                # to include.
                if turn_published:
                    out('[提示] 本回合已保存，但结果显示未完成；请检查当前世界状态。]')
                else:
                    out('[错误] 本回合处理遇到问题；请检查当前世界状态。]')
            _write_transcript(transcript_path, {
                "turn": turn_no, "input": player_input,
                "error": ("turn_output_failed_after_commit" if turn_published
                          else "turn_failed")})
