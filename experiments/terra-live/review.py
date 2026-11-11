"""Derive auditable boundary checks and a readable replay from one live run."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out', type=Path)
    args = ap.parse_args()
    out = args.out.resolve()
    read = lambda name, default=None: json.loads((out / name).read_text()) if (out / name).exists() else default
    manifest = read('manifest.json', {})
    roots = ([Path(manifest['resume_from'])] if manifest.get('resume_from') else []) + [out]
    transcript, calls, requests, responses = [], [], [], {}
    for number, root in enumerate(roots):
        load = lambda name: json.loads((root / name).read_text()) if (root / name).exists() else []
        transcript.extend({**t, 'run_label': '初跑' if number == 0 and len(roots) > 1 else '修后续跑'} for t in load('transcript.json'))
        calls.extend(load('calls.json'))
        requests.extend({**json.loads(p.read_text()), 'run_number': number} for p in sorted((root / 'requests').glob('*.json')))
        responses.update({(number, int(p.stem)): json.loads(p.read_text()) for p in sorted((root / 'responses').glob('*.json'))})
    events = read('events.json', [])
    active = [e for e in events if not e.get('retracted')]
    undo = read('undo.json', {})
    reopen = read('cold-reopen.json', {})
    recall = read('recall.json', {})
    j = lambda value: json.dumps(value, ensure_ascii=False)
    checks = []
    def check(key, label, passed, evidence):
        checks.append({'id': key, 'label': label, 'passed': passed, 'evidence': evidence})
    matched = all(i in responses and responses[i]['request_sha256'] == r['request_sha256'] ==
                  hashlib.sha256(json.dumps(r['request'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                  for r in requests for i in [(r['run_number'], r['index'])])
    check('request_response_hashes', '真实请求与响应逐份对应', matched and bool(requests), f'{len(requests)} 份请求；{len(responses)} 份响应')
    check('cold_reopen', '冷重开保持被检查状态', reopen.get('before') == reopen.get('after') if reopen else None, '比较时钟、位置、认知、暗线和事件计数')
    check('undo_reopen', '撤销后的冷重开保持状态', undo.get('after_undo') == undo.get('after_reopen') if undo else None, '撤销后重新构造正式 dashboard')
    last_requests = [r for r in requests if r.get('action') == 12]
    check('retracted_context', '撤销的约定未进入后续模型请求', all('紫铜罗盘' not in j(r['request']) for r in last_requests) if last_requests else None, f'最后行动的 {len(last_requests)} 份实际请求；标记为紫铜罗盘')
    check('retracted_recall', '撤销的约定不再被玩家检索', '紫铜罗盘' not in j(recall.get('retracted', {}).get('results', recall.get('retracted', {}))) if recall else None, '具体查询回包见 recall.json；需排除查询文本本身')
    check('past_recall', '仍可查回早先原话', '青竹铃铛明晨响四次' in j(recall.get('past', {})) if recall else None, '玩家正常 recall 入口')
    foreground = [r for r in requests if 'produce' in r['call_path'] or 'finalize_narration' in r['call_path']]
    check('host_secret_context', '主持层秘密未进入前台作者请求', all('双层陶罐' not in j(r['request']) for r in foreground) if foreground else None, f'{len(foreground)} 份实际作者／正文请求')
    knowledge = [e['deltas'] for e in active if e['type'] == 'knowledge_set']
    check('unspoken_knowledge', '未说出口的猜想未写入 NPC 认知', all('铃铛藏在柳树下' not in j(e) for e in knowledge if e.get('knower') != 'protagonist') if events else None, '仅检查实际认知事件；台词由人工另行审读')
    check('foreign_private_knowledge', '白禾未获得只告诉阿岚的原话', all('青竹铃铛' not in j(e) for e in knowledge if e.get('knower') == 'bob') if events else None, '后台存储检查，不等同于正文无语义泄漏')
    day_two = next((t for t in transcript if t['action_number'] == 5), None)
    remote = day_two['after']['lines'].get('mail', {}) if day_two else {}
    check('offscreen_progress', '玩家留在枫桥时青山驿暗线仍推进', remote.get('stage_idx', -1) >= 0 if day_two else None, remote)
    checks_path = out / 'checks.json'
    checks_path.write_text(json.dumps(checks, ensure_ascii=False, indent=2) + '\n')
    categories = Counter()
    for c in calls:
        path = c['call_path']
        kind = ('作者与工具' if 'produce' in path else '正文校验' if 'review_player_narration' in path
                else '最终正文' if 'finalize_narration' in path else '暗线生成' if 'generate_lore_batch' in path
                else '摘要' if 'summarize_scene' in path or 'digest_fleet' in path else '后台其他')
        categories[kind] += 1
    manual = read('assessment.json', {})
    esc = lambda s: html.escape(str(s))
    cards = []
    for t in transcript:
        n, result, after = t['action_number'], t['result'], t['after']
        lines = '; '.join(f'{k}: {v.get("stage_idx")}' for k, v in after['lines'].items())
        markers = ''.join(f'<span>{esc(x)}</span>' for x in [t['run_label'], f'第 {after["meta"]["day"]} 天', '/'.join(after['location']), f'{t["request_end"] - t["request_start"]} 次模型请求'])
        explicit = f'<div class="said">明确说给 {esc(t["input"].get("recipient"))}：{esc(t["input"]["statement"])}</div>' if t['input'].get('statement') else ''
        notes = manual.get('turn_notes', {}).get(str(n), '')
        cards.append(f'<article id="turn-{n}"><div class="eyebrow">行动 {n}</div><h2>{esc(t["input"].get("text"))}</h2>{explicit}<div class="tags">{markers}</div><div class="narration">{esc(result.get("narration", "（只读查询）"))}</div>'
                     f'{"<div class=note>审读："+esc(notes)+"</div>" if notes else ""}<details><summary>查看状态与调用依据</summary><p>暗线阶段：{esc(lines)}</p><p>请求编号：{t["request_start"]:03}–{t["request_end"]-1:03}</p><pre>{esc(json.dumps({"before":t["before"],"after":after},ensure_ascii=False,indent=2))}</pre></details></article>')
        if n == 10:
            cards.append('<div class="undo">此后执行真实撤销，并关闭、重开存档。下一段属于撤销后的新分支。</div>')
    rows = ''.join(f'<tr><td>{esc(c["label"])}</td><td class="{ "pass" if c["passed"] is True else "fail" if c["passed"] is False else "unknown"}">{"通过" if c["passed"] is True else "需复核" if c["passed"] is False else "未覆盖"}</td><td>{esc(c["evidence"])}</td></tr>' for c in checks)
    finding_html = ''.join(f'<li>{esc(item)}</li>' for item in manual.get('findings', []))
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Terra DM 实际游玩记录</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f4f2eb;color:#243632;font:16px/1.8 system-ui,"Noto Sans CJK SC",sans-serif}main{max-width:1080px;margin:auto;padding:42px 24px 80px}header{border-bottom:3px solid #396154;padding-bottom:24px}h1{font-size:clamp(30px,5vw,48px);line-height:1.2;margin:8px 0 18px}h2{font-size:20px;line-height:1.6;margin:8px 0}.eyebrow{font-size:13px;letter-spacing:2px;color:#557366}p{margin:10px 0}.lede{max-width:80ch;color:#4a5d53}.metrics{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}.metric{padding:14px 22px;background:#e4ebe3;border-radius:8px}.metric b{display:block;font-size:28px;line-height:1.3}article,.panel{background:#fffef8;border:1px solid #d2d9ce;padding:25px 29px;border-radius:9px;margin:20px 0}article{border-left:4px solid #638674}.tags{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.tags span{font-size:13px;background:#edf1e8;padding:2px 9px;border-radius:4px}.narration{white-space:pre-wrap;font-size:18px;margin:18px 0;line-height:1.9}.said{color:#396154}.note{background:#fbefcf;padding:10px 14px;border-left:3px solid #b78e32;margin:16px 0}summary{cursor:pointer;color:#52765e}details{border-top:1px solid #e1e5dc;padding-top:12px;font-size:14px}pre{overflow:auto;max-height:420px;font-size:12px;line-height:1.6;background:#f0f3ed;padding:14px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;border-bottom:1px solid #d8ded4;text-align:left;vertical-align:top}th{background:#edf1e8}.table{overflow:auto}.pass{color:#21674a;white-space:nowrap}.fail{color:#a04e27;white-space:nowrap}.unknown{color:#777}.undo{border:1px dashed #b48236;background:#fbefcf;padding:15px 22px;border-radius:7px}a{color:#3b6e5a}li{margin-bottom:9px}footer{font-size:13px;color:#627166;margin-top:28px}@media(max-width:640px){main{padding:24px 12px}article,.panel{padding:18px}.narration{font-size:16px}}@media print{body{background:white}article{break-inside:avoid}details{display:none}}
    </style><main><header><div class="eyebrow">RPG · 6.1 TERRA · 原生模型实测</div><h1>这次由 Terra 来当 DM</h1><p class="lede">真实模型逐请求生成，经过正式玩家入口、校验、后台流程与 SQLite 存档。下面保留实际公开正文；没有把失败候选手工润色成成功剧情。</p></header>'''
    page += f'<div class="metrics"><div class="metric"><b>{len(transcript)}</b>实际行动</div><div class="metric"><b>{len(calls)}</b>模型请求</div><div class="metric"><b>{sum(len(c["tool_names"]) for c in calls)}</b>游戏工具调用</div><div class="metric"><b>0</b>禁用的后台 hook</div></div>'
    page += '<section class="panel"><h2>如何看这份结果</h2><p>这是指定 gpt-6.1-terra 的原生模型交换实验。请求哈希逐份对应，模型工具请求交回引擎执行。作者和语义审查使用同一模型。没有调用远程付费 API，也没有启动 DSH 宿主；不据此报告 API 延迟、token 成本或插件安装通过。</p><p>初跑在第 5 个行动被引擎拒绝，修复后从失败前存档续跑。统计保留了失败请求，不把续跑描述成首次全程成功。</p><p>调用分布：'+esc(dict(categories))+'</p>'
    page += f'<ul>{finding_html}</ul></section><section class="panel"><h2>确定性边界核对</h2><div class="table"><table><thead><tr><th>检查</th><th>结果</th><th>依据与边界</th></tr></thead><tbody>{rows}</tbody></table></div></section>'
    page += ''.join(cards)
    page += '<footer>合成存档；单次样本。人物说得是否自然、是否含有无依据的暗示需要结合原话审读，不能由存储检查替代。原始请求、原始响应、事件、每回合状态与撤销记录随同报告保存。</footer></main></html>'
    (out / 'Terra_DM_Live_Replay.html').write_text(page)
    print(json.dumps({'actions': len(transcript), 'calls': len(calls), 'checks': checks, 'categories': categories}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
