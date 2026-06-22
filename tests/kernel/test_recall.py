from kernel.registry import Registry
from kernel.contextsystem import ContextSystem, RecallHit
from kernel.recall import recall
from tests.kernel.fakes import FakeNoteSystem


class _Other(ContextSystem):
    name = "other"
    def recall(self, query, world):
        return [RecallHit("other", 0.5, "low"), RecallHit("other", 2.0, "high")]


def test_recall_fans_out_and_sorts_by_score_desc():
    r = Registry().register(FakeNoteSystem()).register(_Other())
    world = {"systems": {"notes": {"notes": ["匹配的门", "无关"]}}}
    hits = recall(r, query="门", world=world)
    assert hits[0].score == 2.0
    assert any(h.system == "notes" and "门" in h.text for h in hits)


def test_recall_k_truncates():
    r = Registry().register(_Other())
    assert len(recall(r, query="x", world={}, k=1)) == 1
