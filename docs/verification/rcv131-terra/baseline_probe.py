"""RCV131 baseline: normal GUI with deterministic synthetic author proposals."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from docs.verification.rcv130.probe import game, act, propose, INN, MARKET, HERO, LAMP


def main():
    result = {}
    with game() as (engine, dashboard):
        item = lambda to: [{'op': 'transfer', 'item': LAMP, 'to': to}]
        act(dashboard, '把铜灯放在脚边。', propose('你把铜灯放在脚边。', items=item(INN)), 'drop')
        before = engine.store.revision
        try:
            act(dashboard, '捡回刚刚放在脚边的铜灯。', propose('你捡回铜灯。', items=item(HERO)), 'pickup')
            status = 'accepted'
        except Exception as exc:
            status = type(exc).__name__ + ': ' + str(exc)
        result['pickup'] = {'status': status, 'revision_unchanged': before == engine.store.revision,
                            'holder': engine.world['systems']['ontology'].neighbors(LAMP, 'held_by', 1)}
        unique = '朱橘纸鹤停在第三块石头'
        act(dashboard, '请记住这句故事里的原话。', propose('她轻声说：“' + unique + '。”'), 'quote')
        for i in range(9):
            act(dashboard, '继续观察。', propose('你继续观察街边动静。'), 'quiet-' + str(i))
        result['recall'] = {'query': unique, 'hits': dashboard.recall(unique),
                            'in_last_eight': any(unique in text for text in dashboard.state()['narration']),
                            'raw_narration_exists': any(e['type'] == 'narration_recorded' and unique in e.get('deltas', {}).get('text', '') for e in engine.store.iter_events())}
        moved = act(dashboard, '去集市。', propose('你来到集市。', moves=[{'who': HERO, 'to': MARKET}]), 'travel')
        waited = act(dashboard, '一直等到明天中午。', propose('你开始等待。'), 'wait')
        result['travel_wait'] = {'location': moved['state']['scene']['location'],
                                 'after_wait': waited['state']['scene']['time']}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
