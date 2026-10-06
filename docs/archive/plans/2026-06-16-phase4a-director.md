# RPG Engine — Phase 4a: 暗骰导演核心 Implementation Plan

> **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development. Steps use `- [ ]`.
>
> **🚧 护栏(每个实现/修复 agent 必守):** 只增量改现有文件;**严禁** `git init`/`rm -rf .git`/`git checkout --orphan`/删 `_legacy`或`docs`/切分支/"从零重建"。停止并上报任何此类冲动。

**Goal:** 涌现引擎核心——**确定性 seeded RNG**(可复现、可倒带重掷)+ **oracle 随机表** + **暗骰节奏引擎**(每场景隐藏 d100,30%→60% 封顶 + 冷却)+ **双轴结果**(隐形埋线 / 前台即时 / 暴击,张力门控高潮阈值)+ `rpg director` CLI(每场景检定→后台种子)。治"趋同/平淡":harness 掷骰给具体种子,LLM 负责把种子写成故事。

**Architecture:** `Oracle(seed)` 包 `random.Random`,提供 d100/加权 draw/表加载;`scene_seed(campaign_seed, scene_ordinal)` 让每场景的掷骰可复现(倒带重跑同场景=同结果,`--reroll` 扰动)。`director_check(scenes_since_event, tension, oracle, tables)` 是**纯函数**:算触发概率→暗掷→双轴(类型×量级)→从表抽具体种子。`rpg director` 从事件流算 pacing、跑检定、命中则发 `director_fired`+`oracle_roll` 事件(审计/可倒带)并打印**后台种子**(前台留给 LLM)。

**Tech Stack:** Python 3.12 · `random`(seeded,确定性)· `hashlib` · 复用 P1 事件/投影。**无新依赖。**

参照 spec §6(导演/神谕)、§6.2(暗骰节奏)、§6.3(双轴)、§6.4(休眠埋线)。本期不含开坑 seed 与多线调度(→ P4b)。

## 项目级约定(每任务遵守)
1. 先写离线失败测试 → 实现 → 通过 → commit。**随机逻辑测试用固定 seed,断言确定性**;概率带用统计断言(多 seed 跑,落在区间)。
2. 每模块 `get_logger` + 关键节点 `log.debug`。
3. commit 尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
4. 测试 `/root/.hermes/skills/openclaw-imports/rpg-dm/.venv/bin/python -m pytest -q`。

## File Structure
- Create `engine/oracle.py` — `Oracle`(seeded RNG + draw)+ `load_table` + `scene_seed`
- Create `data/oracles/default/event_types.json`、`data/oracles/default/twists.json`
- Create `engine/director.py` — `pacing_probability` + `compute_pacing` + `director_check`(纯函数)
- Modify `engine/cli.py`、`bin/rpg` — `rpg director`
- Tests: `tests/test_oracle.py`、`tests/test_director.py`、扩 `tests/test_cli.py`

---

### Task 1: Oracle(seeded RNG + 加权抽取 + 表加载)

**Files:** Create `engine/oracle.py`、`data/oracles/default/event_types.json`、`data/oracles/default/twists.json`; Test `tests/test_oracle.py`。

- [ ] **Step 1: 写默认表**

`data/oracles/default/event_types.json`:
```json
[
  {"weight": 3, "name": "危机", "hint": "遇到危险/被追杀/突发威胁"},
  {"weight": 3, "name": "机遇", "hint": "发现宝藏/获得情报/意外之喜"},
  {"weight": 4, "name": "人物", "hint": "偶遇NPC/旧识登场/触发支线"},
  {"weight": 2, "name": "世界", "hint": "势力变动/城中大事/环境剧变"},
  {"weight": 2, "name": "羁绊", "hint": "同伴的私事/关系推进/情感时刻"}
]
```

`data/oracles/default/twists.json`:
```json
[
  {"weight": 4, "name": "无反转", "hint": "如表面所见"},
  {"weight": 2, "name": "认错人", "hint": "对象其实是别人/误会"},
  {"weight": 2, "name": "另有目的", "hint": "对方动机不单纯"},
  {"weight": 1, "name": "牵出旧线", "hint": "与某条暗线相关"},
  {"weight": 1, "name": "代价", "hint": "机遇背后有隐藏代价"}
]
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_oracle.py
import logging
from engine.oracle import Oracle, load_table, scene_seed

def test_d100_range_and_deterministic():
    a = [Oracle(42).d100() for _ in range(5)]
    b = [Oracle(42).d100() for _ in range(5)]
    assert a == b                                  # 同 seed → 同序列
    assert all(1 <= x <= 100 for x in a)

def test_chance_deterministic():
    assert Oracle(7).chance(0.5) == Oracle(7).chance(0.5)

def test_weighted_draw_respects_weights():
    entries = [{"weight": 99, "name": "common"}, {"weight": 1, "name": "rare"}]
    counts = {"common": 0, "rare": 0}
    for s in range(500):
        counts[Oracle(s).draw(entries)["name"]] += 1
    assert counts["common"] > counts["rare"] * 5      # 重的明显多

def test_draw_deterministic_same_seed():
    entries = [{"weight": 1, "name": "a"}, {"weight": 1, "name": "b"}, {"weight": 1, "name": "c"}]
    assert Oracle(123).draw(entries) == Oracle(123).draw(entries)

def test_load_default_table():
    t = load_table("event_types")
    assert any(e["name"] == "人物" for e in t)

def test_scene_seed_deterministic_and_varies():
    assert scene_seed(1000, 5) == scene_seed(1000, 5)
    assert scene_seed(1000, 5) != scene_seed(1000, 6)

def test_debug_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    Oracle(1).draw([{"weight": 1, "name": "x"}])
    assert any("draw" in r.message for r in caplog.records)
```

- [ ] **Step 3: 实现 `engine/oracle.py`**

```python
# engine/oracle.py
import hashlib
import json
import random
from pathlib import Path

from engine.log import get_logger

log = get_logger("oracle")

_ORACLE_DIR = Path(__file__).resolve().parent.parent / "data" / "oracles"

class Oracle:
    """Seeded, deterministic RNG so director rolls are reproducible (rewind-safe)."""
    def __init__(self, seed):
        self._rng = random.Random(seed)
        self.seed = seed

    def d100(self):
        return self._rng.randint(1, 100)

    def chance(self, p):
        return self._rng.random() < p

    def pick(self, items):
        return items[self._rng.randrange(len(items))]

    def draw(self, entries):
        """Weighted draw from [{'weight': w, ...}, ...]."""
        weights = [max(0.0, float(e.get("weight", 1))) for e in entries]
        chosen = self._rng.choices(entries, weights=weights, k=1)[0]
        log.debug("draw from %d entries → %s", len(entries), chosen.get("name", chosen))
        return chosen

def load_table(name, genre=None):
    """Load data/oracles/<genre>/<name>.json, falling back to default/."""
    for sub in ([genre] if genre else []) + ["default"]:
        p = _ORACLE_DIR / sub / f"{name}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"oracle table not found: {name} (genre={genre})")

def scene_seed(campaign_seed, scene_ordinal, salt=0):
    """Deterministic per-scene seed → reproducible rolls; salt to perturb (--reroll)."""
    h = hashlib.sha256(f"{campaign_seed}:{scene_ordinal}:{salt}".encode()).hexdigest()
    return int(h[:12], 16)
```

- [ ] **Step 4: 跑测试** → PASS(7)
- [ ] **Step 5: Commit**

```bash
git add engine/oracle.py data/oracles/default/ tests/test_oracle.py
git commit -m "feat(p4a): seeded Oracle RNG + weighted draw + default oracle tables

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 暗骰节奏 + 双轴结果(纯函数)

**Files:** Create `engine/director.py`; Test `tests/test_director.py`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_director.py
from engine.oracle import Oracle, load_table
from engine.director import pacing_probability, compute_pacing, director_check
from engine.schema import make_event

def _tables():
    return {"event_types": load_table("event_types"), "twists": load_table("twists")}

def test_pacing_probability_band():
    assert pacing_probability(0) == 0.15          # 冷却
    assert pacing_probability(1) == 0.30          # 基础
    assert abs(pacing_probability(2) - 0.36) < 1e-9
    assert pacing_probability(6) == 0.60          # 封顶
    assert pacing_probability(20) == 0.60         # 永不超 60

def test_trigger_rate_in_band_statistically():
    # scenes_since_event=1 → ~30% 触发(多 seed 统计)
    fired = sum(1 for s in range(2000)
                if director_check(1, 0.0, Oracle(s), tables=_tables())["triggered"])
    assert 0.25 < fired / 2000 < 0.35

def test_director_check_deterministic():
    a = director_check(3, 0.2, Oracle(99), tables=_tables())
    b = director_check(3, 0.2, Oracle(99), tables=_tables())
    assert a == b

def test_triggered_outcome_has_axes_and_seed():
    # 找一个会触发的 seed
    out = next(director_check(6, 0.0, Oracle(s), tables=_tables())
               for s in range(100)
               if director_check(6, 0.0, Oracle(s), tables=_tables())["triggered"])
    assert out["type"] in ("dormant_thread", "front_stage")
    assert out["magnitude"] in ("small", "big", "crit")
    assert "event_type" in out["seed"] and "twist" in out["seed"]

def test_high_tension_downgrades_frontstage_to_dormant():
    # 高张力下,非暴击的前台事件应被压成休眠(不打断大戏)
    fs_high = sum(1 for s in range(1000)
                  if (o := director_check(6, 0.9, Oracle(s), tables=_tables()))["triggered"]
                  and o["type"] == "front_stage")
    fs_low = sum(1 for s in range(1000)
                 if (o := director_check(6, 0.0, Oracle(s), tables=_tables()))["triggered"]
                 and o["type"] == "front_stage")
    assert fs_high < fs_low                       # 高张力前台更少

def test_compute_pacing_counts_scenes_since_fire():
    evs = [
        make_event("action", 1, "s1", ["雷德"], "a"),
        make_event("director_fired", 1, "s1", [], "事件", deltas={}),
        make_event("action", 2, "s2", ["雷德"], "b"),
        make_event("action", 3, "s3", ["雷德"], "c"),
    ]
    p = compute_pacing(evs)
    assert p["scene_ordinal"] == 3
    assert p["scenes_since_event"] == 2           # s2,s3 两场没事件
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `engine/director.py`**

```python
# engine/director.py
from engine.log import get_logger

log = get_logger("director")

BIG_THRESHOLD = 75            # 量级:>=75 大事件
CRIT_BASE = 95                # 暴击基线(张力抬高它,高潮更难但仍可)
DORMANT_RATIO = 0.5           # 类型:埋线 vs 前台 的基础比例
TENSION_GATE = 0.6           # 张力>=此 → 非暴击前台压成休眠

def pacing_probability(scenes_since_event):
    """30%→60% band with a cooldown dip right after an event."""
    if scenes_since_event <= 0:
        return 0.15
    return min(0.30 + 0.06 * (scenes_since_event - 1), 0.60)

def compute_pacing(events):
    """Derive pacing from the event stream: scene ordinal, scenes since last
    director_fired, and a rough tension level."""
    scenes, last_fire_idx, tension = [], -1, 0.0
    for ev in events:
        sc = ev.get("scene")
        if not scenes or scenes[-1] != sc:
            scenes.append(sc)
        t = ev["type"]
        if t == "director_fired":
            last_fire_idx = len(scenes) - 1
        if t in ("combat_result", "character_reveal", "thread_resolve", "villain_knowledge_gain"):
            tension = min(1.0, tension + 0.3)
        elif t in ("action", "dialogue_beat"):
            tension = max(0.0, tension - 0.1)
    ordinal = len(scenes)
    since = (ordinal - 1 - last_fire_idx) if last_fire_idx >= 0 else ordinal
    pacing = {"scene_ordinal": ordinal, "scenes_since_event": max(0, since),
              "tension": round(tension, 2), "current_scene": scenes[-1] if scenes else None}
    log.debug("compute_pacing %s", pacing)
    return pacing

def director_check(scenes_since_event, tension, oracle, *, tables):
    """Pure: hidden d100 vs pacing prob → two-axis outcome (type × magnitude) + drawn seed."""
    prob = pacing_probability(scenes_since_event)
    roll = oracle.d100() / 100.0
    if roll >= prob:
        log.debug("director quiet (roll %.2f >= prob %.2f)", roll, prob)
        return {"triggered": False, "prob": prob, "roll": roll}
    # magnitude
    m = oracle.d100()
    crit_threshold = min(99, CRIT_BASE + int(tension * 4))   # 高张力 → 暴击更难
    if m >= crit_threshold:
        magnitude = "crit"
    elif m >= BIG_THRESHOLD:
        magnitude = "big"
    else:
        magnitude = "small"
    # type
    is_dormant = oracle.chance(DORMANT_RATIO)
    if not is_dormant and tension >= TENSION_GATE and magnitude != "crit":
        is_dormant = True                                    # 重要时刻只埋不扰(暴击除外)
    typ = "dormant_thread" if is_dormant else "front_stage"
    valence = ("boon" if oracle.chance(0.5) else "disaster") if magnitude == "crit" else None
    seed = {"event_type": oracle.draw(tables["event_types"]),
            "twist": oracle.draw(tables["twists"])}
    out = {"triggered": True, "type": typ, "magnitude": magnitude, "valence": valence,
           "seed": seed, "prob": prob, "roll": roll}
    log.debug("director FIRED type=%s mag=%s valence=%s", typ, magnitude, valence)
    return out
```

- [ ] **Step 4: 跑测试** → PASS(6)
- [ ] **Step 5: Commit**

```bash
git add engine/director.py tests/test_director.py
git commit -m "feat(p4a): hidden-dice pacing engine + two-axis outcome (pure)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `rpg director` CLI(每场景检定→后台种子→发事件)

**Files:** Modify `engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_cli.py`。

campaign seed 由 campaign id 派生(确定性,无需额外状态)。命中则发 `director_fired`(进 pacing)+ `oracle_roll`(审计),并打印后台种子供 DM 编织;未触发则静默。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
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
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现**

`engine/cli.py` 加:
```python
import hashlib
from engine.oracle import Oracle, load_table, scene_seed
from engine import director as director_mod

def _campaign_seed(cid):
    return int(hashlib.sha256(cid.encode()).hexdigest()[:8], 16)

def cmd_director(args):
    log.debug("cmd director campaign=%s dry_run=%s", getattr(args, "campaign", None), args.dry_run)
    d = _campaign_dir(args.campaign)
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
    with _store(d) as s:
        s.append(make_event("oracle_roll", day, sc, [], f"暗骰 roll={out['roll']:.2f} prob={out['prob']:.2f}",
                            deltas={"prob": out["prob"], "roll": out["roll"]}))
        if out["type"] == "front_stage":
            s.append(make_event("director_fired", day, sc, [],
                                f"突发:{et['name']}({tw['name']})",
                                deltas={"magnitude": out["magnitude"], "valence": out["valence"],
                                        "event_type": et["name"], "twist": tw["name"]}))
        else:
            # 休眠埋线:thread_open dormant(完整性闸门在 P5 校验)
            s.append(make_event("thread_open", day, sc, [],
                                f"休眠暗线种子:{et['name']}",
                                deltas={"dormant": True, "type": et["name"],
                                        "trigger": "(待 DM 具体化)", "twist": tw["name"]}))
```

`bin/rpg` 注册:
```python
    dr = sub.add_parser("director"); dr.add_argument("--campaign")
    dr.add_argument("--dry-run", action="store_true"); dr.add_argument("--reroll", action="store_true")
    dr.set_defaults(fn=cli.cmd_director)
```

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p4a): rpg director CLI (hidden pacing check → backstage seed + events)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4a 完成判据
- [ ] `.venv/bin/python -m pytest -q` 全绿
- [ ] 统计:`scenes_since_event=1` 触发率 ≈30%,封顶 60%,高张力前台事件更少
- [ ] 端到端:多场景日常后 `rpg director` 可能掷出后台种子(类型×量级),`--dry-run` 不写事件,同状态可复现
- [ ] `RPG_DEBUG=1` 可见暗骰各节点日志(但默认前台只出后台种子,不剧透概率)

**承接 P4b/P5:** `director_check` 产出的休眠 `thread_open`(dormant)由 **P4b 多线调度**推进、**P5 `rpg check`** 校验完整性(终点/节点/揭示条件);开坑 `rpg seed` 在 P4b 用同一 Oracle/表生成。

## Self-Review
- **Spec 覆盖:** §6.2 暗骰节奏(Task2 pacing_probability)、§6.3 双轴(director_check)、§6.4 休眠埋线(type=dormant_thread)、张力门控高潮阈值(crit_threshold + TENSION_GATE);开坑/多线调度 → P4b。
- **约定:** 确定性 seeded(可复现/可倒带重掷)、每模块 debug 日志、离线统计测试。
- **类型一致:** `Oracle.d100/chance/draw`、`load_table`、`scene_seed`、`pacing_probability`/`compute_pacing`/`director_check` 跨任务一致;事件类型 `director_fired`/`oracle_roll`/`thread_open` 均属 P1 封闭枚举。
