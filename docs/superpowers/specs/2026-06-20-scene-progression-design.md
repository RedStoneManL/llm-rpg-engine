# 场景推进 (Scene Progression) 设计

> 2026-06-20 · rpg-engine-app `app` 分支。
> 填补架构图点名的**头号空洞**:`meta.scene` 静态 → recap 永远 1 个桶、director 节奏带空转、cascade 场景子树打折。世界钟刚建好,正好给了"时间推进"这一半信号。
> 设计决策由我(在用户授权"接着推进、没啥需要拍板"下)拍定,记此备复核。配套:`2026-06-20-world-clock-design.md`、架构图 `docs/2026-06-19-architecture-world-model.md`。

## 目标
让 `meta.scene` 真正推进,从而:
1. **recap 分层**(#1 收益):`NarrativeSystem` 已按 scene 字符串分桶、老桶 aged-out 压摘要——它**早就建好了,只是从没拿到过不同的 scene 字符串**。让 scene 动起来,recap 自动分层(老场景→摘要,近场景留原文,原文不再无限涨)。
2. **director 节奏**:`scene_ordinal` 不再靠 per-turn salt 兜底,成为真实的场景序数。
3. (顺带)给 lore 后续一个"场景边界"信号(clue 冒泡 / 章节感)。

## 背景:现在为什么是死的
- `kernel/projection.py:24` `meta["scene"] = ev["scene"]`;事件的 scene 来自 `app/play._build_scene`(`scene_id = meta.get("scene") or "scene"`,line 60)→ `run_turn` → `apply_turn(scene=scene_id)` → `to_events(scene=...)`。循环锁死在静态 `"scene"`。
- `systems/narrative.py`:`narration_recorded` 按 `d["scene"]` 分桶(line 105-117),scene 不变 → **永远 1 桶** → `aged_out_scene`(line 54-67,window=`RECAP_RAW_SCENES`=2)永不触发 → 原文无限涨。
- `engine/director.py:17-37` `compute_pacing`:`scene_ordinal = len(distinct scenes)`,静态下恒为 1;`loop/director.py:157-166` 用 `salt=next_turn` 兜底让暗骰不冻(workaround,注释自陈"no scene progression yet")。
- **无任何持久场景计数器**(`engine/director.py:32` 的 ordinal 是每回合从事件流现算的,不持久)。

## 设计

### 1. 场景边界规则
**一个新场景开始,当(本回合结束相对开始)主角【位置变了】或【天数变了】。** 两者现在都可观测:位置 = 主角 `located_in` 邻居(图),天数 = `meta.day`(世界钟)。
- 位置变 = 换了地点(去了新地方)→ 新场景。
- 天数变 = 跨了一天(隔夜/时间跳跃)→ 新场景(同地点也算,用来给"长期同地停留"封顶,否则单地点长序列原文照样涨)。
- 同一天、同地点内的细节推进(同地点 band 推进、连续动作)→ **同场景**。
- v1 用 place id 判"位置变";若实测过碎(地牢里换房间也算),再粗化为"二级地图(level-2)变"——记为可调旋钮,不在 v1。

### 2. 事件溯源 + 唯一单调 id
- 场景 id = `"s1","s2",…` **单调唯一**(绝不重复)——否则 recap 同名桶会撞(revisit 同地点会复用 id)。这是选计数器、不选 `f(location,day)` 派生串的原因。
- 新增 **`SceneSystem`**(小系统,harness-authored,无 commit 段,镜像 `TimeSystem` 的 `time_advanced`):owns 事件 `scene_advanced`;`apply` 设 `meta["scene"] = deltas["scene_id"]` 且 `meta["scene_anchor"] = {"location":…, "day":…}`(锚 = 本场景起始的位置+天)。rewind 安全(随事件重放)。

### 3. 检测 + 推进(在 `run_turn`,apply 之后)
本回合 apply 完,比较:
- `prev_loc` = 主角 `located_in`(旧 `world`),`prev_day` = `world.meta.day`
- `new_loc` = 主角 `located_in`(`new_world`),`new_day` = `new_world.meta.day`
- 若 `new_loc != prev_loc` 或 `new_day != prev_day` → **跨界**:取当前场景号 N(解析 `meta.scene` 的整数后缀,缺省 1),emit `scene_advanced{scene_id:f"s{N+1}", location:new_loc, day:new_day}`,append → re-project。

**归属语义**:本回合的内容事件早已用**旧场景**盖章(回合开始时 `scene["id"]`=旧 `meta.scene`);`scene_advanced` 追加在最后,把 `meta.scene` 翻到**新 id**,供**下一回合**用。即:跨界回合是旧场景的收尾(它的 narration 进旧桶),新场景从下一回合开。和世界钟"本回合即生效"风格一致——边界由本回合的位移/跨天**触发**,新场景**下一回合**落地。

### 4. `_build_scene`
`scene["id"] = meta.get("scene") or "s1"`(计数器)。`scene["location"]` 维持现状(place inject 本就从图取真实 `located_in`,不依赖它)。`new_game` 初始化 `meta.scene="s1"` + 锚。

### 5. 消费方自动受益
- **recap**:不同回合在不同地点/天 → 不同 scene 字符串 → 不同桶 → 超出 window 的老桶 aged-out 压摘要。无需改 `NarrativeSystem`。
- **director**:`scene_ordinal` 成真实序数;`salt=next_turn` 兜底保留(无害,且同场景多回合仍需要它变骰)。无需改 director。
- `scene_advanced` 不是 `narration_recorded` → 不建 recap 桶、不进 director 的 tension 计算,只改 `meta.scene`。干净。

## 不在本 spec(follow-up)
- **cascade 场景子树的真正修复**:`loop/cascade.py:248` `_scene_subtree` 把 scene 串当 place id 走 containment——这是**既有 bug**(现在 scene="scene" 也是坏的)。本 spec 让 scene 变 "s2" **不使其更坏**(同样 walk 一个不存在的 place → 空子树)。真正修复 = 给 cascade 传**真实当前 location**(而非场景 id)做子树,scene id 仍用于盖章。signature 改动,单列。
- **二级地图粒度**:把"位置变"粗化为"level-2 区域变"(用 `contained_by` 上溯 level-2)。v1 用 place id。
- lore 的场景钩子。

## 测试(离线、确定性)
- 边界检测:位置变 → `meta.scene` s1→s2;天数变(同地点)→ 推进;都不变 → 不推进。
- `scene_advanced` 的 `apply` 设 `meta.scene`+锚;rewind/重投影复现同序列。
- **recap 分层(核心收益)**:跨多个地点/天的多回合跑 → `narrative` 出多个桶 → 超 window 的老桶被 aged-out 并(经 fake recap_provider)压出 summary。证明静态时永不发生的分层现在发生。
- director:多场景后 `compute_pacing` 的 `scene_ordinal` 反映真实场景数(>1)。
- 回归:既有套件全绿(scene 变 "s2" 不破 place inject / cascade 现状 / 任何按静态 "scene" 的断言——若有测试硬编 `meta.scene=="scene"` 需更新)。
