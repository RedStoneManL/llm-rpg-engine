from kernel.registry import Registry
from kernel.digest import digest_extract
from kernel.turncommit import TurnCommit
from tests.kernel.fakes import FakeNoteSystem


def test_digest_merges_section_decls_from_systems():
    r = Registry().register(FakeNoteSystem())
    tc = digest_extract(r, prose="你推开门走了进去", world={})
    assert isinstance(tc, TurnCommit)
    assert tc.sections["notes"] == [{"text": "你推开门走了进去"}]


def test_digest_empty_prose_yields_no_sections():
    r = Registry().register(FakeNoteSystem())
    assert digest_extract(r, prose="   ", world={}).sections == {}
