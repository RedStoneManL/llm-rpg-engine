# 隐藏发现 B+C：第一版已批准边界

> 状态：**第一版已实现（RCV-113）。** 本文保留原有设计依据；下列“已批准第一版”覆盖先前待决项。

## 已批准第一版

- 玩家可在普通 CLI 用 `/discover` 或 GUI 用“核验附近线索”发起一次**无目标**的核验；请求不能提交地点 ID、路线 key 或授权摘要。
- 可信宿主只在 action-start 从当前公开位置的直连 `undiscovered` 路线中确定一个目标，且该路线必须有**唯一匹配的、已持久化的公开本地宿主线索**；没有线索或多个路线错误共用同一线索时 fail-closed。作者/普通 prompt 只收到泛化的可核验提示。
- 成功时宿主在同一 turn publish 精确 place/route discovery records：出口只在该 turn 成功后进入 POV；主角**不得同回合移动**，下一回合才可按普通可见出口移动。
- 失败、取消、候选/最终校验拒绝和 stale 冲突均不写消费标记或半个发现；整 turn rewind 撤回 discovery，随后新的 action id 可再次核验。
- action-id 只绑定不透明语义摘要；已提交的 cold retry 从 sidecar 恢复，不重新签发机会。native importer 仍独立。

## 目的与已确认基线

当前实现已把“发现既有隐藏地点/路线”定义为宿主权限，而不是普通作者可从叙事中推断出的能力：

- `host_route_discovery_grant(world, scene, destination)` 只接受当前地点直接相邻、已存在且 `visibility=undiscovered` 的目标；第一版的 `host_nearby_discovery_grant()` 还要求该 edge 的私有 clue marker 对应一条既有 `source=host_discovery_clue` 的公开本地观察，并且只在唯一匹配时签发。它产出地点和路线两条 discovery key。
- 普通回合的 `discovery_grant_errors()`、最终 `EventBatch` 校验和 `run_turn()` 都要求作者的 `discover_place` / `discover_route` 写入与精确可信 key 一致。
- `trusted_discovery_keys` 已进入 action-id 请求指纹；同一 action-id 改变该集合必须在生成前 fail-closed。发现写入随整回合原子 publish，撤回与冷重开沿现有 receipt / sidecar 路径工作。
- 已知出口、同回合全新公开前沿地点，和“揭示一个已存在 hidden/undiscovered 路线”是不同通路。后者不能借普通 world、facts、links、narration 或玩家自由文本间接取得。

因此本草案不重新发明发现事件，也不把 NPC 的方向陈述升级为路线事实。它只讨论：若产品希望让玩家在受限玩法里触发一个**已有的、宿主可验证的**隐藏发现，怎样把该宿主决定安全地交给一回合。

## 已解决的第一版选择与后续扩展

第一版已采用下列最小玩法：玩家明确请求一次无目标核验；宿主只接受预先存在的公开本地线索；成功只揭示、不同回合移动；失败、取消与撤回不消耗机会。下表保留的是未来扩展，而非当前实现缺口：

| 决定 | 草案选项 | 尚未批准的问题 |
| --- | --- | --- |
| B：触发机会 | 当前为“已有公开 host 线索 + 玩家明确核验”；UI 只导出布尔能力，CLI 无线索时固定 no-op。 | 更丰富的线索来源、次数或失败后新观察。 |
| C：一次性授权 | 当前为 action-start 重验、精确地点+路线 key、成功只揭示。 | durable 使用次数/过期与其他隐藏内容类型。 |

以下内容不是对这两个选择的默认启用。若用户批准与本文不同的 B/C 定义，应先修订本文及验收矩阵，再实现。

## 建议的接口边界（仅草图）

### 宿主内部候选

建议由可信宿主，而非 CLI 文本、GUI 请求或 Author/Hybrid provider，生成一个不可直接由玩家提交的内部候选。最小形状可为：

```python
@dataclass(frozen=True)
class DiscoveryOpportunity:
    version: int
    actor: str
    action_start_location: str
    # Opaque to player-facing DTO/prompt.  May be an internal ID or resolver input.
    host_selector: object
    # Public-only explanation, optional; must not reveal destination identity.
    public_hint: str | None
```

候选本身不是授权，不改变 POV，也不允许移动。若需要向玩家呈现，只能导出 `public_hint` 和一个无意义的选择句柄；不得输出 hidden 地点 ID、label、canonical topology、discovery key 或宿主 selector。

### 一次性能力解析

在回合开始时，可信宿主将候选重新解析为一个短生命周期内部值；建议 API 形状为：

```python
def resolve_one_shot_discovery(
    world: dict,
    scene: dict,
    opportunity: DiscoveryOpportunity,
    *,
    expected_revision: int | None,
) -> HostDiscoveryGrant:
    """Return exact current keys or fail without writes."""

@dataclass(frozen=True)
class HostDiscoveryGrant:
    version: int
    actor: str
    origin: str
    keys: frozenset[str]       # exact place + route discovery keys
    semantic_id: str           # opaque stable identity for action envelope binding
```

`resolve_one_shot_discovery()` 必须重新调用与现有 `host_route_discovery_grant()` 等价的当前 world/scene 校验：主角、origin、revision、目标存在性、`undiscovered` visibility 与直连路线都需匹配。它不是一个接受 destination 字符串的 player API；普通 CLI/GUI 请求不能提交 `keys`、目标 ID 或 `semantic_id`。

`HostDiscoveryGrant` 只在本次 `run_turn()` 的可信调用栈中转换为现有 `trusted_discovery_keys`。在未另行批准前，不新建“已消费”事件、缓存表或独立 persistence；是否需要持久化 grant 状态是 C 的待决产品选择，而不是本文暗含的结论。

### 可见性、作者材料与提交

1. action-start 前继续使用现有 `pov_world` / `SceneCapabilities`；没有成功 publish 前，目标不进入地图、recall、GUI state、作者的普通公开上下文或可交谈角色集合。
2. 若需要提示作者“这回合可以记录发现”，只传非秘密的结构化能力声明；不要把 hidden label/ID 或完整 canonical route 放入普通 prose prompt。
3. 作者提交仍必须显式给当前 protagonist 写精确 `discover_place` 和 `discover_route`；候选校验和最终 `EventBatch` 校验继续检查 exact key 对。只有整批 publish 成功后，普通 POV 才因 durable knowledge 显示新地点/路线。
4. 当前 native importer 的独立 policy 不应被普通玩家 B/C 自动打开；任何 native 支持需单独设计和批准。

## action-id、原子性与恢复不变量

实现必须遵守下列不变量，不能以一次性授权为由绕过 AID 回收路径：

1. **同一语义才可恢复。** action-id 指纹/sidecar 要绑定 discovery authorization mode 与稳定的 grant semantic identity（或等价、不可歧义的 canonical authorization representation）；同一 action-id 改变、删除或伪造 grant 必须 `RevisionConflict`，且不调用 strategy/provider/generator、不写入。
2. **成功重试不再签发。** 首次成功 publish 后，同 envelope 的 response-loss retry 必须从 receipt/turn-result sidecar 恢复原 `TurnResult`，即使当时的一次性机会已不再可重新解析；不得再次生成、重新消费或重新发现。
3. **失败不消耗。** candidate 拒绝、repair 失败、最终 payload 校验失败、stale revision、异常或取消时，不能写 discovery knowledge、receipt、grant-consumed marker 或部分地图变化。
4. **发布是唯一消费点。** 若 C 最终要求“消耗”，其 durable consume 与 place/route discovery、move、result metadata 必须是同一 publish transaction；不能在 provider 调用前或请求解析时单独消费。
5. **冷重开不信内存。** 恢复只能依据 durable receipt/sidecar 和当前历史；session cache、UI pending、策略消息或 opportunity object 均不是权威。
6. **撤回语义必须明定。** 整 turn rewind 会撤回 discovery records。授权是否在撤回后可重新使用，必须由用户明确选择并由一个 durable、可回放的规则实现；不得因为对象仍留在内存而无声重发。
7. **保密优先于诊断。** 冲突/过期/验证失败只能给出无目标身份的固定错误；日志、trace、GUI/CLI 以及 result metadata 不得输出隐藏地点或 route key。

## 最小离线验收矩阵

| 场景 | 断言 |
| --- | --- |
| 无授权尝试发现 existing hidden route | candidate 与 final payload 都拒绝；revision、SQLite、JSONL、receipt 均不变。 |
| 有效宿主授权 | 只允许精确地点+路线 discovery records；成功 commit 后 POV 地图/recall 才可见；是否同回合移动按获批 C 语义分别测试。 |
| 伪造输入 | 玩家 action、GUI JSON、CLI 命令、provider candidate 直接携带目标/key/semantic id 都不能成为授权。 |
| 过期或 scene/revision 改变 | 在任何模型或写入前安全失败，固定无秘密错误。 |
| candidate 与 final 篡改 | 非本 grant 的 discovery、只写 place/route 之一、broadcast/endowment/facts 伪造、materializer 注入均整批零写入。 |
| response loss | 同 action-id + 精确同信封重开/重试恢复首次结果，strategy/provider/generator 零调用，revision 和 JSONL 不变。 |
| action-id 冲突 | 改 input、scene、grant identity、mode 或 discovery key 都在生成前冲突、零写入。 |
| 失败与取消 | provider/repair/final validator/取消路径均不消耗机会、不留下 discovery 或 receipt；若 C 要持久化消耗状态，同样证明其不出现。 |
| rewind 与冷重开 | 回放后 POV 与历史一致；撤回后机会是否可再用按用户选定策略逐项断言，不能默认。 |
| 隐私出口 | 成功前的 player API/DOM/recall/普通作者 prompt/trace 固定边界不含 hidden label、ID、route key 或私有 selector。 |
| 独立通路 | native import、全新公开前沿地点和既有公开出口的现有行为不被 B/C 改写。 |

所有验收可用固定 Fake provider、临时 SQLite/JSONL 与 loopback 浏览器完成；不需要真实或付费模型、外部网络、部署或凭据修改。

## 批准前的禁止事项

- 不新增 `--discover`、`/api/discovery`、按钮、玩家 DTO 字段或作者 prompt 文本。
- 不把 NPC 报告、任务 summary、world observation、soft steer 或玩家自由输入转换成 discovery grant。
- 不给普通作者新的 topology/world/fact/knowledge 权限，也不改变 native importer 规则。
- 不启用 B1、profiling、真实模型、远程 push、Library/Drive/email 外传。

## 需要用户确认的最小问题

1. B 的触发是纯宿主自动判定、玩家显式确认，还是二者都要？玩家在确认前可以看到什么公开信息？
2. C 成功后是“只揭示”、还是“揭示并可在同一回合移动”？失败、取消、撤回后能否重新尝试？
3. 是否需要 durable 使用次数/过期记录；若需要，它是世界规则还是单次 UI 会话规则？
4. 是否只覆盖已有且直连的 `visibility=undiscovered` 地点/路线（本文建议），还是要扩展到其他隐藏内容类型？

在这些答案到位前，当前代码的默认行为保持：没有可信宿主 grant，就没有既有隐藏路线的发现或移动。
