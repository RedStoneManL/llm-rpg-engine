# RPG Engine — Phase 6b: pre_llm_call hook(自动注入工作记忆) Plan

> **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development。
>
> **🚧 护栏:** 只增量改现有文件 + 新建 `hooks/pre_llm_call`;**严禁** `git init`/`rm -rf .git`/删 `_legacy`或`docs`/切分支/"从零重建"。**严禁自动修改 `~/.hermes/config.yaml`**(启用是用户显式动作)。

**Goal:** 把"每回合 grounding"从协议自觉变成 harness 自动——`pre_llm_call` hook 在跑团会话里自动把工作记忆注入用户消息(hermes 已确认 `pre_llm_call` 返回 `{"context":...}` 即注入)。**安全第一**:hook 自限定(非跑团会话立即静默 no-op)、fail-safe(任何异常都静默,绝不拖垮 hermes——hermes 本身也用 try/except 包 hook)、**不自动改全局 config**(`rpg hooks show` 打印片段,用户自己启用)。

**Scoping(关键,session 无关):** 不依赖 hook 的 `session_id` 与 CLI 的 `HERMES_SESSION_KEY` 相等(已核实二者不保证相等)。改用**心跳 active 标记** `storage/hook_state.json`={active,campaign,ts}:`rpg session on` 开启,跑团命令刷新 ts,TTL(默认 1h)过期自动失活,`rpg session off` 立即关。caveat:同时跑两个 hermes 会话时非跑团会话也会被注入——单用户单会话场景可接受,文档说明 + `off` 兜底。

**Tech Stack:** Python 3.12 · hook 脚本走 venv shebang · 只 import `engine.compact`(轻,无 numpy)· 无新依赖。

参照 spec §7(回合协议自动化)、§10(hermes 集成、自限定)。capture(post_llm_call)/enforce(stop)留作后续(需 OOC/turn-state 处理,风险更高)。

## 项目级约定:debug 日志;离线测试;commit 尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

## File Structure
- Modify `engine/cli.py`、`bin/rpg` — `rpg session on/off/status`、`rpg hooks show`;跑团命令刷新心跳
- Create `hooks/pre_llm_call` — 自限定注入脚本(venv shebang,可执行)
- Tests: 扩 `tests/test_cli.py`、新 `tests/test_hook_pre_llm.py`

---

### Task 1: 会话心跳 `rpg session on/off/status` + 跑团命令刷新

**Files:** Modify `engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_cli.py`。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
def test_session_on_off_status(tmp_path):
    _run(["new", "z"], home=tmp_path)
    assert "ON" in _run(["session", "on"], home=tmp_path).stdout
    import json as _j
    st = _j.loads((tmp_path/"storage"/"hook_state.json").read_text())
    assert st["active"] is True and st["campaign"] == "z"
    assert "OFF" in _run(["session", "off"], home=tmp_path).stdout
    assert _j.loads((tmp_path/"storage"/"hook_state.json").read_text())["active"] is False

def test_play_command_refreshes_heartbeat(tmp_path):
    _run(["new", "z"], home=tmp_path)
    _run(["session", "on"], home=tmp_path)
    import json as _j
    sp = tmp_path/"storage"/"hook_state.json"
    t0 = _j.loads(sp.read_text())["ts"]
    import time; time.sleep(0.05)
    _run(["recap"], home=tmp_path)               # 跑团命令应刷新 ts
    assert _j.loads(sp.read_text())["ts"] >= t0
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现(engine/cli.py)**

加 `import time`(若无)。加辅助 + 命令:
```python
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
```
在 `cmd_recap`、`cmd_log_turn`、`cmd_log_event`、`cmd_director` 入口各加一行 `_touch_session()`(在 `_campaign_dir` 之后)。

`bin/rpg` 注册:`se = sub.add_parser("session"); se.add_argument("action", choices=["on","off","status"]); se.add_argument("--campaign"); se.set_defaults(fn=cli.cmd_session)`。

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p6b): rpg session on/off heartbeat (hook self-scoping state)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `hooks/pre_llm_call` 注入脚本(自限定 + fail-safe)

**Files:** Create `hooks/pre_llm_call`; Test `tests/test_hook_pre_llm.py`。

- [ ] **Step 1: 写失败测试(子进程喂 payload)**

```python
# tests/test_hook_pre_llm.py
import json, os, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "pre_llm_call"

def _run_hook(payload, home):
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True,
                          env=dict(os.environ, RPG_HOME=str(home)))

def _setup(home, active):
    import time
    (home / "storage" / "campaigns" / "z" / "projections").mkdir(parents=True, exist_ok=True)
    # 用 CLI 建本子 + 记一个事件 + compact,产出 working_memory
    rpg = REPO / "bin" / "rpg"
    env = dict(os.environ, RPG_HOME=str(home))
    subprocess.run([sys.executable, str(rpg), "new", "z"], env=env, capture_output=True)
    subprocess.run([sys.executable, str(rpg), "log-event",
                    json.dumps({"type":"location_change","day":1,"scene":"s1","actors":["雷德"],
                                "summary":"到王都","deltas":{"location":"royal_capital"}})],
                   env=env, capture_output=True)
    subprocess.run([sys.executable, str(rpg), "compact"], env=env, capture_output=True)
    state = {"active": active, "campaign": "z", "ts": time.time()}
    (home / "storage" / "hook_state.json").write_text(json.dumps(state))

def test_hook_injects_when_active(tmp_path):
    _setup(tmp_path, active=True)
    r = _run_hook({"hook_event_name": "pre_llm_call", "session_id": "s1", "user_message": "继续"}, tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert "context" in out and "royal_capital" in out["context"]   # 注入了工作记忆

def test_hook_silent_when_inactive(tmp_path):
    _setup(tmp_path, active=False)
    r = _run_hook({"hook_event_name": "pre_llm_call", "session_id": "s1"}, tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""              # 非跑团 → 静默 no-op

def test_hook_silent_when_no_state(tmp_path):
    (tmp_path / "storage").mkdir(parents=True, exist_ok=True)
    r = _run_hook({"hook_event_name": "pre_llm_call", "session_id": "s1"}, tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""

def test_hook_silent_on_malformed_stdin(tmp_path):
    _setup(tmp_path, active=True)
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True, env=dict(os.environ, RPG_HOME=str(tmp_path)))
    assert r.returncode == 0 and r.stdout.strip() == ""              # 坏输入也不崩

def test_hook_silent_when_stale(tmp_path):
    _setup(tmp_path, active=True)
    import json as _j
    sp = tmp_path / "storage" / "hook_state.json"
    st = _j.loads(sp.read_text()); st["ts"] = 0; sp.write_text(_j.dumps(st))  # 过期
    r = _run_hook({"hook_event_name": "pre_llm_call", "session_id": "s1"}, tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""              # TTL 过期 → 失活
```

> 注:测试用 `sys.executable`(venv python)跑 hook,绕过 shebang;生产里 hermes 直接执行(走 shebang)。`engine` 经 `RPG_HOME` 下找不到?——hook 用自身路径定位 skill 根(下面实现),`RPG_HOME` 仅决定 storage 位置。需让 `engine.compact` 能 import:hook 把 skill 根插入 sys.path。

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `hooks/pre_llm_call`**

```python
#!/root/.hermes/skills/openclaw-imports/rpg-dm/.venv/bin/python
"""hermes pre_llm_call hook: inject the active RPG campaign's working memory.
SAFE: self-scoped (only when an RPG session is active+fresh) and fail-safe
(any error → silent no-op; hermes also wraps hooks in try/except)."""
import json
import os
import sys
import time
from pathlib import Path

_SKILL = Path(__file__).resolve().parent.parent
_TTL = 3600  # seconds; stale heartbeat → assume the RPG session ended

def _storage():
    return Path(os.environ.get("RPG_HOME", _SKILL)) / "storage"

def main():
    try:
        json.load(sys.stdin)          # consume payload (we don't need fields, but must read)
    except Exception:
        return
    try:
        sp = _storage() / "hook_state.json"
        if not sp.exists():
            return
        st = json.loads(sp.read_text())
        if not st.get("active"):
            return
        if time.time() - float(st.get("ts", 0)) > _TTL:
            return
        campaign = st.get("campaign")
        cd = _storage() / "campaigns" / str(campaign)
        if not cd.exists():
            return
        sys.path.insert(0, str(_SKILL))
        from engine.compact import build_working_memory
        wm = build_working_memory(cd)
        if wm and wm.strip():
            print(json.dumps({"context": "【跑团 · 当前工作记忆(harness 注入)】\n" + wm},
                             ensure_ascii=False))
    except Exception:
        return  # never break the turn

if __name__ == "__main__":
    main()
```
然后 `chmod +x hooks/pre_llm_call`。

- [ ] **Step 4: 跑测试** → PASS(5)
- [ ] **Step 5: Commit**

```bash
chmod +x hooks/pre_llm_call
git add hooks/pre_llm_call tests/test_hook_pre_llm.py
git commit -m "feat(p6b): pre_llm_call hook — self-scoped, fail-safe working-memory injection

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `rpg hooks show`(打印启用片段,不自动改 config)

**Files:** Modify `engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_cli.py`。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
def test_hooks_show_prints_config(tmp_path):
    r = _run(["hooks", "show"], home=tmp_path)
    assert r.returncode == 0
    assert "pre_llm_call" in r.stdout
    assert "config.yaml" in r.stdout
    assert "hooks/pre_llm_call" in r.stdout      # 指向真实脚本路径
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `cmd_hooks`(engine/cli.py)**

```python
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
```

`bin/rpg`:`hk = sub.add_parser("hooks"); hk.add_argument("action", choices=["show"]); hk.set_defaults(fn=cli.cmd_hooks)`。

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p6b): rpg hooks show (prints opt-in config; never auto-edits hermes)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 6b 完成判据
- [ ] `.venv/bin/python -m pytest -q` 全绿
- [ ] hook 脚本:active+新鲜 → 注入工作记忆;inactive/无state/坏输入/过期 → **静默 no-op exit 0**(绝不崩)
- [ ] `rpg session on/off/status` 正常;跑团命令刷新心跳
- [ ] `rpg hooks show` 打印正确的 config 片段(指向真实脚本路径)
- [ ] **未自动修改 `~/.hermes/config.yaml`**

**承接(后续可选):** post_llm_call 捕获正文(需 OOC 判别)、stop 强制 log-event(需回合基线对比)——本期不做,文档标注。controller 在交付后可据用户意愿,显式协助启用(编辑 config + 授权 + 重启)。

## Self-Review
- **Spec 覆盖:** §7 回合协议自动化(pre_llm_call 注入)、§10 自限定 + 不污染;capture/enforce 明确延后。
- **安全:** 自限定(心跳)+ fail-safe(全 try/except 静默)+ 不自动改全局 config + hermes 自身 try/except 兜底。
- **约定:** 离线测试(子进程喂 payload)、debug 日志、确定性。
- **类型一致:** `_hook_state_path`/`_read_hook_state`/`_touch_session`/`cmd_session`/`cmd_hooks` 与 hook 脚本读的 `hook_state.json` schema 一致。
