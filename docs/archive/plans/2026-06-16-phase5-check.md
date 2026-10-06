# RPG Engine — Phase 5: 完整性闸门 `rpg check` Plan

> **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development。
>
> **🚧 护栏:** 只增量改现有文件;**严禁** `git init`/`rm -rf .git`/删 `_legacy`或`docs`/切分支/"从零重建"。停止并上报任何此类冲动。

**Goal:** 把"严谨"做成机器拦截。`engine/check.py` 一组**纯函数 linter**(读 events + projections),`rpg check` 出分级报告(🔴 block / 🟡 warn),有 🔴 则非零退出(可被协议/hook 用作 gate)。治:反派全知、暗线跑偏/掉线、人设定死、承诺遗忘、时间线乱、纠错残留。

**Architecture:** `check(events, proj) -> [finding]`,每个 linter 是 `(events, proj)->[finding]`。CLI 载事件→`project()`→`check()`→分级打印 + 工作记忆新鲜度提示,退出码反映是否有 🔴。不动 hermes。

**Tech Stack:** Python 3.12 · 复用 P1 `project`、P4a `director._scene_ordinals` · 无新依赖。

参照 spec §8。

## 项目级约定:每模块 debug 日志;离线测试;commit 尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

## File Structure
- Create `engine/check.py` — `check()` + linters
- Modify `engine/cli.py`、`bin/rpg` — `rpg check`
- Tests: `tests/test_check.py`、扩 `tests/test_cli.py`

---

### Task 1: check.py 骨架 + 结构性 linter(🔴 为主)

**Files:** Create `engine/check.py`; Test `tests/test_check.py`。

linter:thread_completeness(🔴)、villain_omniscience(🔴)、timeline(🔴)、dangling_refs(🟡)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_check.py
from engine.schema import make_event
from engine.projection import project
from engine.check import (check, check_thread_completeness, check_villain_omniscience,
                          check_timeline, check_dangling_refs, BLOCK, WARN)

def _proj(evs):
    return project(evs)

def test_thread_completeness_flags_incomplete_active_thread():
    evs = [make_event("thread_open", 1, "s1", [], "残缺暗线", thread_refs=["t1"],
                      deltas={})]  # 无 endpoint/beats/reveal
    f = check_thread_completeness(evs, _proj(evs))
    assert len(f) == 1 and f[0]["severity"] == BLOCK and "缺" in f[0]["message"]

def test_thread_completeness_ok_when_full():
    evs = [make_event("thread_open", 1, "s1", [], "完整暗线", thread_refs=["t1"],
                      deltas={"endpoint":"终","beats":["a"],"reveal_conditions":["x"]})]
    assert check_thread_completeness(evs, _proj(evs)) == []

def test_thread_completeness_skips_dormant():
    evs = [make_event("thread_open", 1, "s1", [], "休眠种子", thread_refs=["t1"],
                      deltas={"dormant": True})]
    assert check_thread_completeness(evs, _proj(evs)) == []   # 休眠暗线豁免(待激活补全)

def test_villain_omniscience_flags_missing_source():
    evs = [make_event("villain_knowledge_gain", 5, "s5", ["反派"], "得知行踪", deltas={})]
    f = check_villain_omniscience(evs, _proj(evs))
    assert len(f) == 1 and f[0]["severity"] == BLOCK

def test_villain_omniscience_ok_with_source():
    evs = [make_event("villain_knowledge_gain", 5, "s5", ["反派"], "得知",
                      deltas={"source":"内线","channel":"口信","delay":"半天"})]
    assert check_villain_omniscience(evs, _proj(evs)) == []

def test_timeline_flags_day_regression():
    evs = [make_event("action", 5, "s1", ["x"], "a"), make_event("action", 3, "s2", ["x"], "b")]
    f = check_timeline(evs, _proj(evs))
    assert len(f) == 1 and f[0]["severity"] == BLOCK and "时间" in f[0]["message"]

def test_dangling_refs_flags_advance_without_open():
    evs = [make_event("thread_advance", 1, "s1", [], "推进不存在的线", thread_refs=["ghost"])]
    f = check_dangling_refs(evs, _proj(evs))
    assert any("ghost" in x["message"] for x in f)

def test_check_aggregates_and_sorts_block_first():
    evs = [make_event("thread_open", 1, "s1", [], "残缺", thread_refs=["t1"], deltas={}),
           make_event("action", 1, "s1", ["x"], "ok")]
    res = check(evs, _proj(evs))
    assert res and res[0]["severity"] == BLOCK     # 🔴 排前
    assert all("linter" in r and "message" in r for r in res)
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `engine/check.py`**

```python
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

# Task 2 appends behavioral linters to this list:
_ALL_LINTERS = list(_STRUCTURAL)
```

- [ ] **Step 4: 跑测试** → PASS(8)
- [ ] **Step 5: Commit**

```bash
git add engine/check.py tests/test_check.py
git commit -m "feat(p5): rpg check — structural linters (thread/villain/timeline/dangling)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 行为性 linter(🟡)

**Files:** Modify `engine/check.py`; Test 扩 `tests/test_check.py`。

linter:thread_followup(N 场未推进)、character_staleness(卷入 N 事件未演化)、promise_aging(挂 N 天)。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_check.py
from engine.check import check_thread_followup, check_character_staleness, check_promise_aging

def test_thread_followup_flags_stale_thread():
    evs = [make_event("thread_open", 1, "s1", [], "久未推进", thread_refs=["t1"],
                      deltas={"endpoint":"e","beats":["b"],"reveal_conditions":["c"]})]
    evs += [make_event("action", i+2, f"s{i+2}", ["x"], f"日常{i}") for i in range(10)]  # 10 场没推
    f = check_thread_followup(evs, _proj(evs))
    assert any("久未推进" in x["message"] and x["severity"] == WARN for x in f)

def test_character_staleness_flags_unevolved():
    evs = [make_event("character_reveal", 1, "s1", ["阿土"], "登场", deltas={"阿土.x":"y"})]
    evs += [make_event("action", i+2, f"s{i+2}", ["阿土"], f"阿土做事{i}") for i in range(6)]  # 6 事件没演化
    f = check_character_staleness(evs, _proj(evs))
    assert any("阿土" in x["message"] and x["severity"] == WARN for x in f)

def test_character_staleness_ok_if_recently_evolved():
    evs = [make_event("action", i+1, f"s{i+1}", ["阿土"], f"做事{i}") for i in range(6)]
    evs.append(make_event("relationship_change", 9, "s9", ["阿土"], "演化", deltas={"阿土.trust":"低→高"}))
    assert check_character_staleness(evs, _proj(evs)) == []   # 刚演化过

def test_promise_aging_flags_old_open_promise():
    evs = [make_event("promise_made", 1, "s1", ["雷德"], "答应看海", id="ev_p"),
           make_event("action", 40, "s40", ["雷德"], "过了很久")]
    f = check_promise_aging(evs, _proj(evs))
    assert any("看海" in x["message"] for x in f)

def test_promise_aging_ok_if_kept():
    evs = [make_event("promise_made", 1, "s1", ["雷德"], "答应看海", id="ev_p"),
           make_event("promise_kept", 5, "s5", ["雷德"], "兑现", deltas={"promise_id":"ev_p"}),
           make_event("action", 40, "s40", ["雷德"], "很久后")]
    assert check_promise_aging(evs, _proj(evs)) == []
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现(加到 `engine/check.py`,并把它们加进 `_ALL_LINTERS`)**

```python
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
```
(把文件末尾原来的 `_ALL_LINTERS = list(_STRUCTURAL)` 替换为上面这行。)

- [ ] **Step 4: 跑测试** → PASS(5 新)
- [ ] **Step 5: Commit**

```bash
git add engine/check.py tests/test_check.py
git commit -m "feat(p5): behavioral linters (thread followup / character staleness / promise aging)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `rpg check` CLI

**Files:** Modify `engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_cli.py`。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
def test_check_clean_campaign_exit0(tmp_path):
    _run(["new", "z"], home=tmp_path)
    _run(["log-event", json.dumps({"type":"action","day":1,"scene":"s1","actors":["x"],"summary":"ok"}, ensure_ascii=False)], home=tmp_path)
    r = _run(["check"], home=tmp_path)
    assert r.returncode == 0

def test_check_block_exit_nonzero(tmp_path):
    _run(["new", "z"], home=tmp_path)
    # 反派无来源知情 = 🔴
    _run(["log-event", json.dumps({"type":"villain_knowledge_gain","day":1,"scene":"s1","actors":["反派"],"summary":"知情","deltas":{}}, ensure_ascii=False)], home=tmp_path)
    r = _run(["check"], home=tmp_path)
    assert r.returncode != 0
    assert "🔴" in r.stdout or "block" in r.stdout.lower()
    assert "反派" in r.stdout
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `cmd_check`(engine/cli.py)**

```python
from engine.check import check as run_check, BLOCK

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

```

`bin/rpg`:`ck = sub.add_parser("check"); ck.add_argument("--campaign"); ck.set_defaults(fn=cli.cmd_check)`。

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p5): rpg check CLI (graded report, nonzero exit on block)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 完成判据
- [ ] `.venv/bin/python -m pytest -q` 全绿
- [ ] `rpg check` 干净本子退出 0;有 🔴(如反派无来源)退出非 0 并报告
- [ ] 七类 linter 各有测试覆盖
- [ ] `RPG_DEBUG=1` 可见 check 节点日志

**承接 P6b:** `rpg check` 可被 stop hook 在边界自动跑(有 🔴 则提醒模型处理);SKILL.md 协议已让 DM 在弧光/边界手动 `rpg check`。

## Self-Review
- **Spec 覆盖:** §8 全部 linter(thread 完整性/follow、反派全知、角色演化、承诺、时间线、悬空引用、工作记忆新鲜度);
- **约定:** 纯函数 + 离线测试 + debug 日志;BLOCK 非零退出可 gate。
- **类型一致:** `check`/`_finding`/各 `check_*`/`BLOCK`/`WARN`/阈值常量 跨任务一致;`_scene_ordinals` 复用 director。
