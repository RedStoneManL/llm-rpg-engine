"""Review saved real outputs and exercise the strict commit gate on failures."""
import json
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, '/root/rpg-engine-app')
from app.engine import build_engine
from kernel.events import kernel_event
from kernel.turncommit import TurnCommit
from candidate import validated_apply, IncompleteAction

live = json.loads((ROOT/'evidence/live-validation.json').read_text())
assert len(live['samples']) == 12
result = {'scope': '12 synthetic one-turn scenes; 6 per strategy; thinking disabled; tools disabled; max_repairs=1',
          'strategies': [], 'real_output_gate_replays': [], 'review_notes': [
              'Accounting success means only the expected hero.coins value and local structural validation; it does not establish narrative quality.',
              'The sample is small, order is counterbalanced, and provider caching differs by request; latency is descriptive, not a population estimate.',
              'Some prose invents prior history or changes the seeded asking price; no claim of general story consistency.',
              'Hybrid freezes its initial prose; failed structural output must not publish that prose as a completed action.',
              'Official reasoning-content guidance and one live tool experiment differ: the legacy follow-up returned HTTP 200; do not label an inevitable HTTP 400.'
          ]}
for name in ['author', 'hybrid']:
    samples = [s for s in live['samples'] if s['strategy'] == name]
    result['strategies'].append({'strategy': name, 'n': len(samples),
        'accepted': sum(s.get('persisted',False) for s in samples),
        'accounting_ok': sum(s.get('accounting_ok',False) for s in samples),
        'repairs': sum(s['repairs'] for s in samples),
        'api_calls': sum(s['calls'] for s in samples),
        'median_seconds': statistics.median(s['seconds'] for s in samples),
        'seconds': [s['seconds'] for s in samples]})
for sample in live['samples']:
    if not sample['dropped']:
        continue
    folder = Path(tempfile.mkdtemp(prefix='gate-live-', dir=ROOT/'experiments'))
    engine = build_engine(folder)
    engine.store.append(kernel_event('entity_created', day=1, scene='inn', summary='gate replay fixture',
        deltas={'id':'hero','etype':'Person','tier':'tracked'}, turn=0))
    engine.store.append(kernel_event('fact_asserted', day=1, scene='inn', summary='gate replay fixture',
        deltas={'subject':'hero','predicate':'coins','value':10,'secrecy':'public'}, turn=0))
    before = len(list(engine.store.iter_events()))
    commit = TurnCommit(sample['narration'], sample['sections'])
    rejected = False
    try:
        validated_apply(engine.registry, engine.store, commit, day=1, scene='inn', dropped_sections=sample['dropped'])
    except IncompleteAction:
        rejected = True
    after = len(list(engine.store.iter_events()))
    assert rejected and before == after
    result['real_output_gate_replays'].append({'scenario':sample['scenario'], 'repeat':sample['repeat'],
        'dropped':sample['dropped'], 'rejected':rejected, 'events_added':after-before,
        'note':'Replay of saved real model output through candidate gate; no new API call'})
result['real_api_calls'] = live['real_calls']
result['usage'] = {k:sum(c.get('usage',{}).get(k,0) for c in live['api_calls'])
                   for k in ['prompt_tokens','completion_tokens','total_tokens']}
result['status'] = 'reviewed_with_limits'
(ROOT/'evidence/live-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
