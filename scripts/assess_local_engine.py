"""Reproducible local assessment on disposable synthetic campaigns only.

No model/API/embedding calls. Timings are this machine's microbenchmarks, not
service SLOs, semantic quality scores or a million-turn endurance claim.
"""
from __future__ import annotations

import argparse
import cProfile
import inspect
import json
from pathlib import Path
import platform
import pstats
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import build_engine
from app.player_recall import player_recall
from context.assembler import assemble_context
from engine.archive import ArchiveStore
from engine.recall import recall as archive_recall
from engine.vectorstore import VectorStore
from kernel.events import kernel_event
from kernel.projection import project
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from llm.tools import build_tool_registry
from loop.strategy import HybridStrategy
from loop.turn import REQUIRED_SECTIONS, run_turn


class Prepared:
    def produce(self, *args, **kwargs):
        return TurnCommit('主角停在码头，等待消息。', {
            'moves': [], 'places': [], 'cast': [], 'facts': [],
            'clock': [{'advance': False, 'days': 0, 'bands': 0, 'reason': '片刻'}],
        })


def timed(fn, repeats=3):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return {'median_ms': round(statistics.median(samples), 3),
            'samples_ms': [round(s, 3) for s in samples]}


def seed(engine, n):
    events = [
        kernel_event('entity_created', day=1, scene='seed', turn=0, summary='hero',
                     deltas={'id': 'protagonist', 'etype': 'Person', 'tier': 'tracked'}),
        kernel_event('entity_created', day=1, scene='seed', turn=0, summary='dock',
                     deltas={'id': 'dock', 'etype': 'Place', 'tier': 'tracked',
                             'attrs': {'level': 3, 'kind': 'venue', 'seed': '码头'}}),
        kernel_event('relation_added', day=1, scene='seed', turn=0, summary='at dock',
                     deltas={'src': 'protagonist', 'rel': 'located_in', 'dst': 'dock'}),
    ]
    for i in range(n):
        text = f'原文定位词{i}。' + '主角走过码头，雨水落在石阶上，送信的承诺尚未完成。' * 12
        events.append(kernel_event(
            'narration_recorded', day=1, scene=f'visit-{i}', turn=i + 1,
            summary='synthetic public narration', deltas={'scene': f'visit-{i}', 'text': text}))
    engine.store.append_many(events)
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world['_revision'] = engine.store.revision


def bench(root, n):
    provider = FakeLLMProvider(json_responses=[{'summary': '主角仍在码头，送信未完成。'}] * 20)
    engine = build_engine(root / f'campaign-{n}', provider=provider, embedder=None)
    try:
        seed(engine, n)
        scene = {'id': f'visit-{n-1}', 'protagonist': 'protagonist', 'present': [],
                 'day': 1, 'location': 'dock'}
        def snapshot_project():
            _, events = engine.store.snapshot()
            return project(engine.registry, events)
        def reopen():
            other = build_engine(root / f'campaign-{n}', provider=provider, embedder=None)
            other.store.close()
        count = 0
        def turn():
            nonlocal count
            count += 1
            result = run_turn(
                engine.registry, engine.store, engine.world, scene, '等消息',
                strategy=Prepared(), provider=provider, required_sections=REQUIRED_SECTIONS,
                action_id=f'profile-{n}-{count}')
            engine.world = result.world
        context = assemble_context(engine.registry, engine.world, scene)
        row = {'narration_events': n, 'initial_events': n + 3,
               'unsummarized_context_chars': len(context),
               'player_recall_raw_keyword_hits': len(player_recall(
                   engine.registry, engine.world, scene, '原文定位词0')),
               'snapshot_project': timed(snapshot_project),
               'context': timed(lambda: assemble_context(engine.registry, engine.world, scene)),
               'player_recall': timed(lambda: player_recall(engine.registry, engine.world, scene, '送信')),
               'cold_reopen': timed(reopen), 'prepared_run_turn': timed(turn)}
        profiler = cProfile.Profile()
        profiler.runcall(turn)
        stats = pstats.Stats(profiler)
        tops = sorted(stats.stats.items(), key=lambda x: x[1][3], reverse=True)[:15]
        row['turn_profile_top_cumulative'] = [
            {'function': f'{Path(k[0]).name}:{k[1]}:{k[2]}', 'primitive_calls': v[0],
             'total_calls': v[1], 'self_seconds': round(v[2], 6),
             'cumulative_seconds': round(v[3], 6)} for k, v in tops]
        with ArchiveStore(root / f'archive-{n}.db') as archive:
            for i in range(n):
                archive.add_chunk(day=1, scene=f's{i}', turn=i, text=f'记录{i}：送信未完成。')
            row['archive_fts'] = timed(lambda: archive.fts_search('送信未完成', k=5))
        with VectorStore(root / f'vectors-{n}.db') as vectors:
            for i in range(n):
                vectors.add(f'v{i}', [float((i + j) % 17) for j in range(64)])
            row['vector_64d_synthetic'] = timed(lambda: vectors.search([1.0] * 64, k=5))
        print(json.dumps({k: v for k, v in row.items() if k != 'turn_profile_top_cumulative'}, ensure_ascii=False), flush=True)
        return row
    finally:
        engine.store.close()


def gaps(root):
    with ArchiveStore(root / 'collision.db') as archive:
        first = archive.add_chunk(day=1, scene='inn', turn=7, text='OLD_BRANCH_QUOTE')
        second = archive.add_chunk(day=2, scene='inn', turn=7, text='NEW_BRANCH_QUOTE')
        collision = {'same_id': first == second, 'old_handle_now_reads': archive.get_chunk(first)['text']}
        wildcard = {'query': '%', 'hits': len(archive.fts_search('%'))}
    with ArchiveStore(root / 'filtered' / 'archive.db') as archive:
        allowed = archive.add_chunk(day=1, scene='allowed', turn=1, text='合规候选', entities=['阿岚'])
        other = archive.add_chunk(day=2, scene='other', turn=2, text='其他日其他人物', entities=['船主'])
    with VectorStore(root / 'filtered' / 'vectors.db') as vectors:
        vectors.add(allowed, [0.0, 1.0])
        vectors.add(other, [1.0, 0.0])
    class QueryVector:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]
    filtered = archive_recall(root / 'filtered', 'UNMATCHED_QUERY', k=1, day=1, entity='阿岚',
                              embedder=QueryVector())
    provider = FakeLLMProvider(responses=['你付清船费，已经乘船抵达对岸。'],
        json_responses=[{'moves': [{'who': 'protagonist', 'to': 'far_bank'}]}, {'moves': []}])
    engine = build_engine(root / 'hybrid', provider=provider, embedder=None)
    try:
        seed(engine, 1)
        scene = {'id': 'visit-0', 'protagonist': 'protagonist', 'present': [], 'day': 1, 'location': 'dock'}
        strategy = HybridStrategy()
        candidate = strategy.produce(engine.registry, engine.world, scene, '尝试渡河', provider=provider)
        repaired = strategy.produce(engine.registry, engine.world, scene, '尝试渡河', provider=provider,
                                    repair='无法付款和移动，moves 必须为空。')
        schemas = build_tool_registry(engine.registry, engine.world, scene).schemas()
        engine.store.append(kernel_event(
            'fact_asserted', day=1, scene='visit-0', turn=2, summary='synthetic secret fact',
            deltas={'subject': 'protagonist', 'predicate': 'hidden',
                    'value': 'SYNTHETIC_HIDDEN_SOURCE', 'secrecy': 'secret'}))
        engine.world = project(engine.registry, engine.store.iter_events())
        return {
            'archive_key_collision': collision, 'short_query_like_wildcard': wildcard,
            'semantic_filter_probe': {'requested_day': 1, 'requested_entity': '阿岚',
                'returned': [{'day': h['day'], 'entities': h['entities']} for h in filtered]},
            'vector_search_signature': str(inspect.signature(VectorStore.search)),
            'tool_names': [s['function']['name'] for s in schemas],
            'host_public_history_is_not_actor_acl': {
                'canonical_secret_in_host_dto': 'SYNTHETIC_HIDDEN_SOURCE' in str(engine.public_history()),
                'player_recall_hits': player_recall(engine.registry, engine.world, scene, 'SYNTHETIC_HIDDEN_SOURCE'),
                'scope': 'host helper probe; not a public API exposure claim'},
            'hybrid_strategy_only': {'initial_moves': candidate.sections['moves'],
                'repaired_moves': repaired.sections['moves'], 'repaired_narration': repaired.narration,
                'same_narration': repaired.narration == candidate.narration,
                'scope': 'strategy probe, not a published turn or real-model quality test'},
        }
    finally:
        engine.store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--sizes', nargs='+', type=int, default=[100, 1000, 5000])
    args = parser.parse_args()
    if any(n < 1 or n > 10000 for n in args.sizes):
        parser.error('sizes must be between 1 and 10000')
    with tempfile.TemporaryDirectory(prefix='rpg-assessment-') as tmp:
        root = Path(tmp)
        report = {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
            'fixtures': 'synthetic; 3 timing repeats; fourth turn cProfile; 64d deterministic vectors, no embedding',
            'scope': 'cold reopening and raw-heavy history, no real generation latency; not full mixed-world load',
            'gap_probes': gaps(root),
            'benchmarks': [bench(root, n) for n in args.sizes]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['gap_probes'], ensure_ascii=False))


if __name__ == '__main__':
    main()
