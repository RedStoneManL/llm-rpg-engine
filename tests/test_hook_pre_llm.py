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
