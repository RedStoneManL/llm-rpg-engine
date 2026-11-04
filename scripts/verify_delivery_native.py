"""Incremental native-model playtest using exact production requests.

Run ``next DIR``; when pending.json appears, supply the native raw response as
the next string in responses.json and rerun. No placeholder is accepted as model
evidence. Foreground-only synthetic fixture; no remote transport or embedder.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.engine import build_engine
from app.gui import PlayerDashboard, _seed_demo_campaign
from app.play import _build_scene
from kernel.events import kernel_event
from kernel.projection import project
from llm.provider import LLMProvider
from loop.strategy import HybridStrategy


class Pending(BaseException):
    """Pause without allowing a background best-effort catch to consume it."""


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class Probe(LLMProvider):
    def __init__(self, directory):
        self.directory = directory
        self.requests = json.loads((directory / 'requests.json').read_text()) if (directory / 'requests.json').exists() else []
        self.responses = json.loads((directory / 'responses.json').read_text()) if (directory / 'responses.json').exists() else []
        self.index = 0

    def complete(self, system, user, **kwargs):
        return self._call({'method': 'complete', 'system': system, 'user': user, 'kwargs': kwargs})

    def complete_messages(self, messages, **kwargs):
        return self._call({'method': 'complete_messages', 'messages': copy.deepcopy(messages), 'kwargs': kwargs})

    def _call(self, request):
        i = self.index
        self.index += 1
        if i < len(self.requests):
            assert request == self.requests[i], f'production request changed at {i}'
        else:
            self.requests.append(request)
            write(self.directory / 'requests.json', self.requests)
        if i >= len(self.responses):
            write(self.directory / 'pending.json', {'index': i, 'request': request})
            raise Pending()
        assert isinstance(self.responses[i], str) and self.responses[i].strip()
        return self.responses[i]


def seed(engine):
    _seed_demo_campaign(engine)
    location = _build_scene(engine)['location']
    for kind, delta in [
        ('character_created', {'id': 'apothecary', 'sketch': '正在等药的药师', 'goal': '救治病人', 'tier': 'tracked'}),
        ('entity_moved', {'who': 'apothecary', 'to': location}),
        ('object_created', {'id': 'medicine', 'description': '封好的药包'}),
        ('item_transferred', {'item': 'medicine', 'to': 'protagonist'}),
    ]:
        engine.store.append(kernel_event(kind, day=1, scene=location, turn=0, summary='synthetic setup', deltas=delta))
    engine.world = project(engine.registry, engine.store.iter_events())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['next'])
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    probe = Probe(args.directory)
    with ExitStack() as stack, tempfile.TemporaryDirectory(prefix='rpg-delivery-native-') as tmp:
        for name in ('digest_fleet', 'run_director', 'run_cascade', 'run_catchup', 'run_density', '_run_demote_on_leave'):
            stack.enter_context(patch('loop.turn.' + name, return_value=[]))
        engine = build_engine(tmp, provider=probe, embedder=None)
        try:
            seed(engine)
            dashboard = PlayerDashboard(engine, strategy=HybridStrategy())
            actions = [
                '我接受这份约定：把背包里的药包（medicine）交给眼前的药师（apothecary），截止第1天中午（包含中午时段）。此刻只答应，还没交药，也没有约定奖励。',
                '我等到今天下午，药包一直留在自己背包，没有交给任何人。',
                '我心想可以骗他药已经交过了，但没有说出口，也没有递出药包。我只是站在原地看看药师。',
                '现在把药包实际递给药师，虽然已经错过中午的期限。',
                '我仍站在药师面前，观察他收药后的反应，看看接下来有什么可做的；不再拿取或交付任何东西。',
            ]
            transcript = []
            delivery_revision = None
            for i, text in enumerate(actions):
                revision = dashboard.state()['revision']
                if i == 3:
                    delivery_revision = revision
                response = dashboard.action(player_input=text, action_id=f'native-{i}', expected_revision=revision)
                transcript.append({'input': text, 'narration': response['state']['narration'],
                                   'objectives': response['state']['objectives']})
                lines = engine.world['systems']['lore']['lines']
                bound = [line for line in lines.values() if line.get('objective', {}).get('kind') == 'deliver']
                assert len(bound) == 1, f'stage {i}: exactly one explicit delivery quest expected'
                line = bound[0]
                if i < 3:
                    assert line['state'] == '明'
                    assert engine.world['systems']['ontology'].neighbors('medicine', 'held_by', 1) == ['protagonist']
                else:
                    assert line['state'] == '了结'
                    assert line['resolved']['resolution']['timing'] == 'late'
                    assert engine.world['systems']['ontology'].neighbors('medicine', 'held_by', 1) == ['apothecary']
            assert len(probe.responses) == probe.index
            saved_lore = copy.deepcopy(engine.world['systems']['lore'])
            engine.store.close()
            engine = build_engine(tmp, provider=probe, embedder=None)
            dashboard = PlayerDashboard(engine, strategy=HybridStrategy())
            assert engine.world['systems']['lore'] == saved_lore
            duplicate = dashboard.action(player_input=actions[3], action_id='native-3', expected_revision=delivery_revision)
            assert duplicate['duplicate']
            dashboard.undo(expected_revision=dashboard.state()['revision'])
            dashboard.undo(expected_revision=dashboard.state()['revision'])
            assert engine.world['systems']['ontology'].neighbors('medicine', 'held_by', 1) == ['protagonist']
            assert any(line['state'] == '明' for line in engine.world['systems']['lore']['lines'].values())
            assert probe.index == len(probe.responses), 'reopen/duplicate/undo must not request model'
            write(args.directory / 'result.json', {
                'generation': 'native gpt-6.1-terra, raw responses supplied externally',
                'transport': 'exact production-request replay; no remote HTTP API invocation',
                'fixture': 'synthetic public foreground play; unrelated background LLM hooks disabled; run_lore active',
                'request_count': probe.index,
                'requests_sha256': hashlib.sha256((args.directory / 'requests.json').read_bytes()).hexdigest(),
                'responses_sha256': hashlib.sha256((args.directory / 'responses.json').read_bytes()).hexdigest(),
                'checks': ['accept', 'overdue_remains_active', 'unspoken_claim_not_delivery', 'actual_late_delivery',
                           'next_turn_continuity', 'cold_reopen', 'duplicate_no_model', 'undo_restores_item_and_quest'],
                'transcript': transcript, 'saved_lore': saved_lore})
            (args.directory / 'pending.json').unlink(missing_ok=True)
            print(f'PASS: {len(actions)} actions, {probe.index} exact native responses')
        except Pending:
            print(f'Pending native response {probe.index - 1}; read pending.json')
        finally:
            engine.store.close()


if __name__ == '__main__':
    main()
