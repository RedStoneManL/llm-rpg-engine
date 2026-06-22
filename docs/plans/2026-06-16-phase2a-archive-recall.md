# RPG Engine — Phase 2a: 归档 + FTS/结构化/锚点召回 + 工作记忆 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.
>
> **护栏(每个实现/修复 agent 必须遵守):** 只在现有文件上做**增量**修改;**严禁** `git init` / `rm -rf .git` / `git checkout --orphan` / 删除 `_legacy/` 或 `docs/` / "从零重建"。任何这类冲动都是危险信号,立即停止并上报。绝不切换分支。

**Goal:** 在 Phase 1 事件核心之上,建逐字**归档 store**(SQLite+FTS5)、**召回**(FTS + 结构化 + 锚点,零模型依赖)、**工作记忆/compact**,并引入**共享 debug 日志基建**。这一期不依赖 embedding/向量(留给 P2b),但已实现"任意旧正文逐字取回"。

**Architecture:** 叙事正文按"回合块"逐字存入 `ArchiveStore`(SQLite + FTS5)。`recall()` 路由三路:FTS 全文、结构化过滤(人物/时间/类型)、锚点(landmark 事件→chunk)。`compact()` 从 Phase 1 投影生成小 `working_memory.md`。所有模块经 `engine/log.py` 在关键节点打 debug 日志。

**Tech Stack:** Python 3.12 · 标准库 `sqlite3`(FTS5,版本 3.45 已支持)· `logging` · `pytest`(`caplog` 断言日志)。**无新增第三方依赖。**

参照 spec:`docs/2026-06-15-rpg-engine-redesign-design.md` §4.3(工作记忆)、§4.4(归档/粒度)、§4.5(召回)。

---

## 项目级约定(每个任务都遵守)

1. **测试包裹每模块**:先写失败测试 → 实现 → 通过 → commit。测试必须**离线、快**(本期天然满足:无模型)。
2. **debug mode 内建**:每模块顶部 `from engine.log import get_logger` → `log = get_logger("<module>")`;在关键节点(入口参数、命中数、写盘路径、分支决策)`log.debug(...)`。`RPG_DEBUG=1` 或 `RPG_LOG_LEVEL=DEBUG` 开启。
3. **commit 尾部**保留 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
4. 运行测试:`/root/.hermes/skills/openclaw-imports/rpg-dm/.venv/bin/python -m pytest -q`(从 repo 根)。

## File Structure

- Create `engine/log.py` — 共享日志(`configure_logging()`, `get_logger(name)`)
- Create `engine/archive.py` — `ArchiveStore`(逐字块 + FTS5)
- Create `engine/recall.py` — `recall()` / `recall_anchor()`(FTS + 结构化 + 锚点)
- Create `engine/compact.py` — `build_working_memory()` / `compact()`
- Modify `engine/cli.py`、`bin/rpg` — 新增 `log-turn` / `recall` / `compact`;入口 `configure_logging()`
- Modify `engine/store.py`、`engine/projection.py`、`engine/cli.py` — 补 debug 日志(retrofit)
- Tests: `tests/test_log.py`、`tests/test_archive.py`、`tests/test_recall.py`、`tests/test_compact.py`,并扩 `tests/test_cli.py`

---

### Task 1: 共享 debug 日志基建 + retrofit Phase 1

**Files:** Create `engine/log.py`; Test `tests/test_log.py`; Modify `engine/store.py` `engine/projection.py` `engine/cli.py` `bin/rpg`.

- [ ] **Step 1: 写失败测试**

```python
# tests/test_log.py
import logging
from engine.log import get_logger, configure_logging

def test_get_logger_namespaced():
    assert get_logger("store").name == "rpg.store"

def test_debug_env_enables_debug_level(monkeypatch):
    monkeypatch.setenv("RPG_DEBUG", "1")
    monkeypatch.delenv("RPG_LOG_LEVEL", raising=False)
    root = configure_logging()
    assert root.level == logging.DEBUG

def test_default_is_quiet(monkeypatch):
    monkeypatch.delenv("RPG_DEBUG", raising=False)
    monkeypatch.delenv("RPG_LOG_LEVEL", raising=False)
    root = configure_logging()
    assert root.level == logging.WARNING

def test_log_level_overrides(monkeypatch):
    monkeypatch.setenv("RPG_LOG_LEVEL", "INFO")
    assert configure_logging().level == logging.INFO

def test_logger_emits_through_caplog(caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    get_logger("demo").debug("hello %s", "world")
    assert any("hello world" in r.message for r in caplog.records)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.log'`

- [ ] **Step 3: 实现 `engine/log.py`**

```python
# engine/log.py
import logging
import os
import sys

_ROOT = "rpg"

def configure_logging():
    """Configure the 'rpg' logger from env. RPG_LOG_LEVEL wins; else RPG_DEBUG → DEBUG; else WARNING."""
    level_name = os.environ.get("RPG_LOG_LEVEL")
    if not level_name:
        level_name = "DEBUG" if os.environ.get("RPG_DEBUG") else "WARNING"
    level = getattr(logging, level_name.upper(), logging.WARNING)
    root = logging.getLogger(_ROOT)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
    root.setLevel(level)
    root.propagate = False
    return root

def get_logger(name):
    return logging.getLogger(f"{_ROOT}.{name}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_log.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Retrofit Phase 1 模块加 debug 日志**

在以下文件顶部加 `from engine.log import get_logger` + `log = get_logger("<name>")`,并在关键节点加 `log.debug`:
- `engine/store.py`:`log = get_logger("store")`;`append` 里 `log.debug("append id=%s seq=%s type=%s", ev["id"], seq, ev["type"])`;`retract_from_seq` 里 `log.debug("retract from seq=%s affected=%s", seq, cur.rowcount)`。
- `engine/projection.py`:`log = get_logger("projection")`;`project` 末尾 `log.debug("project folded chars=%d threads=%d", len(proj["characters"]), len(proj["threads"]))`。
- `engine/cli.py`:`log = get_logger("cli")`;每个 `cmd_*` 入口 `log.debug("cmd %s campaign=%s", "<name>", getattr(args,"campaign",None))`。
- `bin/rpg`:在 `main()` 解析参数后、`args.fn(args)` 前,加 `from engine.log import configure_logging; configure_logging()`。

- [ ] **Step 6: 跑全量,确认无回归 + 提交**

Run: `.venv/bin/python -m pytest -q`(应 ≥ 34 passed:29 旧 + 5 新)

```bash
git add engine/log.py tests/test_log.py engine/store.py engine/projection.py engine/cli.py bin/rpg
git commit -m "feat(p2a): shared debug logging (RPG_DEBUG) + retrofit phase 1

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 归档 store(逐字块 + FTS5)

**Files:** Create `engine/archive.py`; Test `tests/test_archive.py`.

`ArchiveStore` 存逐字正文块。SQLite 表 + FTS5 外部内容表。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_archive.py
import logging
from engine.archive import ArchiveStore

def _arc(campaign):
    return ArchiveStore(campaign / "archive.db")

def test_add_and_get_chunk_verbatim(campaign):
    a = _arc(campaign)
    cid = a.add_chunk(day=1, scene="s1", turn=1,
                      text="「我叫雷德。」他第一次开口。", entities=["雷德"])
    got = a.get_chunk(cid)
    assert got["text"] == "「我叫雷德。」他第一次开口。"   # 逐字
    assert got["entities"] == ["雷德"] and got["day"] == 1

def test_fts_search_finds_by_keyword(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="艾拉在金狮酒馆笨拙地打翻了酒杯")
    a.add_chunk(day=2, scene="s2", turn=2, text="雷德在国立大学的图书馆读书")
    hits = a.fts_search("酒馆")
    assert len(hits) == 1 and "金狮酒馆" in hits[0]["text"]

def test_fts_search_entity_filter(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="雷德练剑", entities=["雷德"])
    a.add_chunk(day=1, scene="s1", turn=2, text="练剑的还有艾拉", entities=["艾拉"])
    hits = a.fts_search("练剑", entity="艾拉")
    assert len(hits) == 1 and hits[0]["entities"] == ["艾拉"]

def test_chunk_id_stable_and_unique(campaign):
    a = _arc(campaign)
    c1 = a.add_chunk(day=1, scene="s1", turn=1, text="一")
    c2 = a.add_chunk(day=1, scene="s1", turn=2, text="二")
    assert c1 != c2

def test_debug_logs_on_add(campaign, caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    _arc(campaign).add_chunk(day=1, scene="s1", turn=1, text="x")
    assert any("add_chunk" in r.message for r in caplog.records)

def test_context_manager_closes(campaign):
    import sqlite3
    with _arc(campaign) as a:
        a.add_chunk(day=1, scene="s1", turn=1, text="x")
    try:
        a.add_chunk(day=1, scene="s1", turn=2, text="y")
        assert False, "should be closed"
    except sqlite3.ProgrammingError:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_archive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.archive'`

- [ ] **Step 3: 实现 `engine/archive.py`**

```python
# engine/archive.py
import json
import sqlite3
from pathlib import Path

from engine.log import get_logger

log = get_logger("archive")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    day INTEGER, scene TEXT, turn INTEGER, kind TEXT,
    text TEXT NOT NULL, entities TEXT, event_ids TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""

class ArchiveStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_chunk(self, *, day, scene, turn, text, entities=None, event_ids=None, kind="narration"):
        chunk_id = f"c_{scene}_{turn}"
        self._conn.execute(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, day, scene, turn, kind, text, entities, event_ids)
               VALUES (?,?,?,?,?,?,?,?)""",
            (chunk_id, day, scene, turn, kind, text,
             json.dumps(entities or [], ensure_ascii=False),
             json.dumps(event_ids or [], ensure_ascii=False)))
        self._conn.commit()
        log.debug("add_chunk id=%s day=%s scene=%s turn=%s len=%d",
                  chunk_id, day, scene, turn, len(text))
        return chunk_id

    def get_chunk(self, chunk_id):
        r = self._conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        return self._row(r) if r else None

    def fts_search(self, query, k=5, entity=None, day=None):
        sql = ("SELECT c.* FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid "
               "WHERE chunks_fts MATCH ?")
        params = [query]
        if day is not None:
            sql += " AND c.day=?"; params.append(day)
        sql += " ORDER BY rank LIMIT ?"; params.append(k * 4 if entity else k)
        rows = [self._row(r) for r in self._conn.execute(sql, params)]
        if entity:
            rows = [r for r in rows if entity in r["entities"]][:k]
        log.debug("fts_search q=%r entity=%s day=%s hits=%d", query, entity, day, len(rows))
        return rows[:k]

    def _row(self, r):
        return {"chunk_id": r["chunk_id"], "day": r["day"], "scene": r["scene"],
                "turn": r["turn"], "kind": r["kind"], "text": r["text"],
                "entities": json.loads(r["entities"] or "[]"),
                "event_ids": json.loads(r["event_ids"] or "[]")}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_archive.py -v`
Expected: PASS(6 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/archive.py tests/test_archive.py
git commit -m "feat(p2a): verbatim ArchiveStore (SQLite + FTS5)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 召回(FTS + 结构化 + 锚点)

**Files:** Create `engine/recall.py`; Test `tests/test_recall.py`.

锚点:Phase 1 的 `landmark` 事件(`deltas` 带 `anchor` 类型如 `first_meeting`,`chunk_ids` 指向原文块)。`recall_anchor(actor, anchor_type)` → 找该 landmark 事件 → 取其 chunk → 逐字返回。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_recall.py
from engine.store import EventStore
from engine.schema import make_event
from engine.archive import ArchiveStore
from engine.recall import recall, recall_anchor

def _setup(campaign):
    arc = ArchiveStore(campaign / "archive.db")
    store = EventStore(campaign / "events.db", campaign / "events.jsonl")
    return arc, store

def test_recall_returns_verbatim_chunk(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=5, scene="s5", turn=10, text="艾拉第一次说『别再为我拼命』", entities=["艾拉"])
    hits = recall(campaign, "拼命")
    assert hits and "别再为我拼命" in hits[0]["text"]   # 逐字

def test_recall_anchor_first_meeting(campaign):
    arc, store = _setup(campaign)
    cid = arc.add_chunk(day=1, scene="s1", turn=1,
                        text="「初次见面,我叫雷德。」", entities=["雷德","Monika"])
    store.append(make_event("landmark", 1, "s1", ["雷德","Monika"], "初次见面",
                            deltas={"anchor": "first_meeting"}, chunk_ids=[cid]))
    hits = recall_anchor(campaign, "first_meeting", actor="Monika")
    assert hits and "初次见面,我叫雷德" in hits[0]["text"]   # 锚点→chunk→逐字

def test_recall_anchor_missing_returns_empty(campaign):
    _setup(campaign)
    assert recall_anchor(campaign, "first_meeting", actor="无此人") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_recall.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.recall'`

- [ ] **Step 3: 实现 `engine/recall.py`**

```python
# engine/recall.py
from pathlib import Path

from engine.archive import ArchiveStore
from engine.store import EventStore
from engine.log import get_logger

log = get_logger("recall")

def _archive(campaign_dir):
    return ArchiveStore(Path(campaign_dir) / "archive.db")

def _events(campaign_dir):
    cd = Path(campaign_dir)
    return EventStore(cd / "events.db", cd / "events.jsonl")

def recall(campaign_dir, query, *, k=5, entity=None, day=None):
    """FTS + structured-filter recall over the verbatim archive. Returns chunks (verbatim)."""
    with _archive(campaign_dir) as a:
        hits = a.fts_search(query, k=k, entity=entity, day=day)
    log.debug("recall q=%r entity=%s day=%s hits=%d", query, entity, day, len(hits))
    return hits

def recall_anchor(campaign_dir, anchor_type, *, actor=None):
    """Resolve a landmark anchor (e.g. first_meeting) → its verbatim chunk(s)."""
    with _events(campaign_dir) as store, _archive(campaign_dir) as a:
        match = None
        for ev in store.iter_events():
            if ev["type"] != "landmark":
                continue
            if ev.get("deltas", {}).get("anchor") != anchor_type:
                continue
            if actor and actor not in ev["actors"]:
                continue
            match = ev
            break   # earliest matching landmark
        if not match:
            log.debug("recall_anchor type=%s actor=%s → none", anchor_type, actor)
            return []
        chunks = [a.get_chunk(cid) for cid in match.get("chunk_ids", [])]
        chunks = [c for c in chunks if c]
    log.debug("recall_anchor type=%s actor=%s → %d chunk(s)", anchor_type, actor, len(chunks))
    return chunks
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_recall.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/recall.py tests/test_recall.py
git commit -m "feat(p2a): recall — FTS + structured + landmark-anchor (verbatim)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 工作记忆 / compact

**Files:** Create `engine/compact.py`; Test `tests/test_compact.py`.

`build_working_memory(campaign_dir)` 从 Phase 1 投影 + 近况生成小 markdown。`compact()` = project + 写投影 + 写 `working_memory.md`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compact.py
from engine.store import EventStore
from engine.schema import make_event
from engine.compact import compact, build_working_memory

def _seed(campaign):
    s = EventStore(campaign / "events.db", campaign / "events.jsonl")
    s.append(make_event("location_change", 3, "s3", ["雷德"], "到王都",
                        deltas={"location": "royal_capital"}))
    s.append(make_event("relationship_change", 3, "s3", ["艾拉"], "信任升",
                        deltas={"艾拉.trust": "中→高"}))
    s.append(make_event("promise_made", 3, "s3", ["雷德"], "答应带艾拉看海", id="ev_pr"))
    s.append(make_event("thread_open", 3, "s3", [], "银的身世", thread_refs=["th_s"],
                        deltas={"endpoint": "恢复记忆", "beats": ["真名"], "reveal_conditions": ["Lv15"]}))
    return s

def test_build_working_memory_contains_key_state(campaign):
    _seed(campaign)
    wm = build_working_memory(campaign)
    assert "royal_capital" in wm        # 当前位置
    assert "艾拉" in wm                  # 在场/近期角色
    assert "银的身世" in wm              # 活跃暗线
    assert "看海" in wm                  # 未兑现承诺

def test_compact_writes_working_memory_file(campaign):
    _seed(campaign)
    compact(campaign)
    wm_path = campaign / "working_memory.md"
    assert wm_path.exists() and "royal_capital" in wm_path.read_text(encoding="utf-8")
    assert (campaign / "projections" / "state.json").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_compact.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.compact'`

- [ ] **Step 3: 实现 `engine/compact.py`**

```python
# engine/compact.py
from pathlib import Path

from engine.store import EventStore
from engine.projection import project, write_projections
from engine.log import get_logger

log = get_logger("compact")

def _project(campaign_dir):
    cd = Path(campaign_dir)
    with EventStore(cd / "events.db", cd / "events.jsonl") as s:
        return project(s.iter_events())

def build_working_memory(campaign_dir):
    proj = _project(campaign_dir)
    st = proj["state"]
    lines = ["# 工作记忆", ""]
    lines.append(f"**当前**:Day {st.get('day')} · 地点 {st.get('location')}")
    if proj["characters"]:
        lines.append("\n## 在场/近期角色")
        for name, c in proj["characters"].items():
            lines.append(f"- {name}:信任={c.get('trust')} · {'; '.join(f'{k}={v}' for k,v in c.get('profile',{}).items())}")
    active = [t for t in proj["threads"].values() if t.get("status") != "已解锁" and not t.get("dormant")]
    if active:
        lines.append("\n## 活跃明/暗线")
        for t in active:
            beats = t.get("beats") or []
            nxt = beats[0] if beats else "?"
            lines.append(f"- {t.get('name')}(进度 {t.get('progress')}):下一拍 {nxt}")
    open_p = [p for p in proj["promises"] if not p["kept"]]
    if open_p:
        lines.append("\n## 未兑现承诺")
        for p in open_p:
            lines.append(f"- {p['text']}")
    if proj["villains"]:
        lines.append("\n## 反派能力边界(防全知)")
        for name, v in proj["villains"].items():
            lines.append(f"- {name}:已知 {len(v.get('knows',[]))} 项(每项须有来源)")
    wm = "\n".join(lines) + "\n"
    log.debug("build_working_memory chars=%d threads=%d promises=%d len=%d",
              len(proj["characters"]), len(active), len(open_p), len(wm))
    return wm

def compact(campaign_dir):
    cd = Path(campaign_dir)
    proj = _project(cd)
    write_projections(proj, cd / "projections")
    wm = build_working_memory(cd)
    (cd / "working_memory.md").write_text(wm, encoding="utf-8")
    log.debug("compact wrote projections + working_memory at %s", cd)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_compact.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/compact.py tests/test_compact.py
git commit -m "feat(p2a): working_memory generation + compact

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: CLI 接线(`log-turn` / `recall` / `compact`)

**Files:** Modify `engine/cli.py`、`bin/rpg`; Modify `tests/test_cli.py`.

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
def test_log_turn_and_recall(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    turn = json.dumps({"day":1,"scene":"s1","turn":1,
                       "text":"艾拉在金狮酒馆笨拙地笑了","entities":["艾拉"]}, ensure_ascii=False)
    assert _run(["log-turn", turn], home=tmp_path).returncode == 0
    r = _run(["recall", "酒馆"], home=tmp_path)
    assert r.returncode == 0 and "金狮酒馆" in r.stdout   # 逐字回原文

def test_compact_cli_writes_working_memory(tmp_path):
    _run(["new", "isekai"], home=tmp_path)
    _run(["log-event", json.dumps({"type":"location_change","day":1,"scene":"s1",
          "actors":["雷德"],"summary":"到王都","deltas":{"location":"royal_capital"}},
          ensure_ascii=False)], home=tmp_path)
    assert _run(["compact"], home=tmp_path).returncode == 0
    wm = tmp_path/"storage"/"campaigns"/"isekai"/"working_memory.md"
    assert wm.exists() and "royal_capital" in wm.read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL(无 `log-turn`/`recall`/`compact` 子命令)

- [ ] **Step 3: 实现 CLI 命令**

在 `engine/cli.py` 增加(`_campaign_dir`/`_store` 已存在;新增 archive/recall/compact 接线):

```python
# engine/cli.py 追加 import
import json
from engine.archive import ArchiveStore
from engine import recall as recall_mod
from engine.compact import compact as compact_fn

def cmd_log_turn(args):
    d = _campaign_dir(args.campaign)
    raw = args.json if args.json else sys.stdin.read()
    p = json.loads(raw)
    with ArchiveStore(d / "archive.db") as a:
        cid = a.add_chunk(day=p["day"], scene=p["scene"], turn=p["turn"],
                          text=p["text"], entities=p.get("entities"),
                          event_ids=p.get("event_ids"), kind=p.get("kind", "narration"))
    print(f"logged turn {cid}")

def cmd_recall(args):
    d = _campaign_dir(args.campaign)
    if args.anchor:
        hits = recall_mod.recall_anchor(d, args.anchor, actor=args.actor)
    else:
        hits = recall_mod.recall(d, args.query, k=args.k, entity=args.entity, day=args.day)
    for h in hits:
        print(f"[{h['chunk_id']} day{h['day']}] {h['text']}")
    if not hits:
        print("(no hits)")

def cmd_compact(args):
    d = _campaign_dir(args.campaign)
    compact_fn(d)
    print(f"compacted → {d / 'working_memory.md'}")
```

在 `bin/rpg` 注册子命令:

```python
    lt = sub.add_parser("log-turn"); lt.add_argument("json", nargs="?")
    lt.add_argument("--campaign"); lt.set_defaults(fn=cli.cmd_log_turn)
    rc = sub.add_parser("recall"); rc.add_argument("query", nargs="?", default="")
    rc.add_argument("--campaign"); rc.add_argument("--k", type=int, default=5)
    rc.add_argument("--entity"); rc.add_argument("--day", type=int)
    rc.add_argument("--anchor"); rc.add_argument("--actor")
    rc.set_defaults(fn=cli.cmd_recall)
    cp = sub.add_parser("compact"); cp.add_argument("--campaign"); cp.set_defaults(fn=cli.cmd_compact)
```

- [ ] **Step 4: 跑全量确认通过**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS(34 + archive 6 + recall 3 + compact 2 + cli 2 ≈ 47)

- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p2a): CLI log-turn / recall / compact

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2a 完成判据

- [ ] `.venv/bin/python -m pytest -q` 全绿(~47)
- [ ] 端到端:`new` → `log-turn`(存逐字正文)→ `recall 关键词`(逐字取回)→ 锚点召回首次见面 → `compact`(生成 working_memory.md)
- [ ] `RPG_DEBUG=1 rpg recall ...` 能看到各节点 debug 日志
- [ ] 无新增第三方依赖

**承接 P2b:** `ArchiveStore` 的 chunk(text + entities)即向量化对象;`recall()` 留出融合点——P2b 加 embedder(fake+bge-m3)+ sqlite-vec 向量,语义命中并入 `recall()` 结果。

## Self-Review

- **Spec 覆盖:** §4.4 逐字归档(Task2)、§4.5 召回 FTS/结构化/锚点(Task3)、§4.3 工作记忆(Task4)、CLI(Task5)。语义/向量 → P2b。
- **debug/test 约定:** 每模块 `get_logger` + 关键节点 `log.debug`;每模块独立测试 + caplog 断言;全离线。
- **类型一致:** `ArchiveStore.add_chunk/get_chunk/fts_search`、`recall/recall_anchor`、`compact/build_working_memory` 在各 Task 与 CLI 中签名一致。
