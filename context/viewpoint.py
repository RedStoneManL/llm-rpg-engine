"""context.viewpoint — POV / guardrail / NPC viewpoint bundler.

build_viewpoint(graph, *, protagonist, present, day, candidate_fact_keys) -> dict

Returns a dict with three keys:
  pov:       {fact_key: value}  — facts the protagonist KNOWS (writable lens)
  guardrail: {fact_key: truth}  — facts protagonist does NOT know but are true in graph
                                  ("constrain, never reveal"; tagged downstream)
  npc:       {npc_id: {fact_key: value}}  — what each present NPC knows about candidates

Semantics:
  - A fact is "known" by an agent if knows(graph, agent, fact_key, day) is not None.
  - Ground-truth for guardrail lookup: interpret fact_key as "subject.predicate"
    (split on first dot). Fact keys without a dot are skipped for guardrail.
  - Present members excluding the protagonist generate npc bundles.
"""
from __future__ import annotations

from facts.graph import FactGraph
from systems.knowledge import knows
from engine.log import get_logger

log = get_logger("context.viewpoint")


def build_viewpoint(
    graph: FactGraph,
    *,
    protagonist: str,
    present: list[str],
    day: int,
    candidate_fact_keys: list[str],
) -> dict:
    """Build the POV/guardrail/NPC viewpoint bundle for one scene turn.

    Args:
        graph:               Shared FactGraph (world["systems"]["ontology"]).
        protagonist:         Entity id of the protagonist (player character).
        present:             List of entity ids currently in the scene.
        day:                 Current game day (for bitemporal lookups).
        candidate_fact_keys: Fact keys (e.g. "桥.status") to consider for the bundle.

    Returns:
        dict with keys:
          "pov":       {fact_key: believed_value}  protagonist knows
          "guardrail": {fact_key: true_value}       unknown to protagonist but exists in graph
          "npc":       {npc_id: {fact_key: value}}  per-NPC knowledge of candidates
    """
    pov: dict[str, object] = {}
    guardrail: dict[str, object] = {}
    npc: dict[str, dict[str, object]] = {}

    for fk in candidate_fact_keys:
        protagonist_belief = knows(graph, protagonist, fk, day)

        if protagonist_belief is not None:
            # Protagonist knows this fact
            pov[fk] = protagonist_belief
        else:
            # Protagonist does NOT know — check if ground truth exists in graph
            truth = _ground_truth(graph, fk, day)
            if truth is not None:
                guardrail[fk] = truth

    # Build NPC bundles for present members excluding the protagonist
    for member in present:
        if member == protagonist:
            continue
        member_knowledge: dict[str, object] = {}
        for fk in candidate_fact_keys:
            val = knows(graph, member, fk, day)
            if val is not None:
                member_knowledge[fk] = val
        npc[member] = member_knowledge

    log.debug(
        "build_viewpoint protagonist=%s day=%d pov=%d guardrail=%d npc_members=%d",
        protagonist, day, len(pov), len(guardrail), len(npc),
    )

    return {"pov": pov, "guardrail": guardrail, "npc": npc}


def _ground_truth(graph: FactGraph, fact_key: str, day: int) -> object | None:
    """Look up the ground truth value for a fact_key in the graph.

    Interprets fact_key as "subject.predicate" (split on first dot).
    Returns None if fact_key has no dot or the value is absent.
    """
    if "." not in fact_key:
        return None
    subject, predicate = fact_key.split(".", 1)
    return graph.value_at(subject, predicate, day)
