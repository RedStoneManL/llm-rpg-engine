"""Capture/replay native model recap requests on disposable, synthetic history.

prepare emits the exact production messages using a capture-only placeholder.
verify requires native responses (JSON array of raw response strings), compares
every request, and checks actual SQLite publication, provenance and cold reopen.
Neither mode makes network calls or loads an embedder. Not a remote API test.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import build_engine
from kernel.events import kernel_event
from kernel.projection import project
from loop.fleet import digest_fleet
from systems.narrative import recap_source_ref, summary_source_ref


SUMMARIES = [
    '第1天，阿岚答应第4天黄昏前把封好的信送到北桥。未承诺替人还债。',
    '第1天，守桥人说桥税可能明日改为三枚铜钱；主角尚未核实，不是已生效的世界事实。',
    '第2天，主角与阿岚和解，但阿岚仍不愿透露信的收件人。',
    '第2天，旧桥封闭，送信路线未定，封信未拆。',
    '第3天，主角找到渡船，船主要求两枚铜钱；尚未付款或上船。',
    '第3天，阿岚请求保密，送信期限仍为第4天黄昏，任务尚未完成。',
    '第3天，主角在码头等待，未向守桥人确认税额。',
]
RAW = [
    '第3天午后，船主说：“两枚铜钱，不赊账。”主角查看空钱袋，没有付款。'
    '阿岚站在岸上，封信仍在他手中。两人还没有上船，送信期限仍是第4天黄昏。',
    '第3天傍晚，主角在码头找短工。',
    '第3天夜，主角留在码头，渡船尚未启程。',
]


class Probe:
    def __init__(self, requests=None, responses=None):
        self.requests = requests
        self.responses = responses
        self.seen = []

    def complete_messages(self, messages, **kwargs):
        request = {'messages': copy.deepcopy(messages), 'kwargs': kwargs}
        i = len(self.seen)
        self.seen.append(request)
        if self.responses is None:
            return '{"summary":"CAPTURE_ONLY_PLACEHOLDER"}'
        assert request == self.requests[i], 'native request differs from capture'
        return self.responses[i]


def seed(engine):
    for i, text in enumerate(SUMMARIES + RAW):
        scene = f'native-{i}'
        engine.store.append(kernel_event(
            'narration_recorded', day=3, scene=scene, turn=i + 1,
            summary='synthetic public narration', deltas={'scene': scene, 'text': text}))
        if i < len(SUMMARIES):
            ns = project(engine.registry, engine.store.iter_events())['systems']['narrative']
            engine.store.append(kernel_event(
                'scene_summarized', day=3, scene=scene, turn=i + 1,
                summary='human-authored synthetic summary', deltas={
                    'scene': scene, 'summary': text,
                    'source': summary_source_ref(ns['scenes'][-1])}))
    engine.world = project(engine.registry, engine.store.iter_events())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'verify'])
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    request_path = args.directory / 'requests.json'
    response_path = args.directory / 'responses.json'
    probe = (Probe() if args.mode == 'prepare' else
             Probe(json.loads(request_path.read_text()), json.loads(response_path.read_text())))
    with tempfile.TemporaryDirectory(prefix='rpg-native-recap-') as tmp:
        engine = build_engine(tmp, provider=probe, embedder=None)
        try:
            seed(engine)
            initial_history = list(engine.store.iter_events())
            result = digest_fleet(engine.registry, engine.store, [], engine.world,
                                  provider=probe, recap_provider=probe)
            assert len(probe.seen) == 2
            assert [e['type'] for e in result] == ['scene_summarized', 'recap_recompressed']
            assert result.recap_status == 'saved'
            if args.mode == 'prepare':
                request_path.write_text(json.dumps(probe.seen, ensure_ascii=False, indent=2) + '\n')
                print('Captured 2 production requests; placeholders are not model evidence.')
                return
            assert len(probe.responses) == 2
            assert all(isinstance(r, str) for r in probe.responses)
            assert 'CAPTURE_ONLY' not in str(result)
            source_world = project(engine.registry, initial_history + [result[0]])
            assert result[1]['deltas']['source'] == recap_source_ref(
                source_world['systems']['narrative'], 6)
            expected = project(engine.registry, engine.store.iter_events())['systems']['narrative']
            engine.store.close()
            engine = build_engine(tmp, provider=probe, embedder=None)
            assert engine.world['systems']['narrative'] == expected
            revision = engine.store.revision
            again = digest_fleet(engine.registry, engine.store, [], engine.world,
                                 provider=probe, recap_provider=probe)
            assert again == [] and again.recap_status == 'not_due'
            assert engine.store.revision == revision and len(probe.seen) == 2
            report = {
                'generation': 'native gpt-6-luna / medium, supplied externally',
                'transport': 'exact production-request replay; no remote API invocation',
                'fixture': 'human-authored synthetic history, no user campaign data',
                'requests_sha256': hashlib.sha256(request_path.read_bytes()).hexdigest(),
                'responses_sha256': hashlib.sha256(response_path.read_bytes()).hexdigest(),
                'request_count': 2, 'repair_count': 0,
                'recap_status': result.recap_status,
                'published': [{'type': e['type'], 'deltas': e['deltas']} for e in result],
                'checks': ['exact_messages', 'source_chain', 'sqlite_publish', 'cold_reopen',
                           'no_repeat_job_or_write'],
            }
            (args.directory / 'result.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({k: v for k, v in report.items() if k != 'published'}, ensure_ascii=False))
        finally:
            engine.store.close()


if __name__ == '__main__':
    main()
