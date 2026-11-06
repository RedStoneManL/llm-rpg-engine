"""Render the stand-alone Chinese review sheet from the versioned issue catalog."""
import json
from html import escape as esc
from pathlib import Path
from urllib.parse import quote

DIR = Path(__file__).resolve().parent
ROOT = DIR.parents[2]
PAGE = ROOT / 'docs/reports/2026-11-07/play-review.html'
issues = json.loads((DIR/'issues.json').read_text())
probes = json.loads((DIR/'probes.json').read_text())
assert len(issues) == len({r['id'] for r in issues})


def source(raw):
    path, _, anchor = raw.partition('@')
    file = ROOT / path
    label, target = path, '../../../' + quote(path)
    if anchor:
        lines = file.read_text().splitlines()
        number = next(i for i,line in enumerate(lines,1) if anchor in line)
        label += f':{number}'
        target += f'#L{number}'
    assert file.is_file()
    return f'<a href="{target}">{esc(label)}</a>'


def options(values):
    return ''.join(f'<option>{esc(v)}</option>' for v in dict.fromkeys(values))


def card(r):
    num = r['id']
    search_text = " ".join(str(r[k]) for k in ("id", "area", "title", "trigger", "impact", "question", "limit"))
    proof = ''
    if r['probe']:
        selected = {k:probes[k] for k in r['probe'].split(',')}
        proof = f'<details><summary>查看本次观测数据</summary><pre>{esc(json.dumps(selected,ensure_ascii=False,indent=2))}</pre></details>'
    return f'''<article id="i{num}" class="issue" data-search="{esc(search_text)}" data-area="{esc(r['area'])}" data-evidence="{esc(r['evidence'])}" data-priority="{esc(r['priority'])}"><header><span class="id">{num}</span><div><small>{esc(r['area'])} · {esc(r['evidence'])} · {esc(r['priority'])}</small><h3>{esc(r['title'])}</h3></div></header><p class="scene">{esc(r['trigger'])}</p><p>{esc(r['impact'])}</p><div class="question">{esc(r['question'])}</div><details><summary>证据边界与源码</summary><p>{esc(r['limit'])}</p><p class="refs">{' '.join(source(s) for s in r['sources'])}</p></details>{proof}<label class="decision">我的判断 <select class="verdict" aria-label="第{num}项判断"><option>未评估</option>{options(['判为问题','需要试玩','保留现状','暂不成立'])}</select></label></article>'''


html='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>世界引擎 · 体验问题评审清单</title><style>
*{box-sizing:border-box}body{margin:0;color:#1c343a;background:#f6f5ee;font:16px/1.72 system-ui,sans-serif}main{max-width:998px;margin:auto;padding:38px 22px 74px}header small,.meta,small{color:#486369;font-size:13px}h1,h2,h3{line-height:1.3}h1{font:clamp(32px,6vw,54px)/1.15 Georgia,"Songti SC",serif;margin:12px 0}h2{font-size:22px;margin:30px 0 12px}h3{font-size:20px;margin:4px 0}p{margin:9px 0}.intro{max-width:74ch}.panel,.issue{border:1px solid #ccd6d0;background:#fffef8;border-radius:9px;padding:18px 22px;margin:13px 0}.quick{columns:2;list-style:none;padding:0}.quick li{break-inside:avoid;margin-bottom:6px}a{color:#09605c}button,input,select,textarea{font:inherit}input,select,button,textarea{border:1px solid #a4b5b3;border-radius:5px;background:white;padding:5px 9px;color:#183c41}button{background:#e2eeea;cursor:pointer}label{display:inline-flex;gap:7px;align-items:center;flex-wrap:wrap}.filters{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}.issue{scroll-margin-top:15px;padding:17px 22px}.issue header{display:flex;gap:14px;align-items:start}.id{font:700 18px/1.6 ui-monospace,monospace;color:#277769;border-bottom:2px solid #277769}.scene{color:#425d61}.question{margin:10px 0;padding:8px 12px;background:#edf3e9;border-left:3px solid #558766}.decision{margin-top:9px}.refs a{display:inline-block;margin:0 10px 4px 0;overflow-wrap:anywhere;font-family:ui-monospace,monospace;font-size:13px}summary{cursor:pointer;color:#355c61}pre{overflow:auto;font:13px/1.5 ui-monospace,monospace;background:#eff3ef;padding:8px}textarea{width:100%;height:240px}output{font-variant-numeric:tabular-nums}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #be6825}.fine{color:#51666a;font-size:14px}.meta a{margin-right:8px}.buttons{display:flex;gap:10px;flex-wrap:wrap}[hidden]{display:none!important}@media(max-width:640px){main{padding:20px 13px}.quick{columns:1}.issue,.panel{padding:14px}.filters label{width:100%}.filters select,.filters input{flex:1;min-width:0}}@media print{.filters,.buttons,.decision{display:none}.issue{break-inside:avoid}main{padding:0}} </style></head><body><main><small>llm-rpg-engine · 2026-11-07 · 源码 286333c</small><h1>世界引擎体验评审清单</h1><p class="intro">共 34 项候选。先读每项的实际触发和玩家后果，再用绿色框中的问题判断是否值得改。“先审”表示影响时间、任务、背包、来源或真实回合提交，不代表已经决定修复。</p><p class="meta"><a href="../../verification/RCV-130-play-audit.md">核验说明与重跑方法</a><a href="world-continuity.html">此前的多线长局对照</a></p><section class="panel"><h2>优先看这九项</h2><ol class="quick">QUICK</ol></section><section><h2>按场景评估</h2><p class="fine">入口复现使用真实普通控制器或玩家读接口及合成数据，故意给出不利候选，验证会不会被接受。模块复现指单独运行组件。既有游玩来自先前的 Terra 单样本。源码线索与玩法取舍仍需裁决。本轮门禁 2240 项通过，1 项按旧 slow 条件排除。</p><div class="filters"><label>检索<input id="search" type="search" placeholder="编号、场景或关键词"></label><label>领域<select id="area"><option value="">全部</option>AREAS</select></label><label>证据<select id="evidence"><option value="">全部</option>EVIDENCE</select></label><label>优先级<select id="priority"><option value="">全部</option>PRIORITIES</select></label><label>我的判断<select id="decision"><option value="">全部</option>DECISIONS</select></label></div><p><output id="count">34 / 34 项</output></p><div class="buttons"><button id="reset" type="button">清除筛选</button><button id="export" type="button">生成可复制的评审摘要</button></div><div id="exportbox" hidden><label for="out">已评项目，可直接复制发回</label><textarea readonly id="out"></textarea></div><small id="saveinfo">判断记录尽量保存在当前浏览器；跨设备使用上方摘要。</small><noscript><p>下面是完整清单；启用 JavaScript 可筛选并导出判断。</p></noscript>CARDS</section></main><script>
const byId=id=>document.getElementById(id), cards=[...document.querySelectorAll('.issue')], controls=['search','area','evidence','priority','decision'];
const key='llm-rpg-review-286333c-v1';let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')||{}}catch(_){}
const valid=new Set(['未评估','判为问题','需要试玩','保留现状','暂不成立']);
function refresh(){let shown=0,rated=0;const query=byId('search').value.trim().toLowerCase();for(const c of cards){const val=c.querySelector('.verdict').value;if(val!=='未评估')rated++;const pass=(!query||c.dataset.search.toLowerCase().includes(query)) && ['area','evidence','priority'].every(k=>!byId(k).value||c.dataset[k]===byId(k).value) && (!byId('decision').value||val===byId('decision').value);c.hidden=!pass;if(pass)shown++}byId('count').textContent=`显示 ${shown} / ${cards.length} 项，已评 ${rated} 项`}
for(const c of cards){const el=c.querySelector('.verdict');el.value=valid.has(saved[c.id])?saved[c.id]:'未评估';el.addEventListener('change',()=>{saved[c.id]=el.value;try{localStorage.setItem(key,JSON.stringify(saved))}catch(_){byId('saveinfo').textContent='当前预览不支持本地保存，请生成并复制评审摘要。'}refresh();if(!byId('exportbox').hidden)exportText()})}
for(const id of controls)byId(id).addEventListener('input',refresh);
function clearFilters(){for(const id of controls)byId(id).value='';refresh()}
byId('reset').addEventListener('click',clearFilters);for(const a of document.querySelectorAll('.quick a'))a.addEventListener('click',clearFilters);
function exportText(){const rows=cards.filter(c=>c.querySelector('.verdict').value!=='未评估').map(c=>`${c.id.slice(1)} ${c.querySelector('.verdict').value} · ${c.querySelector('h3').textContent}`);byId('out').value=['llm-rpg-engine / 286333c / 体验评审',...(rows.length?rows:['尚未标记任何项目'])].join('\\n');byId('exportbox').hidden=false}
byId('export').addEventListener('click',()=>{exportText();byId('out').select()});refresh();
</script></body></html>'''
html=html.replace('QUICK',''.join(f'<li><a href="#i{r["id"]}">{r["id"]}　{esc(r["title"])}</a></li>' for r in issues if r['priority']=='先审'))
for name,field in [('AREAS','area'),('EVIDENCE','evidence'),('PRIORITIES','priority')]:
    html=html.replace(name,options(r[field] for r in issues))
html=html.replace('DECISIONS',options(['未评估','判为问题','需要试玩','保留现状','暂不成立'])).replace('CARDS','\n'.join(card(r) for r in issues))
PAGE.write_text(html)
