# engine/check.py
from engine.log import get_logger

log = get_logger("check")

BLOCK = "block"
WARN = "warn"

def _finding(linter, severity, message, suggestion=""):
    return {"linter": linter, "severity": severity, "message": message, "suggestion": suggestion}

def check_thread_completeness(events, proj):
    out = []
    for tid, th in proj["threads"].items():
        if th.get("dormant") or th.get("status") == "已解锁":
            continue
        missing = [k for k in ("endpoint", "beats", "reveal_conditions") if not th.get(k)]
        if missing:
            out.append(_finding("thread_completeness", BLOCK,
                f"暗线 {th.get('name', tid)} 缺 {'/'.join(missing)}",
                "补全设计(终点/关键节点/揭示条件),或设为 dormant 休眠"))
    return out

def check_villain_omniscience(events, proj):
    out = []
    for ev in events:
        if ev["type"] != "villain_knowledge_gain":
            continue
        d = ev.get("deltas", {})
        miss = [k for k in ("source", "channel", "delay") if not d.get(k)]
        if miss:
            who = "/".join(ev.get("actors", [])) or "反派"
            out.append(_finding("villain_omniscience", BLOCK,
                f"反派 {who} 于 {ev['id']} 知情但缺 {'/'.join(miss)}",
                "补 source/channel/delay,否则撤销该事件(DM 作弊)"))
    return out

def check_timeline(events, proj):
    out = []
    max_day = None
    for ev in events:
        day = ev.get("day")
        if day is None:
            continue
        if max_day is not None and day < max_day:
            out.append(_finding("timeline", BLOCK,
                f"{ev['id']} day={day} 早于此前 day={max_day}(时间倒流)",
                "修正 day 或事件顺序"))
        max_day = day if max_day is None else max(max_day, day)
    return out

def check_dangling_refs(events, proj):
    out = []
    opened = set(proj["threads"].keys())
    promise_ids = {p["id"] for p in proj["promises"]}
    for ev in events:
        if ev["type"] == "thread_advance":
            for tid in (ev.get("thread_refs") or []):
                if tid not in opened:
                    out.append(_finding("dangling_ref", WARN,
                        f"{ev['id']} thread_advance 指向未开线 {tid}", "先 thread_open 或修正 thread_refs"))
        elif ev["type"] == "promise_kept":
            ref = ev.get("deltas", {}).get("promise_id")
            if ref and ref not in promise_ids:
                out.append(_finding("dangling_ref", WARN,
                    f"{ev['id']} promise_kept 指向未知承诺 {ref}", "核对 promise_id"))
    return out

_STRUCTURAL = [check_thread_completeness, check_villain_omniscience, check_timeline, check_dangling_refs]

def check(events, proj):
    """Run all linters; return findings sorted BLOCK-first."""
    findings = []
    for linter in _ALL_LINTERS:
        findings.extend(linter(events, proj))
    findings.sort(key=lambda f: 0 if f["severity"] == BLOCK else 1)
    log.debug("check: %d findings (%d block)", len(findings),
              sum(1 for f in findings if f["severity"] == BLOCK))
    return findings

THREAD_STALE_SCENES = 8
CHAR_STALE_EVENTS = 5
PROMISE_STALE_DAYS = 30
_EVOLUTION_TYPES = ("relationship_change", "character_development", "character_reveal")

def check_thread_followup(events, proj):
    from engine.director import _scene_ordinals
    ords, total = _scene_ordinals(events)
    out = []
    for tid, th in proj["threads"].items():
        if th.get("dormant") or th.get("status") == "已解锁":
            continue
        since = total - ords.get(th.get("last_advanced_scene"), 0)
        if since > THREAD_STALE_SCENES:
            beats = th.get("beats") or []
            out.append(_finding("thread_followup", WARN,
                f"暗线 {th.get('name', tid)} 已 {since} 场未推进(久未推进)",
                f"下一拍:{beats[0] if beats else '(待设计)'}"))
    return out

def check_character_staleness(events, proj):
    counts = {}
    for ev in events:
        evolve = ev["type"] in _EVOLUTION_TYPES
        for a in ev.get("actors", []):
            counts[a] = 0 if evolve else counts.get(a, 0) + 1
    out = []
    for a, c in counts.items():
        if c >= CHAR_STALE_EVENTS:
            out.append(_finding("character_staleness", WARN,
                f"角色 {a} 卷入 {c} 个事件未演化", "发 character_development/relationship_change 让人设演化"))
    return out

def check_promise_aging(events, proj):
    made_day = {ev["id"]: ev.get("day", 0) for ev in events if ev["type"] == "promise_made"}
    cur = proj["state"].get("day") or 0
    out = []
    for p in proj["promises"]:
        if p["kept"]:
            continue
        age = cur - made_day.get(p["id"], cur)
        if age > PROMISE_STALE_DAYS:
            out.append(_finding("promise_aging", WARN,
                f"承诺 '{p['text']}' 已挂 {age} 天未兑现", "兑现或推进该承诺"))
    return out

_ALL_LINTERS = _STRUCTURAL + [check_thread_followup, check_character_staleness, check_promise_aging]
