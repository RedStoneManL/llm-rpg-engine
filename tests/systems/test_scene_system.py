from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event
from systems.ontology import OntologySystem
from systems.scene import SceneSystem


def _reg():
    r = Registry()
    r.register(OntologySystem())
    r.register(SceneSystem())
    return r


def test_scene_system_owns_event_no_commit_section():
    ss = SceneSystem()
    assert ss.event_types() == {"scene_advanced"}
    assert ss.commit_sections() == set()
    assert "ontology" in ss.requires()


def test_scene_advanced_sets_meta_scene_and_counter_and_anchor():
    r = _reg()
    world = project(r, [
        kernel_event("scene_advanced", day=3, scene="s2",
                     summary="场景推进→s2",
                     deltas={"scene_id": "s2", "scene_no": 2,
                             "location": "canglang_ridge", "day": 3},
                     turn=1),
    ])
    # meta.scene flows from projection (ev["scene"]); counter+anchor from apply
    assert world["meta"]["scene"] == "s2"
    assert world["meta"]["scene_no"] == 2
    assert world["meta"]["scene_anchor"] == {"location": "canglang_ridge", "day": 3}


def test_scene_advanced_sequence_keeps_latest():
    r = _reg()
    world = project(r, [
        kernel_event("scene_advanced", day=1, scene="s2",
                     deltas={"scene_id": "s2", "scene_no": 2, "location": "a", "day": 1},
                     summary="→s2", turn=1),
        kernel_event("scene_advanced", day=2, scene="s3",
                     deltas={"scene_id": "s3", "scene_no": 3, "location": "b", "day": 2},
                     summary="→s3", turn=2),
    ])
    assert world["meta"]["scene"] == "s3"
    assert world["meta"]["scene_no"] == 3
    assert world["meta"]["scene_anchor"] == {"location": "b", "day": 2}
