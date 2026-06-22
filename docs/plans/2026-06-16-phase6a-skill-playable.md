# RPG Engine — Phase 6a: 让它可玩(SKILL.md 宪法 + reference + CLI 易用性 + 部署) Plan

> **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development。
>
> **🚧 护栏:** 只增量改现有文件;**严禁** `git init`/`rm -rf .git`/删 `_legacy`或`docs`/切分支/"从零重建"。但**允许读 `_legacy/`**(抢救旧指导文本)。

**Goal:** 让 rpg-engine 真正能被 hermes 加载、模型按协议跑团——**零 hermes 全局改动**(纯 skill 文件 + CLI)。核心交付:① 仓库根 `SKILL.md`(宪法 ≤10 条 + 回合协议 + OOC 命令 + 触发词);② `reference/`(从 `_legacy` 抢救:日轻风格 / 暗线 schema / 角色塑造经验 / 修罗场);③ CLI 易用性(auto-turn、`rpg recap`、`rpg status` 显示 turn);④ `rpg doctor` 自检 + 部署核验。hooks 自动化/强制留 **P6b**。

**Architecture:** 模型读 `SKILL.md` → 按回合协议调用绝对路径 `bin/rpg`(`RPG_HOME` 默认 skill 根,campaign 落 skill `storage/`)。grounding = `rpg recap`(看工作记忆)+ `rpg recall`(旧细节);叙事后 `rpg log-turn`(逐字)+ `rpg log-event`(状态);场景边界 `rpg director`;纠错 `rpg rewind`。全部 model-driven,P6b 再用 hook 自动化。

**Tech Stack:** Python 3.12 · 复用全部 P1–P4a engine。无新依赖。

## 项目级约定:每模块 debug 日志;离线测试包裹;commit 尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

## File Structure
- Modify `engine/cli.py`、`bin/rpg` — auto-turn、`recap`、`status` 显 turn、`doctor`
- Modify `engine/archive.py` — `next_turn()` 辅助(= max_turn+1)
- Create(controller 亲写)仓库根 `SKILL.md`、`reference/*.md`
- Tests: 扩 `tests/test_cli.py`、`tests/test_archive.py`

---

### Task 1: CLI 易用性(auto-turn + recap + status 显 turn)

**Files:** Modify `engine/archive.py`、`engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_archive.py`、`tests/test_cli.py`。

协议是"log-turn 起新回合 → log-event 挂到该回合"。auto-turn:`log-turn` 省略 turn → `max_turn()+1`;`log-event` 省略 turn → `max_turn()`(挂到当前回合,无则 1)。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_archive.py
def test_next_turn(campaign):
    a = _arc(campaign)
    assert a.next_turn() == 1
    a.add_chunk(day=1, scene="s1", turn=1, text="x")
    assert a.next_turn() == 2
```

```python
# 追加到 tests/test_cli.py
def test_log_turn_auto_turn(tmp_path):
    _run(["new", "z"], home=tmp_path)
    # 不带 turn,自动 1、2
    _run(["log-turn", json.dumps({"day":1,"scene":"s1","text":"第一回合"}, ensure_ascii=False)], home=tmp_path)
    _run(["log-turn", json.dumps({"day":1,"scene":"s2","text":"第二回合"}, ensure_ascii=False)], home=tmp_path)
    out = _run(["recall", "回合", "--no-semantic"], home=tmp_path).stdout
    assert "c_s1_1" in out and "c_s2_2" in out          # turn 自动递增

def test_log_event_auto_turn_attaches_to_current(tmp_path):
    _run(["new", "z"], home=tmp_path)
    _run(["log-turn", json.dumps({"day":1,"scene":"s1","text":"正文"}, ensure_ascii=False)], home=tmp_path)
    # log-event 不带 turn → 挂到当前回合 1
    _run(["log-event", json.dumps({"type":"action","day":1,"scene":"s1","actors":["雷德"],"summary":"动作"}, ensure_ascii=False)], home=tmp_path)
    r = _run(["rewind", "--last"], home=tmp_path)        # 倒带回合1 应同时撤事件+原文
    assert r.returncode == 0 and "-1 events" in r.stdout and "-1 chunks" in r.stdout

def test_recap_prints_working_memory(tmp_path):
    _run(["new", "z"], home=tmp_path)
    _run(["log-event", json.dumps({"type":"location_change","day":1,"scene":"s1","actors":["雷德"],"summary":"到王都","deltas":{"location":"royal_capital"}}, ensure_ascii=False)], home=tmp_path)
    r = _run(["recap"], home=tmp_path)
    assert r.returncode == 0 and "royal_capital" in r.stdout

def test_status_shows_turn(tmp_path):
    _run(["new", "z"], home=tmp_path)
    _run(["log-turn", json.dumps({"day":1,"scene":"s1","text":"x"}, ensure_ascii=False)], home=tmp_path)
    assert "turn=1" in _run(["status"], home=tmp_path).stdout
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现**

`engine/archive.py` 加:
```python
    def next_turn(self):
        return self.max_turn() + 1
```

`engine/cli.py`:
- `cmd_log_turn`:若 payload 无 `turn`(或为 None)→ `turn = a.next_turn()`(在 ArchiveStore 打开后取)。
- `cmd_log_event`:若 payload 无 `turn` → 用当前回合:`with ArchiveStore(d/"archive.db") as a: cur = a.max_turn() or 1`,塞进 payload 再 `make_event`。
- `cmd_status`:加 `turn={max_turn}`(开 ArchiveStore 取 max_turn)。
- 新增 `cmd_recap`:`from engine.compact import build_working_memory; print(build_working_memory(d))`(无需落盘,直接打印当前工作记忆)。
- 入口都加 `log.debug`。

`bin/rpg` 注册 `recap` 子命令:`rp2 = sub.add_parser("recap"); rp2.add_argument("--campaign"); rp2.set_defaults(fn=cli.cmd_recap)`。

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/archive.py engine/cli.py bin/rpg tests/test_archive.py tests/test_cli.py
git commit -m "feat(p6a): CLI ergonomics — auto-turn, recap, status turn

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `rpg doctor`(自检 + 端到端冒烟)

**Files:** Modify `engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_cli.py`。

`rpg doctor` 在临时 campaign 上跑 new→log-turn→log-event→recall→compact→rewind 一条龙,报告每步 OK,验证安装完好。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
def test_doctor_smoke(tmp_path):
    r = _run(["doctor"], home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    assert "FAIL" not in r.stdout
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `cmd_doctor`(engine/cli.py)**

```python
def cmd_doctor(args):
    import tempfile, os
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
```

`bin/rpg`:`dc = sub.add_parser("doctor"); dc.set_defaults(fn=cli.cmd_doctor)`。

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p6a): rpg doctor self-test (end-to-end smoke)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3(controller 亲写):SKILL.md 宪法 + reference + 部署核验

由 controller 直接编写(高判断力 prose,不派 subagent):
- 仓库根 `SKILL.md`:frontmatter(name + description 含触发词 跑团/rpg dm/开始跑团/trpg)+ 宪法 ≤10 条 + 回合协议(model-driven CLI)+ OOC 命令表 + reference 指针。
- `reference/narrative-style.md`(日轻)、`reference/threads.md`(暗线 schema)、`reference/characters.md`(角色塑造经验,从 `_legacy/REFLECTION.md` 抢救)、`reference/shura.md`(修罗场)。
- 部署核验:确认 hermes 能发现该 SKILL.md(它已在 `~/.hermes/skills/openclaw-imports/rpg-dm/`);`rpg doctor` 通过。

## Phase 6a 完成判据
- [ ] `.venv/bin/python -m pytest -q` 全绿
- [ ] `bin/rpg doctor` 全 OK
- [ ] 仓库根有 `SKILL.md`(宪法+协议+触发词)+ `reference/` 四篇
- [ ] 模型仅凭 SKILL.md 即可按协议跑团(grounding→叙事→log-turn/log-event→director→rewind)
- [ ] 零 hermes 全局改动(hooks 留 P6b)

**承接 P6b:** SKILL.md 协议就位后,P6b 加 `pre_llm_call` hook(自动注入 working_memory)、`post_llm_call`(捕获正文)、`stop`(强制 log-event),并做 session 级自限定 + config 注册 + 首次授权。

## Self-Review
- **Spec 覆盖:** §7 宪法+协议(SKILL.md)、§10 部署(Task3);hooks → P6b。
- **约定:** debug 日志、离线测试;reference 从 `_legacy` 抢救不浪费旧积累。
- **类型一致:** `next_turn`、`cmd_recap`/`cmd_doctor` 跨任务/CLI 一致。
