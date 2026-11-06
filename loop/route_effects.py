"""A declared, public stage consequence may change an existing local route."""
from kernel.events import kernel_event


def local_routes(graph, venues, day):
    scope = {venue for venue in venues if isinstance(venue, str)
             and (entity := graph.get_entity(venue))
             and entity.attrs.get("visibility") not in {"secret", "hidden", "undiscovered"}}
    return sorted({tuple(sorted((r.src, r.dst))) for r in graph.relations
                   if r.rel == "adjacent_to" and r.valid_at(day) and r.src in scope and r.dst in scope
                   and not r.attrs.get("discovery_clue_id")})


def allowed_effect(effect, routes):
    return (isinstance(effect, dict) and type(effect.get("passable")) is bool
            and isinstance(effect.get("a"), str) and isinstance(effect.get("b"), str)
            and tuple(sorted((effect["a"], effect["b"]))) in routes)


def apply_stage_effects(store, world, stage_events):
    graph = world.get("systems", {}).get("ontology")
    if graph is None:
        return []
    lines = world["systems"]["lore"]["lines"]
    consequences = []
    for event in sorted(stage_events, key=lambda row: row["day"]):
        if event["type"] != "lore_advanced":
            continue
        delta = event["deltas"]
        line = lines[delta["id"]]
        stages = line["stages"]
        stage = stages[delta["stage_idx"]]
        effect = stage.get("route_effect") if isinstance(stage, dict) else None
        if not isinstance(effect, dict) or not line.get("route_effects_enabled"):
            continue
        candidates = local_routes(graph, (effect.get("a"), effect.get("b")), event["day"])
        if not allowed_effect(effect, candidates):
            continue
        change = kernel_event("route_changed", day=event["day"], turn=event.get("turn"), scene=event["scene"],
                              summary="道路恢复通行" if effect["passable"] else "道路中断",
                              thread_refs=[event["id"]], deltas={**effect, "line": line["id"]})
        store.append(change)
        consequences.append(change)
    return consequences
