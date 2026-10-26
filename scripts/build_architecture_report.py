"""Generate a standalone Chinese architecture report from reviewed source + live contracts.

No model, embedding, telemetry, external assets or user campaign access.
The inventory is AST-derived; semantic explanations are curated, not a claim that
static imports constitute an exhaustive runtime call graph.
"""
from __future__ import annotations
import argparse
import ast
import html
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.engine import build_engine
from llm.provider import FakeLLMProvider

GROUPS = {
 'app': ('入口与玩家界面', 'CLI、回环 WSGI GUI、玩家 DTO、初始化与会话控制。把用户操作转为受约束的引擎调用，负责展示与恢复。'),
 'loop': ('回合与世界编排', '前台生成／修复／提交，创世，暗骰导演、级联、补时、任务与摘要维护。调用模型与调度顺序主要集中于此。'),
 'context': ('上下文与权限', '将 canonical 世界转换为 POV 可见材料；定义旅行、事实、知识、物品与任务权限，同时提供候选和最终事件两道检查。'),
 'kernel': ('内核协议', '系统注册、事件路由、投影、结构验证和片段拼装。描述“如何接入一种规则”，不承担所有玩法语义。'),
 'systems': ('领域规则系统', '14 个默认系统 + 1 个 opt-in 私有解释系统。拥有事件／提交段，并实现投影、验证、事件生成、上下文和召回钩子。'),
 'facts': ('时态事实图', 'Entity、Fact、Relation 与 FactGraph。承载地点、人物、关系、资源和角色知识的共享结构。'),
 'llm': ('模型与工具协议', 'Provider、消息与工具回路、结构化 JSON 校验修复、创世失败收集。模型给出候选，由宿主决定是否生效。'),
 'memory': ('评分与反思', '重要性、近期性和向量相关性排序；达到阈值后生成反思。不是一个完整的角色记忆数据库或自主 Agent。'),
 'engine': ('存储与兼容基础设施', 'EventStore／EventBatch 是主链基础设施；Archive／VectorStore 是可复用检索底座。同时保留旧 CLI、旧投影与 compact 路线。不能整目录视为废弃。'),
}
PURPOSE = {
 'app/__main__.py':'普通 CLI 启动与参数检查；选择显式 provider、创建／恢复 campaign，再进入 play_loop。',
 'app/engine.py':'组合 Registry、系统、EventStore、provider 与 embedder；从同一 snapshot 恢复 world/revision；提供新游戏与撤回。',
 'app/gui.py':'本机 WSGI 路由、PlayerDashboard／GmDashboard 与 HTML 页面；action-id 恢复、同源写入检查、被动版本刷新。',
 'app/play.py':'普通回合与 OOC 命令、scene 构建、明确交流、compare、撤回、transcript 和已保存状态展示。',
 'app/player_character.py':'从主角 POV 生成角色状态；过滤内部 capability bookkeeping。',
 'app/player_objectives.py':'将主角目标与公开任务转换为统一玩家账本。',
 'app/player_observations.py':'将持久化的本地公开观察转换为玩家可读 DTO。',
 'app/player_people.py':'只展示当前公开同场人物的可见特征，不返回秘密与内部证据。',
 'app/player_recall.py':'在 POV redacted world 上调用 kernel recall，只返回有界文本列表。',
 'app/player_resources.py':'把 durable resource resolution 解释为玩家可见的支付结果。',
 'app/session_zero.py':'交互收集开局所需的最低信息，避免未完成配置直接创世。',
 'app/genesis_preview.py':'对显式创世来源生成只读预览，不调用 provider、不创建存档。',
 'app/trace.py':'本地 trace 查看与诊断入口；operator 材料不能当成玩家公开 API。',
 'context/access.py':'根据 protagonist、co-presence、knowledge 与 secrecy 生成独立 POV FactGraph。',
 'context/capabilities.py':'SceneCapabilities／HostDiscoveryGrant；同一行动范围用于 prompt、玩家地图和两阶段发布校验。',
 'context/native_authoring.py':'宿主安全交接、条件抵达资料、明确交流 receipt 签发与校验，以及独立 native import policy。',
 'context/assembler.py':'组合 stable／scene／volatile、事实锚点、capabilities、公开观察和已过滤交接；不读取全库 Archive。',
 'context/viewpoint.py':'按主角／NPC 认知生成 viewpoint bundle；输入来自调用者提供的图与事实键。',
 'context/place_observations.py':'提取已知当前／可达地点的公开观察，保留范围与时间，不隐式透露未知地点。',
 'kernel/contextsystem.py':'ContextSystem 插件契约，以及 ValidationError、Fragment、RecallHit 数据对象。',
 'kernel/registry.py':'事件类型和 commit section 的单 owner 路由；注册时检查依赖与重复归属。',
 'kernel/projection.py':'按活动事件顺序重放：meta + owner.apply；旧未知 owner 事件整体忽略，不推进时钟。',
 'kernel/turncommit.py':'TurnCommit = narration + sections；正文是保留字段，不属于任一领域 section。',
 'kernel/validation.py':'跨系统 validate_commit 和 repair request 构造；校验 section owner 与新建引用。',
 'kernel/events.py':'kernel_event 工厂与受 Registry 事件类型限制的 open_store。',
 'kernel/assembler.py':'收集各系统 inject() 的 Fragment 并按稳定层顺序渲染。',
 'kernel/recall.py':'对已给定 world 扇出各系统 recall()，合并 RecallHit 并排序。',
 'kernel/clock.py':'纯游戏时间运算；不依赖真实墙钟推进世界。',
 'kernel/observability.py':'Noop／可选 Langfuse tracer；带凭据的遥测是独立外部能力，本报告未启用。',
 'facts/entity.py':'实体 ID、类型、追踪层级、attrs。',
 'facts/fact.py':'事实与关系的有效时间区间、摄入 turn、source_event 和 secrecy。',
 'facts/graph.py':'实体／事实／关系索引，当前值、历史值与邻接查询；内存投影可从事件重建。',
 'facts/rules.py':'场景定义的结构化约束，不把特定题材规则硬编码进叙事。',
 'llm/provider.py':'LLMProvider、Fake、OpenAI-compatible／DeepSeek／Zhipu／Anthropic 适配，消息和有界工具循环。',
 'llm/structured.py':'解析 JSON、validate、有限 repair、错误返回；结构合格不代表语义忠实。',
 'llm/tools.py':'POV ToolRegistry 与六个默认只读工具；DM 才增加 ground-truth query。没有历史 locate/open。',
 'llm/generation_guard.py':'收集耗尽重试的生成失败，阻止真实创世路径把 stub／失败伪装成成功。',
 'loop/turn.py':'主交易网关：duplicate/preflight、权限、资源裁定、策略、后台、publish 和对话缓存恢复。',
 'loop/strategy.py':'Author 的 JSON 联合创作与可选工具／多轮会话；Hybrid 的 prose-first + structure，保留 frozen prose。',
 'loop/resources.py':'解析登记资源意图；确定性判断支付／不足／等待，再约束作者及最终余额。',
 'loop/entity_resolve.py':'验证前解析名字到实体引用并补齐新实体声明；随后仍须通过普通作者权限检查。',
 'loop/narration_guard.py':'拒绝明显重复最近已发布正文；不是通用结果一致性或事实审查器。',
 'loop/fleet.py':'重要性累积与反思；独立 scene summary／recap maintenance；保守任务 backstop。',
 'loop/director.py':'持久暗骰导演触发、线程推进与指令事件；在前台效果之后执行。',
 'loop/cascade.py':'基于触发事件传播世界影响；层级节点与横向区域传播、有限 ThreadPoolExecutor 调用，顺序合并写入。',
 'loop/time.py':'游戏内跳时与进入视野的 stale 实体 catch-up；不会因为纯冷重开重复补时。',
 'loop/lore.py':'任务线创建、生命周期与按需重排；推进暗／明／终局相关状态。',
 'loop/lore_disclosure.py':'当前位置的 station-push 线索注入，只披露匹配范围的材料。',
 'loop/density.py':'按地区密度／复杂度上限生成任务骨架，控制内容补充量。',
 'loop/endgame.py':'复杂任务线的救援概率、灾变和终局辅助逻辑。',
 'loop/variation.py':'生成前选择并记录叙事变化，靠持久 seed 支持可回放变化。',
 'loop/compare.py':'在同一 pre-turn snapshot 生成 Author／Hybrid 候选；比较阶段不写存档。',
 'loop/bootstrap.py':'创世编排、原子封装和 reroll；真实 provider 失败不发布半成品。',
 'loop/genesis_spec.py':'标准 GenesisSpec 的归一化、合并与最低完整性约束。',
 'loop/genesis_blueprint.py':'从文件加载并校验玩家提供的 blueprint。',
 'loop/import_sillytavern.py':'角色卡／世界书转换为 GenesisSpec；导入源不自动成为已保存 campaign 的修改。',
 'loop/flavor.py':'题材 pack 的解析、默认值与持久化。',
 'loop/graph_utils.py':'共享祖先／层级图遍历，减少世界编排中的重复逻辑。',
 'loop/genesis/world.py':'世界框架、区域和本地地图生成器与结构校验。',
 'loop/genesis/cast.py':'主角、势力、NPC 生成器。',
 'loop/genesis/lore.py':'Codex、任务骨架和开场正文生成器。',
 'loop/genesis/common.py':'创世公共抽样与字段检查。',
 'memory/importance.py':'事件启发式重要性，可选模型评分；用于后台反思触发。',
 'memory/reflection.py':'累积重要性达到阈值后生成带来源的 arc／反思候选。',
 'memory/recall.py':'近期性 0.35 + 重要性 0.35 + 相关性 0.30；不是历史全文检索。',
 'engine/store.py':'SQLite 权威存储、同 revision snapshot、EventBatch/savepoint、receipt/sidecar、可恢复 JSONL 镜像。',
 'engine/archive.py':'旧逐字 chunks + FTS5 trigram + 短词 LIKE；RCV-120 共享精确 day/entity 范围；键仍为 scene+turn。',
 'engine/vectorstore.py':'SQLite float32 向量与 NumPy cosine；可接受宿主 allowed_ids，在 SQL 选取后排序。',
 'engine/recall.py':'旧 Archive 的 FTS／语义混合检索及重建索引；不等于普通作者的 kernel recall。',
 'engine/embed.py':'FakeEmbedder 接线替身与惰性 FastEmbed；默认 none，数据库本地不意味着推理全本地。',
 'engine/schema.py':'基础事件对象形状校验及旧事件枚举；kernel 以 registry allowed_types 扩展。',
 'engine/settings.py':'进程级运行设置：verbosity、style、conversation mode、tool-round ceiling 等。',
 'engine/log.py':'统一日志，配合调试输出与本地诊断。',
 'engine/oracle.py':'确定性随机、场景 seed、表与 pack manifest；可回放的随机底座。',
 'engine/cli.py':'旧 rpg 命令／hook 入口：log-turn、log-event、recall、compact 等；与 python -m app 不同。',
 'engine/projection.py':'旧投影格式 state/characters/threads 等；不是 kernel Registry 投影。',
 'engine/compact.py':'旧工作记忆／compact 文件路线；不等于 NarrativeSystem 的分段摘要协议。',
 'engine/rewind.py':'旧 CLI 撤回联动 Archive／向量重建；现代 app.engine.rewind 使用当前引擎协议。',
 'engine/director.py':'旧 CLI 导演路线；现代回合用 loop.director + systems.director。',
 'engine/seed.py':'旧 campaign 初始化帮助函数。',
 'engine/check.py':'旧 campaign 一致性／暗线检查命令。',
}
SYS_INFO = {
 'ontology':('事实底座','FactGraph：entities / facts / relations；状态变更带 source_event 与时态。','提供最底层事实，不替其他系统决定玩家权限。'),
 'place':('地点与移动','状态写共享 FactGraph：Place、contained_by、adjacent_to、located_in。','包含区域／城镇／地点层级；可见路线与合法移动另外受 SceneCapabilities 限制。'),
 'character':('人物与关系','Person 实体、sketch/goal/mood/state 事实、关系状态写共享图。','NPC 私有目标不能因为共用图就成为玩家已知。'),
 'object':('物品与归属','Object 实体与 held_by 关系写共享图。','普通作者交接限于当前可见背包、人物与地点，不开放任意造资产。'),
 'faction':('势力与成员','Faction 实体、member/rank/group 相关关系／事实写共享图。','普通玩家回合的 factions 写入受额外策略约束，系统支持不等于作者可任意调用。'),
 'knowledge':('知识与通信来源','knows:<fact_key> 等认知事实写共享图；belief 与 canonical truth 可不同。','heard_player 只能由宿主签发的精确 statement receipt 支持；听到不等于内容为真。'),
 'director':('导演与暗骰','pending 指令、consumed_through_turn、threads；seed/roll 也进入事件。','当前同步回合钩子，不是常驻 Agent 进程。'),
 'cascade':('世界后果','queue、changes、consumed_through_turn + 地点／群体事实。','传播模型判定有范围与预算，不能把内部线程池说成完全自主世界。'),
 'time':('游戏时钟','主要更新 world.meta 的 day/band 与时间相关事实；独立 slice 为空。','游戏时钟由事件推进，与机器运行多久无直接等价关系。'),
 'narrative':('旁白与摘要','scenes 原文桶、super_summary、summarized_through_index，以及逐桶／递归来源。','长期保存的 narration 原文不因摘要生成而从事件库删除；注入上下文才发生替换。'),
 'scene':('场景序号','主要更新 meta.scene / scene_no；独立 slice 为空。','位置或天数变化后登记 scene_advanced，不是单独地图存储。'),
 'lore':('任务与长期线索','lines 任务字典与 gen 生成状态；暗／明／了结及期限／终局相关事件。','任务线与普通世界事实分开；明账可显示，隐藏骨架不直接给玩家。'),
 'codex':('世界设定典','entries：题目、类型、正文等公开设定块。','供查询与设定一致性使用，不是角色原话或行动生效证明。'),
 'resources':('资源裁定','按场景规则配置并校验 before/after/outcome；余额写共享图。','已具先裁定的局部基础；未覆盖所有行动的最终 prose 同步。'),
 'interpretive_memory':('私有解释 · opt-in','by_actor：以 actor 分组的解释记录及 host-bound 来源。','默认不注册；本地固定生成器实验，不是已完成的生产 NPC 心智。'),
}
LEGACY={'engine/cli.py','engine/projection.py','engine/compact.py','engine/rewind.py','engine/director.py','engine/seed.py','engine/check.py'}
MAIN={'app/__main__.py','app/play.py','app/gui.py','app/engine.py','loop/turn.py','loop/strategy.py','context/assembler.py','context/capabilities.py','context/native_authoring.py','engine/store.py','kernel/projection.py','kernel/registry.py','kernel/validation.py'}

def esc(x): return html.escape(str(x), quote=True)
def pathid(p): return 'file-'+p.replace('/','-').replace('.','-')
def ref(path, symbol=None):
    tree=ast.parse((ROOT/path).read_text())
    nodes=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.ClassDef,ast.AsyncFunctionDef)) and n.name==symbol]
    line=nodes[0].lineno if nodes else 1
    return f'<a class="source" href="#{pathid(path)}">{esc(path)}:{line}</a>'
def chips(values): return ''.join(f'<code>{esc(v)}</code>' for v in values) or '<span class="muted">无作者提交段</span>'
def table(head, rows): return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def section(ident,num,title,intro,body): return f'<section id="{ident}" class="section"><div class="section-head"><span class="num">{num}</span><div><h2>{title}</h2><p>{intro}</p></div></div>{body}</section>'
def note(title,text,kind='blue'): return f'<aside class="note {kind}"><b>{title}</b><p>{text}</p></aside>'
def card(title,body): return '<article class="card"><h3>'+title+'</h3>'+body+'</article>'


def inventory():
    out=[]
    for group in GROUPS:
        for p in sorted((ROOT/group).rglob('*.py')):
            if p.name=='__init__.py': continue
            rel=p.relative_to(ROOT).as_posix(); src=p.read_text();t=ast.parse(src)
            symbols=[]
            for n in t.body:
                if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                    symbols.append({'name':n.name,'line':n.lineno,'kind':'class' if isinstance(n,ast.ClassDef) else 'fn'})
                    if isinstance(n,ast.ClassDef):
                        symbols += [{'name':f'{n.name}.{m.name}','line':m.lineno,'kind':'method'} for m in n.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef))]
            imports=set()
            for n in ast.walk(t):
                names=[]
                if isinstance(n,ast.Import): names=[a.name for a in n.names]
                if isinstance(n,ast.ImportFrom) and n.module: names=[n.module]
                for name in names:
                    if name.split('.')[0] in GROUPS:
                        target=name.replace('.','/')+'.py'
                        if (ROOT/target).is_file(): imports.add(target)
            desc=PURPOSE.get(rel)
            if rel.startswith('systems/'):
                name=p.stem;info=SYS_INFO[name];desc=info[0]+'。'+info[1]+info[2]
            assert desc, rel
            out.append({'path':rel,'group':group,'lines':len(src.splitlines()),'symbols':symbols,'imports':sorted(imports),'desc':desc,'tag':'legacy' if rel in LEGACY else 'main' if rel in MAIN else 'support'})
    return out


def system_contracts():
    with tempfile.TemporaryDirectory(prefix='rpg-architecture-') as tmp:
        e=build_engine(tmp,provider=FakeLLMProvider(),embedder=None,enable_interpretive_memory=True)
        try:
            return [{'name':s.name,'class':type(s).__name__,'events':sorted(s.event_types()),'sections':sorted(s.commit_sections()),'requires':sorted(s.requires()),'methods':[n for n in ('apply','validate','to_events','created_ids','inject','recall') if n in type(s).__dict__]} for s in e.registry.systems]
        finally: e.store.close()


def build():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='docs/reports/2026-10-26/architecture-status.html');parser.add_argument('--date',default='2026-10-26');args=parser.parse_args()
    files=inventory();systems=system_contracts()
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    branch=subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()
    perf=json.loads((ROOT/'docs/verification/rcv119-assessment.json').read_text())
    count_events=len({v for s in systems if s['name']!='interpretive_memory' for v in s['events']})
    content=[]
    body=note('接下来优先优化什么？','先补“原始输入 → 可授权读取的来源 → 逐步查证”这条闭环，并同步推进结果裁定后的最终正文。保留已验证的事务与来源底座；性能优化从减少重复投影和控制上下文增长入手。')
    body+='<div class="cards three">'+card('底座已经具备', '<p>事件溯源、14 个默认系统、严格普通作者边界、同回合原子保存、撤回／冷重开、带来源的分段与递归摘要。</p><span class="badge good">实现 + 回归证据</span>')+card('核心体验仍有断点','<p>普通行动原 input 尚无完整同事务档案；旧原文无法让模型逐步 locate/open；修正结构后正文仍可能保留旧结果。</p><span class="badge warn">优先补闭环</span>')+card('长期运行需正视规模','<p>5,000 条未摘要旁白产生约 161 万字符上下文。单回合反复重放和复制历史，背景模拟仍依附前台同步运行。</p><span class="badge warn">有测量，未完成治理</span>')+'</div>'
    body+=table(['问题','当前判断','应采取的动作'],[
      ['B1 / profiling 是否还暂停？','用户已明确解除旧 HOLD。','按当前边界继续实现与验证，无需再次申请旧暂停解除。'],
      ['是否需要重做数据库／上向量服务？','目前 SQLite／FTS／NumPy 已可用。','先统一来源身份和读取授权；向量只生成候选。'],
      ['是否应该继续堆摘要？','摘要来源链已经补齐，语义仍会失真或遗漏。','让摘要提供导航，再读原文；保留“未找到／未核实”状态。'],
      ['主要验收障碍？','本地开发无权限阻塞；远程 API 凭据和 Chromium 缺失。','对应验收单独记录；不把环境缺口放大为停止整个项目。'],
    ])
    content.append(section('overview','01','现状判断与优化方向','这是代码现状报告。实现、离线回归、原生模型样本和未来设计分别标注。',body))

    svg='''<svg class="architecture" viewBox="0 0 1000 730" role="img" aria-labelledby="diagram-title"><title id="diagram-title">普通回合主链：入口到策略、暂存与持久化；兼容检索尚未连通逐步原文工具</title><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#62818d"/></marker></defs>
<g fill="none" stroke="#62818d" stroke-width="2" marker-end="url(#arrow)"><path d="M255 110 H380"/><path d="M620 110 H735"/><path d="M845 160 V218 H500 V250"/><path d="M380 300 H265"/><path d="M620 300 H735"/><path d="M500 350 V410"/><path d="M500 510 V570"/><path d="M735 620 H620"/></g>
<g class="svgnode"><rect x="35" y="60" width="220" height="100" rx="12"/><text x="55" y="92">app · CLI / GUI</text><text class="sub" x="55" y="120">输入、scene、显式交流</text><text class="sub" x="55" y="143">玩家 DTO 与 action-id 恢复</text></g>
<g class="svgnode"><rect x="380" y="60" width="240" height="100" rx="12"/><text x="400" y="92">loop.run_turn</text><text class="sub" x="400" y="120">preflight / revision / 权限</text><text class="sub" x="400" y="143">资源先裁定、宿主交接</text></g>
<g class="svgnode"><rect x="735" y="60" width="230" height="100" rx="12"/><text x="755" y="92">Strategy + Provider</text><text class="sub" x="755" y="120">Context → 模型 / 工具</text><text class="sub" x="755" y="143">TurnCommit → 校验与修复</text></g>
<g class="svgnode"><rect x="380" y="250" width="240" height="100" rx="12"/><text x="400" y="282">EventBatch · 暂存</text><text class="sub" x="400" y="310">前台效果 + narration</text><text class="sub" x="400" y="333">同一 turn，尚未持久化</text></g>
<g class="svgnode"><rect x="35" y="250" width="230" height="100" rx="12"/><text x="55" y="282">后台钩子</text><text class="sub" x="55" y="310">digest / director / cascade…</text><text class="sub" x="55" y="333">savepoint，影响加入同批</text></g>
<g class="svgnode"><rect x="735" y="250" width="230" height="100" rx="12"/><text x="755" y="282">Registry + Systems</text><text class="sub" x="755" y="310">owner.apply → world</text><text class="sub" x="755" y="333">FactGraph + 独立 slices</text></g>
<g class="svgnode"><rect x="380" y="410" width="240" height="100" rx="12"/><text x="400" y="442">publish · 短事务</text><text class="sub" x="400" y="470">revision / preflight / payload</text><text class="sub" x="400" y="493">events + receipt + sidecar</text></g>
<g class="svgnode solid"><rect x="380" y="570" width="240" height="100" rx="12"/><text x="400" y="602">SQLite EventStore</text><text class="sub" x="400" y="630">持久权威 → JSONL 镜像</text><text class="sub" x="400" y="653">显示结果、更新会话缓存</text></g>
<g class="svgnode"><rect x="735" y="570" width="230" height="100" rx="12"/><text x="755" y="602">重开 / rewind</text><text class="sub" x="755" y="630">活动事件 → project</text><text class="sub" x="755" y="653">一致 snapshot + revision</text></g>
<g class="svgnode planned"><rect x="35" y="475" width="230" height="150" rx="12"/><text x="55" y="510">独立 Archive / FTS / 向量</text><text class="sub" x="55" y="540">旧 CLI 路线与可复用底座</text><text class="sub" x="55" y="567">尚无安全 source session</text><text class="sub" x="55" y="594">尚未连通 locate / open 工具</text></g></svg>'''
    body=svg+note('图中是主调用关系，不是线程时序','后台写入仍进入 EventBatch；有些钩子会并发执行模型判定，但主回合仍等待。project 在准备、钩子后和发布校验中多次发生；图中只画一次以便阅读。兼容 Archive 路线没有画成主作者已接通的长期原文工具。')
    body+=table(['层','核心输入','核心输出','主要源码'],[
      ['入口','动作／明确讲话／action_id／expected_revision','scene、host receipt、TurnResult 展示',ref('app/play.py','play_loop')+' '+ref('app/gui.py','PlayerDashboard')],
      ['编排','world + scene + action + policy','已校验 commit、同 turn 暂存事件',ref('loop/turn.py','run_turn')],
      ['上下文','canonical world + 当前 POV','有限可见上下文／工具查询结果',ref('context/assembler.py','assemble_context')+' '+ref('llm/tools.py','build_tool_registry')],
      ['规则','各 section 声明、已有世界','ValidationError / domain events / projected state',ref('kernel/contextsystem.py','ContextSystem')+' '+ref('kernel/registry.py','Registry')],
      ['持久化','待发布事件 + 预期版本 + sidecar','durable revision / receipt / 可重放历史',ref('engine/store.py','EventStore')],
    ])
    content.append(section('architecture','02','整体结构：协议内核 + 领域系统 + 回合编排','先看路径，再看文件。下方模块索引提供具体类与函数行号。',body))

    objects=[
      ['Engine','registry / store / provider / embedder / world / campaign_seed / cascade_provider','运行时装配容器，持有当前 projection。不是新的持久化权威。',ref('app/engine.py','Engine')],
      ['world','meta: day / band / scene / scene_no / timeline；systems: ontology + 各 slice；_revision','从同一历史 snapshot 重建；_revision 用于拒绝过期行动。',ref('kernel/projection.py','empty_world')],
      ['scene','id / protagonist / present / day / location；宿主追加受控提示与 resolution','本次行动的视角与能力范围；不能由模型任意替换 actor 或隐藏目标。',ref('app/play.py','_build_scene')],
      ['TurnCommit','narration: str；sections: dict[str, declaration]','模型候选；正文与结构放在一起，不表示两者语义已被完备证明。',ref('kernel/turncommit.py','TurnCommit')],
      ['event','id / type / day / scene / actors / summary / deltas / turn / retracted；持久 seq','宿主路由并校验后发布的状态变更／叙事记录。事件各自拥有者决定含义。',ref('kernel/events.py','kernel_event')],
      ['EventBatch','snapshot / revision / events / retractions / turn / publish_validators','外部生成时不占 SQLite 写锁；savepoint 回滚局部；最后短事务提交。',ref('engine/store.py','EventBatch')],
      ['Fact / Relation','event_time_start/end；ingest_turn；source_event；secrecy 或 attrs','有有效时间与摄入轮次的共享图数据；角色的 belief 不等于 canonical value。',ref('facts/fact.py','Fact')],
      ['Fragment / RecallHit','Fragment: layer/text/affordance；RecallHit: system/score/text/ref','内部接口已有 ref，但当前玩家查询与工具输出通常只呈现文本，未形成可打开来源句柄。',ref('kernel/contextsystem.py','RecallHit')],
    ]
    body=table(['对象','结构','职责与边界','源码'],objects)
    body+=note('关键约束：一份事实图，多种语义入口','Place、Character、Object、Faction、Knowledge 等系统的 empty_state() 返回空字典，是因为主要状态写入 systems.ontology 的 FactGraph。Time／Scene 又主要写 world.meta。只有看到空 slice 就判断“这个系统没实现”，会读错架构。','amber')
    content.append(section('objects','03','核心对象与状态结构','区分候选、暂存、持久记录和投影，才能判断一次动作究竟在哪里生效。',body))

    paths={
      'normal':('正常行动',[
       ('01','入口构造','CLI play_loop 或 GUI PlayerDashboard.action 构造 scene、action_id、expected_revision。明确交流单独产生 host receipt；普通 input 不自动等于 NPC 听到的话。','app/gui.py','action'),
       ('02','恢复检查与快照','run_turn 先检查已提交 action 和请求指纹；新请求创建 EventBatch，读取同一 snapshot/revision，再注册最终 payload validators。','loop/turn.py','run_turn'),
       ('03','宿主前置裁定','登记资源存在时 prepare_resources 解析意图并确定性裁定余额／等待；准备 variation 与已过滤的安全作者交接。','loop/resources.py','prepare_resources'),
       ('04','上下文与生成','produce_turn → strategy.produce；assemble_context 做 POV、系统注入、摘要、事实锚点和能力范围；Author 可调用六个只读工具。','loop/strategy.py','AuthorStrategy'),
       ('05','候选验证与修复','实体引用补齐 → 系统 validate + narration/resource/authority 检查。局部 repair_sections；有些普通错误耗尽后可丢 section，权限类错误严格拒绝。','loop/turn.py','produce_turn'),
       ('06','暂存前台事件','apply_turn 依 owner.to_events 转结构为事件；旁白另外记录 narration_recorded，同一 turn。opt-in 私有解释绑定该 narration。此时仍未写入 durable store。','loop/turn.py','_run_turn_staged'),
       ('07','同步后台','digest → director → cascade → catch-up → lore → 离场 demote → density；各 hook savepoint，追加后按需 project；最后场景推进。','loop/turn.py','_backstage_call'),
       ('08','最终事务','重新核对裁定余额、制作 result sidecar。batch.publish → EventStore.append_many：revision、完整投影 preflight、最终事件权限校验，然后 events/receipt/sidecar 同事务提交。','engine/store.py','append_many'),
       ('09','提交后展示','同步 JSONL 镜像（失败标 mirror_pending）；成功提交后才把 narration 计入策略会话。入口更新 world 并显示已保存正文；可选 transcript 是提交后的另写。','loop/turn.py','run_turn'),
      ]),
      'duplicate':('重复请求／响应丢失',[
       ('01','收到相同 action_id','入口先恢复历史请求上下文，run_turn 从 SQLite 读取已提交 action 与 sidecar。','loop/turn.py','_restore_completed_turn'),
       ('02','核对身份','检查 fingerprint、基础版本、权限模式及是否被撤回。相同 ID 不代表可以重写不同动作；缺少可证信息时保守拒绝／报告未确认。','loop/turn.py','_turn_request_fingerprint'),
       ('03','返回原结果','合法重试返回原 narration/events/recap_status 与 duplicate receipt；不重新调用作者、不重新扣款、不重复写入。','app/gui.py','_stored_context'),
      ]),
      'failure':('失败／取消／冲突',[
       ('01','生成或候选失败','尚在 EventBatch 准备阶段；越权或无法修复的关键旅行失败拒绝，不能将假成功正文当已保存。','loop/turn.py','produce_turn'),
       ('02','普通后台异常','savepoint 恢复该 hook 的事件、撤回意图与 validators；前台及其他成功 hook 可继续。摘要任务还有 job-local 边界。','engine/store.py','savepoint'),
       ('03','取消或最终校验失败','CancelledError 传播，整批不 publish；revision 冲突／最终校验失败在事务中回滚，策略会话恢复旧状态。','loop/turn.py','run_turn'),
       ('04','已写库但展示／镜像失败','不能重报“行动未保存”并再跑一次；通过 durable receipt + sidecar 恢复原动作，镜像可补同步。','engine/store.py','read_committed_action'),
      ]),
      'rewind':('撤回与冷重开',[
       ('01','显式撤回','检查 expected revision 与受保护的创世 turn 0；按完整 turn 标记 retracted，保留历史而非直接删行。','app/engine.py','rewind'),
       ('02','重建世界','project 只应用活动历史；narration、summary、前序 recap 与私有解释随同 turn 撤回；不能复活已撤回来源。','kernel/projection.py','project'),
       ('03','清理会话与作用域','CLI／GUI 重置策略缓存，恢复有效 prior scene，避免冷重开或撤回后重复 catch-up。','app/play.py','_previous_scene_before_latest_action'),
       ('04','重新打开','build_engine 注册系统，store.snapshot 同时取 history/revision，重新投影；旧 branch 的 action 或未来 source 句柄不能无条件复用。','app/engine.py','build_engine'),
      ])
    }
    body='<div class="tabs" role="tablist" aria-label="回合路径">'+''.join(f'<button type="button" role="tab" id="tab-{k}" aria-controls="flow-{k}" aria-selected="{str(k=="normal").lower()}" data-flow="{k}">{v[0]}</button>' for k,v in paths.items())+'</div>'
    for k,(title,steps) in paths.items():
        body+=f'<div class="flow-panel" role="tabpanel" id="flow-{k}" aria-labelledby="tab-{k}"'+(' hidden' if k!='normal' else '')+'><ol class="flow-list">'
        for number,title,desc,p,sym in steps:
            body+=f'<li><span>{number}</span><div><h3>{title}</h3><p>{desc}</p>{ref(p,sym)}</div></li>'
        body+='</ol></div>'
    body+=note('“已生成”与“已保存”不是同一阶段','模型输出、修复和所有后台工作都在 durable publish 前完成。SQLite 事务里不进行网络模型调用。返回给玩家的结果必须对应最终成功提交的那一批事件。')
    content.append(section('flow','04','一次回合到底怎样调用','切换四条路径，查看正常执行及恢复边界；调用顺序按当前函数体核对。',body))

    hookrows=[
      ['digest_fleet','事件重要性 → 阈值反思；分段摘要与总概要各一次；保守任务兜底','可能调用评分／反思／摘要；每项摘要最多一次格式 repair','loop/fleet.py'],
      ['run_director','按暗骰与线程状态产生导演指令／触发记录','主要是持久 seed + Oracle 判定','loop/director.py'],
      ['run_cascade','基于事件触发地点层级／区域影响传播','有限节点模型判定，可线程池并发；主线程合并事件','loop/cascade.py'],
      ['run_catchup','玩家进入视野或跳时后，补充 stale 人物／地点变化','有条件的模型调用；不是每次重开都刷新所有 NPC','loop/time.py'],
      ['run_lore','推进任务线、期限、暗转明与终局状态','以生命周期与暗骰处理为主','loop/lore.py'],
      ['_run_demote_on_leave','离开区域时整理／降级相关任务状态','必要时任务重排模型','loop/turn.py'],
      ['run_density','按地区密度与复杂度预算补充任务骨架','有条件的批量生成','loop/density.py'],
      ['scene progression','位置或日期变化时推进 scene_no／scene id','宿主事件，无需模型','systems/scene.py'],
    ]
    body=table(['顺序与入口','作用','模型／运行方式','位置'],[[a,b,c,ref(d)] for a,b,c,d in hookrows])
    body+=note('后台当前是“回合内后台”','没有玩家动作时，这些 hook 不会自动持续推进。ThreadPoolExecutor 的并发模型调用与持久工作队列是两回事；后续若做异步化，需要先设计任务身份、revision 过期处理、重试幂等和提交边界。','amber')
    content.append(section('backstage','05','后台世界：有哪些钩子，什么时候跑','它们提供世界后果和持续内容，但尚不构成离线常驻的世界模拟服务器。',body))

    body=table(['协议钩子','输入 → 输出','用途'],[
      ['event_types / commit_sections / requires','无运行输入 → 类型集合／前置系统集合','注册所有权与依赖；同一事件／section 只能有一个 owner。'],
      ['empty_state','无 → 初始 slice 或 FactGraph','创建 world.systems 的初态；空 slice 也可能通过共享图／meta 保存状态。'],
      ['validate','section + declaration + world → ValidationError[]','验证候选声明，尚不写入存档。'],
      ['created_ids','section + declaration → 新实体 ID 集合','允许同一 commit 内创建与引用的正确解析。'],
      ['to_events','section + declaration + turn/day/scene → event[]','将通过验证的声明转换为领域事件；宿主后续暂存／发布。'],
      ['apply','world + event → 更新 projection','所有回放、预发布重放与冷重开共用领域解释。'],
      ['inject','scene + world → Fragment 或 None','按 stable/scene/volatile 分类贡献上下文。'],
      ['recall','query + world → RecallHit[]','在调用者已提供的世界投影上返回候选；没有实现时默认空列表。'],
    ])
    body+='<div class="system-grid">'
    for i,s in enumerate(systems,1):
        name=s['name'];title,state,limit=SYS_INFO[name]
        body+=f'<details class="system-card" id="system-{name}"><summary><span class="index">{i:02}</span><div><b>{title}</b><small>{s["class"]}</small></div><span class="badge {"warn" if name=="interpretive_memory" else "good"}">{"默认关闭" if name=="interpretive_memory" else "默认注册"}</span></summary><div class="detail-body"><p><b>状态：</b>{state}</p><p><b>责任边界：</b>{limit}</p><h4>接收的提交段</h4><div class="chips">{chips(s["sections"])}</div><h4>拥有的事件类型</h4><div class="chips">{chips(s["events"])}</div><h4>本类实现的协议钩子</h4><div class="chips">{chips(s["methods"])}</div><p><b>注册依赖：</b>{esc(", ".join(s["requires"]) or "无前置系统依赖")}</p>{ref("systems/"+name+".py",s["class"])}</div></details>'
    body+='</div>'+note('同一协议的两个方向','写入方向：commit section → validate / to_events → event。读取方向：event → apply → world → inject / recall。某系统没有 commit section，可能表示它只接收宿主事件；不表示缺少实现。event_types 和 sections 均从当前实际注册对象读取。')
    content.append(section('systems','06','15 个领域系统的内部结构','点击卡片查看每个系统的状态归属、事件、作者声明与协议实现。',body))

    body=table(['阶段','当前调用／结构','输出与失败语义'],[
      ['来源收集','session_zero / genesis_blueprint / import_sillytavern → GenesisSpec.normalize / merge','pitch、角色卡、世界书与 blueprint 归一到同一规范；preview 可以不调用模型。'],
      ['原子创世','bootstrap_world 外层原子封装；固定 campaign seed 和各步骤 Oracle','真实生成耗尽失败时不发布 stub 或半个创世；所有 genesis 事件 turn=0。'],
      ['世界结构','gen_frame → gen_regions → gen_local_map','框架、区域与本地地图事件；部分批量生成可选较便宜的 cascade provider。'],
      ['角色与设定','gen_protagonist → factions → NPCs；Codex / threads / opening','主角、势力、人物、任务骨架与开场旁白；经校验后入账。'],
      ['局部重掷','reroll_all / reroll_step + 持久 seed / generation boundaries','重做明确步骤并保持依赖；不是对已有普通存档静默再导入创世源。'],
      ['进入游玩','project → Engine.world → CLI / PlayerDashboard','正式 provider 模式与 demo provenance 分开；普通入口要求显式 provider。'],
    ])
    body+='<div class="refs">'+ref('loop/bootstrap.py','bootstrap_world')+ref('loop/genesis_spec.py','normalize')+ref('app/genesis_preview.py')+'</div>'
    body+=note('并非只有“写一段开场白”','创世是多步骤结构生成与事务发布；后续优化应保留其失败不产生伪成功存档的边界。资源规则仍为显式 opt-in 场景配置。')
    content.append(section('genesis','07','创世、导入与配置如何进入世界','单独于普通行动主链，但最终复用同一套事件与投影底座。',body))

    body='<div class="cards two">'+card('AuthorStrategy · 甲','<p><b>上下文 → JSON 候选（正文 + sections）→ 校验／局部修复。</b>新回合在 provider 支持时可进入工具循环；repair 通常复用消息链。可选多轮对话与压缩，成功 publish 后才 commit_to_thread。</p><p>优点：结构与正文一起产生、工具可查询当前 POV。限制：修复结构仍不自动证明正文对应最终裁定。</p>'+ref('loop/strategy.py','AuthorStrategy'))+card('HybridStrategy · 丙','<p><b>先 prose → 再为 prose 生成结构 → 修复结构。</b>第一份 prose 存在 _frozen_prose，修复时通常继续复用；某些 narration 级修复另处理。</p><p>已复现风险：moves 被修为空，正文仍宣称付钱渡河成功。需要真正的结果说明 → 最终正文阶段。</p>'+ref('loop/strategy.py','HybridStrategy'))+'</div>'
    body+=table(['上下文组成','当前实现','注意点'],[
      ['POV 与能力','pov_world + SceneCapabilities + public_place_observations','共享读边界先确定可见内容；不能把 canonical 图直接交给玩家。'],
      ['stable','总概要、较老分段摘要、稳定规则与长期设定','摘要帮助保持方向，不能证明精确原话。'],
      ['scene','最近两个 raw bucket、尚未摘要的旧原文、当前人物／地点、事实锚点和安全交接','保留未摘要原文避免静默遗忘，但故障长局会不断增长。'],
      ['volatile recall','kernel.recall → memory.rank → top k 文本，默认 k=6','候选来自各系统当前 recall；day 近似、importance 默认 5；不是 Archive 原文逐步检索。'],
      ['工具','map_query / recall_query / characters_query / factions_query / ambient_query / codex_query','默认 6 个；DM 才增加 dm_world_query。轮数设置默认 12，可配置。'],
      ['provider','OpenAI-compatible／DeepSeek／Zhipu／Anthropic／Fake','工具能力以 supports_tools() 为准；Fake 仅证明接线，适配器存在不代表已联网验证。'],
    ])
    body+=note('现有防护不是完备语义裁判','narration_guard 检测明显重复；capabilities 对特定旅行矛盾有检查；resources 约束确定性余额。这些有价值，但尚不能覆盖任意正文与全部最终事件的语义一致性。','amber')
    content.append(section('author','08','模型、上下文与工具回路','同一世界资料经过不同的安全投影和生成策略，不应混成一个“大 prompt”。',body))

    body=table(['证据类型','现在在哪里','能够证明','不能据此声称'],[
      ['玩家原 input','请求指纹只有 hash；GUI envelope 只含特定宿主上下文；可选 CLI transcript 另写','提供了被哈希／另记的请求上下文，部分明确讲话可另证','所有普通 input 都有同事务全文档案；NPC 听到了整段 input。'],
      ['明确讲话 receipt','host_player_statement_receipt → heard_player → source-bound knowledge_set','特定收件人被授权记录主角说过精确 statement','讲话内容是世界真相，其他 NPC 也听见。'],
      ['公共旁白原文','narration_recorded → narrative bucket 的 raw/source_event_ids','当时保存的显示文本及来源事件','所有 prose 都有 canonical 效果，旁白对话自动等于 receipt。'],
      ['世界事实／关系','FactGraph + source_event + 有效时间与摄入 turn','经引擎批准的状态及当前／历史有效值','未记录的心理活动，或人物陈述的客观真实性。'],
      ['分段／总概要','scene_summarized / recap_recompressed；source v1 链','由哪些原文／摘要／前序总概要生成，是否仍属活动来源','摘要语义完整准确，或可以反向重造引号内原话。'],
      ['私有解释','opt-in by_actor + host read/write session','该角色在绑定来源上的主观解释记录','公共上下文、事实权限或其他角色已知。'],
    ])
    body+='<div class="cards two">'+card('当前主链的记忆','<ol><li>narration 先进入 EventBatch 并持久化。</li><li>同场最多 6 回合一桶；最近 2 桶注入原文。</li><li>旧桶未摘要仍注入，成功摘要才替换上下文中的表示。</li><li>连续已摘要前缀超过 6 时，每次压缩最老 6 桶。</li><li>RCV-118 每次摘要／总概要各做最多一项，各最多一次格式修复。</li></ol><p>摘要事件不删除事件库原文；来源链使用事件 ID + 文本／source hash，兼容旧档的 legacy_unbound。</p>'+ref('systems/narrative.py')+ref('loop/fleet.py','digest_fleet'))+card('兼容检索底座的现状','<ol><li>ArchiveStore 保存 chunks，FTS5 trigram 与短词 LIKE 定位。</li><li>VectorStore 保存 float32 向量，用 NumPy 全量 cosine。</li><li>RCV-120 将 day/entity 与 allowed_ids 放到排序前。</li><li>实体过滤不是角色 ACL；scene+turn 键仍会被重写复用。</li><li>无跨 EventStore／Archive／vector 的统一 revision 与引用会话。</li></ol><p>缺少 host 选定授权集合 → 不透明 source handle → open/邻文分页 → 引用核验这一层。</p>'+ref('engine/archive.py')+ref('engine/recall.py')+ref('engine/vectorstore.py'))+'</div>'
    body+=note('下一版读取协议 · 设计中','① 从同一 campaign/actor/revision 活动历史确定授权集合；② locate 返回目录与线索；③ open 读取分类型原文；④ expand 前后文或改写查询；⑤ 回答引用仅指向实际读到的原文。到达预算时说明搜索未完成；没有原话时明确无法核对。','amber')
    body+=note('当前两条 recall 不能混称','app/player_recall 与 llm/tools 的 recall_query 调用 kernel.recall（当前系统投影）；engine.recall 调用 Archive／FTS／向量（兼容路线）。RCV-120 修复了后者的过滤，但并没有自动把它接进普通作者工具。')
    content.append(section('memory','09','本地记忆与原文查证：已经连通哪里','“能保存”“能找到候选”“能授权打开原文”“能可靠引用”是四个独立能力。',body))

    body=table(['存储／状态','权威性与用途','重开／失败语义'],[
      ['events.db · events','活动与已撤回事件；seq、id、type、deltas、turn 等','当前状态按活动历史投影。'],
      ['store_revision','整个 EventStore 的单调版本','最终事务拒绝 stale proposal。'],
      ['action_receipts','action_id → fingerprint / first_seq / last_seq / revision','恢复已提交动作，防止重复执行；被撤回后不能原 ID 再执行。'],
      ['action_result_metadata','带 version 的结果 sidecar、请求指纹、权限模式、host envelope','与事件／receipt 同事务；不是全量原 input 档案。'],
      ['events.jsonl','EventStore 镜像，用于可读导出／恢复相关流程','镜像同步失败不推翻 SQLite 已提交事实；标 mirror_pending。'],
      ['world / 策略线程','可重建 projection / 会话缓存','不能取代 SQLite；跨实例版本变化、undo 或失败需刷新／重置。'],
      ['archive.db / vectors.db','独立派生检索结构／兼容原文档案','来源身份、active branch 与 actor/revision 尚未统一；不能作权限或事实权威。'],
      ['transcript / trace','可选操作记录与 operator 诊断','不是玩家开放历史 API，也不是同事务原文来源协议。'],
    ])
    body+=table(['边界','当前保证','保留的限制'],[
      ['读取','POV 图过滤、玩家 DTO、私有解释隔离','public_event_history 只是排除私有解释，仍可能含 canonical secret；不能直接接成模型 source reader。'],
      ['候选写入','普通 app 入口打开旅行／知识／事实／人物／地点／物品／任务等约束','底层 run_turn 默认 flags 多为 False，可信内部／native 路线策略不同；新入口必须明确接入正确 policy。'],
      ['最终写入','候选通过后仍验证最终 event payload，preflight 后再核验','新增工具或后台类别要继续覆盖最终事件边界，不能只验证 prompt。'],
      ['来源链','新 summary／recap host 绑定，回放再验；旧来源明确 unbound','hash 只证明输入身份与内容未替换，不证明摘要语义。'],
      ['取消与并发','取消不发布；revision 冲突回滚；普通后台异常只回滚自身','尚非任意并发／异步世界任务都已完成幂等设计。'],
    ])
    content.append(section('boundaries','10','持久化、授权与恢复边界','这些是后续优化必须保持的行为，尤其不能为了性能绕过最终校验。',body))

    rows=[]
    for b in perf['benchmarks']:
        rows.append([f'{b["narration_events"]:,}',f'{b["snapshot_project"]["median_ms"]:.3f} ms',f'{b["cold_reopen"]["median_ms"]:.3f} ms',f'{b["prepared_run_turn"]["median_ms"]:.3f} ms',f'{b["unsummarized_context_chars"]:,}',f'{b["archive_fts"]["median_ms"]:.3f} ms'])
    body=table(['旁白数','snapshot + project','冷重开','固定策略 run_turn','未摘要 context 字符','FTS'],rows)
    maxchars=max(b['unsummarized_context_chars'] for b in perf['benchmarks'])
    body+='<div class="chart" aria-label="未摘要上下文字符数量"><h3>上下文增长比查询时间更迫切</h3>'+''.join(f'<div class="bar-row"><span>{b["narration_events"]:,} 条</span><div><i style="width:{100*b["unsummarized_context_chars"]/maxchars:.2f}%"></i></div><b>{b["unsummarized_context_chars"]:,} 字符</b></div>' for b in perf['benchmarks'])+'</div>'
    body+=note('测量范围与日期','以上复用 2026-10-25 RCV-119 在 e69029a 基线上的真实本地测量：每规模 3 次取中位数，人工合成约 300 字旁白，故意不预先摘要；固定策略、固定本地摘要，64 维人工向量。不是模型调用延迟、token 数、混合大图谱或生产 SLO。当前报告未重跑性能测试。')
    body+='<div class="cards three">'+card('重复 project','<p>实测一次回合有 12 次 project；热点还包括行反序列化、历史 deepcopy 和 publish preflight。</p><p><b>方向：</b>先测分阶段开销，再做 revision-scoped 复用／增量投影；最终事务的必要检查不能省。</p>')+card('原文注入失控','<p>未摘要原文保留解决静默遗忘，但长期故障会把整段历史推给作者。</p><p><b>方向：</b>来源可查证之后，再以目录＋原文工具按需读取，预算应可解释、不能直接删掉经历。</p>')+card('范围与质量分离','<p>FTS 可定位，向量可排名；两者都不决定角色可读范围，也不能证明原话不存在。</p><p><b>方向：</b>先建立授权集合与引用协议，再比较关键词、时间浏览和语义召回的增益。</p>')+'</div>'
    content.append(section('performance','11','性能与长期运行风险','保留原始测量的版本和条件，不把回归通过数量当成长期可玩性分数。',body))

    priorities=[
      ['P0 · 先做','原始输入同事务档案','新增 host-owned 原 input 来源，绑定 actor／action／turn；只与成功事件一起 publish。旧存档缺失不补造。','失败／取消零记录；duplicate 不重复；撤回冷重开一致；input 不变成 heard_player，不进公共 recap。','提供“哪天输入过什么”的第一手材料，支撑下一项。'],
      ['P0 · 接着做','安全 source read session','以同一 snapshot 构建允许读取的 narration／本人 input／receipt 集合；引用绑定 campaign、actor、revision 和来源内容。','跨角色／跨存档／过期 revision／撤回重写／private ref 拒绝；所有读操作零世界写入。','避免把旧 Archive 键和全库向量直接暴露给模型。'],
      ['P0 · 同级目标','结果先行最终正文','结构化效果经过规则裁定后，生成受限 outcome 描述，再写最终 prose。复用已有资源 resolution，改造 Hybrid frozen-prose。','余额不足、非法移动、物品未交接、交流未听到时正文不得称成功；修正文不二次扣款；取消和响应恢复一致。','消除“账修对了、故事仍错”的核心体验问题。'],
      ['P1 · 依赖来源协议','B1 逐步原文查证','接入现有 ToolRegistry：locate → open → 前后文／重查 → 带来源答案；向量仅作为可选候选。','摘要漏词、相同句子、同名人物、跨天重访、缺失原 input；引用必须对得上实际打开的文字。','让长程记忆真正可核查，取代无止境加摘要。'],
      ['P1 · 依赖测量与正确性','上下文预算／投影成本','按阶段剖析、有限积压任务、revision-scoped 缓存；工具打开内容在有界预算内分页。','长期摘要故障仍可找回原文；撤回／peer update 不返回旧投影；性能比较使用同一负载。','控制 161 万字符式增长及重复 O(history) 工作。'],
      ['P2 · 后续闭环','一位 NPC 的持续行动','经历 → 来源知识 → 私人解释 → 行为提议 → 宿主裁定；再考虑持久异步调度。','误解／澄清／失约／传播延迟；NPC 不共享玩家全知；重试不重复后果。','先把一个角色做实，再扩大世界自主性。'],
    ]
    body=table(['顺序','切片','实现结构','验收条件','为何优先'],priorities)
    body+=note('建议本轮报告后的第一项','先实现成功回合的原 input 同事务保存，并为后续 source session 留出明确的类型与身份字段。它比先接向量召回更基础；“结果先行最终正文”应作为同级产品主线，不能继续被小界面改动挤掉。')
    content.append(section('roadmap','12','下一阶段优化顺序与验收标准','这是尚未实现的工作建议；优先级是根据当前源码和反例做出的工程判断。',body))

    body=table(['证据层级','当前记录','可以支持的结论','不覆盖'],[
      ['当前代码','RCV-120 / 03cabc4；本报告按该 HEAD 审读','模块归属、函数结构、主要调用顺序与现有开关','未运行路径的实际质量与所有并发情形。'],
      ['完整确定性回归','2026-10-25：2171 passed, 1 deselected；127.06s','该版本在当前离线门禁下的协议与回归表现','真实 embedding、远程 provider、长期剧情质量。'],
      ['新 scope 专项','RCV-120：71 passed；修前 23 failed / 5 passed','过滤发生在排名前，空集合／重开／陈旧向量／字面查询等','actor ACL、统一 revision、不可变引用身份。'],
      ['Luna medium 原生样本','RCV-118：2 份真实生成，逐请求精确回放，零格式修复','这两份响应可经生产摘要校验、来源绑定、保存和冷重开','远程 HTTP transport、工具调用策略或长局智能。'],
      ['性能与反例','RCV-119：100 / 1000 / 5000 条临时合成历史','重放成本、上下文增长、旧 ID 与 Hybrid 风险','高负载在线服务和真实模型吞吐承诺。'],
      ['本报告验证','当前阶段只读源码、实际 Registry 契约与 AST 索引，生成自包含 HTML','报告链接／结构／交互可独立检查；不修改引擎行为','不把历史 gate 写成本轮重新执行。'],
    ])
    body+='<div class="refs">'+''.join(f'<a href="../../verification/{p}">{title}</a>' for p,title in [('RCV-117-recap-source-chain.md','RCV-117 来源链'),('RCV-118-independent-recap-jobs.md','RCV-118 摘要维护与 Luna'),('RCV-119-current-environment-assessment.md','RCV-119 重新评估'),('RCV-120-retrieval-scope.md','RCV-120 检索范围')])+'</div>'
    content.append(section('evidence','13','验收证据、版本与限制','所有实测成绩保留其产生日期和版本；此报告没有运行真实模型或上传存档。',body))

    groupcards='<div class="cards three">'
    for group,(title,desc) in GROUPS.items():
        groupfiles=[f for f in files if f['group']==group]
        groupcards+=card(f'{group}/ · {title}',f'<p>{desc}</p><p class="muted">{len(groupfiles)} 个模块 · {sum(f["lines"] for f in groupfiles):,} 行（含注释与空行）</p>')
    body=groupcards+'</div>'
    body+=table(['目录／入口','结构与职责','与主回合的关系'],[
      ['data/oracles/','classic／isekai 等题材 pack，包含 pack.json 与暗骰表、人物／地形／线程类型资料。','engine.oracle / loop.flavor / genesis 读取；是内容与配置，不是模型生成后的世界权威。'],
      ['bin/rpg + hooks/pre_llm_call','旧命令包装与会话 hook；旧工作记忆注入路线。','与 python -m app / app.gui 分开；不能将旧 hook 的存在当作现代原文查证工具已接通。'],
      ['scripts/','native context 导出／候选导入、创世预览、离线演示、验证与 profiling、此报告生成器。','维护和受控验收入口；native import 由独立 policy 管理。'],
      ['tests/','kernel／systems／loop／context／app 与跨模块回归，包含原子性、权限、恢复和回放。','以确切命令／版本记录通过结果；slow real embedding 单列，不以数量推断真实模型质量。'],
      ['reference/','人物、任务、叙事风格、反派等参考材料。','参考文本不自动变成 canonical 事件或授权；运行是否读取仍以具体调用为准。'],
      ['docs/ + review_material/','当前进度／交接／设计／verification，另含历史方案与截图。','历史文档不能覆盖当前源码或用户新授权；本报告优先使用当前契约与近期实测。'],
      ['requirements / pyproject.toml','运行／开发依赖；pytest 默认排除 slow。','本次生成报告不新增引擎运行依赖，HTML 无 CDN、字体下载或远程脚本。'],
    ])
    body+='<div class="catalog-controls"><label for="module-search">模块搜索</label><input id="module-search" type="search" placeholder="输入路径、函数、职责，例如 receipt / cascade / recall" autocomplete="off"><label for="module-layer">路径类型</label><select id="module-layer"><option value="all">全部模块</option><option value="main">主调用链</option><option value="support">领域与共享组件</option><option value="legacy">旧兼容路径</option></select><button id="expand-visible" type="button">展开当前结果</button><button id="collapse-visible" type="button">折叠全部</button><output id="module-count" aria-live="polite"></output></div>'
    body+='<p class="muted">导入依赖由 AST 提取，仅列已定位的内部模块；动态／条件调用以正文审读为准。共 '+str(len(files))+' 个 .py（排除 __init__.py 包标记），不含 tests、scripts、内容包和历史报告脚本。行数不是复杂度评分。</p><div id="module-catalog">'
    for f in files:
        search=' '.join([f['path'],f['desc']]+[s['name'] for s in f['symbols']])
        symbols='<div class="symbol-list">'+''.join(f'<span><code>{esc(s["name"])}</code><small>{s["kind"]} · L{s["line"]}</small></span>' for s in f['symbols'])+'</div>'
        imports=' '.join(f'<a class="source" href="#{pathid(p)}">{esc(p)}</a>' for p in f['imports']) or '<span class="muted">无直接定位到的内部文件导入</span>'
        tag={'main':'主调用链','support':'领域／共享','legacy':'旧兼容'}[f['tag']]
        body+=f'<details class="module" id="{pathid(f["path"])}" data-kind="{f["tag"]}" data-search="{esc(search.lower())}"><summary><div><b>{esc(f["path"])}</b><span>{esc(f["desc"])}</span></div><small>{tag} · {f["lines"]:,} 行</small></summary><div class="detail-body"><h4>内部结构：类、函数与方法</h4>{symbols}<h4>静态内部依赖</h4><div class="refs">{imports}</div></div></details>'
    body+='</div>'
    content.append(section('catalog','14','完整模块索引与代码导航','搜索职责或符号；展开后查看真实类／函数／方法及源码行号。',body))

    toc=[('overview','判断与优先级'),('architecture','整体架构'),('objects','核心数据结构'),('flow','回合调用流程'),('backstage','后台世界钩子'),('systems','领域系统详解'),('genesis','创世与导入'),('author','作者与上下文'),('memory','记忆与原文查证'),('boundaries','存储与权限'),('performance','性能与规模'),('roadmap','优化路线'),('evidence','验证证据'),('catalog','完整模块索引')]
    payload={'source_head':sha,'branch':branch,'date':args.date,'modules':files,'systems':systems,'note':'Static inventory + reviewed semantics. Prior benchmark, no fresh full test run.'}
    data=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')
    css=CSS
    html_out='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>llm-rpg-engine · 架构现状与优化路线</title><style>'''+css+'''</style></head><body><a class="skip" href="#overview">跳到正文</a><aside class="sidebar"><a class="brand" href="#top"><span class="brand-mark">RPG</span><b>ENGINE ATLAS</b></a><p>架构现状 / 模块 / 调用链</p><nav aria-label="报告目录">'''+''.join(f'<a href="#{i}"><span>{n:02}</span>{name}</a>' for n,(i,name) in enumerate(toc,1))+'''</nav><div class="sidebar-foot">SELF-CONTAINED REPORT<br>本地阅读 · 无外部资源<br>源码与设计分别标注</div></aside><main id="top"><header class="hero"><div class="eyebrow">SYSTEM REVIEW · '''+esc(args.date)+''' · 上海时间</div><h1>从一次行动，<br>到一个可回放的世界。</h1><p class="hero-sub">llm-rpg-engine 当前架构、完整模块结构与下一阶段优化分析</p><div class="hero-meta"><span>源码 '''+sha[:7]+'''</span><span>'''+esc(branch)+'''</span><span>RCV-120 基线</span></div><div class="stats"><div><b>14<span> + 1</span></b><small>默认系统 + 私有解释 opt-in</small></div><div><b>'''+str(len(files))+'''</b><small>源码实现模块</small></div><div><b>'''+str(count_events)+'''</b><small>默认注册事件类型</small></div><div><b>2,171</b><small>上次完整 gate 通过 · 非本轮重跑</small></div></div></header><div class="report-body">'''+''.join(content)+'''<footer><b>阅读终点，也是实施起点。</b><p>先补可查证的第一手来源，再让检索与最终正文围绕经裁定的事实运行。所有代码仍保留在当前接续分支，报告不发布任何运行服务。</p><p class="muted">源码 HEAD：'''+sha+''' · 报告生成器：scripts/build_architecture_report.py</p><a href="#top">返回顶部 ↑</a></footer></div></main><script id="report-data" type="application/json">'''+data+'''</script><script>'''+JS+'''</script><noscript><style>.flow-panel[hidden]{display:block!important}</style><p class="no-js">JavaScript 未启用：目录与折叠模块仍可使用；全部调用路径已展开显示。</p></noscript></body></html>'''
    dest=ROOT/args.output;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(html_out)
    print(json.dumps({'output':str(dest),'source_head':sha,'modules':len(files),'systems':len(systems),'default_events':count_events,'bytes':dest.stat().st_size},ensure_ascii=False))

CSS=r'''
:root{--ink:#172c39;--muted:#586d77;--line:#d8e2e5;--paper:#f3f6f6;--nav:#112a36;--teal:#087e82;--amber:#996018;--white:#fff}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:25px}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.8 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}a{color:var(--teal);text-underline-offset:3px}button,input,select{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #e3a24d;outline-offset:4px}.skip{position:absolute;left:-9999px}.skip:focus{position:fixed;left:260px;top:12px;background:white;z-index:9;padding:10px}.sidebar{position:fixed;inset:0 auto 0 0;width:240px;background:var(--nav);color:#a9c3cc;padding:30px 22px;overflow:auto;z-index:5}.brand{color:white;text-decoration:none;display:flex;align-items:center;gap:10px;font-size:13px;letter-spacing:1px}.brand-mark{padding:6px 7px;border:1px solid #4d737b;border-radius:7px;font-size:12px;color:#7ed8d0}.sidebar>p{font-size:11px;letter-spacing:1px;margin:20px 0}.sidebar nav{display:flex;flex-direction:column;gap:3px}.sidebar nav a{color:#bed0d6;text-decoration:none;font-size:13px;padding:8px;border-radius:6px;display:flex;gap:12px}.sidebar nav a:hover,.sidebar nav a.active{background:#24414d;color:white}.sidebar nav span{font:10px/2.4 monospace;color:#6da8ae}.sidebar-foot{font-size:10px;line-height:1.9;letter-spacing:1px;margin-top:25px;border-top:1px solid #35505c;padding-top:20px}main{margin-left:240px}.hero{padding:58px 6% 30px;background:#e6efed;border-bottom:1px solid #cddbd9;position:relative;overflow:hidden}.hero:before{content:"";position:absolute;width:350px;height:350px;border:1px solid #d4e3df;border-radius:50%;right:-150px;top:-70px;pointer-events:none}.eyebrow{font:11px/1.5 monospace;letter-spacing:2px;color:#326f72}.hero h1{font-size:clamp(33px,4.5vw,61px);line-height:1.22;letter-spacing:-2px;font-weight:700;margin:25px 0 20px}.hero-sub{font-size:17px;color:#46636c;max-width:700px}.hero-meta{display:flex;gap:9px;flex-wrap:wrap;margin:25px 0 32px}.hero-meta span{border:1px solid #c1d3d1;border-radius:4px;padding:3px 8px;font:11px/1.8 monospace;overflow-wrap:anywhere}.stats{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid #bbcecb;padding-top:22px;gap:16px}.stats div{display:flex;flex-direction:column}.stats b{font-size:32px;line-height:1.4;letter-spacing:-1px}.stats b span{font-size:20px;color:#689092}.stats small{color:#577078;font-size:11px}.report-body{max-width:1440px;margin:auto;padding:0 5.5% 35px}.section{padding:46px 0 12px;scroll-margin-top:20px}.section-head{display:flex;gap:18px;align-items:flex-start;margin-bottom:24px}.num{color:var(--teal);font:12px/1.6 monospace;padding:8px 0;border-top:2px solid var(--teal);min-width:24px}.section-head h2{font-size:25px;letter-spacing:-.6px;line-height:1.5;margin:0}.section-head p{color:var(--muted);font-size:13px;margin:7px 0 0}h3{font-size:17px;line-height:1.55;margin:0 0 12px}h4{font-size:12px;color:var(--muted);margin:18px 0 7px;letter-spacing:.5px}p{margin:0 0 13px}p:last-child{margin-bottom:0}ol{padding-left:21px}.muted{color:var(--muted);font-size:12px}.note{padding:19px 22px;border-left:3px solid var(--teal);background:#e7f1f1;margin:18px 0;border-radius:0 9px 9px 0}.note p{font-size:13px;margin:6px 0 0}.note.amber{background:#fcf2e3;border-left-color:#c38b42}.cards{display:grid;gap:14px;margin:18px 0}.cards.three{grid-template-columns:repeat(3,minmax(0,1fr))}.cards.two{grid-template-columns:repeat(2,minmax(0,1fr))}.card{background:var(--white);border:1px solid var(--line);border-radius:12px;padding:22px;min-width:0}.card p,.card ol{font-size:13px}.badge{display:inline-block;border:1px solid;padding:2px 7px;font-size:10px;border-radius:4px;white-space:nowrap;line-height:1.6}.badge.good{color:#157b66;background:#e9f4ee;border-color:#c2e1d3}.badge.warn{color:#8f5911;background:#fff2df;border-color:#ead4b0}.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:white;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:12px;line-height:1.8}th{font-weight:650;text-align:left;background:#eaf0f0;color:#34525e;padding:12px 14px;border-bottom:1px solid var(--line);white-space:nowrap}td{padding:13px 14px;border-bottom:1px solid #e8edef;vertical-align:top;min-width:95px;overflow-wrap:anywhere}td:first-child{font-weight:600}tr:last-child td{border-bottom:0}.source{font:10px/1.9 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;display:inline-block;margin:2px 8px 2px 0;text-decoration:none;border-bottom:1px dotted #9ab9ba}code{font:11px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;color:#285968;overflow-wrap:anywhere}.chips{display:flex;gap:5px;flex-wrap:wrap}.chips code{padding:3px 7px;background:#edf2f3;border-radius:4px}.architecture{display:block;width:100%;height:auto;background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px}.svgnode rect{fill:#f7faf9;stroke:#b8cfce;stroke-width:1}.svgnode text{font-size:17px;fill:#193d4d;font-weight:600}.svgnode text.sub{font-size:13px;fill:#5e7680;font-weight:400}.svgnode.solid rect{fill:#def0e9;stroke:#70a89a}.svgnode.planned rect{fill:#fff6e9;stroke:#cca05f;stroke-dasharray:6 4}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:22px}.tabs button{border:1px solid #c5d5d7;border-radius:6px;background:#fff;padding:9px 16px;font-size:12px;color:#43616b}.tabs button[aria-selected=true]{background:var(--nav);color:#fff;border-color:var(--nav)}[hidden]{display:none!important}.flow-list{padding:0;list-style:none;max-width:1000px}.flow-list li{display:grid;grid-template-columns:42px minmax(0,1fr);gap:20px;position:relative;padding-bottom:24px}.flow-list li:before{content:"";position:absolute;width:1px;left:20px;top:35px;bottom:0;background:#b4ced0}.flow-list li:last-child:before{display:none}.flow-list li>span{background:#dcebec;border:1px solid #b5cecf;color:#267a7c;border-radius:50%;height:40px;width:40px;font:12px/40px monospace;text-align:center}.flow-list li>div{border:1px solid var(--line);background:white;border-radius:10px;padding:18px 22px}.flow-list h3{margin-bottom:5px}.flow-list p{font-size:13px}.system-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}details{background:white;border:1px solid var(--line);border-radius:9px;min-width:0}summary{cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}.system-card>summary{display:flex;gap:12px;padding:18px;align-items:center}.system-card>summary:after{content:'+';color:#4d8a8d;margin-left:auto;font-size:20px}.system-card[open]>summary:after{content:'−'}.system-card summary .index{font:11px monospace;color:#6a9698}.system-card summary div{flex:1;min-width:0}.system-card summary b{display:block;font-size:14px}.system-card summary small{font:10px/1.8 monospace;color:var(--muted);overflow-wrap:anywhere}.detail-body{border-top:1px solid #e8edef;padding:18px;font-size:12px}.detail-body p{font-size:12px}.refs{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}.refs>a:not(.source){border:1px solid var(--line);border-radius:5px;padding:5px 9px;font-size:12px;background:#fff;text-decoration:none}.chart{background:#fff;padding:23px;border:1px solid var(--line);border-radius:10px}.bar-row{display:grid;grid-template-columns:75px minmax(0,1fr) 145px;gap:15px;align-items:center;font-size:12px;margin:18px 0}.bar-row>div{background:#e8eff0;height:16px;border-radius:4px;overflow:hidden}.bar-row i{display:block;min-width:3px;height:100%;background:#19898b}.bar-row b{font:11px monospace;text-align:right}.catalog-controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:18px;background:#e3eeee;border-radius:10px;margin:22px 0 12px}.catalog-controls label{font-size:12px}.catalog-controls input{flex:1;min-width:190px;max-width:100%;border:1px solid #b7cdcf;padding:8px 10px;border-radius:5px;font-size:12px}.catalog-controls select,.catalog-controls button{border:1px solid #b7cdcf;background:white;padding:8px 10px;border-radius:5px;font-size:12px}.catalog-controls output{font:11px monospace;color:#527379}.module{margin:9px 0;scroll-margin-top:20px}.module summary{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:15px 18px}.module summary b{font:12px/1.8 monospace;color:#245e6a;display:block}.module summary span{display:block;font-size:12px;margin-top:5px;color:#57707a;max-width:850px}.module summary>small{font-size:10px;color:#7c9199;white-space:nowrap}.module:target{border-color:var(--teal);box-shadow:0 0 0 3px #087e8214}.symbol-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 18px}.symbol-list>span{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;padding:6px 0;border-bottom:1px solid #eff2f2}.symbol-list small{font:9px/2 monospace;color:#7d9098;white-space:nowrap}footer{margin-top:50px;padding:28px 0;border-top:1px solid var(--line)}footer>b{font-size:22px}footer p{margin:14px 0;font-size:13px;max-width:850px}.no-js{position:fixed;bottom:0;background:#fff2df;color:#664815;padding:5px;font-size:12px}body{overflow-wrap:break-word}@media(min-width:1650px){.hero{padding-left:max(6%,calc((100% - 1280px)/2));padding-right:max(6%,calc((100% - 1280px)/2))}}@media(max-width:1100px){.sidebar{width:205px;padding:25px 14px}main{margin-left:205px}.cards.three{grid-template-columns:1fr}.report-body{padding:0 5% 30px}.system-grid{grid-template-columns:1fr}.hero{padding:42px 5% 25px}.stats b{font-size:26px}.symbol-list{grid-template-columns:1fr}}@media(max-width:720px){.sidebar{position:relative;width:100%;padding:18px 20px}.sidebar>p,.sidebar-foot{display:none}.sidebar nav{display:flex;flex-direction:row;overflow:auto;margin-top:15px;gap:4px}.sidebar nav a{white-space:nowrap;padding:6px}.sidebar nav span{display:none}main{margin-left:0}.hero{padding:35px 22px 25px}.hero h1{font-size:38px}.hero-sub{font-size:14px}.stats{grid-template-columns:repeat(2,1fr);gap:21px}.report-body{padding:0 20px 25px}.section{padding-top:35px}.section-head{gap:12px}.section-head h2{font-size:21px}.cards.two{grid-template-columns:1fr}.card{padding:18px}.flow-list li{gap:12px;grid-template-columns:32px minmax(0,1fr)}.flow-list li>span{width:30px;height:30px;line-height:30px}.flow-list li:before{left:15px;top:30px}.flow-list li>div{padding:14px}.system-card>summary{padding:14px;gap:8px}.system-card summary .badge{font-size:9px}.bar-row{grid-template-columns:55px minmax(0,1fr);gap:9px}.bar-row b{grid-column:2;text-align:left}.module summary{display:block}.module summary>small{display:block;margin-top:7px}.architecture{min-width:600px}.section:has(>.architecture){overflow-x:auto}.table-wrap table{min-width:640px}.catalog-controls input{width:100%;flex:auto}.symbol-list>span{display:block}.symbol-list small{display:block}.hero-meta span{font-size:10px}}@media print{.sidebar,.catalog-controls,.tabs,.skip{display:none}main{margin:0}.hero{padding:20px;background:white}.hero h1{font-size:35px}.report-body{padding:0}.section{break-before:auto;padding-top:28px}.card,.note,details,.flow-list li{break-inside:avoid}.flow-panel[hidden]{display:block!important}.cards{display:block}.card{margin:10px 0}.system-grid{display:block}.system-card{margin:10px 0}table{font-size:10px}a{color:#234f59;text-decoration:none}.module .detail-body{font-size:10px}.table-wrap{overflow:visible}.architecture{max-height:640px}.section-head{break-after:avoid}footer{break-before:avoid}}
'''
JS=r'''
(function(){
'use strict';
const tabs=Array.from(document.querySelectorAll('[data-flow]'));
function activate(tab){tabs.forEach(b=>b.setAttribute('aria-selected',String(b===tab)));document.querySelectorAll('.flow-panel').forEach(p=>p.hidden=p.id!=='flow-'+tab.dataset.flow);}
tabs.forEach((b,i)=>{b.addEventListener('click',()=>activate(b));b.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();let n=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;activate(tabs[n]);tabs[n].focus();}})});
const q=document.getElementById('module-search'),layer=document.getElementById('module-layer'),modules=Array.from(document.querySelectorAll('.module')),counter=document.getElementById('module-count');
function filter(){let n=0;const needle=q.value.trim().toLowerCase();modules.forEach(m=>{m.hidden=!(m.dataset.search.includes(needle)&&(layer.value==='all'||layer.value===m.dataset.kind));if(!m.hidden)n++;});counter.textContent=n+' / '+modules.length+' 个模块';}
q.addEventListener('input',filter);layer.addEventListener('change',filter);filter();
document.getElementById('expand-visible').addEventListener('click',()=>modules.forEach(m=>{if(!m.hidden)m.open=true}));
document.getElementById('collapse-visible').addEventListener('click',()=>modules.forEach(m=>m.open=false));
function reveal(){const id=decodeURIComponent(location.hash.slice(1));const el=document.getElementById(id);if(el&&el.classList.contains('module')){q.value='';layer.value='all';filter();el.open=true;el.scrollIntoView({block:'start'});}}
window.addEventListener('hashchange',reveal);reveal();
if('IntersectionObserver' in window){const links=Array.from(document.querySelectorAll('.sidebar nav a'));const ob=new IntersectionObserver(entries=>{entries.filter(e=>e.isIntersecting).forEach(e=>{links.forEach(a=>a.classList.toggle('active',a.hash==='#'+e.target.id));})},{rootMargin:'-5% 0px -80% 0px'});document.querySelectorAll('.section').forEach(s=>ob.observe(s));}
})();
'''
if __name__=='__main__': build()
