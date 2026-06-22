"""context.assembler — cache-layered context assembler.

assemble_context(registry, world, scene, *, query=None, embedder=None, k=6) -> str

Composes per-scene context in cache-friendly stable→scene→volatile order:

  stable  → per-system inject fragments (OntologySystem rules, etc.)
  scene   → per-system inject fragments (place/character scene state)
             + POV facts (protagonist knows)
             + guardrail facts (unknown-but-true, tagged ⚠️只约束·勿泄露)
             + NPC knowledge bundles
  volatile → ranked recall hits (only if query is provided)

Steps:
  1. kernel.assembler.assemble → layer-sorted Fragment list from all systems.
  2. If query: kernel.recall.recall → RecallHit candidates; rank via
     memory.recall.rank (recency from world["meta"]["day"], default importance,
     relevance via embed_query / FakeEmbedder); take top-k.
  3. build_viewpoint from scene (protagonist/present/day) + candidate fact_keys
     (all unique fact_keys found in current knowledge facts in the graph).
  4. Compose: render fragments via kernel.assembler.render for base layers;
     append viewpoint facts into scene layer; append recall block into volatile.
     Return one string.
"""
from __future__ import annotations

from kernel.registry import Registry
from kernel.assembler import assemble, render, LAYER_ORDER
from kernel.recall import recall as kernel_recall
from kernel.contextsystem import Fragment
from memory.recall import rank, embed_query
from context.viewpoint import build_viewpoint
from facts.graph import FactGraph
from engine.log import get_logger
import systems.narrative as nmod

log = get_logger("context.assembler")

_GUARDRAIL_TAG = "⚠️只约束·勿泄露"


def assemble_context(
    registry: Registry,
    world: dict,
    scene: dict,
    *,
    query: str | None = None,
    embedder=None,
    k: int = 6,
) -> str:
    """Assemble the per-turn narrator context string.

    Args:
        registry:  Kernel registry with all registered systems.
        world:     Projected world state dict.
        scene:     Current scene dict with keys: protagonist, present, day, location.
        query:     Optional natural-language recall query.
        embedder:  Optional embedder for semantic ranking; falls back to score-only
                   ranking (recency + importance) when None.
        k:         Max recall hits to include.

    Returns:
        A single string with stable→scene→volatile cache layer ordering.
    """
    # ------------------------------------------------------------------
    # Step 1: per-system inject fragments (already layer-sorted)
    # ------------------------------------------------------------------
    frags = assemble(registry, scene, world)
    log.debug("assemble_context: %d inject fragments from systems", len(frags))

    # ------------------------------------------------------------------
    # Step 2: ranked recall (volatile layer)
    # ------------------------------------------------------------------
    recall_lines: list[str] = []
    if query:
        hits = kernel_recall(registry, query, world)
        if hits:
            # Convert RecallHit → candidates for memory.recall.rank
            day = scene.get("day") or (world.get("meta", {}).get("day") or 0)
            candidates = []
            for h in hits:
                candidates.append({
                    "text": h.text,
                    "day": day,         # approximate — systems don't track fact day
                    "importance": 5.0,  # neutral default
                    "_hit": h,
                })
            # Embed query for relevance scoring
            if embedder is not None:
                q_vec = embed_query(query, embedder)
            else:
                q_vec = []  # no relevance scoring without embedder

            ranked = rank(candidates, q_vec, now_day=float(day), embedder=embedder)
            top = ranked[:k]
            if top:
                recall_lines.append(f"## [{LAYER_ORDER[2]}]")
                recall_lines.append("# [recall]")
                for cand, score in top:
                    recall_lines.append(f"  {cand['text']}  (score={score:.3f})")
        log.debug("assemble_context: recall query=%r hits=%d rendered=%d",
                  query, len(hits) if hits else 0, len(recall_lines))

    # ------------------------------------------------------------------
    # Step 3: viewpoint bundle (POV / guardrail / NPC)
    # ------------------------------------------------------------------
    protagonist = scene.get("protagonist")
    present = scene.get("present", [])
    day = scene.get("day", 0)

    # Derive candidate fact_keys from all current knowledge facts in the graph
    candidate_fact_keys: list[str] = []
    g: FactGraph | None = world.get("systems", {}).get("ontology")
    if g is not None and protagonist:
        seen: set[str] = set()
        for f in g.facts:
            if f.predicate.startswith("knows:") and f.is_current():
                fk = f.predicate[len("knows:"):]
                if fk not in seen:
                    seen.add(fk)
                    candidate_fact_keys.append(fk)

    viewpoint_frags: list[str] = []
    if protagonist and candidate_fact_keys:
        vp = build_viewpoint(
            g,
            protagonist=protagonist,
            present=present,
            day=day,
            candidate_fact_keys=candidate_fact_keys,
        )
        # Render POV facts → scene layer
        if vp["pov"]:
            pov_lines = [f"{fk} = {val}" for fk, val in vp["pov"].items()]
            viewpoint_frags.append("# [pov · 主角所知]")
            viewpoint_frags.extend(pov_lines)

        # Render guardrail facts → scene layer, tagged
        if vp["guardrail"]:
            guardrail_lines = [f"{fk} = {val}" for fk, val in vp["guardrail"].items()]
            viewpoint_frags.append(f"# [{_GUARDRAIL_TAG}]")
            viewpoint_frags.extend(guardrail_lines)

        # Render NPC bundles → scene layer
        if vp["npc"]:
            for npc_id, npc_knowledge in vp["npc"].items():
                if npc_knowledge:
                    npc_lines = [f"  {fk} = {val}" for fk, val in npc_knowledge.items()]
                    viewpoint_frags.append(f"# [npc · {npc_id}]")
                    viewpoint_frags.extend(npc_lines)

    # ------------------------------------------------------------------
    # Step 4: compose into stable→scene→volatile string
    # ------------------------------------------------------------------
    # Render the base inject fragments first (includes NarrativeSystem.inject scene-raw
    # and LoreSystem.inject (明账) — both force-pushed, query-independent).
    base = render(frags)

    # ------------------------------------------------------------------
    # Step 4a: Recap stable-summary block (PUSH, spec §1 — query-independent)
    # ------------------------------------------------------------------
    # NarrativeSystem.inject already returns the recent-N raw narration as a SCENE
    # fragment (appears in `base` above).  The STABLE-layer summary block (aged scene
    # summaries + super_summary) is rendered here directly from the slice — one system
    # contributes one inject fragment to avoid double-rendering the recent raw.
    recap_summary_lines: list[str] = []
    ns = world.get("systems", {}).get("narrative") or {}
    buckets = ns.get("scenes", [])
    super_summary = ns.get("super_summary")
    # Aged buckets: those beyond the recent-N window that have a summary
    aged_with_summary = [
        b for idx, b in enumerate(buckets)
        if idx < len(buckets) - nmod.RECAP_RAW_SCENES and b.get("summary")
    ]
    if super_summary or aged_with_summary:
        recap_summary_lines.append("## [stable]")
        recap_summary_lines.append("【往昔概要】（更早剧情的压缩记忆）")
        if super_summary:
            recap_summary_lines.append(f"«总览» {super_summary}")
        for b in aged_with_summary:
            recap_summary_lines.append(f"«{b['scene']}» {b['summary']}")

    # Build final output: recap stable block + base + scene viewpoint block + volatile recall block
    parts: list[str] = []
    if recap_summary_lines:
        parts.extend(recap_summary_lines)
    if base:
        parts.append(base)

    if viewpoint_frags:
        # Viewpoint is scene-layer content — add under a scene header if not already there
        parts.append("## [scene]")
        parts.extend(viewpoint_frags)

    if recall_lines:
        # recall_lines already starts with ## [volatile]
        parts.extend(recall_lines)

    result = "\n".join(parts)
    log.debug("assemble_context: output length=%d chars", len(result))
    return result
