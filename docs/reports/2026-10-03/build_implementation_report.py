from pathlib import Path
import html,json,re,statistics,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parent
def read(n):return json.loads((ROOT/'evidence'/n).read_text())
def esc(x):return html.escape(str(x))
live=read('implemented-live-play.json'); review=read('implemented-live-review.json')
bench=read('index-benchmark-implemented.json'); endurance=read('implemented-endurance.json')
tests=ET.parse(ROOT/'evidence/implementation-tests.xml').getroot()[0].attrib
turns=[t for c in live['campaigns'] for t in c['turns']]
def diagram():
 out=['<svg viewBox="0 0 1040 560" role="img" aria-label="原架构与改进架构"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="none" stroke="#71887b"/></marker></defs>']
 def text(x,y,s,size=14,color='#536e61'):
  out.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-family="sans-serif">{s}</text>')
 def box(x,y,title,lines,old=False,w=230):
  out.append(f'<rect x="{x}" y="{y}" width="{w}" height="105" rx="12" fill="{"#fff1e7" if old else "#eaf2e8"}" stroke="{"#dcb897" if old else "#acc6b0"}"/>')
  text(x+17,y+33,title,19,'#183e2f')
  for i,l in enumerate(lines):text(x+17,y+63+i*23,l)
 def edge(x1,y1,x2,y2):out.append(f'<path d="M{x1} {y1}L{x2} {y2}" stroke="#71887b" fill="none" stroke-width="2" marker-end="url(#arrow)"/>')
 text(15,25,'原架构 · 每个环节各自推进',21,'#183e2f')
 for i,(a,b) in enumerate([('LLM 叙事与状态',['返修耗尽，丢弃坏段']),('逐条提交事件',['SQLite / JSONL 各自写']),('后台钩子再追加',['各自推进 turn']),('世界、会话、显示',['提交和撤销边界分散'])]):
  box(15+i*255,48,a,b,True)
  if i<3:edge(246+i*255,100,266+i*255,100)
 text(15,207,'已实施 · 先准备完整行动，再发布可信状态',21,'#183e2f')
 for i,(a,b) in enumerate([('玩家行动',['资源意图 → 规则算术','明确等待 → 绝对时刻']),('上下文 + 叙事',['POV / 事实锚点 / 记忆','风格包 / 随机细节提示']),('校验 / 修复',['必填段 / 资源 / 时钟','正文格式 / 重复检测']),('EventBatch 暂存',['主动作 + 叙事 + 后台','同一 turn / 保存点'])]):
  box(15+i*255,230,a,b)
  if i<3:edge(246+i*255,282,266+i*255,282)
 edge(895,337,895,390)
 box(525,402,'一次事务发布',['revision 检查 + 全历史预演','events + revision + receipt；JSONL 可重建'],w=485)
 edge(523,453,492,453)
 box(15,402,'成功后才显示、写入会话',['撤销完整动作与即时后台后果','清理会话缓存；拒绝旧版本提案'],w=475)
 text(15,546,'所有模型调用在数据库写事务外；同步领域系统保留，异步世界服务器仍是后续工作。',13)
 out.append('</svg>');return ''.join(out)
def table(headers,rows):
 return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
changes=[
('原子行动','逐条写入，错误可能留下半个动作。','EventBatch、预演、版本检查与批量事务；每个后台钩子有独立暂存回滚点。','engine/store.py'),
('完整撤销','后台也推进 turn，对话先于持久化更新。','新动作共享根 turn；提交成功后更新会话；撤销及版本变化使缓存失效。','loop/turn.py'),
('玩家信息边界','隐藏地点参与搜索；在场 NPC 的私有目标从 recall 泄漏。','先过滤实体及字段再匹配；私有事实按玩家信念读取；NPC POV 需 DM 权限。','context/access.py'),
('持续记忆','同场景原文无限累积；旧摘要被反复压缩。','6 回合分块、保留最近 2 块；推进摘要游标，合并已有总览和新增摘要。','systems/narrative.py'),
('资源裁定','模型漏写账本或重复扣钱，已在真实测试复现。','场景包声明资源；模型解析明确意图，Python 判断支出与不足；叙事不能改写结果。','loop/resources.py'),
('时间与正文','明确等待未落实；原始 JSON 进入正文；复制旧情节。','明确等待校验绝对终点；空白、结构化正文和近期长文重复被修复或拒绝。','loop/narration_guard.py'),
('随机性','已有暗骰多用于剧情转折，日常细节易趋同。','增加日常、感官、人物习惯等风格表；可复现抽样，尽量避开最近两项。','loop/variation.py'),
('事实索引','事实更新与读取反复扫描长列表。','按事实键、主体、历史分组索引，保留原日志和历史查询语义。','facts/graph.py'),
('创世失败','API 全失败也可能保存占位世界。','创世和重掷暂存；真实失败不发布、不删除原世界；测试 stub 显式声明。','loop/bootstrap.py'),
('模型适配','通用兼容接口缺乏结构化及工具协议细节。','DeepSeek 原生 profile、按调用启用 JSON、完整工具消息组，保留原 Zhipu 入口。','llm/provider.py')]
change_html=''.join(f'<article class="change"><span>{i+1:02}</span><div><h3>{a}</h3><div class="twocol"><p><small>之前</small>{b}</p><p><small>现在</small>{c}</p></div><a class="file" href="../rpg-engine-app/{d}">{d}</a></div></article>' for i,(a,b,c,d) in enumerate(changes))
decisions=[
('adopt','落实','原子提交、可见性、事实索引','独立复现后采用，并扩展到持久回执、版本检查、字段信念及完整动作撤销。'),
('change','调整','散文先行，再事后抽取记账','漏扣与重复扣款改变了方案：明确资源先裁定，未通过的行动不发布正文。'),
('reject','否决','投影报错就跳过事件','错误可能已经产生半个效果。选择在事务前预演，保留最后健康状态。'),
('hold','后续研究','把 13 个系统直接砍到 6 个','保留领域边界，新增资源系统后共 14 个。目录或模块数量不是体验指标。'),
('hold','后续研究','立即把所有后台都改成异步','先统一提交和版本，再研究任务生命周期、取消、倒带及跨回合因果关系。'),
('change','修正','完全删除多轮历史','保留可重建的短会话，每轮刷新当前世界；真实重复问题新增发布关口，长期效果仍需比较。'),
('reject','否决该收益口径','“同模型 126 秒变 3 秒”','原图混入估算。不同模型、首句与完整回合的耗时不能当作同条件提速证明。')]
decision_html=''.join(f'<article class="decision" data-kind="{k}"><label class="{k}">{s}</label><h3>{t}</h3><p>{b}</p></article>' for k,s,t,b in decisions)
labels={"pay":"购买干粮","insufficient":"余额不足","quiet":"日常观察","recall":"回忆承诺","explore":"离开探索","return":"回到原处","wait":"等待到次日中午","promise":"归还蓝伞"}
stories=''
for c in live['campaigns']:
 stories+=f'<h3 class="world-title">{esc(c["genesis"]["summary"]["world_name"])} <small>{c["flavor"]}</small></h3>'
 for t in c['turns']:
  stories+=f'<details class="story"><summary><label class="adopt">{"已提交" if t["accepted"] else "被拒绝"}</label><b>{esc(labels.get(t["label"],t["label"]))}</b><small>{t["seconds"]:.1f} 秒 · {t["calls"]} 次 API · {t.get("repairs",0)} 次返修</small></summary><p class="player">{esc(t["action"])}</p><div class="prose">{esc(t.get("narration",t.get("error","")))}</div><p class="caption">金币 {t.get("coins","?")} · 第 {t.get("day","?")} 天 / 时段 {t.get("band","?")} · 回放 {"一致" if t.get("replay_equal") else "未通过"}</p></details>'
bars=''
mx=max(r['baseline_ms'] for r in bench['rows'])
for i,r in enumerate(bench['rows']):
 y=48+i*58
 bars+=f'<text x="12" y="{y+15}" font-size="13" fill="#5b6f60">{r["events"]:,}</text><rect x="76" y="{y}" width="{r["baseline_ms"]/mx*385:.1f}" height="17" rx="3" fill="#c9956a"/><rect x="76" y="{y+22}" width="{max(2,r["implemented_ms"]/mx*385):.1f}" height="17" rx="3" fill="#3e7a59"/>'
chart='<svg viewBox="0 0 500 294" role="img" aria-label="事实投影基准"><text x="76" y="23" font-size="13" fill="#9b714e">之前</text><text x="150" y="23" font-size="13" fill="#3e7a59">已实施</text>'+bars+'</svg>'
sources=[
('evidence/provenance.json','目录与 Claude 会话出处'),('recovered/rpg-engine-audit.html','Claude 原始审核'),('recovered/rpg-engine-architecture.html','Claude 原架构建议'),
('evidence/probes.json','原架构探针'),('evidence/implementation-tests.xml','当前全量回归'),('evidence/implemented-live-review.json','真实游玩人工复核'),
('evidence/implemented-live-play.json','最终轮全部模型输出'),('evidence/implemented-live-play-v1-failures.json','V1 漏账与格式失败'),('evidence/implemented-live-play-v2-failures.json','V2 重复扣款'),
('evidence/implemented-live-play-v3-mixed.json','V3 状态通过、正文有问题'),('evidence/implemented-endurance.json','120 动作持续测试'),('evidence/legacy-replay-implemented.json','7 份旧存档兼容'),
('evidence/index-benchmark-implemented.json','当前代码性能'),('evidence/live-summary-baseline.json','初始 Author / Hybrid 对照'),('implementation-progress.json','持续任务记录'),
('changes.patch','实现变更补丁'),('../rpg-engine-app/docs/action-integrity.md','运行与扩展文档')]
source_html=''.join(f'<a href="{a}">{b}</a>' for a,b in sources)
css='''
:root{--ink:#193e2f;--muted:#6e7c70;--line:#dbe2d7;--paper:#f5f6f0}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:70px}body{margin:0;background:var(--paper);color:#344b3c;font:16px/1.8 system-ui,"Noto Sans CJK SC",sans-serif}a{color:#286841;text-underline-offset:4px}nav{position:sticky;top:0;z-index:20;background:#f5f6f0ed;backdrop-filter:blur(12px);display:flex;gap:24px;padding:13px max(3%,calc((100vw - 1160px)/2));border-bottom:1px solid var(--line);overflow:auto;white-space:nowrap;font-size:13px}nav strong{margin-right:auto}nav a{text-decoration:none}main{max-width:1160px;margin:auto;padding:0 24px 50px}header{padding:56px 0 34px}h1{font-size:clamp(32px,4.8vw,56px);letter-spacing:-2px;line-height:1.25;color:var(--ink);margin:20px 0 25px}h2{font-size:29px;line-height:1.4;color:var(--ink);margin:8px 0 24px}h3{font-size:19px;line-height:1.5;color:var(--ink);margin:0 0 15px}p{margin:10px 0 17px}.lead{font-size:19px;max-width:900px}.eyebrow,.num{font-size:11px;letter-spacing:2px;font-weight:700;color:#4b7956}.path,small,.caption{color:var(--muted);font-size:12px}.path{overflow-wrap:anywhere}.banner{padding:18px 23px;border-left:4px solid #4b8356;border-radius:0 12px 12px 0;background:#e8f0e2}.warning{border-color:#b27b37;background:#fcf1df}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}.metric{padding:20px;background:white;border:1px solid var(--line);border-radius:13px}.metric b{display:block;font-size:36px;color:var(--ink);line-height:1.3}.metric span{display:block;font-size:14px;margin:7px 0}.metric small{display:block;line-height:1.6}section{padding:40px 0;border-top:1px solid var(--line)}.grid,.twocol{display:grid;grid-template-columns:1fr 1fr;gap:24px}.panel{background:white;border:1px solid var(--line);border-radius:13px;padding:25px}.diagram{margin:24px 0;padding:20px 12px;background:white;border:1px solid var(--line);border-radius:13px;overflow:auto}svg{width:100%;display:block}.diagram svg{min-width:730px}.scroll{overflow:auto;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;background:white;font-size:14px}th,td{padding:12px 15px;text-align:left;border-bottom:1px solid #edf0e7;vertical-align:top}th{background:#edf1e6;color:var(--ink)}.change{display:grid;grid-template-columns:42px 1fr;gap:19px;padding:25px 0;border-bottom:1px solid var(--line)}.change>span{font-size:15px;color:#67896b}.change p{font-size:14px}.change small{display:block;letter-spacing:2px;margin-bottom:4px}.file{font:12px monospace}.toolbar{display:flex;gap:9px;flex-wrap:wrap;margin:20px 0}button{font:13px system-ui;border:1px solid #bfcfba;border-radius:7px;padding:8px 14px;background:white;color:var(--ink);cursor:pointer}button.active{background:var(--ink);color:white}.decisions{display:grid;grid-template-columns:1fr 1fr;gap:15px}.decision{padding:22px;border:1px solid var(--line);border-radius:12px;background:white}.decision h3{margin-top:12px}.decision p{font-size:14px}.decision[hidden]{display:none}label{display:inline-block;padding:2px 8px;border-radius:5px;font-size:11px;background:#e9ede1;color:#627752}label.adopt{background:#e0edda;color:#2b673d}label.reject{background:#f7e6d6;color:#915d2a}label.change,label.hold{background:#ece9d5;color:#8a7527}.world-title{margin:30px 0 14px}.story{border:1px solid var(--line);border-radius:10px;background:white;margin:10px 0;overflow:hidden}summary{padding:15px 20px;cursor:pointer}summary b{font-size:14px;margin:0 13px}summary small{font-size:12px}.story[open] summary{background:#edf3e8;border-bottom:1px solid var(--line)}.player{margin:19px 24px;border-left:3px solid #c49a64;padding:7px 15px;color:#8c652c;font-size:14px}.prose{white-space:pre-wrap;font:15px/2 Georgia,"Noto Serif CJK SC",serif;padding:4px 24px 20px;max-height:580px;overflow:auto}.story>.caption{padding:0 24px}pre,code{font:12px/1.8 ui-monospace,Consolas,monospace}pre{padding:20px;border-radius:10px;white-space:pre-wrap;overflow-wrap:anywhere;background:#193e2f;color:#e5eee0}code{padding:2px 5px;background:#e7eddf;overflow-wrap:anywhere}.sources{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.sources a{padding:14px;border:1px solid var(--line);border-radius:8px;background:white;font-size:13px}.note{padding:10px 19px;border-left:3px solid #bfcfb8;font-size:14px}ul{padding-left:21px}li{margin:10px 0}.foot{padding:20px 0;font-size:12px;color:var(--muted)}.step{display:grid;grid-template-columns:32px 1fr;gap:15px;margin:20px 0}.step b{color:#7b9063}.step p{font-size:14px;margin:0}
@media(max-width:700px){h1{font-size:29px;letter-spacing:-1px}nav{padding:10px 17px;gap:20px}nav strong{display:none}main{padding:0 17px 30px}header{padding-top:32px}.metrics{grid-template-columns:1fr 1fr;gap:10px}.metric{padding:15px}.metric b{font-size:30px}.grid,.twocol,.decisions{grid-template-columns:1fr}.twocol{gap:0}.sources{grid-template-columns:1fr 1fr}.change{grid-template-columns:24px 1fr;gap:12px}h2{font-size:25px}.lead{font-size:17px}summary small{display:block;margin-top:7px}.panel{padding:20px}}
@media print{nav,.toolbar{display:none}body{background:white}header{padding-top:0}main{max-width:100%;padding:0}.panel,.change,.decision,.metric{break-inside:avoid}.diagram svg{min-width:0}.prose{max-height:none}.metrics{grid-template-columns:repeat(4,1fr)}a{color:inherit}}
'''
sections=[]
def section(id,num,title,body):sections.append(f'<section id="{id}"><div class="num">{num}</div><h2>{title}</h2>{body}</section>')
section('architecture','01 / BEFORE & AFTER','保留领域扩展点，统一一次行动的提交边界',
'<p>原系统已经有地图、人物、势力、知识、时间、暗线、场景密度和叙事记忆。主要风险在这些系统如何一起完成一次行动。本次保留领域划分，让所有候选变化先暂存、验证，再成为同一段可信历史。</p><div class="diagram">'+diagram()+'</div><p class="caption">资源与绝对等待由启用规则的场景使用。图中展示的是已实施的同步流程，没有把异步世界服务器画成既成能力。</p>')
section('changes','02 / SHIPPED','修复了什么，新增了什么',change_html)
check_table=table(['检查','最终轮观测'],[(esc(k),esc(v)) for k,v in review['checks'].items()])
iteration=''.join(f'<div class="step"><b>{a}</b><p>{b}</p></div>' for a,b in [
('V1','62 次调用。付款漏账；格式重问把 JSON 当正文保存。保留失败输出。'),
('V2','56 次调用。原生 JSON 与事实锚点让首次付款正确，但余额不足后仍重复扣款，提示词不足以管账。'),
('V3','74 次调用。规则层守住账本；正文却会整段重复、写错等待时刻，不能算整体叙事通过。'),
('V4',review['final_iteration'])])
perf_table=table(['事件数','之前','现在','比值'],[(f'{r["events"]:,}',f'{r["baseline_ms"]:.1f} ms',f'{r["implemented_ms"]:.1f} ms',f'{r["speedup"]:.1f}×') for r in bench['rows']])
section('experiments','03 / REAL TESTS','失败样本改变了实现，也保留在报告里',
f'<div class="grid"><div class="panel"><h3>实际迭代过程</h3>{iteration}</div><div class="panel"><h3>最终轮验收读数</h3>{check_table}<p class="caption">真实 deepseek-flash；thinking disabled；最多 2 轮工具、3 次修复。全新世界中加入明确的资源、承诺和隐藏地点测试夹具。各轮世界不同，耗时不作修复的因果比较。</p></div></div><div class="banner warning" style="margin:24px 0"><b>状态通过，不等于全文语义正确。</b><br>{esc(review["quality_note"])}</div><div class="grid"><div class="panel"><h3>旧存档与持续运行</h3><p>play2–play8 共 7 份 SQLite 副本，实体、事实、关系历史与原实现一致；原数据库哈希不变。</p><p>当前代码运行 120 个动作，验证 12 次写入故障、10 次完整撤销、8 次重开。最终燃料为 880，与 1000 − 120 一致；10 次撤销重试的随机提示一致。</p><p class="caption">确定性 provider 验证恢复机制，不能证明模型叙事质量。旧存档历史回合分组没有自动改写。</p></div><div class="panel"><h3>事实索引性能</h3>{chart}{perf_table}<p class="caption">同一投影工作负载，3 次取中位数，事实结果等价；原审计另验证 4320 次历史查询。这是合成事实投影，不是模型回合速度，也不覆盖关系密集负载。</p></div></div><h3 style="margin-top:34px">可展开的完整游玩记录</h3><p class="caption">展示最终轮全部正文，不只选最好看的片段。原始状态与人工语义复核另有证据链接。</p>{stories}')
section('world','04 / WORLD CONTINUITY','玩家行动之外，世界如何保持连续',
'<div class="grid"><div class="panel"><h3>事件、事实、信念、摘要各有用途</h3><p>事件保留发生过什么；事实索引回答现在是什么；知识记录谁相信什么。玩家只读 POV 视图，未知的 NPC 私有目标留给幕后系统。</p><p>每回合刷新当前状态和带日期的事实锚点。聊天缓存只留 8 组对话；同场景也按 6 回合分块，最近 2 块保留原文，早期内容进入摘要。</p><p>60 回合同场景专测得到 10 个记忆块，提示保留最近 12 条原文；事件日志仍保留全部 60 条。摘要是有损的派生记忆，需要事实约束。</p></div><div class="panel"><h3>可复现的随机提示</h3><p>新增安静日常、感官、人物习惯、对话节奏、既有后果和空间关系等选择。由战役种子、动作序号、地点生成抽样，尽量排除最近两项。</p><p>风格包可覆盖同名表；经典和异世界各有配置，未来科幻或日常题材可用同一接口。抽签记录在事件中，撤销后能复现。</p><p class="note">随机提示只影响创作方向，不能创造事实、判定成败或强迫接任务。尚无盲评证据证明它一定让文学质量更好；原有导演过度制造危机仍需后续节奏实验。</p></div></div>')
section('claude','05 / INDEPENDENT REVIEW','对 Claude 建议的取舍',
'<p>找到的原报告来自 2026-09-07、同一 Claude session。发现清单用于排查，实施决策则依赖独立反例与测试。</p><div class="toolbar">'+''.join(f'<button data-filter="{k}" class="{"active" if k=="all" else ""}">{v}</button>' for k,v in [('all','全部'),('adopt','落实'),('change','调整'),('reject','否决'),('hold','后续')])+'</div><div class="decisions">'+decision_html+'</div>')
roadmap=table(['下一步','为什么需要','通过条件'],[
('有来源的行动效果、交换与奖励','补完资源裁定范围，保持物品和资金守恒。','买卖双方、奖励权限、失败交易、重试和撤销；模型不得擅自换购。'),
('结构化承诺与持有关系','文字锚点仍挡不住模型补写借伞人、地点与过去。','100+ 真实回合保持人物、借出/借入方向、绝对日期、持有人一致，重开和压缩后仍正确。'),
('有版本的异步世界任务','有了提交边界，才适合减少同步等待。','任务取消、超时、切图、撤销及旧分支完成都不污染当前世界，测完整可行动时间。'),
('多题材盲评与记忆评估','证明随机性降低同质化，而不牺牲事实一致性。','固定世界、动作、模型预算，对照有无随机表；评估角色差异、玩家选择权、重复与事实偏差。')])
section('target','06 / EXTENSION & LIMITS','可扩展性保留；未证明的能力不宣称完成',
'<div class="grid"><div class="panel"><h3>规则从场景包进入</h3><p>保留 pack.json 的 voice、tone、select_hints 与原 oracle 表。新增 narrative_variations.json 及可选 resources。资源名来自数据，氧气、燃料、钱币都能复用。</p><pre>{&quot;resources&quot;: {&quot;oxygen&quot;: {\n  &quot;initial&quot;: 100, &quot;type&quot;: &quot;integer&quot;,\n  &quot;min&quot;: 0, &quot;max&quot;: 100, &quot;label&quot;: &quot;氧气&quot;\n}}}</pre><p>领域仍通过 ContextSystem 注册校验、事件、投影。通用 fact_rules 支持类型、上下界、枚举与不可变字段。</p></div><div class="panel"><h3>实际边界</h3><ul><li>资源模块覆盖明确支出与不足拒绝；奖励、偷窃、双方交易和物品转移需要更多效果处理。登记余额的自由改写会被拒绝。</li><li>意图解析仍由模型完成，算术正确不保证它理解了每句话。</li><li>模型仍会补写既往细节、混淆持有关系；正文检测不是语义证明器。</li><li>旧地图未标记实体默认公开；摘要、暗线文本还需更广的秘密测试。</li><li>后台随动作同步运行，没有离线持续 tick 的服务器；16 个最终轮动作不等于千回合真实验收。</li></ul></div></div><div style="margin-top:24px">'+roadmap+'</div>')
section('run','07 / RUN & REPRODUCE','使用改进后的引擎',
'<div class="grid"><div class="panel"><h3>专用启动入口</h3><pre>cd /root/rpg-engine-app\n./run-deepseek.sh \\\n  --campaign /root/games/my-new-adventure \\\n  --flavor isekai \\\n  --pitch \'轻快的异世界旅行，从普通人的生活开始\'</pre><p>密钥保存在项目外的 0600 文件。此入口新局启用资源规则，可在创建前设置 RPG_RESOURCE_RULES=0 使用原自由记账方式。规则随存档持久化；原 Zhipu run.sh 保留。</p></div><div class="panel"><h3>复查入口</h3><pre>python3 -m pytest -q\n\npython3 /root/rpg-engine-audit-20261003/experiments/implemented_endurance.py</pre><p>真实测试有 API 调用预算，使用新目录且拒绝覆盖旧实验。按调用记录工具、token、修复和耗时。</p><p class="caption">DeepSeek 按官方 <a href="https://api-docs.deepseek.com/zh-cn/guides/json_mode/">JSON Output</a> 与 <a href="https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/">思考/工具协议</a> 适配。结构化调用使用 JSON 模式，工具回合保留完整 assistant 消息组。</p></div></div>')
section('evidence','08 / EVIDENCE','源码、实验与原报告入口','<div class="sources">'+source_html+'</div><p class="note">正常流程测试夹具补齐了必填段；原来期待“部分成功”的测试改为拒绝行动、零写入。离线创世夹具显式允许 stub，生产失败另作注入验证；没有删除旧测试。</p><p class="caption">本报告为自包含 HTML，支持桌面、手机及打印；完整包带证据与补丁，不含密钥。</p>')
speed=bench['rows'][-1]['speedup']
metrics=[(f'{int(tests["tests"]):,}','当前回归通过',f'失败 {tests["failures"]} / 错误 {tests["errors"]}'),(f'{sum(t["accepted"] for t in turns)}/{len(turns)}','最终轮真实动作提交','2 个题材；叙事质量另行复核'),('120','当前代码持续动作','12 次故障 · 10 次撤销 · 8 次重开'),(f'{speed:.1f}×','12K 事实投影加速','3 次中位数；不等于回合提速')]
page='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>RPG Engine · 改进实施与实测</title><style>'+css+'</style></head><body><nav><strong>RPG / ENGINE REVIEW</strong>'+''.join(f'<a href="#{a}">{b}</a>' for a,b in [('architecture','架构对照'),('changes','已改什么'),('experiments','实际测试'),('world','世界与记忆'),('claude','复核 Claude'),('target','扩展与边界'),('evidence','证据')])+'</nav><main><header><div class="eyebrow">IMPLEMENTATION & EMPIRICAL REVIEW · 2026.10.03</div><h1>让每次行动有可信的结果，<br>让世界记住发生过什么。</h1><p class="lead">已直接改进 rpg-engine-app：统一动作、叙事与幕后变化的提交边界，修复记忆及可见性，增加场景包驱动的随机提示与资源规则。保留原有多题材扩展方式，真实失败样本也纳入结论。</p><p class="path">项目 /root/rpg-engine-app · 基线 fb7ebd8<br>Claude session：3f2736aa-1b06-454d-8ad8-0f987c6b0a9f · 2026-09-07<br>/root/llm-rpg-engine 是较早副本；原 run.sh 与原始游玩存档保留。</p><div class="banner"><b>'+esc(review['headline'])+'</b><br>'+esc(review['scope_statement'])+'</div><div class="metrics">'+''.join(f'<div class="metric"><b>{a}</b><span>{b}</span><small>{c}</small></div>' for a,b,c in metrics)+'</div></header>'+''.join(sections)+'<footer class="foot">独立审核与实施 · 2026-10-03 · 基线 fb7ebd8 → 当前工作区。<br>已实施、已验证与后续候选分别标注。请以验收范围和原始证据理解结果。</footer></main><script>document.querySelectorAll("[data-filter]").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll("[data-filter]").forEach(x=>x.classList.toggle("active",x===b));document.querySelectorAll(".decision").forEach(c=>c.hidden=b.dataset.filter!=="all"&&c.dataset.kind!==b.dataset.filter)}));</script></body></html>'
(ROOT/'report.html').write_text(page)
print('report.html bytes:',len(page.encode()))
