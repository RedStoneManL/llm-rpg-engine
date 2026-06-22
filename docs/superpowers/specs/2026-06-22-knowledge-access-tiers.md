# 知识访问层级 / 迷雾的"读 vs 写" — 设计决定 (2026-06-22 讨论结晶)

> 起因:用户对 P3 fog + scene-cast 的两点关键批评 —— (1) 非物理在场的人/事也能与在场者产生联系,用 location 硬 block 会出问题;(2) 找一个**没有角色卡、也不值得建卡的路人**打听信息,LLM 怎么知道这路人知道多少、用什么权限查知识库?

## 核心区分:fog 卡"读",不卡"写"
- **读(POV 工具)= 一致性下限**:只为了防止叙事模型把"主角可证明还不知道的硬秘密"当成已知写出来。
- **写(叙事模型 commit)+ 打听/联系/够得着 = 创作自由,信任 LLM**:"我去打听隔壁王国 / 找路人问"是一次**获取知识**,走 commit(narration + `knowledge_set` 授予学到的事实),**不经只读工具,fog 从不阻止它**。
- 所以"打听远方/联系非在场者"从来没被 location 挡住;它本就是该信 LLM 发挥的地方。location(`present`/`pov∈present`)只管"台上有谁"和"借谁的眼睛看",不限制 reach。

## 三个访问层级(权限)—— 路人问题的答案
查询世界知识有**三种透镜/权限**,都建立在**已有数据**上(per-agent `knows:` 事实 + Fact 的 `secrecy` 字段):

1. **POV(被追踪 agent 的 `knows`)** —— "主角 / 某在场 NPC 知道什么"。精确到实体的 fog。需要 entity + `knows:` 授予。

2. **Public / Ambient(`secrecy=="public"`,按地点)** —— **"一个随机本地人 / 路人 / 街谈巷议会说什么"**。
   - **路人不建卡、不存其知识**(那荒谬)。路人是临时叙事道具(tier=mentioned 或根本不入图)。
   - 其"知道多少"= **派生**,不是存的:`= 本地 public 事实 + LLM 判断的合理传闻`。
   - **权限 = public 层**:可吐露 `secrecy==public` 的事实 + 合理传闻;**结构性吃不到 `restricted/secret`**(灭口真相、密信内容这种需要特定 knower)。这是地板也是天花板。
   - **LLM 的活(信任它)**:判断**这个**路人(鱼贩 vs 卫兵 vs 小孩)会知道/愿说哪些 public 事实、传闻可信几分。引擎给 public 地板 + secret 天花板,LLM 填中间的人物质感。
   - public 都没有的 → 叙事模型让路人"不知道",或编个标注为"传闻"的合理猜测(LLM,非事实)。

3. **DM(地面真相,全 secrecy,钉 `protagonist_knows`)** —— 作者侧,为打听远方"已确立真相"时保持设定一致。即 T9。

> 路人 = **第 2 层(public)+ LLM 人物化**。主角/在场 NPC = 第 1 层。作者保一致 = 第 3 层。

## 关键发现:`secrecy` 字段是现成但**没被用**的杠杆
Fact 早有 `secrecy ∈ {public, restricted, secret}`(facts/fact.py),但当前 fog 工具**只用 per-agent `knows`、完全没用 secrecy**(又一个 stored-but-unused)。"public-ambient 透镜"= **开始用 secrecy**——正是路人/常识/传闻这一层的结构基础。

## 待办(由这次讨论确立)
- **T9 DM 工具**:不只是"作者看真相",还要支持**按 secrecy 分层**(public-only 透镜给路人/ambient;full-truth 给作者保一致)。
- **public-ambient 查询/工具**:`secrecy=="public"`(+ 地点 scope)的只读透镜,供"找路人打听"用;LLM 在其上人物化。
- 验证 commit 路径不挡 `knowledge_set`(打听到的知识能自由授予)。
- 原则定调:**读=结构性下限(只挡硬秘密)/ 写+打听+reach=信任 LLM**。秘密地板结构挡死,地板之上交 LLM。

## 现状锚点(供 compaction 后续接)
P3a 完成(map/recall/characters/factions POV 工具,fog=knows-gated,AuthorStrategy 接线,glm-5.1 实测会调工具)。scene-cast 修了(present=同地点派生)。dormancy(★6)后台在跑。下一步本应 T9——而这次讨论把 T9 从"DM 看真相"扩成"**按 secrecy 分层 + public-ambient 透镜 + 信任 LLM 定 reach**"。
