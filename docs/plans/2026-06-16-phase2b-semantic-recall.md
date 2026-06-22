# RPG Engine — Phase 2b: 语义/向量召回 Implementation Plan

> **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development. Steps use `- [ ]`.
>
> **🚧 护栏(每个实现/修复 agent 必守):** 只增量改现有文件;**严禁** `git init`/`rm -rf .git`/`git checkout --orphan`/删 `_legacy`或`docs`/切分支/"从零重建"。任何这类冲动=危险信号,停止上报。

**Goal:** 在 P2a 的 FTS/结构化/锚点召回之上,加**语义向量召回**:可插拔 embedder(离线 **FakeEmbedder** 喂测试 + 真 **bge-m3**(fastembed/ONNX))+ **numpy-cosine 向量库** + 把语义命中**融进 `recall()`**。无 embedder 时优雅退回 P2a 的 FTS 行为。

**Architecture:** `Embedder.embed(texts)->vectors`(Fake=确定性哈希向量,离线;FastEmbed=bge-m3 懒加载)。`VectorStore`(向量持久化为 SQLite blob,numpy 暴力 cosine top-k——单本几千块足够快)。`rpg reindex` 把归档块向量化。`recall()` 增向量路:embed 查询→向量搜→块→与 FTS 结果去重融合。

**Tech Stack:** Python 3.12 · `numpy`(新增,向量计算)· `fastembed`(新增,Task 5 真 embedder,免 torch)· `pytest`。**测试只用 FakeEmbedder + numpy,绝不下载模型。**

参照 spec §4.5(召回三路之向量)、§16 #1(已定 bge-m3)、#4(语义切片起步可换:本期块级嵌入,子块切片留后)。

## 项目级约定(每任务遵守)
1. 先写离线失败测试 → 实现 → 通过 → commit。**embedder 测试一律用 FakeEmbedder,不触网/不下模型。**
2. 每模块 `from engine.log import get_logger` + 关键节点 `log.debug`。
3. commit 尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
4. 测试:`/root/.hermes/skills/openclaw-imports/rpg-dm/.venv/bin/python -m pytest -q`(根目录)。

## File Structure
- Create `engine/embed.py` — `Embedder`/`FakeEmbedder`/`FastEmbedEmbedder`/`get_embedder`
- Create `engine/vectorstore.py` — `VectorStore`(numpy-cosine,SQLite 持久化)
- Modify `engine/recall.py` — `reindex()` + 向量路融进 `recall()`
- Modify `engine/cli.py`、`bin/rpg` — `rpg reindex` + `recall --semantic/--no-semantic`
- Tests: `tests/test_embed.py`、`tests/test_vectorstore.py`、扩 `tests/test_recall.py`、`tests/test_cli.py`

---

### Task 1: Embedder 接口 + FakeEmbedder + get_embedder

**Files:** Create `engine/embed.py`; Test `tests/test_embed.py`.

- [ ] **Step 1: 写失败测试**

```python
# tests/test_embed.py
import logging
from engine.embed import FakeEmbedder, get_embedder

def test_fake_embedder_deterministic():
    e = FakeEmbedder()
    a = e.embed(["艾拉笑了"])[0]
    b = e.embed(["艾拉笑了"])[0]
    assert a == b                       # 同文本→同向量
    assert len(a) == e.dim

def test_fake_embedder_different_texts_differ():
    e = FakeEmbedder()
    assert e.embed(["甲"])[0] != e.embed(["乙"])[0]

def test_fake_embedder_unit_norm():
    e = FakeEmbedder()
    v = e.embed(["x"])[0]
    assert abs(sum(c*c for c in v) ** 0.5 - 1.0) < 1e-6   # 单位向量

def test_get_embedder_fake_by_name():
    assert isinstance(get_embedder("fake"), FakeEmbedder)

def test_get_embedder_none_when_disabled():
    assert get_embedder("none") is None

def test_get_embedder_env(monkeypatch):
    monkeypatch.setenv("RPG_EMBEDDER", "fake")
    assert isinstance(get_embedder(), FakeEmbedder)

def test_debug_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    FakeEmbedder().embed(["x", "y"])
    assert any("embed" in r.message for r in caplog.records)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_embed.py -v` → FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `engine/embed.py`**

```python
# engine/embed.py
import hashlib
import math
import os

from engine.log import get_logger

log = get_logger("embed")

class FakeEmbedder:
    """Deterministic, offline embedder for tests. Same text → same unit vector.
    Not semantically meaningful — only for plumbing/ranking tests."""
    dim = 64

    def embed(self, texts):
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [((h[i % len(h)] / 255.0) * 2.0 - 1.0) for i in range(self.dim)]
            n = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / n for x in vec])
        log.debug("FakeEmbedder.embed n=%d dim=%d", len(texts), self.dim)
        return out

class FastEmbedEmbedder:
    """Real local bge-m3 via fastembed (ONNX, no torch). Lazy-loads the model."""
    def __init__(self, model_name="BAAI/bge-m3"):
        self.model_name = model_name
        self._model = None
        self.dim = 1024

    def _ensure(self):
        if self._model is None:
            from fastembed import TextEmbedding
            log.debug("loading fastembed model %s", self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts):
        model = self._ensure()
        vecs = [list(map(float, v)) for v in model.embed(list(texts))]
        log.debug("FastEmbedEmbedder.embed n=%d", len(texts))
        return vecs

def get_embedder(name=None):
    """name (or RPG_EMBEDDER env): 'fake' | 'fastembed' | 'none'. Default: none if unset."""
    name = (name or os.environ.get("RPG_EMBEDDER") or "none").lower()
    if name == "fake":
        return FakeEmbedder()
    if name == "fastembed":
        return FastEmbedEmbedder()
    return None
```

- [ ] **Step 4: 跑测试确认通过** → PASS(7)
- [ ] **Step 5: Commit**

```bash
git add engine/embed.py tests/test_embed.py
git commit -m "feat(p2b): pluggable Embedder (FakeEmbedder offline + bge-m3 fastembed)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 向量库(numpy-cosine,SQLite 持久化)

**Files:** Create `engine/vectorstore.py`; Test `tests/test_vectorstore.py`. **先装 numpy。**

- [ ] **Step 1: 装 numpy**

Run: `.venv/bin/pip install -q numpy && echo numpy ok`
然后把 `numpy` 追加进 `requirements-dev.txt`。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_vectorstore.py
import logging
from engine.vectorstore import VectorStore

def _vs(campaign):
    return VectorStore(campaign / "vectors.db")

def test_add_and_search_returns_nearest(campaign):
    vs = _vs(campaign)
    vs.add("a", [1.0, 0.0, 0.0])
    vs.add("b", [0.0, 1.0, 0.0])
    vs.add("c", [0.9, 0.1, 0.0])
    hits = vs.search([1.0, 0.0, 0.0], k=2)
    ids = [h[0] for h in hits]
    assert ids[0] == "a" and "c" in ids          # 最近的是自己,其次方向相近的 c
    assert hits[0][1] >= hits[1][1]              # 分数降序

def test_search_empty_store(campaign):
    assert _vs(campaign).search([1.0, 0.0], k=3) == []

def test_persist_across_reopen(campaign):
    _vs(campaign).add("a", [1.0, 0.0])
    assert _vs(campaign).search([1.0, 0.0], k=1)[0][0] == "a"

def test_add_replaces_same_id(campaign):
    vs = _vs(campaign)
    vs.add("a", [1.0, 0.0]); vs.add("a", [0.0, 1.0])
    assert len(vs.search([0.0, 1.0], k=5)) == 1   # 不重复

def test_debug_logs(campaign, caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    vs = _vs(campaign); vs.add("a", [1.0, 0.0]); vs.search([1.0, 0.0], k=1)
    assert any("search" in r.message for r in caplog.records)

def test_context_manager_closes(campaign):
    import sqlite3
    with _vs(campaign) as vs:
        vs.add("a", [1.0, 0.0])
    try:
        vs.add("b", [0.0, 1.0]); assert False
    except sqlite3.ProgrammingError:
        pass
```

- [ ] **Step 3: 实现 `engine/vectorstore.py`**

```python
# engine/vectorstore.py
import sqlite3
from pathlib import Path

import numpy as np

from engine.log import get_logger

log = get_logger("vectorstore")

class VectorStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors (block_id TEXT PRIMARY KEY, vec BLOB NOT NULL)")
        self._conn.commit()

    def add(self, block_id, vector):
        v = np.asarray(vector, dtype=np.float32)
        self._conn.execute("INSERT OR REPLACE INTO vectors (block_id, vec) VALUES (?,?)",
                            (block_id, v.tobytes()))
        self._conn.commit()
        log.debug("add block_id=%s dim=%d", block_id, v.shape[0])

    def search(self, vector, k=5):
        rows = self._conn.execute("SELECT block_id, vec FROM vectors").fetchall()
        if not rows:
            log.debug("search empty store")
            return []
        q = np.asarray(vector, dtype=np.float32)
        qn = q / (np.linalg.norm(q) or 1.0)
        ids, mats = [], []
        for bid, blob in rows:
            ids.append(bid); mats.append(np.frombuffer(blob, dtype=np.float32))
        M = np.vstack(mats)
        norms = np.linalg.norm(M, axis=1)
        norms[norms == 0] = 1.0
        sims = (M @ qn) / norms
        order = np.argsort(-sims)[:k]
        out = [(ids[i], float(sims[i])) for i in order]
        log.debug("search k=%d candidates=%d → %d", k, len(rows), len(out))
        return out

    def clear(self):
        self._conn.execute("DELETE FROM vectors"); self._conn.commit()

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

- [ ] **Step 4: 跑测试** → PASS(6)
- [ ] **Step 5: Commit**

```bash
git add engine/vectorstore.py tests/test_vectorstore.py requirements-dev.txt
git commit -m "feat(p2b): numpy-cosine VectorStore (SQLite-persisted blobs)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: reindex(归档块→向量)

**Files:** Modify `engine/recall.py`(加 `reindex`); Test 扩 `tests/test_recall.py`.

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_recall.py
from engine.embed import FakeEmbedder
from engine.recall import reindex
from engine.vectorstore import VectorStore

def test_reindex_embeds_all_chunks(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="艾拉在酒馆")
    arc.add_chunk(day=2, scene="s2", turn=2, text="雷德在图书馆")
    n = reindex(campaign, embedder=FakeEmbedder())
    assert n == 2
    with VectorStore(campaign / "vectors.db") as vs:
        assert len(vs.search(FakeEmbedder().embed(["艾拉在酒馆"])[0], k=5)) == 2

def test_reindex_is_rebuildable(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="一")
    reindex(campaign, embedder=FakeEmbedder())
    reindex(campaign, embedder=FakeEmbedder())   # 重跑不翻倍
    with VectorStore(campaign / "vectors.db") as vs:
        assert len(vs.search(FakeEmbedder().embed(["一"])[0], k=10)) == 1
```

- [ ] **Step 2: 跑测试确认失败**（ImportError: reindex）

- [ ] **Step 3: 在 `engine/recall.py` 加 `reindex`**

```python
# engine/recall.py 追加 import
from engine.vectorstore import VectorStore
from engine.embed import get_embedder

def _vectors(campaign_dir):
    return VectorStore(Path(campaign_dir) / "vectors.db")

def reindex(campaign_dir, *, embedder=None):
    """Embed all archive chunks into the vector store. Returns count. Rebuilds from scratch."""
    emb = embedder or get_embedder()
    if emb is None:
        log.debug("reindex skipped: no embedder")
        return 0
    with _archive(campaign_dir) as a:
        chunks = list(a.iter_chunks())
    texts = [c["text"] for c in chunks]
    vecs = emb.embed(texts) if texts else []
    with _vectors(campaign_dir) as vs:
        vs.clear()
        for c, v in zip(chunks, vecs):
            vs.add(c["chunk_id"], v)
    log.debug("reindex embedded=%d", len(chunks))
    return len(chunks)
```

**注意:** `ArchiveStore` 需要 `iter_chunks()` — 若不存在,在 `engine/archive.py` 加:
```python
    def iter_chunks(self):
        for r in self._conn.execute("SELECT * FROM chunks ORDER BY rowid"):
            yield self._row(r)
```

- [ ] **Step 4: 跑测试** → PASS(2 新)
- [ ] **Step 5: Commit**

```bash
git add engine/recall.py engine/archive.py tests/test_recall.py
git commit -m "feat(p2b): reindex archive chunks into vector store

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 语义路融进 recall() + CLI

**Files:** Modify `engine/recall.py`、`engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_recall.py`、`tests/test_cli.py`.

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_recall.py
def test_recall_semantic_path_finds_exact(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="独一无二的句子甲")
    arc.add_chunk(day=2, scene="s2", turn=2, text="另一句乙")
    reindex(campaign, embedder=FakeEmbedder())
    hits = recall(campaign, "独一无二的句子甲", semantic=True, embedder=FakeEmbedder())
    assert any("独一无二的句子甲" in h["text"] for h in hits)

def test_recall_fuses_and_dedups(campaign):
    # 同一块既被 FTS(子串)又被语义命中,只出现一次
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="共同关键词出现在此")
    reindex(campaign, embedder=FakeEmbedder())
    hits = recall(campaign, "共同关键词", semantic=True, embedder=FakeEmbedder())
    ids = [h["chunk_id"] for h in hits]
    assert ids.count("c_s1_1") == 1

def test_recall_no_embedder_is_fts_only(campaign):
    arc, _ = _setup(campaign)
    arc.add_chunk(day=1, scene="s1", turn=1, text="只有FTS能命中的词")
    hits = recall(campaign, "只有FTS能命中的词", semantic=True, embedder=None)  # 无 embedder
    assert any("只有FTS" in h["text"] for h in hits)   # 优雅退回 FTS
```

```python
# 追加到 tests/test_cli.py
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
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 改 `recall()` 加语义融合**

把 `engine/recall.py` 的 `recall` 改为:

```python
def recall(campaign_dir, query, *, k=5, entity=None, day=None, semantic=True, embedder=None):
    """FTS + structured + (optional) semantic recall over the verbatim archive.
    Returns verbatim chunks, deduped by chunk_id (FTS hits first)."""
    with _archive(campaign_dir) as a:
        fts_hits = a.fts_search(query, k=k, entity=entity, day=day)
        sem_hits = []
        emb = embedder if embedder is not None else get_embedder()
        if semantic and emb is not None:
            try:
                qv = emb.embed([query])[0]
                with _vectors(campaign_dir) as vs:
                    for cid, _score in vs.search(qv, k):
                        ch = a.get_chunk(cid)
                        if ch:
                            sem_hits.append(ch)
            except Exception as e:           # 向量路失败不应连累 FTS
                log.debug("semantic path failed: %s", e)
        seen, merged = set(), []
        for h in fts_hits + sem_hits:
            if h["chunk_id"] not in seen:
                seen.add(h["chunk_id"]); merged.append(h)
    log.debug("recall q=%r fts=%d sem=%d merged=%d", query, len(fts_hits), len(sem_hits), len(merged))
    return merged
```

`engine/cli.py` 的 `cmd_recall` 改为支持 `--semantic/--no-semantic`(默认开,用 `get_embedder()`):

```python
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
```

`bin/rpg` 的 recall 子命令加 `--semantic`/`--no-semantic`(默认 True):

```python
    rc.add_argument("--semantic", dest="semantic", action="store_true", default=True)
    rc.add_argument("--no-semantic", dest="semantic", action="store_false")
```

- [ ] **Step 4: 跑全量** → 全 PASS（51 + embed 7 + vectorstore 6 + recall 4 + cli 1 ≈ 69）
- [ ] **Step 5: Commit**

```bash
git add engine/recall.py engine/cli.py bin/rpg tests/test_recall.py tests/test_cli.py
git commit -m "feat(p2b): fuse semantic vector recall into recall() + CLI --semantic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 真 bge-m3 后端联调(慢测,默认跳过,优雅缺省)

**Files:** Test `tests/test_embed_real.py`(标 slow);可能微调 `engine/embed.py`。

- [ ] **Step 1: 装 fastembed**

Run: `.venv/bin/pip install -q fastembed && echo fastembed ok`(把 `fastembed` 追加进 `requirements-dev.txt`)

- [ ] **Step 2: 写慢测(联网下载模型;无网/失败则 skip,绝不让 CI 红)**

```python
# tests/test_embed_real.py
import pytest
from engine.embed import FastEmbedEmbedder

@pytest.mark.slow
def test_bge_m3_real_embedding():
    try:
        emb = FastEmbedEmbedder()
        v = emb.embed(["第一次见面你对我说的话"])[0]
    except Exception as e:
        pytest.skip(f"bge-m3 unavailable (offline?): {e}")
    assert len(v) == emb.dim == 1024
    # 语义合理性:近义句的 cosine 高于无关句
    import numpy as np
    a, b, c = emb.embed(["我害怕黑暗", "我对黑暗感到恐惧", "今天天气很好"])
    cos = lambda x, y: float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y)))
    assert cos(a, b) > cos(a, c)
```

在 `pyproject.toml` 注册 marker(避免告警):

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-q -m 'not slow'"
markers = ["slow: requires model download / network"]
```

- [ ] **Step 3: 默认跑(应跳过 slow)** → `.venv/bin/python -m pytest -q` 全绿,slow 被 deselect
- [ ] **Step 4: 手动联调一次(可选,controller 执行)** → `.venv/bin/python -m pytest -m slow -q`,确认 bge-m3 真能跑、近义 cosine 更高
- [ ] **Step 5: Commit**

```bash
git add tests/test_embed_real.py pyproject.toml requirements-dev.txt
git commit -m "test(p2b): real bge-m3 verification (slow, skipped offline) + register marker

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2b 完成判据
- [ ] `.venv/bin/python -m pytest -q` 全绿(~69,slow 跳过)
- [ ] `RPG_EMBEDDER=fake rpg reindex && RPG_EMBEDDER=fake rpg recall "句" --semantic` 跑通
- [ ] 无 embedder 时 `recall` 优雅退回 FTS(不报错)
- [ ] (可选)`pytest -m slow` 联调 bge-m3 通过:近义句 cosine > 无关句
- [ ] 新依赖仅 `numpy` + `fastembed`(均免 torch)

**承接 P3:** `recall()` 已统一三路;P3 倒带在 `retract_from_seq` 基础上加 rewind,语义索引在重录后由 `reindex` 重建。

## Self-Review
- **Spec 覆盖:** §4.5 向量路(Task1-4)、§16#1 bge-m3 落地(Task1/5)、优雅缺省(Task4)。子块语义切片 → 留后(spec §16#4 允许)。
- **约定:** 每模块 debug 日志 + 离线测试;**embedder 测试全用 FakeEmbedder,真模型仅 slow 测且可跳过**。
- **类型一致:** `Embedder.embed`、`get_embedder`、`VectorStore.add/search/clear`、`reindex`、`recall(...semantic,embedder)` 跨任务签名一致。
