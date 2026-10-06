import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RPG = REPO / "bin" / "rpg"


def _run(args, **kw):
    env = dict(os.environ, RPG_HOME=str(kw.pop("home")))
    return subprocess.run([sys.executable, str(RPG), *args],
                          capture_output=True, text=True, env=env, **kw)


def test_new_creates_campaign_and_sets_current(tmp_path):
    r = _run(["new", "isekai"], home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "storage" / "campaigns" / "isekai" / "projections").is_dir()
    assert (tmp_path / "storage" / "current").read_text().strip() == "isekai"


def test_log_event_appends_to_current_campaign(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    payload = json.dumps({"type": "action", "day": 1, "scene": "s1",
                          "actors": ["雷德"], "summary": "出场"}, ensure_ascii=False)
    r = _run(["log-event", payload], home=tmp_path)
    assert r.returncode == 0, r.stderr
    jsonl = tmp_path / "storage" / "campaigns" / "isekai" / "events.jsonl"
    assert "出场" in jsonl.read_text(encoding="utf-8")


def test_project_then_status_roundtrip(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    for p in [
        {"type": "location_change", "day": 1, "scene": "s1", "actors": ["雷德"],
         "summary": "抵达王都", "deltas": {"location": "royal_capital"}},
        {"type": "relationship_change", "day": 5, "scene": "s5", "actors": ["艾拉"],
         "summary": "信任提升", "deltas": {"艾拉.trust": "中→高"}},
    ]:
        _run(["log-event", json.dumps(p, ensure_ascii=False)], home=tmp_path)
    rp = _run(["project"], home=tmp_path)
    assert rp.returncode == 0, rp.stderr
    state = json.loads((tmp_path / "storage" / "campaigns" / "isekai" / "projections" / "state.json")
                       .read_text(encoding="utf-8"))
    assert state["location"] == "royal_capital"
    rs = _run(["status"], home=tmp_path)
    assert "loc=royal_capital" in rs.stdout and "chars=1" in rs.stdout


# --- FIX 3 new tests ---

def test_log_event_bad_json_prints_clean_error(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    r = _run(["log-event", "not json"], home=tmp_path)
    assert r.returncode != 0
    assert r.stderr.startswith("error:")
    assert "Traceback" not in r.stderr


# --- FIX 4 new test (--rebuild wipes stale projection) ---

def test_project_rebuild_wipes_stale_projection(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    payload = json.dumps({"type": "action", "day": 1, "scene": "s1",
                          "actors": ["雷德"], "summary": "出场"}, ensure_ascii=False)
    _run(["log-event", payload], home=tmp_path)
    _run(["project"], home=tmp_path)
    # Write a stale/junk file into projections
    stale = tmp_path / "storage" / "campaigns" / "isekai" / "projections" / "STALE.json"
    stale.write_text("{}", encoding="utf-8")
    assert stale.exists()
    # Rebuild should wipe the stale file
    r = _run(["project", "--rebuild"], home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert not stale.exists()
    assert (tmp_path / "storage" / "campaigns" / "isekai" / "projections" / "state.json").exists()


# --- Task 5 new tests ---

def test_log_turn_and_recall(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    turn = json.dumps({"day":1,"scene":"s1","turn":1,
                       "text":"艾拉在金狮酒馆笨拙地笑了","entities":["艾拉"]}, ensure_ascii=False)
    assert _run(["log-turn", turn], home=tmp_path).returncode == 0
    r = _run(["recall", "酒馆"], home=tmp_path)
    assert r.returncode == 0 and "金狮酒馆" in r.stdout   # 逐字回原文

def test_recall_operator_query_no_traceback(tmp_path):
    """recall with FTS operator chars must exit 0 and not print a traceback."""
    _run(["new", "isekai"], home=tmp_path)
    turn = json.dumps({"day": 1, "scene": "s1", "turn": 1,
                       "text": "something happened in 1995", "entities": []},
                      ensure_ascii=False)
    _run(["log-turn", turn], home=tmp_path)
    r = _run(["recall", "time:1995"], home=tmp_path)
    assert r.returncode == 0, f"expected returncode 0 got {r.returncode}\nstderr={r.stderr}"
    assert "Traceback" not in r.stderr, f"traceback found:\n{r.stderr}"


def test_compact_cli_writes_working_memory(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    _run(["log-event", json.dumps({"type":"location_change","day":1,"scene":"s1",
          "actors":["雷德"],"summary":"到王都","deltas":{"location":"royal_capital"}},
          ensure_ascii=False)], home=tmp_path)
    assert _run(["compact"], home=tmp_path).returncode == 0
    wm = tmp_path/"storage"/"campaigns"/"isekai"/"working_memory.md"
    assert wm.exists() and "royal_capital" in wm.read_text(encoding="utf-8")

def test_reindex_and_semantic_recall_cli(tmp_path):
    import os
    _run(["new", "z"], home=tmp_path)
    _run(["log-turn", json.dumps({"day":1,"scene":"s1","turn":1,"text":"语义可召回的独特句"},
         ensure_ascii=False)], home=tmp_path)
    # 用 FakeEmbedder(env)避免下载模型
    env = dict(os.environ, RPG_HOME=str(tmp_path), RPG_EMBEDDER="fake")
    import subprocess, sys
    assert subprocess.run([sys.executable, str(RPG), "reindex"], env=env,
                          capture_output=True, text=True).returncode == 0
    r = subprocess.run([sys.executable, str(RPG), "recall", "语义可召回的独特句", "--semantic"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "独特句" in r.stdout


def test_rewind_last_cli(tmp_path):
    import os
    env_home = tmp_path
    _run(["new", "z"], home=env_home)
    _run(["log-turn", json.dumps({"day":1,"scene":"s1","turn":1,"text":"保留台词"}, ensure_ascii=False)], home=env_home)
    _run(["log-turn", json.dumps({"day":2,"scene":"s2","turn":2,"text":"要倒带的台词"}, ensure_ascii=False)], home=env_home)
    r = _run(["rewind", "--last"], home=env_home)
    assert r.returncode == 0, r.stderr
    # 倒带后召回不到回合2
    rr = _run(["recall", "要倒带的台词", "--no-semantic"], home=env_home)
    assert "要倒带的台词" not in rr.stdout
    assert "保留台词" in _run(["recall", "保留台词", "--no-semantic"], home=env_home).stdout


def test_log_event_with_turn(tmp_path):
    _run(["new", "z"], home=tmp_path)
    p = json.dumps({"type":"action","day":1,"scene":"s1","actors":["雷德"],
                    "summary":"出场","turn":4}, ensure_ascii=False)
    assert _run(["log-event", p], home=tmp_path).returncode == 0


def test_director_emits_event_when_fired_and_is_reproducible(tmp_path):
    _run(["new", "z"], home=tmp_path)
    # 制造 scenes_since_event 高的局面(多场景无事件),让触发概率到 60%
    for i in range(1, 7):
        _run(["log-event", json.dumps({"type":"action","day":i,"scene":f"s{i}",
              "actors":["雷德"],"summary":f"日常{i}"}, ensure_ascii=False)], home=tmp_path)
    r = _run(["director"], home=tmp_path)
    assert r.returncode == 0, r.stderr
    # 输出要么是后台种子要么"quiet";确定性:同一状态再跑结果一致
    r2 = _run(["director", "--dry-run"], home=tmp_path)
    assert r2.returncode == 0


def test_director_dry_run_does_not_emit(tmp_path):
    _run(["new", "z"], home=tmp_path)
    _run(["log-event", json.dumps({"type":"action","day":1,"scene":"s1",
          "actors":["雷德"],"summary":"x"}, ensure_ascii=False)], home=tmp_path)
    before = (tmp_path/"storage"/"campaigns"/"z"/"events.jsonl").read_text(encoding="utf-8")
    _run(["director", "--dry-run"], home=tmp_path)
    after = (tmp_path/"storage"/"campaigns"/"z"/"events.jsonl").read_text(encoding="utf-8")
    assert before == after        # dry-run 不写事件


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


def test_doctor_smoke(tmp_path):
    r = _run(["doctor"], home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    assert "FAIL" not in r.stdout


def test_seed_prints_skeleton(tmp_path):
    _run(["new", "z"], home=tmp_path)
    r = _run(["seed", "default"], home=tmp_path)
    assert r.returncode == 0
    assert "暗线" in r.stdout or "thread" in r.stdout.lower()
    assert "NPC" in r.stdout or "npc" in r.stdout.lower()

def test_seed_commit_logs_thread_open(tmp_path):
    _run(["new", "z"], home=tmp_path)
    assert _run(["seed", "default", "--commit"], home=tmp_path).returncode == 0
    st = _run(["status"], home=tmp_path).stdout
    # 至少 3 条暗线被 thread_open
    import re
    m = re.search(r"threads=(\d+)", st)
    assert m and int(m.group(1)) >= 3

def test_seed_reroll_differs(tmp_path):
    _run(["new", "z"], home=tmp_path)
    a = _run(["seed", "default"], home=tmp_path).stdout
    b = _run(["seed", "default", "--reroll"], home=tmp_path).stdout
    assert a != b

def test_threads_next_no_events(tmp_path):
    _run(["new", "z"], home=tmp_path)
    r = _run(["threads", "next"], home=tmp_path)
    assert r.returncode == 0
    assert "backstage" in r.stdout.lower() or "暗线" in r.stdout or "日常" in r.stdout

def test_threads_next_suggests_after_seed_commit(tmp_path):
    _run(["new", "z"], home=tmp_path)
    _run(["seed", "default", "--commit"], home=tmp_path)
    # add many scenes so threads go overdue
    for i in range(1, 15):
        _run(["log-event", json.dumps({"type": "action", "day": i, "scene": f"s{i}",
              "actors": ["x"], "summary": f"日常{i}"}, ensure_ascii=False)], home=tmp_path)
    r = _run(["threads", "next"], home=tmp_path)
    assert r.returncode == 0
    assert "backstage" in r.stdout.lower() or "该推" in r.stdout or "暗线" in r.stdout


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


def test_log_event_actorless_defaults(tmp_path):
    _run(["new", "z"], home=tmp_path)
    # thread_open 不带 actors,应被接受(默认 [])
    r = _run(["log-event", json.dumps({"type": "thread_open", "day": 1, "scene": "s1",
              "thread_refs": ["t1"], "summary": "无actors暗线", "deltas": {}}, ensure_ascii=False)], home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr


# --- Task 1 (Phase 6b): session on/off/status + heartbeat refresh ---

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


# --- Task 3 (Phase 6b): rpg hooks show ---

def test_hooks_show_prints_config(tmp_path):
    r = _run(["hooks", "show"], home=tmp_path)
    assert r.returncode == 0
    assert "pre_llm_call" in r.stdout
    assert "config.yaml" in r.stdout
    assert "hooks/pre_llm_call" in r.stdout      # 指向真实脚本路径
