from kernel.registry import Registry
from kernel.contextsystem import ContextSystem, Fragment
from kernel.assembler import assemble, render
from tests.kernel.fakes import FakeNoteSystem


class _Stable(ContextSystem):
    name = "rules"
    def inject(self, scene, world):
        return Fragment("rules", "stable", "宪法", affordance="")


def test_assemble_orders_layers_stable_first():
    r = Registry().register(FakeNoteSystem()).register(_Stable())
    world = {"systems": {"notes": {"notes": ["n1"]}}}
    frags = assemble(r, scene={}, world=world)
    assert [f.layer for f in frags] == ["stable", "scene"]
    assert frags[0].system == "rules"


def test_systems_returning_none_are_skipped():
    r = Registry().register(ContextSystem())  # base inject() -> None
    assert assemble(r, scene={}, world={}) == []


def test_render_emits_layer_headers_and_affordances():
    r = Registry().register(FakeNoteSystem())
    world = {"systems": {"notes": {"notes": ["n1"]}}}
    text = render(assemble(r, scene={}, world=world))
    assert "Notes: n1" in text and "记一条便签" in text
