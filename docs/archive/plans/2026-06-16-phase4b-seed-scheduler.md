# RPG Engine — Phase 4b: 开坑 seed + 多线调度 Implementation Plan

> **REQUIRED SUB-SKILL:** superpowers:subagent-driven-development。
>
> **🚧 护栏:** 只增量改现有文件;**严禁** `git init`/`rm -rf .git`/删 `_legacy`或`docs`/切分支/"从零重建"。停止并上报任何此类冲动。

**Goal:** 补完导演——① **开坑 `rpg seed <genre>`**:老虎机式掷世界框架 + 3-5 条暗线(类型×速度×终点钩) + 开局 NPC(角色×动机×秘密×特质组合) + 主角钩子,可整体/逐项重 roll,`--commit` 落成 `thread_open` 事件;② **多线调度**:按速度+staleness 算各活跃暗线"该推度",建议推哪条(或都不推),线池薄时提示开新线。全部确定性 seeded(复用 P4a Oracle)。

**Architecture:** `engine/seed.py` `seed_campaign(genre, oracle)` 纯函数掷结构化开局种子(DM 据此写世界圣经+开叙)。调度在 `engine/director.py` 加 `thread_due_scores`/`pick_thread_to_advance`(纯函数,从事件流算场景序+各线 staleness)。CLI `rpg seed`、`rpg threads next`。新增 oracle 表(world_frames/thread_archetypes/npc_roles/npc_traits)。

**Tech Stack:** Python 3.12 · 复用 P4a `Oracle`/`load_table` · 无新依赖。

参照 spec §6.1(开坑)、§6.5(多线调度)。

## 项目级约定:确定性测试用固定 seed;每模块 debug 日志;离线;commit 尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

## File Structure
- Create `data/oracles/default/{world_frames,thread_archetypes,npc_roles,npc_traits}.json`
- Create `engine/seed.py` — `seed_campaign(genre, oracle)`
- Modify `engine/oracle.py` — 加 `Oracle.random()`(供 jitter)
- Modify `engine/director.py` — `thread_due_scores` + `pick_thread_to_advance`
- Modify `engine/cli.py`、`bin/rpg` — `rpg seed`、`rpg threads next`
- Tests: `tests/test_seed.py`、`tests/test_scheduler.py`、扩 `tests/test_cli.py`

---

### Task 1: oracle 表 + `seed_campaign`

**Files:** Create 4 个表 + `engine/seed.py`; Modify `engine/oracle.py`(加 `random()`); Test `tests/test_seed.py`。

- [ ] **Step 1: 写默认表**(最小但真实,genre 无关放 default/)

`data/oracles/default/world_frames.json`:
```json
[
  {"weight": 2, "name": "暗流都市", "tone": "都市悬疑", "central_conflict": "看似平静下的隐秘势力博弈", "factions": 3},
  {"weight": 2, "name": "异世重生", "tone": "日轻冒险", "central_conflict": "穿越者在陌生世界求生与崛起", "factions": 4},
  {"weight": 2, "name": "末世余烬", "tone": "废土生存", "central_conflict": "资源与人性的双重考验", "factions": 3},
  {"weight": 1, "name": "宫廷棋局", "tone": "权谋", "central_conflict": "继承权之争与暗杀", "factions": 5}
]
```

`data/oracles/default/thread_archetypes.json`:
```json
[
  {"weight": 3, "name": "身世之谜", "type": "身世线", "endpoint_hint": "揭开主角/同伴的真实出身", "hook": "一件来历不明的旧物"},
  {"weight": 3, "name": "复仇宿敌", "type": "阴谋线", "endpoint_hint": "与幕后黑手的最终对决", "hook": "一桩未解的旧案"},
  {"weight": 2, "name": "禁忌之力", "type": "物品线", "endpoint_hint": "力量的代价与归宿", "hook": "一次失控的异象"},
  {"weight": 2, "name": "势力暗涌", "type": "势力线", "endpoint_hint": "某方势力的崛起或崩塌", "hook": "街头流传的谣言"},
  {"weight": 2, "name": "情之羁绊", "type": "情感线", "endpoint_hint": "一段关系的质变", "hook": "一个欲言又止的眼神"}
]
```

`data/oracles/default/npc_roles.json`:
```json
[
  {"weight": 3, "name": "情报贩子", "motivation": "求利避险", "secret": "同时为两方效力"},
  {"weight": 3, "name": "落魄旧贵", "motivation": "复兴家门", "secret": "握有一件关键证物"},
  {"weight": 2, "name": "沉默护卫", "motivation": "守护某人", "secret": "背负着血债"},
  {"weight": 2, "name": "市井少年", "motivation": "出人头地", "secret": "身世不凡"},
  {"weight": 2, "name": "神秘旅人", "motivation": "寻找某物", "secret": "来自主角的未来/过去"}
]
```

`data/oracles/default/npc_traits.json`:
```json
[
  {"weight": 2, "name": "嘴硬心软"}, {"weight": 2, "name": "笨拙而敏锐"},
  {"weight": 2, "name": "毒舌"}, {"weight": 2, "name": "表里不一"},
  {"weight": 2, "name": "重情重义"}, {"weight": 1, "name": "深不可测"},
  {"weight": 1, "name": "天真烂漫"}, {"weight": 1, "name": "城府极深"}
]
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_seed.py
from engine.oracle import Oracle
from engine.seed import seed_campaign

def test_seed_structure():
    s = seed_campaign("default", Oracle(1))
    assert "frame" in s and "tone" in s["frame"]
    assert 3 <= len(s["threads"]) <= 5
    for th in s["threads"]:
        assert th["speed"] in ("快", "中", "慢")
        assert th["endpoint"] and th["archetype"]
    assert 2 <= len(s["npcs"]) <= 4
    for n in s["npcs"]:
        assert len(n["traits"]) == 2 and n["role"]
    assert len(s["protagonist_hooks"]) >= 1

def test_seed_deterministic_same_seed():
    assert seed_campaign("default", Oracle(42)) == seed_campaign("default", Oracle(42))

def test_seed_varies_by_seed():
    a = seed_campaign("default", Oracle(1))
    b = seed_campaign("default", Oracle(2))
    assert a != b                       # 不同 seed → 不同骨架(防趋同)
```

- [ ] **Step 3: 实现**

`engine/oracle.py` 加方法:
```python
    def random(self):
        return self._rng.random()

    def randint(self, a, b):
        return self._rng.randint(a, b)
```

`engine/seed.py`:
```python
# engine/seed.py
from engine.oracle import load_table
from engine.log import get_logger

log = get_logger("seed")

def seed_campaign(genre, oracle):
    """Slot-machine campaign opening: world frame + 3-5 threads + opening NPCs + hooks.
    Deterministic given the oracle's seed. Returns a structured seed (the DM weaves it)."""
    frame = oracle.draw(load_table("world_frames", genre))
    n_threads = oracle.randint(3, 5)
    arche = load_table("thread_archetypes", genre)
    threads = []
    for i in range(n_threads):
        a = oracle.draw(arche)
        threads.append({
            "id": f"th_seed{i+1}", "archetype": a["name"], "type": a.get("type"),
            "speed": oracle.draw([{"weight": 2, "name": "快"}, {"weight": 3, "name": "中"},
                                  {"weight": 2, "name": "慢"}])["name"],
            "endpoint": a.get("endpoint_hint"), "hook": a.get("hook"),
        })
    n_npc = oracle.randint(2, 4)
    roles = load_table("npc_roles", genre)
    traits = load_table("npc_traits")
    npcs = []
    for _ in range(n_npc):
        r = oracle.draw(roles)
        npcs.append({"role": r["name"], "motivation": r.get("motivation"),
                     "secret": r.get("secret"),
                     "traits": [oracle.draw(traits)["name"], oracle.draw(traits)["name"]]})
    hooks = [oracle.draw(arche)["name"] for _ in range(2)]
    seed = {"genre": genre, "frame": frame, "threads": threads, "npcs": npcs,
            "protagonist_hooks": hooks}
    log.debug("seed_campaign genre=%s threads=%d npcs=%d", genre, len(threads), len(npcs))
    return seed
```

- [ ] **Step 4: 跑测试** → PASS(3)
- [ ] **Step 5: Commit**

```bash
git add data/oracles/default/ engine/seed.py engine/oracle.py tests/test_seed.py
git commit -m "feat(p4b): campaign seed generator + opening oracle tables

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `rpg seed` CLI(打印种子 + --reroll + --commit)

**Files:** Modify `engine/cli.py`、`bin/rpg`; Test 扩 `tests/test_cli.py`。

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_cli.py
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
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现 `cmd_seed`(engine/cli.py)**

```python
from engine.seed import seed_campaign

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
```

`bin/rpg` 注册:
```python
    sd = sub.add_parser("seed"); sd.add_argument("genre", nargs="?", default="default")
    sd.add_argument("--campaign"); sd.add_argument("--commit", action="store_true")
    sd.add_argument("--reroll", action="store_true"); sd.set_defaults(fn=cli.cmd_seed)
```

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/cli.py bin/rpg tests/test_cli.py
git commit -m "feat(p4b): rpg seed CLI (slot-machine opening; --reroll/--commit)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 多线调度 + `rpg threads next`

**Files:** Modify `engine/director.py`、`engine/cli.py`、`bin/rpg`; Test `tests/test_scheduler.py`、扩 `tests/test_cli.py`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_scheduler.py
from engine.oracle import Oracle
from engine.director import thread_due_scores, pick_thread_to_advance

def _threads():
    return {
        "fast": {"id": "fast", "speed": "快", "status": "活跃", "dormant": False, "last_advanced_scene": "s1"},
        "slow": {"id": "slow", "speed": "慢", "status": "活跃", "dormant": False, "last_advanced_scene": "s1"},
        "done": {"id": "done", "speed": "中", "status": "已解锁", "dormant": False, "last_advanced_scene": "s1"},
        "hidden": {"id": "hidden", "speed": "快", "status": "活跃", "dormant": True, "last_advanced_scene": "s1"},
    }

def _events(n_scenes):
    from engine.schema import make_event
    return [make_event("action", i+1, f"s{i+1}", ["x"], f"场景{i+1}") for i in range(n_scenes)]

def test_scheduler_skips_resolved_and_dormant():
    scores = thread_due_scores(_events(10), _threads(), Oracle(1))
    ids = [s[0] for s in scores]
    assert "done" not in ids and "hidden" not in ids        # 已解锁/休眠不参与
    assert set(ids) == {"fast", "slow"}

def test_fast_thread_more_due_than_slow_after_many_scenes():
    # 都从 s1 起,过 10 场;快线该推度应高于慢线(用同 seed 去掉 jitter 差异看趋势,多 seed 统计)
    fast_wins = sum(1 for s in range(200)
                    if dict(thread_due_scores(_events(10), _threads(), Oracle(s)))["fast"]
                    >= dict(thread_due_scores(_events(10), _threads(), Oracle(s)))["slow"])
    assert fast_wins > 150                                    # 绝大多数情况下快线更该推

def test_pick_returns_none_when_nothing_due():
    # 才过 1 场,没线 overdue
    assert pick_thread_to_advance(_events(1), _threads(), Oracle(1)) is None

def test_pick_returns_a_thread_when_overdue():
    tid = pick_thread_to_advance(_events(20), _threads(), Oracle(1))
    assert tid in ("fast", "slow")
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现(加到 `engine/director.py`)**

```python
SPEED_CADENCE = {"快": 3, "中": 6, "慢": 12}
MIN_ACTIVE_THREADS = 2

def _scene_ordinals(events):
    scenes = []
    for ev in events:
        sc = ev.get("scene")
        if not scenes or scenes[-1] != sc:
            scenes.append(sc)
    return {sc: i + 1 for i, sc in enumerate(scenes)}, len(scenes)

def thread_due_scores(events, threads, oracle):
    """Per active non-dormant thread: due = scenes_since_advance / cadence(speed) * jitter."""
    ords, total = _scene_ordinals(events)
    scores = []
    for tid, th in threads.items():
        if th.get("status") == "已解锁" or th.get("dormant"):
            continue
        last_ord = ords.get(th.get("last_advanced_scene"), 0)
        since = total - last_ord
        cadence = SPEED_CADENCE.get(th.get("speed"), 6)
        jitter = 0.7 + 0.6 * oracle.random()
        scores.append((tid, round((since / cadence) * jitter, 3)))
    scores.sort(key=lambda x: -x[1])
    log.debug("thread_due_scores %s", scores)
    return scores

def pick_thread_to_advance(events, threads, oracle, *, threshold=1.0):
    scores = thread_due_scores(events, threads, oracle)
    if scores and scores[0][1] >= threshold:
        return scores[0][0]
    return None
```

`engine/cli.py` 加 `cmd_threads_next`:
```python
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
```

`bin/rpg` 注册(子命令 `threads`,带 `next`):
```python
    th = sub.add_parser("threads"); th.add_argument("action", choices=["next"])
    th.add_argument("--campaign"); th.set_defaults(fn=cli.cmd_threads_next)
```

- [ ] **Step 4: 跑全量** → 全 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/director.py engine/cli.py bin/rpg tests/test_scheduler.py tests/test_cli.py
git commit -m "feat(p4b): multi-thread scheduler (due-ness by speed+staleness) + rpg threads next

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4b 完成判据
- [ ] `.venv/bin/python -m pytest -q` 全绿
- [ ] `rpg seed default` 掷出世界/3-5暗线/NPC/钩子;`--reroll` 不同;`--commit` 落 thread_open
- [ ] `rpg threads next` 按速度/staleness 建议推哪条(或都不推),线池薄时提示
- [ ] 同 seed 可复现

**承接 P5/P6b:** seed 落的 thread_open(beats/reveal 空)由 **P5 `rpg check`** 完整性闸门提醒补全;调度建议可被 **P6b** 的 hook 或 `rpg director` 联动。

## Self-Review
- **Spec 覆盖:** §6.1 开坑(Task1-2)、§6.5 多线调度+线池(Task3);
- **约定:** 确定性 seeded、debug 日志、离线测试(统计断言用多 seed)。
- **类型一致:** `seed_campaign`、`Oracle.random/randint`、`thread_due_scores`/`pick_thread_to_advance`/`SPEED_CADENCE`/`MIN_ACTIVE_THREADS` 跨任务一致;事件 `thread_open` 属 P1 枚举。
