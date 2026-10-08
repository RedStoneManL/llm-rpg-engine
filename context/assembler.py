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
import json
from context.public_npc_evidence import public_npc_evidence, format_public_npc_evidence

from kernel.registry import Registry
from kernel.assembler import assemble, render, LAYER_ORDER
from kernel.recall import recall as kernel_recall
from kernel.contextsystem import Fragment
from memory.recall import rank, embed_query
from context.viewpoint import build_viewpoint
from facts.graph import FactGraph
from context.access import pov_world
from context.player_evidence import read_player_evidence, format_player_evidence
from context.cast_evidence import read_cast_evidence, format_cast_evidence
from engine.log import get_logger
import systems.narrative as nmod

log = get_logger("context.assembler")

_GUARDRAIL_TAG = "⚠️只约束·勿泄露"


def _canonical_grounding(graph, scene, day):
    """Render host bindings from the already POV-filtered graph only.

    Do not infer an empty inventory from an absent/redacted relation, or expose
    off-scene people's inventories merely because their identities are known.
    """
    actor = scene.get('protagonist')
    if graph is None or not isinstance(actor, str) or graph.get_entity(actor) is None:
        return ''
    local_holders = {actor, scene.get('location')} | set(scene.get('present') or [])
    location = scene.get('location')
    if location:
        local_holders.update(entity.id for entity in graph.entities.values()
            if entity.etype == 'Person' and location in graph.neighbors(entity.id, 'located_in', day))
    inventory = []
    for relation in graph.relations:
        if relation.rel != 'held_by' or not relation.valid_at(day) or relation.dst not in local_holders:
            continue
        item, holder = graph.get_entity(relation.src), graph.get_entity(relation.dst)
        if item is None or item.etype != 'Object' or holder is None or holder.etype not in {'Person', 'Place'}:
            continue
        inventory.append({'item_id': relation.src, 'holder_id': relation.dst,
                          'since_day': relation.event_time_start})
    inventory.sort(key=lambda row: (row['holder_id'] != actor, row['item_id'], row['holder_id']))
    binding = {'actor_id': actor, 'inventory': inventory[:24],
               'inventory_truncated': len(inventory) > 24}
    return ('【引擎绑定·仅供结构输出与连续性校验，禁止在正文复述】\n'
        + json.dumps(binding, ensure_ascii=False) + '\n'
        'actor_id 是本回合主角的实际实体 id；结构引用应原样使用，不把角色称谓当成新 id。'
        '上述物品持有记录来自当前可见的 held_by，优先于旧摘要或自由 facts 中的归属别名；'
        '未列出不表示无人持有。物品实际转移只写 items，不在 facts/knowledge 中另建同义持有者账本；'
        '颜色、材质等描述性事实仍可保留。元数据、字段名和校验意见不是剧情或角色台词。')


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
    # Only the actor-bound source reader may access the private original-input
    # slice. Generic inject/recall/tools receive a view with that slice removed.
    player_evidence = read_player_evidence(world, scene, query)
    cast_evidence = read_cast_evidence(world, scene, query)
    public_npc = public_npc_evidence(world, scene)
    world = pov_world(world, scene)
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
    if public_npc is not None:
        viewpoint_frags.append(format_public_npc_evidence(public_npc))
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
    grounding = _canonical_grounding(g, scene, day or world.get('meta', {}).get('day') or 1)
    if grounding:
        base = grounding + ('\n\n' + base if base else '')
    # Fact anchors are repeated independently of free-form recap. Date-stamped
    # commitments must not acquire an invented history after a context reset.
    if g is not None and protagonist:
        anchors = []
        for fact in g.facts:
            if not fact.is_current() or fact.predicate.startswith('knows:'):
                continue
            entity = g.get_entity(fact.subject)
            if fact.subject == protagonist or (entity and entity.etype == 'Object' and fact.secrecy == 'public'):
                anchors.append(f'{fact.subject}.{fact.predicate} = {json.dumps(fact.value, ensure_ascii=False)} '
                               f'（确立于第 {fact.event_time_start} 天）')
        if anchors:
            base += ('\n\n【事实锚点·当前有效】\n' + '\n'.join(anchors[-24:]) + '\n'
                '回忆必须忠于上述事实，不补写未经记录的借出人、交易优惠或发生日期。相对日期以事实确立日为基准。'
                '遵守对应系统的写入协议：已登记资源由规则裁定，物品持有变化只写 items；'
                '其他客观变化才按原有字段记入 facts，并在适用时同步本人 knowledge；不得用近义字段另开账本。'
                '未完成的行动不能描述为已完成；叙事中的数值与提交后的余额必须一致。')

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
        if ns.get('summarized_through_index', 0) <= idx < len(buckets) - nmod.RECAP_RAW_SCENES and b.get("summary")
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

    if protagonist and g is not None:
        actor_entity = g.get_entity(protagonist) if isinstance(protagonist, str) else None
        if actor_entity is not None and actor_entity.etype == 'Person':
            parts.append("## [volatile]")
            parts.append(format_player_evidence(player_evidence))
            parts.append(format_cast_evidence(cast_evidence))

    result = "\n".join(parts)
    log.debug("assemble_context: output length=%d chars", len(result))
    return result
