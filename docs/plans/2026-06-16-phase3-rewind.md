# RPG Engine — Phase 3: 纠错/倒带 (rewind + reproject) Implementation Plan

> **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development. Steps use `- [ ]`.
>
> **🚧 护栏(每个实现/修复 agent 必守):** 只增量改现有文件;**严禁** `git init`/`rm -rf .git`/`git checkout --orphan`/删 `_legacy`或`docs`/切分支/"从零重建"。任何这类冲动=危险信号,停止上报。

**Goal:** 实现确定性倒带原语 `rewind(turn)`:把 ≥该回合的 **事件**(retract,append-only 友好)和 **原文块**(删除)一起撤掉,再 **reproject + recompact + reindex**——状态、工作记忆、召回索引全部自动回到从前。靠的是事件溯源:投影是纯函数,撤掉产物即回滚。

**Architecture:** 给事件加 campaign-全局 `turn` 标(让事件与原文块按回合对齐)。`rewind(campaign,turn)` = `EventStore.retract_from_turn` + `ArchiveStore.delete_from_turn` + `compact()`(重投影+工作记忆)+ `reindex()`(向量)。CLI `rpg rewind <turn>|--last|--to-scene`。`/oops`(倒带上一回合)、`/retcon`(到更早)、`/veto`(撤导演事件回合)都映射到此原语;命令解析与"倒带前确认"流属 P6 prompt 层。`--reroll`(换命运)依赖 P4 导演,本期占位不实现。

**Tech Stack:** Python 3.12 · `sqlite3` · 复用 P1 `retract_from_seq`/`_rewrite_jsonl`、P2a `ArchiveStore`(DELETE 触发器已保证 FTS 一致)、`compact`、P2b `reindex`。

参照 spec §5(纠错/倒带)。

## 项目级约定(每任务遵守)
1. 先写离线失败测试 → 实现 → 通过 → commit。embedder 测试用 FakeEmbedder。
2. 每模块 `get_logger` + 关键节点 `log.debug`。
3. commit 尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
4. 测试 `/root/.hermes/skills/openclaw-imports/rpg-dm/.venv/bin/python -m pytest -q`。

## File Structure
- Modify `engine/schema.py`、`engine/store.py` — 事件加 `turn` 字段 + `retract_from_turn`
- Modify `engine/archive.py` — `delete_from_turn` + `max_turn` + `min_turn_of_scene`
- Create `engine/rewind.py` — `rewind()` 编排 + `last_turn()`
- Modify `engine/cli.py`、`bin/rpg` — `rpg rewind`(`--last`/`--to-scene`/`<turn>`)+ `log-event --turn`
- Tests: 扩 `tests/test_store.py`、`tests/test_archive.py`、新 `tests/test_rewind.py`、扩 `tests/test_cli.py`

---

### Task 1: 事件加 `turn` 标 + `retract_from_turn`

**Files:** Modify `engine/schema.py`、`engine/store.py`; Test 扩 `tests/test_store.py`、`tests/test_schema.py`。

`turn` 为可空 int(向后兼容:旧事件 turn=None,不受 turn 倒带影响)。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_schema.py
def test_make_event_accepts_turn():
    ev = make_event("action", 1, "s1", ["雷德"], "出场", turn=7)
    assert ev["turn"] == 7

def test_make_event_turn_defaults_none():
    assert make_event("action", 1, "s1", ["雷德"], "出场")["turn"] is None
```

```python
# 追加到 tests/test_store.py
def test_turn_roundtrips(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲", turn=3))
    assert list(s.iter_events())[0]["turn"] == 3

def test_retract_from_turn(campaign):
    s = _store(campaign)
    s.append(make_event("action", 1, "s1", ["雷德"], "甲", turn=1))
    s.append(make_event("action", 1, "s2", ["雷德"], "乙", turn=2))
    s.append(make_event("action", 1, "s3", ["雷德"], "丙", turn=3))
    s.append(make_event("world_fact", 1, "s0", [], "无turn旧事件"))  # turn=None
    n = s.retract_from_turn(2)            # 撤 turn>=2 的 乙、丙
    assert n == 2
    kept = [e["summary"] for e in s.iter_events()]
    assert kept == ["甲", "无turn旧事件"]  # turn=None 不受影响
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_store.py tests/test_schema.py -q` → FAIL

- [ ] **Step 3: 实现**

`engine/schema.py` — `make_event` 增 `turn=None` 参数(放在 `id=None` 旁),并写入 `ev["turn"]`:
```python
def make_event(type, day, scene, actors, summary, *, arc=None, deltas=None,
               thread_refs=None, chunk_ids=None, secrecy=None, roll=None, turn=None, id=None):
    ev = {
        ...（原字段不变）...
        "roll": roll, "turn": turn,
        "retracted": False,
    }
    validate_event(ev)
    return ev
```
（`validate_event` 不强制 turn,无需改。）

`engine/store.py`:
- `_SCHEMA` 的 events 表加列 `turn INTEGER`(放在 `retracted` 前)。
- `append` 的 row dict 加 `"turn": ev.get("turn")`,INSERT 列与 VALUES 同步加 `turn`。
- `_row_to_event` 返回里加 `"turn": r["turn"]`。
- 新增方法:
```python
    def retract_from_turn(self, turn) -> int:
        cur = self._conn.execute(
            "UPDATE events SET retracted=1 WHERE turn IS NOT NULL AND turn>=? AND retracted=0",
            (turn,))
        self._conn.commit()
        self._rewrite_jsonl()
        log.debug("retract_from_turn turn>=%s affected=%s", turn, cur.rowcount)
        return cur.rowcount
```

- [ ] **Step 4: 跑确认通过 + 全量无回归**

Run: `.venv/bin/python -m pytest -q`（应 72+：原 70 + 4 新）

- [ ] **Step 5: Commit**

```bash
git add engine/schema.py engine/store.py tests/test_store.py tests/test_schema.py
git commit -m "feat(p3): event turn field + retract_from_turn

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 归档 `delete_from_turn` + turn 边界查询

**Files:** Modify `engine/archive.py`; Test 扩 `tests/test_archive.py`。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_archive.py
def test_delete_from_turn_removes_and_keeps_fts_consistent(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="第一回合台词")
    a.add_chunk(day=1, scene="s2", turn=2, text="第二回合台词")
    a.add_chunk(day=1, scene="s3", turn=3, text="第三回合台词")
    n = a.delete_from_turn(2)
    assert n == 2
    assert a.fts_search("第三回合台词") == []      # 已删,且 FTS 无残留
    assert len(a.fts_search("第一回合台词")) == 1   # 保留

def test_max_turn(campaign):
    a = _arc(campaign)
    assert a.max_turn() == 0
    a.add_chunk(day=1, scene="s1", turn=5, text="x")
    a.add_chunk(day=1, scene="s2", turn=9, text="y")
    assert a.max_turn() == 9

def test_min_turn_of_scene(campaign):
    a = _arc(campaign)
    a.add_chunk(day=1, scene="sA", turn=3, text="a1")
    a.add_chunk(day=1, scene="sA", turn=4, text="a2")
    a.add_chunk(day=1, scene="sB", turn=5, text="b1")
    assert a.min_turn_of_scene("sA") == 3
    assert a.min_turn_of_scene("missing") is None
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现(加到 `engine/archive.py` 的 `ArchiveStore`)**

```python
    def delete_from_turn(self, turn):
        cur = self._conn.execute("DELETE FROM chunks WHERE turn>=?", (turn,))
        self._conn.commit()
        log.debug("delete_from_turn turn>=%s removed=%s", turn, cur.rowcount)
        return cur.rowcount

    def max_turn(self):
        r = self._conn.execute("SELECT COALESCE(MAX(turn),0) FROM chunks").fetchone()
        return r[0]

    def min_turn_of_scene(self, scene):
        r = self._conn.execute("SELECT MIN(turn) FROM chunks WHERE scene=?", (scene,)).fetchone()
        return r[0]
```
（删除会触发 P2a 的 `chunks_ad` AFTER DELETE 触发器,FTS 自动一致。）

- [ ] **Step 4: 跑确认通过**
- [ ] **Step 5: Commit**

```bash
git add engine/archive.py tests/test_archive.py
git commit -m "feat(p3): archive delete_from_turn + turn boundary queries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `engine/rewind.py` 编排

**Files:** Create `engine/rewind.py`; Test `tests/test_rewind.py`。

- [ ] **Step 1: 写失败测试(端到端回滚是核心)**

```python
# tests/test_rewind.py
from engine.store import EventStore
from engine.schema import make_event
from engine.archive import ArchiveStore
from engine.embed import FakeEmbedder
from engine.projection import project
from engine.recall import recall
from engine.rewind import rewind, last_turn

def _setup(campaign):
    s = EventStore(campaign / "events.db", campaign / "events.jsonl")
    a = ArchiveStore(campaign / "archive.db")
    return s, a

def test_rewind_rolls_back_events_chunks_projection_and_recall(campaign):
    s, a = _setup(campaign)
    # 回合1:初次见面,信任建立
    a.add_chunk(day=1, scene="s1", turn=1, text="初次见面台词")
    s.append(make_event("relationship_change", 1, "s1", ["艾拉"], "信任建立",
                        deltas={"艾拉.trust": "无→中"}, turn=1))
    # 回合2:被误解的剧情(要倒带掉)
    a.add_chunk(day=2, scene="s2", turn=2, text="被理解歪的台词")
    s.append(make_event("relationship_change", 2, "s2", ["艾拉"], "信任崩坏",
                        deltas={"艾拉.trust": "中→敌对"}, turn=2))
    # 倒带前:trust=敌对,能召回回合2台词
    assert project(s.iter_events())["characters"]["艾拉"]["trust"] == "敌对"
    assert any("被理解歪" in h["text"] for h in recall(campaign, "被理解歪的台词", embedder=None))
    # 倒带回合2
    res = rewind(campaign, 2, embedder=FakeEmbedder())
    assert res["events_retracted"] == 1 and res["chunks_removed"] == 1
    # 倒带后:trust 自动回到中,回合2台词召回不到
    s2 = EventStore(campaign / "events.db", campaign / "events.jsonl")
    assert project(s2.iter_events())["characters"]["艾拉"]["trust"] == "中"
    assert recall(campaign, "被理解歪的台词", embedder=None) == []
    # 工作记忆已重建
    assert (campaign / "working_memory.md").exists()

def test_last_turn(campaign):
    s, a = _setup(campaign)
    a.add_chunk(day=1, scene="s1", turn=1, text="一")
    a.add_chunk(day=1, scene="s2", turn=2, text="二")
    assert last_turn(campaign) == 2

def test_rewind_last_is_noop_safe_when_empty(campaign):
    _setup(campaign)
    res = rewind(campaign, 1, embedder=FakeEmbedder())
    assert res["events_retracted"] == 0 and res["chunks_removed"] == 0
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `engine/rewind.py`**

```python
# engine/rewind.py
from pathlib import Path

from engine.store import EventStore
from engine.archive import ArchiveStore
from engine.compact import compact
from engine.recall import reindex
from engine.log import get_logger

log = get_logger("rewind")

def last_turn(campaign_dir):
    with ArchiveStore(Path(campaign_dir) / "archive.db") as a:
        return a.max_turn()

def rewind(campaign_dir, turn, *, embedder=None):
    """Retract events + remove chunks with turn>=`turn`, then reproject /
    recompact / reindex. State, working memory and vectors roll back automatically."""
    cd = Path(campaign_dir)
    with EventStore(cd / "events.db", cd / "events.jsonl") as s:
        n_ev = s.retract_from_turn(turn)
    with ArchiveStore(cd / "archive.db") as a:
        n_ch = a.delete_from_turn(turn)
    compact(cd)                      # reproject + working_memory
    reindex(cd, embedder=embedder)   # rebuild vector index (no-op if no embedder)
    log.debug("rewind turn>=%s: events=%d chunks=%d", turn, n_ev, n_ch)
    return {"events_retracted": n_ev, "chunks_removed": n_ch}
```

- [ ] **Step 4: 跑确认通过 + 全量无回归**
- [ ] **Step 5: Commit**

```bash
git add engine/rewind.py tests/test_rewind.py
git commit -m "feat(p3): rewind orchestration (retract+delete → reproject/recompact/reindex)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: CLI `rpg rewind` + `log-event --turn`

**Files:** Modify `engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_cli.py`。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
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
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现**

`engine/cli.py` 加命令(注意 `make_event` 现已支持 `turn`,raw payload 路径无需改):
```python
from engine import rewind as rewind_mod

def cmd_rewind(args):
    log.debug("cmd rewind campaign=%s last=%s to_scene=%s turn=%s",
              getattr(args, "campaign", None), args.last, args.to_scene, args.turn)
    d = _campaign_dir(args.campaign)
    if args.last:
        turn = rewind_mod.last_turn(d)
        if turn <= 0:
            print("nothing to rewind"); return
    elif args.to_scene:
        from engine.archive import ArchiveStore
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
```

`bin/rpg` 注册:
```python
    rw = sub.add_parser("rewind")
    rw.add_argument("turn", nargs="?", type=int)
    rw.add_argument("--campaign"); rw.add_argument("--last", action="store_true")
    rw.add_argument("--to-scene"); rw.set_defaults(fn=cli.cmd_rewind)
```

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p3): rpg rewind CLI (--last/--to-scene/<turn>) + log-event --turn

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 完成判据
- [ ] `.venv/bin/python -m pytest -q` 全绿
- [ ] 端到端:log 两回合 → `rpg rewind --last` → 第二回合的事件/原文/投影/召回全部回滚,第一回合保留
- [ ] `RPG_DEBUG=1` 可见 rewind 各节点日志

**承接 P4:** rewind 已就位;P4 导演的 `oracle_roll`/`director_fired` 事件带 `turn`,倒带后 `--reroll` 可换命运重掷(P4 实现)。`/oops /retcon /veto` 的命令解析 + 倒带前确认 UX 在 P6 prompt 层接。

## Self-Review
- **Spec 覆盖:** §5 rewind 原语(T1-3)+ CLI(T4);`/`命令映射与确认流标注给 P6;`--reroll` 标注依赖 P4。
- **约定:** 每模块 debug 日志;离线测试;事件溯源回滚靠纯函数重投影(T3 端到端证明)。
- **类型一致:** `retract_from_turn`、`delete_from_turn`/`max_turn`/`min_turn_of_scene`、`rewind`/`last_turn`、`make_event(...,turn=)` 跨任务一致。
