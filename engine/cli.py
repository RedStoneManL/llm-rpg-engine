import hashlib
import json
import os
import sys
import time
from pathlib import Path

from engine.store import EventStore
from engine.schema import make_event
from engine.projection import project, write_projections
from engine.log import get_logger
from engine.archive import ArchiveStore
from engine import recall as recall_mod
from engine.compact import compact as compact_fn
from engine import rewind as rewind_mod
from engine.oracle import Oracle, load_table, scene_seed
from engine import director as director_mod
from engine.seed import seed_campaign
from engine.check import check as run_check, BLOCK

log = get_logger("cli")


def _home() -> Path:
    return Path(os.environ.get("RPG_HOME", Path(__file__).resolve().parent.parent))


def _campaigns() -> Path:
    return _home() / "storage" / "campaigns"


def _current_file() -> Path:
    return _home() / "storage" / "current"


def _campaign_dir(cid=None) -> Path:
    if not cid:
        cf = _current_file()
        cid = cf.read_text().strip() if cf.exists() else None
    if not cid:
        raise SystemExit("no current campaign; run: rpg new <id>")
    return _campaigns() / cid


def _store(d) -> EventStore:
    return EventStore(d / "events.db", d / "events.jsonl")


def _hook_state_path():
    return _home() / "storage" / "hook_state.json"


def _read_hook_state():
    p = _hook_state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"active": False, "campaign": None, "ts": 0}


def _touch_session():
    """Refresh heartbeat ts if a session is active (called by play commands)."""
    st = _read_hook_state()
    if st.get("active"):
        st["ts"] = time.time()
        p = _hook_state_path(); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False))


def cmd_session(args):
    log.debug("cmd session action=%s", args.action)
    p = _hook_state_path(); p.parent.mkdir(parents=True, exist_ok=True)
    if args.action == "on":
        cf = _current_file()
        camp = args.campaign or (cf.read_text().strip() if cf.exists() else None)
        if not camp:
            raise SystemExit("no campaign; run: rpg new <id> first")
        p.write_text(json.dumps({"active": True, "campaign": camp, "ts": time.time()}, ensure_ascii=False))
        print(f"rpg session ON for {camp}(hooks 将注入其工作记忆)")
    elif args.action == "off":
        p.write_text(json.dumps({"active": False, "campaign": None, "ts": time.time()}, ensure_ascii=False))
        print("rpg session OFF")
    else:
        st = _read_hook_state()
        print(f"session active={st.get('active')} campaign={st.get('campaign')}")


def cmd_new(args):
    log.debug("cmd new campaign=%s", getattr(args, "campaign", None))
    d = _campaigns() / args.id
    (d / "projections").mkdir(parents=True, exist_ok=True)
    (d / "archive").mkdir(parents=True, exist_ok=True)
    cf = _current_file()
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(args.id)
    print(f"created campaign {args.id} at {d}")


def cmd_log_event(args):
    log.debug("cmd log-event campaign=%s", getattr(args, "campaign", None))
    d = _campaign_dir(args.campaign)
    _touch_session()
    raw = args.json if args.json else sys.stdin.read()
    payload = json.loads(raw)
    if "id" in payload and "retracted" in payload:
        ev = payload
    else:
        if not payload.get("turn"):
            with ArchiveStore(d / "archive.db") as a:
                cur = a.max_turn() or 1
            payload["turn"] = cur
            log.debug("cmd log-event auto-turn=%s", cur)
        payload.setdefault("actors", [])  # actor-less events (thread_open/world_fact/...) needn't pass actors
        ev = make_event(**payload)
    with _store(d) as s:
        seq = s.append(ev)
    print(f"logged {ev['id']} seq={seq}")


def cmd_project(args):
    log.debug("cmd project campaign=%s", getattr(args, "campaign", None))
    d = _campaign_dir(args.campaign)
    proj_dir = d / "projections"
    if getattr(args, "rebuild", False) and proj_dir.exists():
        import shutil
        shutil.rmtree(proj_dir)
    with _store(d) as s:
        proj = project(s.iter_events())
    write_projections(proj, proj_dir)
    print(f"projected → {proj_dir}")


def cmd_status(args):
    log.debug("cmd status campaign=%s", getattr(args, "campaign", None))
    d = _campaign_dir(args.campaign)
    with _store(d) as s:
        proj = project(s.iter_events())
    with ArchiveStore(d / "archive.db") as a:
        max_turn = a.max_turn()
    s2 = proj["state"]
    open_p = sum(1 for p in proj["promises"] if not p["kept"])
    print(f"campaign={d.name} day={s2['day']} loc={s2['location']} "
          f"chars={len(proj['characters'])} threads={len(proj['threads'])} "
          f"open_promises={open_p} turn={max_turn}")


def cmd_log_turn(args):
    log.debug("cmd log-turn campaign=%s", getattr(args, "campaign", None))
    d = _campaign_dir(args.campaign)
    _touch_session()
    raw = args.json if args.json else sys.stdin.read()
    p = json.loads(raw)
    with ArchiveStore(d / "archive.db") as a:
        if not p.get("turn"):
            turn = a.next_turn()
            log.debug("cmd log-turn auto-turn=%s", turn)
        else:
            turn = p["turn"]
        cid = a.add_chunk(day=p["day"], scene=p["scene"], turn=turn,
                          text=p["text"], entities=p.get("entities"),
                          event_ids=p.get("event_ids"), kind=p.get("kind", "narration"))
    print(f"logged turn {cid}")


def cmd_recap(args):
    log.debug("cmd recap campaign=%s", getattr(args, "campaign", None))
    from engine.compact import build_working_memory
    d = _campaign_dir(args.campaign)
    _touch_session()
    print(build_working_memory(d))


def cmd_recall(args):
    log.debug("cmd recall campaign=%s anchor=%s", getattr(args, "campaign", None), args.anchor)
    d = _campaign_dir(args.campaign)
    if args.anchor:
        hits = recall_mod.recall_anchor(d, args.anchor, actor=args.actor)
    else:
        hits = recall_mod.recall(d, args.query, k=args.k, entity=args.entity,
                                 day=args.day, semantic=args.semantic)
    for h in hits:
        print(f"[{h['chunk_id']} day{h['day']}] {h['text']}")
    if not hits:
        print("(no hits)")


def cmd_reindex(args):
    log.debug("cmd reindex campaign=%s", getattr(args, "campaign", None))
    from engine.embed import get_embedder
    d = _campaign_dir(args.campaign)
    emb = get_embedder()
    n = recall_mod.reindex(d, embedder=emb)
    print(f"reindexed {n} chunk(s)")


def cmd_compact(args):
    log.debug("cmd compact campaign=%s", getattr(args, "campaign", None))
    d = _campaign_dir(args.campaign)
    compact_fn(d)
    print(f"compacted → {d / 'working_memory.md'}")


def cmd_rewind(args):
    log.debug("cmd rewind campaign=%s last=%s to_scene=%s turn=%s",
              getattr(args, "campaign", None), args.last, args.to_scene, args.turn)
    d = _campaign_dir(args.campaign)
    if args.last:
        turn = rewind_mod.last_turn(d)
        if turn <= 0:
            print("nothing to rewind"); return
    elif args.to_scene:
        with ArchiveStore(d / "archive.db") as a:
            turn = a.min_turn_of_scene(args.to_scene)
        if turn is None:
            print(f"scene {args.to_scene} not found"); return
    elif args.turn is not None:
        turn = args.turn
    else:
        raise ValueError("rewind needs <turn> or --last or --to-scene")
    res = rewind_mod.rewind(d, turn)
    print(f"rewound turn>={turn}: -{res['events_retracted']} events, -{res['chunks_removed']} chunks")


def _campaign_seed(cid):
    return int(hashlib.sha256(cid.encode()).hexdigest()[:8], 16)


def cmd_director(args):
    log.debug("cmd director campaign=%s dry_run=%s", getattr(args, "campaign", None), args.dry_run)
    d = _campaign_dir(args.campaign)
    _touch_session()
    with _store(d) as s:
        events = list(s.iter_events())
    pacing = director_mod.compute_pacing(events)
    tables = {"event_types": load_table("event_types"), "twists": load_table("twists")}
    seed_int = scene_seed(_campaign_seed(d.name), pacing["scene_ordinal"],
                          salt=1 if args.reroll else 0)
    out = director_mod.director_check(pacing["scenes_since_event"], pacing["tension"],
                                      Oracle(seed_int), tables=tables)
    if not out["triggered"]:
        print("(quiet scene)")
        return
    et, tw = out["seed"]["event_type"], out["seed"]["twist"]
    # 后台种子(给 DM 编织,前台不直接出现)
    print(f"[DIRECTOR · backstage] type={out['type']} magnitude={out['magnitude']}"
          + (f" valence={out['valence']}" if out['valence'] else ""))
    print(f"  事件原型: {et['name']} — {et.get('hint','')}")
    print(f"  反转: {tw['name']} — {tw.get('hint','')}")
    if out["type"] == "dormant_thread":
        print("  → 隐形埋线:后台登记休眠暗线,前台此刻不显(撞触发器才浮现)")
    if args.dry_run:
        return
    sc = pacing["current_scene"] or "s0"
    day = events[-1]["day"] if events else 0
    with ArchiveStore(d / "archive.db") as a:
        cur_turn = a.max_turn() or 1   # stamp current turn so /veto (rewind) can retract these
    with _store(d) as s:
        s.append(make_event("oracle_roll", day, sc, [], f"暗骰 roll={out['roll']:.2f} prob={out['prob']:.2f}",
                            turn=cur_turn, deltas={"prob": out["prob"], "roll": out["roll"]}))
        if out["type"] == "front_stage":
            s.append(make_event("director_fired", day, sc, [],
                                f"突发:{et['name']}({tw['name']})", turn=cur_turn,
                                deltas={"magnitude": out["magnitude"], "valence": out["valence"],
                                        "event_type": et["name"], "twist": tw["name"]}))
        else:
            # 休眠埋线:thread_open dormant(完整性闸门在 P5 校验)
            s.append(make_event("thread_open", day, sc, [],
                                f"休眠暗线种子:{et['name']}", turn=cur_turn,
                                deltas={"dormant": True, "type": et["name"],
                                        "trigger": "(待 DM 具体化)", "twist": tw["name"]}))


def cmd_doctor(args):
    import tempfile
    log.debug("cmd doctor")
    checks = []
    # 用独立临时 RPG_HOME 跑,不污染真实 storage —— 通过临时目录 + 直接调用 engine
    from engine.archive import ArchiveStore
    from engine.recall import recall as _recall
    from engine.compact import compact as _compact
    from engine.rewind import rewind as _rewind
    from engine.store import EventStore
    from engine.schema import make_event
    with tempfile.TemporaryDirectory() as tmp:
        cd = Path(tmp) / "camp"; (cd / "projections").mkdir(parents=True)
        try:
            with ArchiveStore(cd / "archive.db") as a:
                a.add_chunk(day=1, scene="s1", turn=1, text="自检台词")
            checks.append(("archive", True))
            with EventStore(cd / "events.db", cd / "events.jsonl") as s:
                s.append(make_event("location_change", 1, "s1", ["x"], "到某地",
                                    deltas={"location": "loc"}, turn=1))
            checks.append(("events", True))
            hits = _recall(cd, "自检台词", embedder=None)
            checks.append(("recall", any("自检台词" in h["text"] for h in hits)))
            _compact(cd)
            checks.append(("compact", (cd / "working_memory.md").exists()))
            res = _rewind(cd, 1)
            checks.append(("rewind", res["chunks_removed"] == 1))
        except Exception as e:
            checks.append((f"error:{type(e).__name__}", False))
    for name, ok in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("doctor: all OK"); return
    raise SystemExit("doctor: some checks FAILED")


def cmd_threads_next(args):
    log.debug("cmd threads next campaign=%s", getattr(args, "campaign", None))
    d = _campaign_dir(args.campaign)
    with _store(d) as s:
        events = list(s.iter_events())
    from engine.projection import project
    threads = project(events)["threads"]
    tid = director_mod.pick_thread_to_advance(events, threads, Oracle(_campaign_seed(d.name) + len(events)))
    active = [t for t in threads.values() if t.get("status") != "已解锁" and not t.get("dormant")]
    if tid:
        th = threads[tid]
        beats = th.get("beats") or []
        print(f"[THREADS · backstage] 该推:{th.get('name', tid)}(进度 {th.get('progress')})"
              f" → 下一拍:{beats[0] if beats else '(待设计)'}")
    else:
        print("[THREADS · backstage] 暂无暗线 overdue,可继续日常")
    if len(active) < director_mod.MIN_ACTIVE_THREADS:
        print(f"  ⚠ 活跃暗线仅 {len(active)} 条,建议开新线(rpg seed 或手动 thread_open)")


_SEV_ICON = {"block": "🔴", "warn": "🟡"}

def cmd_check(args):
    log.debug("cmd check campaign=%s", getattr(args, "campaign", None))
    d = _campaign_dir(args.campaign)
    from engine.projection import project
    with _store(d) as s:
        events = list(s.iter_events())
    proj = project(events)
    findings = run_check(events, proj)
    if not findings:
        print("rpg check: ✓ 无问题")
    for f in findings:
        icon = _SEV_ICON.get(f["severity"], "·")
        line = f"  {icon} [{f['linter']}] {f['message']}"
        if f["suggestion"]:
            line += f" → {f['suggestion']}"
        print(line)
    # 工作记忆新鲜度提示
    wm = d / "working_memory.md"
    edb = d / "events.db"
    if events and (not wm.exists() or (edb.exists() and wm.exists() and wm.stat().st_mtime < edb.stat().st_mtime)):
        print("  🟡 [working_memory] 落后于最新事件 → rpg compact")
    n_block = sum(1 for f in findings if f["severity"] == BLOCK)
    if n_block:
        raise SystemExit(f"rpg check: {n_block} 个 🔴 需处理")


def cmd_hooks(args):
    log.debug("cmd hooks action=%s", args.action)
    hook_path = (Path(__file__).resolve().parent.parent / "hooks" / "pre_llm_call")
    if args.action == "show":
        print("要启用 pre_llm_call 自动注入工作记忆,在 ~/.hermes/config.yaml 的 hooks: 块加入:\n")
        print("hooks:")
        print("  pre_llm_call:")
        print(f"    - {hook_path}")
        print("\n然后首次运行会要求授权(或在 config 设 hooks_auto_accept: true),重启 hermes 生效。")
        print("启用后:`rpg session on` 开启注入,`rpg session off` 关闭;非跑团会话自动静默。")
        print("⚠ 该 hook 全局生效但自限定:只在 active+新鲜 的跑团会话注入,任何异常静默 no-op。")
    else:
        raise SystemExit(f"unknown hooks action: {args.action}")


def cmd_seed(args):
    log.debug("cmd seed genre=%s commit=%s reroll=%s", args.genre, args.commit, args.reroll)
    d = _campaign_dir(args.campaign)
    seed_int = scene_seed(_campaign_seed(d.name), 0, salt=1 if args.reroll else 0)
    s = seed_campaign(args.genre, Oracle(seed_int))
    f = s["frame"]
    print(f"[SEED · backstage] genre={s['genre']}")
    print(f"  世界: {f['name']} — {f['tone']} · 冲突:{f['central_conflict']} · 势力×{f.get('factions')}")
    print("  暗线:")
    for th in s["threads"]:
        print(f"    - [{th['speed']}] {th['archetype']}({th['type']}) → {th['endpoint']};钩子:{th['hook']}")
    print("  开局 NPC:")
    for n in s["npcs"]:
        print(f"    - {n['role']}(动机:{n['motivation']};秘密:{n['secret']};特质:{'/'.join(n['traits'])})")
    print(f"  主角钩子: {', '.join(s['protagonist_hooks'])}")
    if args.commit:
        sc = "s0"
        with _store(d) as st:
            for th in s["threads"]:
                st.append(make_event("thread_open", 0, sc, [], f"暗线:{th['archetype']}",
                                     thread_refs=[th["id"]],
                                     deltas={"type": th["type"], "speed": th["speed"],
                                             "endpoint": th["endpoint"], "hook": th["hook"],
                                             "beats": [], "reveal_conditions": []}))
        print(f"  → 已落 {len(s['threads'])} 条 thread_open(beats/reveal 待 DM 补全)")
