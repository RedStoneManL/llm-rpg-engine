"""Mechanically replay the untouched failed Terra candidate on a cloned save.

This is recorded-response regression evidence, not another live model sample.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from app.engine import build_engine, rewind
from app.gui import PlayerDashboard
from app.play import _build_scene
from context.capabilities import player_turn_fact_errors
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.strategy import AuthorStrategy


def main():
    out, campaign = map(Path, sys.argv[1:3])
    raw = json.loads((out / 'responses/011.json').read_text())['message']['content']
    candidate = json.loads(raw)
    original = subprocess.check_output(['git', 'show', '9acb6f3:context/capabilities.py'], cwd=REPO, text=True)
    namespace = {'__name__': 'context.capabilities'}
    exec(compile(original, 'baseline/context/capabilities.py', 'exec'), namespace)
    with tempfile.TemporaryDirectory(prefix='terra-failure-replay-') as temporary:
        path = Path(temporary)
        with sqlite3.connect(f'file:{campaign / "events.db"}?mode=ro', uri=True) as source:
            with sqlite3.connect(path / 'events.db') as destination:
                source.backup(destination)
        provider = FakeLLMProvider(json_responses=[candidate])
        engine = build_engine(path, provider=provider, embedder=None)
        try:
            rewind(engine, 5, expected_revision=engine.store.revision)
            scene = _build_scene(engine)
            commit = TurnCommit.from_dict(candidate)
            before = namespace['player_turn_fact_errors'](commit, engine.world, scene)
            after = player_turn_fact_errors(commit, engine.world, scene)
            assert any(e.code == 'player_fact_authority_boundary' for e in before)
            assert after == []
            d = PlayerDashboard(engine, strategy=AuthorStrategy(), demo=False, max_repairs=0)
            result = d.action(player_input='我留在枫桥镇等到明天早晨，再看看集市有什么变化。',
                              action_id='recorded-terra-failure-regression', expected_revision=engine.store.revision)
            graph = engine.world['systems']['ontology']
            day = engine.world['meta']['day']
            value = graph.value_at('mistaken_courier', 'reported:船契', day)
            assert value == candidate['facts'][0]['value']
            assert graph.neighbors('mistaken_courier', 'located_in', day) == ['b_market']
            report = {'evidence': 'untouched recorded response through ordinary dashboard; no new model generation',
                      'candidate_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                      'before_errors': [e.code for e in before], 'after_errors': [],
                      'published': result['ok'], 'stored_report': value, 'day': day,
                      'stored_player_knowledge': graph.value_at('protagonist', 'knows:mistaken_courier.reported:船契', day)}
            (out / 'exact-failure-replay.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(report, ensure_ascii=False))
        finally:
            engine.store.close()


if __name__ == '__main__':
    main()
