# 分段原文查证与结果先行叙事

设计日期：2026-10-25（Asia/Shanghai）。这是当前仓库的渐进实施计划，不是已完成能力清单。

## 用户确认的方向

摘要不是原文替代品。玩家记得的细节与模型摘要保留的细节可能不同；例如“哪一天我对谁说过哪句话”
必须能定位相关经历、逐步展开原文并核对。不能凭摘要重造引号内的原话，也不能因为没有检索到就断言没发生。
叙事应在硬结果裁定后生成，不以事后修正结构而保留矛盾正文。其余长期后果与角色认知按阶段推进。

## 证据分层

| 来源 | 可以证明什么 | 不能证明什么 |
| --- | --- | --- |
| 玩家原始行动输入 | 玩家当时提交了这些文字 | NPC 听到了全部输入、意图实际执行成功 |
| 宿主明确交流记录 | 指定收件人听到了精确的一句话 | 内容是真相、其他人也知道 |
| 已保存公共旁白原文 | 当时展示给玩家的叙述 | 所有文字都有 canonical 效果、旁白中的对话等价于交流 receipt |
| 结构化事件 | 经引擎批准的状态变化及来源 | 未记录的心理活动或隐含对话 |
| 摘要／总概要 | 定位相关经历、快速了解语境 | 精确引用、完整覆盖每个细节、授予知识或行动权限 |
| 角色私有解释 | 该角色对经历的主观判断 | 世界真相、其他角色知道、玩家可见 |

原玩家输入目前不是统一、完整的角色原话档案：GUI host envelope、显式 action 的结果元数据、CLI transcript
和 heard_player 的职责不同。后续要设计最小、与成功回合同事务的输入来源记录，不把模型上下文、修复消息或私有
解释一起存成可公开检索的“对话”。旧记录缺失时明确“无可核对原文”，不可从摘要反向补档。

## 目标读取协议（未启用工具）

1. 宿主按当前 actor、活动分支、同一 revision 快照建立授权阅读集合；不要先搜索全库再过滤结果。
2. 模型按日期／回合／人物／关键词定位片段。只返回小型目录、摘要线索及会话内不透明引用，不返回隐藏事件 ID。
3. 模型选择片段展开逐条原文；可分页读前后文，返回条目类型、时间和来源，标明是否还有未读部分。
4. 读后可改查询、扩展范围或停止。引用原话必须指向实际读取的文字；不命中只能说“本次查证未找到”。
5. 查到旁白不自动授予 NPC 知识。过去可以看到、现在不在场的人物也不因此变成当前可交流对象。

这是 agentic progressive disclosure，不是固定 top-k 摘要拼接。摘要只是入口；关键词搜索需要支持改写查询和
按时间浏览来降低漏检。先做纯本地确定性索引；没有外部 embedding 或新模型依赖。

引用绑定 campaign／actor／revision；分支撤回、重开或权限变化后须重新验证，不接受另一存档或旧分支引用。
查询／展开只读，不写知识、发现、receipt 或任务。限额用每次结果页、工具轮数与实际模型窗口，不用固定 8000
截断整段历史；达到上限返回未完成状态，不伪造完整查证结论。不支持工具的 provider 不声称已查证。

**当前权限边界（2026-10-25 更新）：** 用户已明确解除 B1 detail retrieval 与 profiling 旧 HOLD，要求重新全量评估。
定位／展开工具可以按此设计逐步实施，仍须验证 actor／revision／活动历史范围。指定真实模型可用 Luna medium，
私有解释默认关闭、不改凭据、不外传私人数据或部署的边界不变。后文旧阶段描述保留历史性质。

## 当前落地：RCV-116 来源绑定底座

- narrative bucket 保存原 narration event IDs；新 `scene_summarized.deltas.source` v1 包含首事件 bucket ID、
  有序完整 source IDs、原文与 IDs 的 SHA-256。引用由宿主在 provider 调用前生成，不接受模型自报来源。
- projection 验证 scene、完整源集合及文本摘要；绑定错误 fail-closed，不回退到同名场景的另一段。
  新已绑定 bucket 不吸收后来原文；seq 不进入引用，staging 与 durable replay 一致。
- 历史无 source 的 summary 仍按原语义读取，不迁移、不补造已验证 provenance；旧 bucket 划分也保留。
- 总概要的 prefix index 只跨越连续已有摘要的片段，不能跳过中间尚无摘要的原文。
- 这些字段是内部来源信息，不新增模型工具、公共历史 API 或玩家／NPC 原始输入记录。

这不证明摘要语义忠实，也未把旧总概要的索引式覆盖升级为完整来源链。总概要来源、调度、积压和检索仍是后续任务。

## 当前落地补充：RCV-117 总概要来源链

RCV-116 上述局限中的「新总概要来源」已在此切片实现；调度／积压／检索仍未实现。
沿用现有 SQLite EventStore、NarrativeSystem projection 与 digest_fleet，不建立新索引或数据库。

新 `recap_recompressed.deltas.source` v1 协议：

| 字段 | 含义与校验 |
| --- | --- |
| `version` | 精确整数 1；`true`、浮点数或未知版本不兼容回退 |
| `from_index` / `through_index` | 必须从当前覆盖前沿严格向前推进，终点与 event 的 `summarized_through_index` 一致；只接受完整连续、非空的已摘要范围 |
| `previous` | 首次为 null；之后绑定上一总概要 event ID、SHA-256 和 `source_status` |
| `summaries` | 按 bucket 顺序逐项绑定 `bucket_id`、摘要 `event_id`、SHA-256 与 `source_status`；不可漏项、交换、重复或跨分支复用 |

摘要项的 SHA-256 输入为 `[scene, summary_text, scene_summary_source_or_null]`；前序总概要为
`[super_summary_text, recap_source_or_null]`。统一使用 UTF-8、`ensure_ascii=False`、`sort_keys=True`、
`separators=(',', ':')` 的 JSON 编码；不包含临时／持久 seq，因此 staging 与冷重开一致。
来源在 provider 调用前由 host 冻结；provider 仍只提供摘要正文，其自报来源不被采用。
所有新来源在标准 publish preflight 和 replay 时重新从活动历史投影验证，失败不更新覆盖范围。

`source_status=bound` 仅表示该直接依赖有经过校验的 source 字段；不证明摘要语义正确，也不保证整个祖先链
都没有 legacy 记录。`legacy_unbound` 明确表示旧来源未验证：旧摘要记录只补投影中的已有 event ID，不给
历史事件加 source、不从原文反推它当年覆盖了什么、不改变旧桶划分。旧总概要的 index-only 回放仍兼容；
旧的 partial update 若没有一条同时记录正文与覆盖范围的前序事件，可读取，但拒绝据此生成新 bound 来源，
不能伪造一个 predecessor。后来的 legacy recap 会清除投影中旧 bound 描述，避免误标。

来源链是内部叙事 provenance，不是角色阅读授权。没有新模型工具、公共 DTO、原话档案、向量索引或事实写入；
私有解释仍默认关闭。离线验证见 [RCV-117](../verification/RCV-117-recap-source-chain.md)。

### 本轮原文协议审计与剩余缺口

- `loop/turn.py` 将玩家输入纳入请求 **hash**；result sidecar 保存结果与可选 host envelope，并未统一保存原输入正文。
- GUI envelope 保存场景、明确交流片段／收件人及 receipt，不保存普通 action input；CLI 的可选 transcript
  保存 input、turn、revision、action_id，但在 publish 后另写，不能冒充与成功回合同事务的来源档案。
- `knowledge.heard_player` 只为获 host receipt 授权的收件人写精确 statement，`source_kind=player_statement`；
  旁白、原始行动、NPC 听到的内容仍是不同证据。该切片不改变这些写入语义。
- `engine/archive.py` 的 `c_{scene}_{turn}` 可在撤回重写后重用；`engine/vectorstore.py.search()` 没有 actor/revision
  范围参数。它们仍只可作为后续派生索引的复用组件，不能直接作为安全来源句柄或事实／权限权威。

下一无 HOLD 依赖切片优先拆分 scene summary 与 recap recompression 调度：目前总概要维护仍嵌套在本次
成功生成分段摘要之后，已有摘要积压但没有新 aged raw 时不会独立重试。统一输入来源档案及结果先行叙事仍待实施。
locate/open/邻文重查工具与 B1 detail retrieval 的暂停范围直接重合；实际接入前需要明确解除该工具接入范围的
HOLD，当前没有启用或换名绕过。profiling 同样未执行。

## 后续实施顺序

### A. 公共历史来源与查证

总概要来源绑定已见 RCV-117；RCV-118 已拆分摘要／总概要维护，每次各一项、各最多一次格式修复；
部分失败不吞成功项，取消整体回滚。没有常驻重试队列，空闲时不会自动推进。
然后审查并补齐输入来源保存，建立仅限授权公共叙事／本人输入／明确交流的本地目录。
定位和展开工具已获重新推进授权，先补输入来源与安全读取集合，再接现有工具回路。

验收：同地重访、同一句重复出现、跨天同名人物、摘要删掉关键词、撤回重写、冷重开、私有引用、跨角色与
跨 campaign；每次回答引用必须能对回具体原文。旧输入未存、受限材料和不完整搜索均有明确不足状态。

### B. 结果先行的叙事（下一独立生产切片）

理解行动 → 提议结构化效果 → 规则校验和暂存 → 宿主生成受限结果说明 → 最终正文 → 检查 → 原子发布。
先覆盖资源支付、移动、物品交接和明确交流。模型可写表现细节，但不能更改结果。正文修复不重做付款或行动。
发布前再次校验 revision；失败／取消不留下半个回合，response-loss 恢复原正文而非另写一个版本。
Hybrid 的 prose-first/frozen-prose 路径需要显式调整，不能仅加提示就声称顺序已改变。

语义核对非完备证明：即便增加模型审查也会漏判。确定性状态、语义审查与人工剧情评审分别报告。

### C. 长期后果与角色认知

承诺、未决问题、期限形成带来源的关注事项，而不是新世界真相；履约必须由实际事件证明。
导演提供机会，角色提出受自己知识／资源约束的行动，宿主决定合法效果。先一位 NPC 的记忆闭环：
经历 → 来源知识 → 主观解释 → 可公开行为提议；私有解释不直接灌进公共作者上下文。
验证失约／澄清／误解／纠正和信息传播，不能让所有 NPC 共享玩家全知视角。

## 验收和交付规则

每个可运行切片先有失败反例，再跑真实 store／projection／run_turn 离线链路和全量 gate，本地提交与完整
恢复包。假 provider 证明协议，不证明真实作者质量；真实模型仅按当前授权做受控验证，不外传私人数据或部署。连续剧情另做有界评审，
不把通过数量当作长期可玩性评分。


## 解除暂停后的评估：RCV-119

当前 B1/profiling 已获明确授权，旧阶段的 HOLD 描述不再是实施阻塞。重新评估与实际 100／1000／5000 条
profiling 见 [RCV-119](../verification/RCV-119-current-environment-assessment.md)。优先修复 FTS／语义
范围分歧，再实现同事务输入档案及 actor/revision 绑定读取集合。`public_event_history` 只排除私有解释，
不能作为完整 actor ACL；现有 player recall 不查任意历史旁白。总概要质量和准确引用不能由 provenance hash 代替。
结果先行应复用已有 `loop.resources` 确定性裁定，不重复实现资源余额；实际缺口是全部结构结果与最终正文同步。
