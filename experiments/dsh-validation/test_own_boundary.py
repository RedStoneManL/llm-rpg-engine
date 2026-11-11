"""Apply the same hidden-truth counterfactual to our actual POV/tool boundary."""
import json

import pytest

from app.engine import build_engine
from context.access import pov_world
from context.assembler import assemble_context
from kernel.events import kernel_event
from kernel.projection import project
from kernel.recall import recall
from llm.tools import build_tool_registry


def ev(kind, **deltas):
    return kernel_event(kind, day=1, scene="inn", turn=0, summary="fixture", deltas=deltas)


@pytest.mark.parametrize("with_belief", [False, True])
def test_hidden_truth_change_does_not_change_visible_context_or_recall(tmp_path, with_belief):
    results, unscoped = [], []
    for i, truth in enumerate(["去河边散步", "寻找银月密钥 HIDDEN_CANARY"]):
        engine = build_engine(tmp_path / str(i))
        try:
            events = [
                ev("entity_created", id="hero", etype="Person", tier="tracked"),
                ev("entity_created", id="inn", etype="Place", tier="tracked",
                   attrs={"level": 3, "kind": "venue", "seed": "客栈"}),
                ev("entity_created", id="npc", etype="Person", tier="tracked"),
                ev("relation_added", src="hero", rel="located_in", dst="inn"),
                ev("relation_added", src="npc", rel="located_in", dst="inn"),
                ev("fact_asserted", subject="npc", predicate="sketch", value="穿灰衣的掌柜", secrecy="public"),
                ev("fact_asserted", subject="npc", predicate="goal", value=truth, secrecy="secret"),
            ]
            if with_belief:
                events.append(ev("knowledge_set", knower="hero", fact_key="npc.goal",
                                 value="掌柜想喝茶", via="heard"))
            engine.store.append_many(events)
            engine.world = project(engine.registry, engine.store.iter_events())
            scene = {"id": "s", "day": 1, "protagonist": "hero", "location": "inn", "present": ["hero", "npc"]}
            query = "银月密钥"
            tools = build_tool_registry(engine.registry, engine.world, scene)
            results.append({
                "context": assemble_context(engine.registry, engine.world, scene, query=query),
                "tool": json.loads(tools.execute("recall_query", {"q": query})),
                "hits": [h.text for h in recall(engine.registry, query, pov_world(engine.world, scene))],
            })
            unscoped.append([h.text for h in recall(engine.registry, query, engine.world)])
        finally:
            engine.store.close()
    assert results[0] == results[1]
    assert "HIDDEN_CANARY" not in json.dumps(results, ensure_ascii=False)
    if with_belief:
        assert "掌柜想喝茶" in results[0]["context"]
    assert unscoped[0] != unscoped[1], "Negative control must detect bypassed POV projection"
