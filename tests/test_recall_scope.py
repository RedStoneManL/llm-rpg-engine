"""Legacy hybrid lookup must use one exact scope before ranking either path.

Entity/day are query filters, not actor authorization. Explicit vectors below
test filtering and nearest-neighbour ordering, not embedding semantics.
"""
import pytest

from engine.archive import ArchiveStore
from engine.recall import recall
from engine.vectorstore import VectorStore


class QueryVector:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


def seed(path):
    with ArchiveStore(path / 'archive.db') as archive:
        ids = []
        for i, (day, entities) in enumerate([(1, ['阿岚']), (2, ['阿岚']), (1, ['船主'])]):
            ids.append(archive.add_chunk(day=day, scene=f's{i}', turn=i + 1,
                                        text=f'合成原文{i}', entities=entities))
    with VectorStore(path / 'vectors.db') as vectors:
        vectors.add(ids[0], [0.1, 1.0])
        vectors.add(ids[1], [1.0, 0.0])
        vectors.add(ids[2], [0.9, 0.1])
    return ids


@pytest.mark.parametrize('filters, expected_index', [
    ({'day': 1}, 2), ({'entity': '阿岚'}, 1), ({'day': 1, 'entity': '阿岚'}, 0),
])
def test_semantic_scope_is_applied_before_top_k_and_survives_reopen(tmp_path, filters, expected_index):
    ids = seed(tmp_path)
    # recall reopens both actual SQLite stores each time; FTS has no matches.
    for _ in range(2):
        hits = recall(tmp_path, 'UNMATCHED_QUERY', k=1, embedder=QueryVector(), **filters)
        assert [h['chunk_id'] for h in hits] == [ids[expected_index]]


@pytest.mark.parametrize('filters', [{'day': 99}, {'entity': '陌生人'}, {'day': 2, 'entity': '船主'}])
def test_empty_scope_never_falls_back_to_unfiltered_vectors(tmp_path, filters):
    seed(tmp_path)
    embedder = QueryVector()
    assert recall(tmp_path, 'UNMATCHED_QUERY', embedder=embedder, **filters) == []
    assert embedder.calls == 0


def test_vector_allowlist_filters_before_loading_dimensions_and_scoring(tmp_path):
    with VectorStore(tmp_path / 'vectors.db') as vectors:
        vectors.add('disallowed-perfect', [1.0, 0.0])
        vectors.add('disallowed-wrong-dimension', [1.0])
        vectors.add('allowed', [0.1, 1.0])
        assert vectors.search([1.0, 0.0], k=1, allowed_ids={'allowed'})[0][0] == 'allowed'
        assert vectors.search([1.0, 0.0], k=1, allowed_ids=set()) == []
        assert vectors.search([1.0, 0.0], k=1, allowed_ids={'missing'}) == []


def test_vector_scope_larger_than_one_sql_batch(tmp_path):
    with VectorStore(tmp_path / 'vectors.db') as vectors:
        vectors.add('outside', [1.0, 0.0])
        vectors.add('late-1200', [0.1, 1.0])
        ids = {f'candidate-{i}' for i in range(1200)} | {'late-1200'}
        assert vectors.search([1.0, 0.0], k=1, allowed_ids=ids)[0][0] == 'late-1200'


@pytest.mark.parametrize('scope', ['allowed', [None], [3]])
def test_invalid_vector_scope_is_rejected(tmp_path, scope):
    with VectorStore(tmp_path / 'vectors.db') as vectors:
        with pytest.raises((TypeError, ValueError)):
            vectors.search([1.0, 0.0], allowed_ids=scope)


@pytest.mark.parametrize('entity', ['A%', 'A_', 'A"Q', r'A\B', '阿岚', ''])
@pytest.mark.parametrize('query', ['线', '公开线索'])
def test_fts_and_candidate_scope_use_exact_json_entity_membership(tmp_path, entity, query):
    with ArchiveStore(tmp_path / 'archive.db') as archive:
        wanted = archive.add_chunk(day=1, scene='wanted', turn=1, text='公开线索甲', entities=[entity])
        archive.add_chunk(day=1, scene='other', turn=2, text='公开线索乙', entities=['AX', f'前缀{entity}后缀'])
        archive.add_chunk(day=2, scene='later', turn=3, text='公开线索丙', entities=[entity])
        assert [h['chunk_id'] for h in archive.fts_search(query, k=1, entity=entity, day=1)] == [wanted]
        assert [h['chunk_id'] for h in archive.iter_chunks(entity=entity, day=1)] == [wanted]


@pytest.mark.parametrize('literal', ['%', '_', '\\'])
def test_short_fts_queries_treat_like_metacharacters_as_literal(tmp_path, literal):
    with ArchiveStore(tmp_path / 'archive.db') as archive:
        wanted = archive.add_chunk(day=1, scene='wanted', turn=1, text=f'原话包含{literal}符号')
        archive.add_chunk(day=2, scene='other', turn=2, text='没有该符号的原话')
        assert [h['chunk_id'] for h in archive.fts_search(literal)] == [wanted]


def test_hybrid_paths_share_scope_and_preserve_fts_when_vector_fails(tmp_path):
    ids = seed(tmp_path)
    class BrokenEmbedder:
        def embed(self, texts):
            raise RuntimeError('offline failure')
    for embedder in (QueryVector(), BrokenEmbedder()):
        hits = recall(tmp_path, '合成原文', day=1, entity='阿岚', embedder=embedder)
        assert [h['chunk_id'] for h in hits] == [ids[0]]


def test_deleted_archive_candidate_cannot_displace_valid_semantic_match(tmp_path):
    ids = seed(tmp_path)
    with ArchiveStore(tmp_path / 'archive.db') as archive:
        archive.delete_from_turn(2)  # vector rows intentionally remain stale
    assert [h['chunk_id'] for h in recall(tmp_path, 'UNMATCHED_QUERY', k=1, embedder=QueryVector())] == [ids[0]]


def test_semantic_candidate_text_cannot_retarget_during_embedding(tmp_path):
    ids = seed(tmp_path)
    class ReplacingEmbedder(QueryVector):
        def embed(self, texts):
            with ArchiveStore(tmp_path / 'archive.db') as archive:
                archive.add_chunk(day=2, scene='s0', turn=1, text='REPLACEMENT_OTHER_SCOPE', entities=['船主'])
            return super().embed(texts)
    hits = recall(tmp_path, 'UNMATCHED_QUERY', k=1, day=1, entity='阿岚', embedder=ReplacingEmbedder())
    assert [h['chunk_id'] for h in hits] == [ids[0]]
    assert hits[0]['text'] == '合成原文0'
    assert hits[0]['day'] == 1 and hits[0]['entities'] == ['阿岚']
    # This is an in-call candidate snapshot, not a durable history identity.
    with ArchiveStore(tmp_path / 'archive.db') as archive:
        assert archive.get_chunk(ids[0])['text'] == 'REPLACEMENT_OTHER_SCOPE'
