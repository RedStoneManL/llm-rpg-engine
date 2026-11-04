# RCV-123：NPC 状态与关系方向回到作者角色卡

日期：2026-11-04（Asia/Shanghai）。基于 RCV-122 `4d2537d`。

## 问题与修复

`cast.evolve` 已能保存 NPC 的 `state`，GUI 的人物读模型也能展示，但 `CharacterSystem.inject` 只输出
sketch、goal、mood；状态未进入当前作者角色卡。`trust:<npc>` 虽可存在于主角事实锚点中，也没有明确地
对应到当前这个角色。连续互动因而缺少一个稳定、就近的状态提示。

现在当前角色卡追加已存在的 state 和主角指向该 NPC 的 attitude；无值不编造，按当前游戏日取有效值。
沿用既有 POV graph，离场人物不获得当前在场角色卡；NPC 对主角的私人信任不被倒置成主角已知态度。
共享作者提示要求从该状态接着回应，仅在实际改变时记录 mood/state，不为推进而每轮重置人物或强造突发事件。

没有新模型调用、新事件类型或存档迁移，也没有新建 NPC 自主行动／内心模拟系统。

## 实际验证

- 新增 6 个案例：正常 GUI 连续两回合的实际 provider 请求、关闭重开、undo、关系方向、状态替换、离场与空值。
- 固定测试 fixture 后，将 HEAD `4d2537d` 的旧 `CharacterSystem.inject` 装入独立测试进程复现：
  **4 failed, 2 passed**。没有回退工作区代码；原始旧方法只用于确认缺口。
- 专项：**197 passed in 2.67s**（context、character、玩家角色、普通作者上下文、策略及最终正文）。
- 完整 gate：**2191 passed, 1 deselected in 39.84s**；仍仅排除原有 slow real embedding。

GUI 测试保留真实 PlayerDashboard→run_turn→SQLite→projection，但用离线 provider 与禁用后台 hook
隔离前台顺序。检查的是作者确实拿到了状态、保存和恢复一致，不是模型必然据此写出高质量角色反应。
同场角色没有可读目标时仍不会补造目标；本项没有为 NPC 反向创建知识或传播玩家自由输入。

体验优先的总体顺序见 [PLAY_EXPERIENCE_PLAN](../designs/PLAY_EXPERIENCE_PLAN.md)。无外部上传或远程发布。
