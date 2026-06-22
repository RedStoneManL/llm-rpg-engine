"""loop.cascade — post-turn world-cascade hook (Phase C1 + C2).

run_cascade(registry, store, world, *, scene, provider, cascade_provider=None,
            max_subagents=4, max_concurrency=None) -> list[dict]
    1. Derive the last non-harness player turn from the event stream.
    2. Compute trigger roots via cascade_trigger (D1 predicate).
    3. BFS-walk the containment subtree of each root (vertical descent).
       Each visited child Place gets ONE cheap LLM call via _node_verdict.
       Budget cap = CASCADE_BREADTH nodes per level/round.
    4. Lightweight-validate each verdict (referential, drop-on-fail, no repair).
    5. On evolve:true → append place_evolved (+ populace_shifted if mood given).
       On evolve:false → record roll-up note, stop descending that subtree.
    6. Append events through the strict store, return appended list.

C1: BLOCKING, vertical descent only. No horizontal axis, no async queue.
C2 (Task 8): parallel fan-out of _node_verdict via ThreadPoolExecutor,
    configurable max_concurrency (env RPG_CASCADE_CONCURRENCY > max_subagents > 3).
    Results are collected then mutated sequentially in the main thread.
    FakeLLMProvider's call counter is never relied on for response→node assignment (D5).

Hook shape mirrors loop.director: post-apply, tracer span in caller,
never fatal, re-project on append.
"""
from __future__ import annotations

import os
import concurrent.futures

from kernel.events import kernel_event
from memory.importance import heuristic_floor
from engine.log import get_logger
from llm.structured import complete_structured

log = get_logger("loop.cascade")

# ---------------------------------------------------------------------------
# Module-level constants (referenced via `cmod.CASCADE_BREADTH` in tests)
# ---------------------------------------------------------------------------

# Breadth cap: max nodes processed per descent level per run_cascade call.
# ADDENDUM override: the human specified per-round breadth cap, not a global
# total. We apply this cap at each BFS frontier level.
CASCADE_BREADTH: int = 6

# Importance floor for a trigger event (heuristic_floor must reach this).
# NB: world_change has base=1 and gets +1 delta bonus → score 2.
# CASCADE_FLOOR=2 keeps them as triggers while bare action
# (base=1, no deltas, score=1) stays silent.
CASCADE_FLOOR: int = 2

# P1: secondary ("keep_spreading") spread is at most ONE hop and bounded in
# breadth by this constant (replaces the removed depth-3 horizontal chain and
# CASCADE_MAX_REGIONS root-spread cap).
CASCADE_SECONDARY_BREADTH: int = 3

# Per-turn total node budget (C2 Task 10). Caps the TOTAL number of _node_verdict
# calls per run_cascade invocation across drain-at-start + new-trigger descent.
# The per-round CASCADE_BREADTH=6 still caps each frontier; CASCADE_NODE_BUDGET=12
# is the envelope across ALL rounds this turn.
CASCADE_NODE_BUDGET: int = 12

# ---------------------------------------------------------------------------
# Concurrency helpers (C2 — Task 8)
# ---------------------------------------------------------------------------

def _resolve_concurrency(explicit: int | None, max_subagents: int | None) -> int:
    """Resolve the ThreadPoolExecutor max_workers for this run_cascade call.

    Precedence: explicit arg > env RPG_CASCADE_CONCURRENCY > max_subagents > 3.
    All values are clamped to >= 1.
    """
    if explicit is not None:
        return max(1, int(explicit))
    env_val = os.environ.get("RPG_CASCADE_CONCURRENCY")
    if env_val is not None:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass  # fall through to max_subagents
    if max_subagents is not None:
        return max(1, int(max_subagents))
    return 3


# Harness-authored event types that should NOT count as player-turn events
# when deriving the trigger window.
# NOTE: world_change is deliberately excluded here — it can be emitted by
# the narrator/player layer as a significant event that seeds the cascade,
# so it MUST be included in the trigger window detection. Only the OUTPUTS
# of a cascade run (place_evolved, populace_shifted) and director outputs
# are excluded from the player-turn window.
_HARNESS_TYPES: frozenset[str] = frozenset({
    "place_evolved",
    "populace_shifted",
    "oracle_roll",
    "director_fired",
    "character_evolved",
    "thread_open",
    "thread_advance",
    # P2 backstage/derived events: digest_fleet appends these at turns HIGHER than
    # the player turn, so they MUST be excluded from the player-turn window — else
    # narration_recorded shadows the player turn and cascade_trigger looks at the
    # wrong turn, missing the narrator's world_change (live-caught: a capstone run
    # declared world-changes but cascade emitted 0 place_evolved because P2's
    # narration_recorded bumped _last_nonharness_turn past the player turn).
    "narration_recorded",
    "scene_summarized",
    "recap_recompressed",
})

# Event types that can trigger a cascade. P1: ONLY the narrator-declared
# world_change (via the `world` commit section) triggers — movement and
# place creation no longer self-trigger (the narrator decides scope/spread).
_TRIGGER_TYPES: frozenset[str] = frozenset({"world_change"})

# ---------------------------------------------------------------------------
# LLM prompt / schema for per-node verdict
# ---------------------------------------------------------------------------

_NODE_SYSTEM = """\
你是 TRPG 的世界引擎。父地点刚发生了一件大事，请判断这个邻近的子地点受到怎样的影响。
只输出一个 JSON 对象（不要任何散文/代码块）。其中 state（地点的新状态）与 populace_mood
（当地民众的情绪）必须用【中文】填写，简洁、具体、有画面感。"""

_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "id":            {"type": "string"},
        "evolve":        {"type": "boolean"},
        "state":         {"type": "string"},
        "populace_mood": {"type": "string"},
        "note":          {"type": "string"},
        # P1: optional at-most-one-hop secondary spread. The node may name
        # adjacent area ids the event keeps spreading to (the LAST outward ring).
        "keep_spreading": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["evolve"],
}


def _node_prompt(place_id: str, context: str) -> str:
    """Build the user prompt for a single node verdict.

    The place_id is embedded verbatim so a KeyedFakeProvider (DD5) and the
    ThreadPoolExecutor workers key their response by place_id without relying
    on call order. `keep_spreading` is the optional at-most-one-hop secondary
    spread (see DD3): the LAST outward ring, never descended further than once.
    """
    return (
        f"子地点 id：{place_id}\n"
        f"上级发生的事件：{context}\n\n"
        f"地点「{place_id}」是否因此发生变化(evolve)？"
        f"若是(evolve:true)，给出 state（新状态，中文）与/或 populace_mood（民众情绪，中文）。"
        f"若否(evolve:false)，则不再向下传播。"
        f"如果这场变故剧烈到足以越境波及紧邻的地区，可在 keep_spreading 中列出那些相邻地点的 id"
        f"（这是最后一圈外扩，系统不会再从那里继续蔓延）。无需外扩时省略 keep_spreading。"
    )


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _is_place(graph, pid: str) -> bool:
    """True if pid resolves to a Place entity in the graph."""
    e = graph.get_entity(pid)
    return e is not None and e.etype == "Place"


def _root_place(event: dict, graph) -> str | None:
    """Extract the root Place id from a trigger event, or None."""
    d = event.get("deltas", {})
    for key in ("place", "id", "to"):
        pid = d.get(key)
        if pid and _is_place(graph, pid):
            return pid
    return None


def _children(graph, parent: str, day: int) -> list[str]:
    """Return Place ids whose contained_by relation points to parent at day."""
    result = []
    for r in graph.relations:
        if r.rel == "contained_by" and r.dst == parent and r.valid_at(day):
            if _is_place(graph, r.src):
                result.append(r.src)
    return result


def _adjacent_regions(graph, place_id: str, day: int) -> list[str]:
    """Return de-duped adjacent Place ids for the horizontal chain (C2 Task 9).

    Adjacency = explicit adjacent_to neighbors of place_id (via place_linked events)
              UNION same-contained_by-parent siblings (excluding self)
              UNION parent's own adjacent_to neighbors (the containing region's neighbors).

    Explicit neighbors come first, then parent's neighbors, then siblings.
    De-duped, _is_place-filtered, self excluded.
    This matches the spec: we use the real relation, not an invented one.
    """
    seen: set[str] = {place_id}
    result: list[str] = []

    # 1. Explicit adjacent_to neighbors of this place
    for neighbor in graph.neighbors(place_id, "adjacent_to", day):
        if neighbor not in seen and _is_place(graph, neighbor):
            seen.add(neighbor)
            result.append(neighbor)

    # 2. Same-parent siblings + parent's adjacent_to neighbors
    for r in graph.relations:
        if r.rel == "contained_by" and r.src == place_id and r.valid_at(day):
            parent = r.dst
            # 2a. Parent's own adjacent_to neighbors (region-level adjacency)
            for neighbor in graph.neighbors(parent, "adjacent_to", day):
                if neighbor not in seen and _is_place(graph, neighbor):
                    seen.add(neighbor)
                    result.append(neighbor)
            # 2b. Same-parent siblings (other children of the same parent)
            for sibling in _children(graph, parent, day):
                if sibling not in seen and _is_place(graph, sibling):
                    seen.add(sibling)
                    result.append(sibling)

    return result


def _merge_same_region(
    nodes: list[dict], graph, day: int
) -> list[dict]:
    """Dedupe hop-intent dicts by region (last-writer-wins) in stable first-seen order.

    Each node has keys: region, level, valence, magnitude.
    Drops entries whose region is not a Place in the graph.
    Returns the merged list preserving first-seen region order.
    """
    order: list[str] = []
    by_region: dict[str, dict] = {}
    for node in nodes:
        region = node.get("region", "")
        if not region or not _is_place(graph, region):
            continue
        if region not in by_region:
            order.append(region)
        by_region[region] = node  # last-writer-wins
    return [by_region[r] for r in order]


def _scene_subtree(graph, scene_id: str, day: int) -> set[str]:
    """Return the set of Place ids in the scene's containment subtree.

    Walks 'contained_by' UP from scene_id to the topmost ancestor (scene root),
    then collects ALL descendants downward via reverse 'contained_by'.
    Includes scene_id itself.

    Used in C2 Task 10 to distinguish local (inline) hops from remote (deferred) hops.
    §12 line 176: current-scene subtree hops are processed inline; remote hops deferred.
    """
    if not scene_id:
        return set()

    # Walk UP to root
    root = scene_id
    visited_up: set[str] = {scene_id}
    queue_up = [scene_id]
    while queue_up:
        current = queue_up.pop()
        for r in graph.relations:
            if r.rel == "contained_by" and r.src == current and r.valid_at(day):
                parent = r.dst
                if parent not in visited_up:
                    visited_up.add(parent)
                    queue_up.append(parent)
                    root = parent

    # Walk DOWN from root (collect all descendants)
    subtree: set[str] = set()
    queue_down = [root]
    while queue_down:
        current = queue_down.pop()
        subtree.add(current)
        for child in _children(graph, current, day):
            if child not in subtree:
                queue_down.append(child)

    return subtree


# ---------------------------------------------------------------------------
# Trigger predicate (D1)
# ---------------------------------------------------------------------------

def cascade_trigger(new_events: list[dict], world: dict) -> list[str]:
    """Return de-duped root Place ids that qualify for a cascade this turn.

    An event qualifies if:
      1. Its type is in _TRIGGER_TYPES  OR  deltas.get("world_change") is truthy.
      2. heuristic_floor(event) >= CASCADE_FLOOR.
      3. Its root place id resolves to an existing Place entity.

    Returns a list preserving first-seen order, de-duped.
    """
    g = world["systems"]["ontology"]
    roots: list[str] = []
    seen: set[str] = set()

    for ev in new_events:
        t = ev.get("type", "")
        d = ev.get("deltas", {})

        # C2 Task 10: self-trigger guard (load-bearing correctness requirement).
        # Cascade's OWN world_change outputs — deferral markers and consume-watermark
        # bookkeeping — must NEVER re-trigger a fresh cascade. A player/narrator
        # world_change carries NEITHER key, so it still triggers.
        if t == "world_change" and (
            d.get("deferred") or d.get("deferred_consume_through") is not None
        ):
            continue

        qualifies_type = t in _TRIGGER_TYPES
        if not qualifies_type:
            continue
        if heuristic_floor(ev) < CASCADE_FLOOR:
            continue
        pid = _root_place(ev, g)
        if pid and pid not in seen:
            roots.append(pid)
            seen.add(pid)

    log.debug("cascade_trigger → roots=%s", roots)
    return roots


# ---------------------------------------------------------------------------
# Lightweight referential validation (§12 line 177)
# ---------------------------------------------------------------------------

def lightweight_validate(verdict: object, graph, allowed_ids: set[str]) -> dict | None:
    """Referential check only — no repair loop, ever (§11 / §12 line 177).

    Returns the verdict dict if it passes, or None if it should be dropped.
    """
    if not isinstance(verdict, dict):
        log.warning("cascade verdict is not a dict; dropped")
        return None

    pid = verdict.get("id")
    if not pid:
        log.warning("cascade verdict missing id; dropped")
        return None

    if graph.get_entity(pid) is None and pid not in allowed_ids:
        log.warning("cascade verdict dangling place=%s; dropped", pid)
        return None

    return verdict


# ---------------------------------------------------------------------------
# Per-node processing (standalone function — C2 will parallelize this)
# ---------------------------------------------------------------------------

def _node_validate(obj):
    if not isinstance(obj, dict):
        return ['response must be a single JSON object']
    if not isinstance(obj.get("evolve"), bool):
        return ['missing required boolean field "evolve" (true or false)']
    if obj["evolve"] and not (
        (isinstance(obj.get("state"), str) and obj["state"].strip())
        or (isinstance(obj.get("populace_mood"), str) and obj["populace_mood"].strip())
    ):
        return ['when "evolve" is true, provide a non-empty "state" and/or "populace_mood" (Chinese)']
    return []


def _node_verdict(place_id: str, ctx: str, provider) -> dict:
    """Run one cheap LLM call for a single child place and return the raw verdict.

    This is the unit C2 will submit to ThreadPoolExecutor — no shared mutable
    state, no side effects. Returns the raw dict from complete_structured (may be
    invalid; caller must lightweight_validate).
    """
    user = _node_prompt(place_id, ctx)
    obj, errors = complete_structured(
        provider,
        system=_NODE_SYSTEM,
        user=user,
        validate=_node_validate,
        max_repairs=1,
        schema_reminder='Required: "evolve" (boolean). If true: "state" and/or "populace_mood" (Chinese). Optional: "note", "keep_spreading" (array of ids).',
        log_label="cascade",
    )
    if errors:
        log.warning("cascade: node %s did not conform: %s", place_id, "; ".join(errors)[:120])
    result = obj if (isinstance(obj, dict) and not errors) else {}
    return {**result, "id": place_id}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _last_nonharness_turn(events: list[dict]) -> int:
    """Max turn among events whose type is NOT a harness-authored type.

    This isolates the 'player' turn so cascade does not re-trigger on its own
    appended events (mirrors run_director's isolation pattern).
    """
    return max(
        (e.get("turn") or 0 for e in events if e.get("type") not in _HARNESS_TYPES),
        default=0,
    )


def _next_cascade_turn(store) -> int:
    """Max turn in the store + 1 (cascade events get their own slot)."""
    max_t = 0
    for ev in store.iter_events():
        t = ev.get("turn") or 0
        if t > max_t:
            max_t = t
    return max_t + 1


# ---------------------------------------------------------------------------
# Hop-emission helper — extracted from _vertical_bfs (C2 Task 9+10)
# Also called by the root-level spread path in run_cascade (C2 bugfix).
# ---------------------------------------------------------------------------

def _emit_hops(
    chain_targets: dict,
    graph,
    day: int,
    scene: str,
    turn: int,
    scene_subtree: set | None,
    store,
) -> list[dict]:
    """Emit at-most-one-hop secondary world_change events for keep_spreading targets.

    chain_targets: mapping region_id → {"region", "level"}.
    Applies _merge_same_region + the CASCADE_SECONDARY_BREADTH breadth cap +
    local (inline) vs remote (deferred) split via scene_subtree (§12 line 176).
    Returns list of emitted events (already appended to store).
    """
    if not chain_targets:
        return []
    appended: list[dict] = []
    merged = _merge_same_region(list(chain_targets.values()), graph, day)
    n_emitted = 0
    for hop in merged:
        if n_emitted >= CASCADE_SECONDARY_BREADTH:
            log.info(
                "cascade: secondary breadth cap %d hit; %d region(s) dropped: %s",
                CASCADE_SECONDARY_BREADTH, len(merged) - n_emitted,
                [h["region"] for h in merged[n_emitted:]],
            )
            break
        region = hop["region"]
        hop_level = hop["level"]
        is_local = (scene_subtree is None) or (region in scene_subtree)
        if is_local:
            ev_hop = kernel_event(
                "world_change", day=day, scene=scene,
                summary=f"{region} 受波及",
                deltas={"place": region, "level": hop_level, "summary": f"{region} 受波及"},
                turn=turn,
            )
        else:
            ev_hop = kernel_event(
                "world_change", day=day, scene=scene,
                summary=f"{region} 受波及",
                deltas={"place": region, "level": hop_level, "summary": f"{region} 受波及",
                        "deferred": True, "reason": "remote", "depth": hop_level},
                turn=turn,
            )
        store.append(ev_hop)
        appended.append(ev_hop)
        log.debug("cascade: secondary hop region=%s level=%d local=%s",
                  region, hop_level, is_local)
        n_emitted += 1
    return appended


# ---------------------------------------------------------------------------
# BFS walker — C1 vertical descent
# ---------------------------------------------------------------------------

def _vertical_bfs(
    roots: list[str],
    graph,
    day: int,
    ctx: str,
    provider,
    store,
    scene: str,
    turn: int,
    max_concurrency: int = 3,
    root_level: int = 1,
    allowed_ids: set | None = None,
    scene_subtree: set | None = None,
    allow_secondary: bool = False,
    evolve_roots: bool = True,
) -> list[dict]:
    """BFS over contained_by children of each root; emit place_evolved / populace_shifted.

    Per-round breadth cap CASCADE_BREADTH is applied at each frontier level:
    process at most CASCADE_BREADTH nodes at each level; log and skip the rest.

    C2 Task 8: per-round _node_verdict calls are submitted to a ThreadPoolExecutor
    (max_workers=max_concurrency). The per-node LLM call releases the GIL (urllib I/O)
    so real providers overlap. Results are collected then mutated sequentially in the
    main thread. FakeLLMProvider's call counter is never relied on for response→node
    assignment (D5). No store.append or graph access occurs inside the executor.

    P1 Task 5: accumulates keep_spreading targets for at-most-one-hop secondary
    spread. When allow_secondary is True (declared-area descent) and an evolve:true
    verdict names keep_spreading, adjacent real Place ids not already touched become
    deferred ring-1 hops via _emit_hops. When allow_secondary is False (drain path),
    keep_spreading is ignored — no ring 2.

    C2 Task 10: splits hops into local (inline) and remote (deferred). Remote hops
    emit the world_change PLUS a deferred:true marker; their children are NOT
    descended this turn. Local hops are not deferred; their world_change is emitted
    without a deferral marker. scene_subtree is the set of Place ids in the current
    scene's containment subtree; if None, all hops are treated as local.

    evolve_roots: when True (default, used by new-trigger descent), seed the BFS
    frontier with the roots themselves — each narrator-declared area gets a
    _node_verdict call and emits place_evolved if evolve:true. Un-named children of
    an evolving root are enqueued for the next level normally. When False (drain
    path), seed from roots' children instead — the root region was already
    world_change-emitted as a deferred hop; we only descend into its children.

    Returns list of appended events (including hop world_change events).
    """
    appended: list[dict] = []
    # allowed_ids: ids created this cascade run (for lightweight_validate)
    if allowed_ids is None:
        allowed_ids = set()

    # Accumulator for secondary keep_spreading hop candidates (P1 Task 5)
    # keyed by region id; dict-keyed write IS the same-region merge
    chain_targets: dict[str, dict] = {}

    # Seed the BFS frontier.
    # evolve_roots=True (new-trigger): each narrator-declared area is itself evaluated.
    #   A child that is ALSO a named root is de-duped and processed once — the single
    #   pass handles both its own evolution AND enqueues its own children.
    # evolve_roots=False (drain): the root region was already emitted as a deferred
    #   world_change; descend only into its children (original behaviour).
    seen: set[str] = set()
    if evolve_roots:
        # Frontier = the roots themselves (de-duped, preserving order)
        frontier: list[str] = []
        for root in roots:
            if root not in seen:
                seen.add(root)
                frontier.append(root)
    else:
        # Frontier = children of each root (roots are already-changed; skip them)
        frontier = []
        seen = set(roots)
        for root in roots:
            for child in _children(graph, root, day):
                if child not in seen:
                    seen.add(child)
                    frontier.append(child)

    nodes_processed = 0  # total _node_verdict calls — capped by CASCADE_NODE_BUDGET
    while frontier:
        # Apply per-round breadth cap at this level
        if len(frontier) > CASCADE_BREADTH:
            skipped = frontier[CASCADE_BREADTH:]
            frontier = frontier[:CASCADE_BREADTH]
            log.info(
                "cascade: breadth cap %d hit at this level; %d place(s) skipped: %s",
                CASCADE_BREADTH,
                len(skipped),
                skipped,
            )

        # Apply the TOTAL node budget across the whole cascade. Each node is up to
        # max_repairs+1 backstage LLM calls, so this caps worst-case cost/turn.
        remaining_budget = CASCADE_NODE_BUDGET - nodes_processed
        if remaining_budget <= 0:
            log.info("cascade: node budget %d exhausted; %d frontier place(s) skipped",
                     CASCADE_NODE_BUDGET, len(frontier))
            break
        if len(frontier) > remaining_budget:
            skipped = frontier[remaining_budget:]
            frontier = frontier[:remaining_budget]
            log.info("cascade: node budget %d would be exceeded; %d place(s) skipped: %s",
                     CASCADE_NODE_BUDGET, len(skipped), skipped)
        nodes_processed += len(frontier)

        next_frontier: list[str] = []

        # -----------------------------------------------------------------------
        # Phase 1 (parallel): submit _node_verdict for each frontier node to pool.
        # Only pure LLM calls in the pool — no shared mutable state touched here.
        # -----------------------------------------------------------------------
        results: list[tuple[str, dict | None]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, max_concurrency)
        ) as ex:
            fut_to_pid = {
                ex.submit(_node_verdict, pid, ctx, provider): pid
                for pid in frontier
            }
            for fut in concurrent.futures.as_completed(fut_to_pid):
                pid = fut_to_pid[fut]
                try:
                    results.append((pid, fut.result()))
                except Exception as exc:
                    log.warning(
                        "cascade: _node_verdict raised for place=%s (%s); dropping node",
                        pid, exc,
                    )
                    # treat as "no verdict" → skipped (same as sequential path)

        # Re-sort into the deterministic frontier order so emission / child-enqueue
        # order does not depend on thread completion order.
        by_pid = dict(results)
        ordered = [(pid, by_pid[pid]) for pid in frontier if pid in by_pid]

        # -----------------------------------------------------------------------
        # Phase 2 (single-threaded main thread): validate, emit, enqueue children.
        # ALL store.append / graph mutations happen here, sequentially.
        # -----------------------------------------------------------------------
        for place_id, raw in ordered:
            verdict = lightweight_validate(raw, graph, allowed_ids)

            if verdict is None:
                # Drop this node's verdict — do NOT descend further
                log.warning(
                    "cascade: dropped invalid verdict for place=%s; stopping subtree",
                    place_id,
                )
                continue

            if not verdict.get("evolve"):
                # Prune — record roll-up note, stop descent
                log.info(
                    "cascade: prune at place=%s (evolve=false) — roll-up recorded",
                    place_id,
                )
                continue

            # evolve:true — emit place_evolved
            ev_evolved = kernel_event(
                "place_evolved",
                day=day,
                scene=scene,
                summary=f"{place_id} 演化",
                deltas={
                    "id": place_id,
                    "state": verdict.get("state", ""),
                    "note": verdict.get("note", ""),
                },
                turn=turn,
            )
            store.append(ev_evolved)
            appended.append(ev_evolved)
            allowed_ids.add(place_id)
            log.debug("cascade: place_evolved appended place=%s state=%r",
                      place_id, verdict.get("state"))

            # Emit populace_shifted if mood given
            if verdict.get("populace_mood"):
                ev_mood = kernel_event(
                    "populace_shifted",
                    day=day,
                    scene=scene,
                    summary=f"{place_id} 民心转变",
                    deltas={
                        "id": place_id,
                        "mood": verdict["populace_mood"],
                        "note": verdict.get("note", ""),
                    },
                    turn=turn,
                )
                store.append(ev_mood)
                appended.append(ev_mood)
                log.debug("cascade: populace_shifted appended place=%s mood=%r",
                          place_id, verdict["populace_mood"])

            # P1: at-most-one-hop secondary spread. Only when allow_secondary
            # (the declared-area descent); a region reached via a hop does NOT
            # open a further ring (drain passes allow_secondary=False).
            if allow_secondary and verdict.get("keep_spreading"):
                adj = set(_adjacent_regions(graph, place_id, day))
                for region in verdict["keep_spreading"]:
                    if (region in adj and region not in allowed_ids
                            and region not in seen and region not in chain_targets):
                        chain_targets[region] = {"region": region, "level": root_level + 1}

            # Enqueue this node's children for the next level
            children = _children(graph, place_id, day)
            for child in children:
                if child not in seen:
                    seen.add(child)
                    next_frontier.append(child)

        frontier = next_frontier

    # -------------------------------------------------------------------------
    # P1 Task 5: emit secondary keep_spreading hops (after vertical BFS done)
    # Split local (inline) vs remote (deferred, §12 line 176).
    # -------------------------------------------------------------------------
    hop_events = _emit_hops(
        chain_targets=chain_targets,
        graph=graph,
        day=day,
        scene=scene,
        turn=turn,
        scene_subtree=scene_subtree,
        store=store,
    )
    appended.extend(hop_events)

    return appended


# ---------------------------------------------------------------------------
# Public hook — mirrors run_director shape
# ---------------------------------------------------------------------------

def run_cascade(
    registry,
    store,
    world: dict,
    *,
    scene: str,
    provider,
    cascade_provider=None,
    max_subagents: int = 4,
    max_concurrency: int | None = None,
) -> list[dict]:
    """Post-apply cascade hook (Phase C1 + C2 — vertical descent, blocking).

    Args:
        registry:         Kernel registry (must include CascadeSystem).
        store:            EventStore (open, strict allow-set).
        world:            Current projected world (pre-cascade).
        scene:            Scene id string (used as scene attr on emitted events).
        provider:         Main LLMProvider (fallback cascade provider).
        cascade_provider: Optional cheap LLMProvider for cascade node calls.
                          If None, falls back to provider. Wiring should pass a
                          cheap model provider (e.g. glm-4.7) here.
        max_subagents:    Back-compat alias for concurrency (C2). Used by
                          _resolve_concurrency if max_concurrency is None and
                          env RPG_CASCADE_CONCURRENCY is unset.
        max_concurrency:  Explicit ThreadPoolExecutor max_workers override.
                          Resolved by _resolve_concurrency (precedence:
                          explicit > env RPG_CASCADE_CONCURRENCY > max_subagents > 3).

    Returns:
        List of appended events (possibly empty).
    """
    # Guard: if either required system slice is missing, return clean no-op.
    # Prevents a hard KeyError being swallowed silently by run_turn's try/except.
    _systems = world.get("systems", {})
    if _systems.get("cascade") is None or _systems.get("ontology") is None:
        log.debug(
            "run_cascade: cascade=%s ontology=%s — system(s) not registered, skipping",
            _systems.get("cascade") is not None,
            _systems.get("ontology") is not None,
        )
        return []

    cp = cascade_provider if cascade_provider is not None else provider
    conc = _resolve_concurrency(max_concurrency, max_subagents)

    g = world["systems"]["ontology"]
    day_meta = (world.get("meta") or {}).get("day") or 1

    # Compute scene subtree for local vs remote classification (C2 Task 10).
    scene_subtree = _scene_subtree(g, scene, day_meta)

    appended: list[dict] = []
    # Shared allowed_ids across drain + new-trigger BFS so cross-run IDs are consistent
    shared_allowed_ids: set[str] = set()

    # -------------------------------------------------------------------------
    # C2 Task 10: drain-at-start — process deferred queue entries first.
    # Mirrors run_director's consume-last-turn idiom.
    # -------------------------------------------------------------------------
    cascade_slice = world["systems"]["cascade"]
    through = cascade_slice.get("consumed_through_turn", 0)
    pending = [
        q for q in cascade_slice.get("queue", [])
        if not q.get("consumed") and (q.get("enqueue_turn") or 0) > through
    ]

    if pending:
        log.debug("run_cascade: draining %d queued deferred entry(ies)", len(pending))
        drain_turn = _next_cascade_turn(store)
        drain_day = day_meta
        max_enqueue_turn = 0

        for entry in pending:
            region = entry.get("region", "")
            if not region or not _is_place(g, region):
                log.warning("cascade drain: skipping invalid queue entry %r", entry)
                continue
            entry_turn = entry.get("enqueue_turn", 0)
            max_enqueue_turn = max(max_enqueue_turn, entry_turn)

            # Descend the deferred region's children (same vertical BFS, shared budget).
            # evolve_roots=False: the region itself was already emitted as a deferred
            # world_change hop; we only descend into its children here.
            drain_appended = _vertical_bfs(
                roots=[region],
                graph=g,
                day=drain_day,
                ctx=f"延迟处理：{region}",
                provider=cp,
                store=store,
                scene=scene,
                turn=drain_turn,
                max_concurrency=conc,
                root_level=entry.get("level", 1),
                allowed_ids=shared_allowed_ids,
                scene_subtree=scene_subtree,
                evolve_roots=False,
            )
            appended.extend(drain_appended)
            # Bump drain_turn so subsequent entries get unique turn slots
            if drain_appended:
                drain_turn = _next_cascade_turn(store)

        if max_enqueue_turn > 0:
            # Emit the consume-watermark bookkeeping event (event-sourced, rewind-safe).
            # CascadeSystem.apply handles deferred_consume_through → sets consumed_through_turn.
            watermark_turn = _next_cascade_turn(store)
            ev_watermark = kernel_event(
                "world_change",
                day=drain_day,
                scene=scene,
                summary="cascade 延迟队列消费水位",
                deltas={
                    "place": scene,
                    "deferred_consume_through": max_enqueue_turn,
                },
                turn=watermark_turn,
            )
            store.append(ev_watermark)
            appended.append(ev_watermark)
            log.debug(
                "run_cascade: drain complete; consumed_through_turn → %d", max_enqueue_turn
            )

    # -------------------------------------------------------------------------
    # New-trigger descent (if any trigger roots this turn)
    # -------------------------------------------------------------------------
    events = list(store.iter_events())
    player_turn = _last_nonharness_turn(events)
    trigger_events = [e for e in events if (e.get("turn") or 0) == player_turn]

    roots = cascade_trigger(trigger_events, world)
    if not roots and not pending:
        log.debug("run_cascade: no trigger roots and no queue — quiet")
        return []

    if roots:
        day = max((e.get("day") or 1 for e in trigger_events), default=day_meta)
        day = max(day, day_meta)
        turn = _next_cascade_turn(store)

        # Build a context summary from the trigger events' summaries
        ctx_parts = [e.get("summary", "") for e in trigger_events if e.get("summary")]
        ctx = "; ".join(ctx_parts) if ctx_parts else "world change"

        # Derive root_level from trigger world_change deltas["level"], default 1.
        root_level = max(
            (e.get("deltas", {}).get("level", 1) for e in trigger_events
             if e.get("type") == "world_change"),
            default=1,
        )

        log.debug(
            "run_cascade: roots=%s day=%d turn=%d conc=%d root_level=%d",
            roots, day, turn, conc, root_level,
        )

        # P1: pass allow_secondary=True so node verdicts' keep_spreading opens ring-1.
        new_appended = _vertical_bfs(
            roots=roots,
            graph=g,
            day=day,
            ctx=ctx,
            provider=cp,
            store=store,
            scene=scene,
            turn=turn,
            max_concurrency=conc,
            root_level=root_level,
            allowed_ids=shared_allowed_ids,
            scene_subtree=scene_subtree,
            allow_secondary=True,
        )
        appended.extend(new_appended)

    log.debug("run_cascade: done appended=%d", len(appended))
    return appended
