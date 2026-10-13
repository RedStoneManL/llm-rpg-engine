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

from kernel.registry import Registry
from kernel.assembler import assemble, render, LAYER_ORDER
from kernel.recall import recall as kernel_recall
from kernel.contextsystem import Fragment
from memory.recall import rank, embed_query
from context.viewpoint import build_viewpoint
from facts.graph import FactGraph
from context.access import pov_world
from context.capabilities import scene_capabilities
from context.place_observations import public_place_observations
from context.native_authoring import native_authoring_context
from engine.log import get_logger
import systems.narrative as nmod

log = get_logger("context.assembler")

_GUARDRAIL_TAG = "⚠️只约束·勿泄露"


def safe_authoring_handoff(world: dict, scene: dict, *, trusted_events=(),
                           player_statement_receipts=()) -> str:
    """Render the normal author's narrow share of native-safe scene material.

    A store-owning host may supply a private, in-memory active-event snapshot.
    This function immediately projects it through ``native_authoring_context``;
    its return value is safe text, never a snapshot.  It neither grants a
    communication receipt nor reads canonical destination state directly.
    Direct assembler callers simply get the same public-arrival material
    without the optional verified-word recall.
    """
    if not isinstance(trusted_events, (tuple, list)):
        trusted_events = ()
    safe = native_authoring_context(
        world, scene, trusted_events=trusted_events,
        player_statement_receipts=list(player_statement_receipts),
    )
    current_people = safe.get("present_people", [])
    arrivals = safe.get("conditional_arrivals", [])
    lines: list[str] = []

    def evidence_lines(people, *, prefix: str) -> list[str]:
        rendered: list[str] = []
        for person in people:
            if not isinstance(person, dict) or not isinstance(person.get("id"), str):
                continue
            for evidence in person.get("speakable_evidence", []):
                if not isinstance(evidence, dict):
                    continue
                key, value, source = evidence.get("fact_key"), evidence.get("value"), evidence.get("source")
                if not all(isinstance(item, str) and item for item in (key, value, source)):
                    continue
                if source == "recipient_heard_player_statement":
                    rendered.append(
                        f"- {prefix}{person['id']} 已被可信事件验证为听过主角原话：{value}"
                        "（这是该角色的记录，不是世界事实；本入口不得据玩家输入自动新建 heard_player）。"
                    )
                else:
                    rendered.append(f"- {prefix}{person['id']} 的公开可归因材料：{key} = {value}（{source}）。")
        return rendered

    current_evidence = evidence_lines(current_people, prefix="当前同场人物 ")
    if current_evidence:
        lines.append("【安全交谈材料·当前场景】")
        lines.extend(current_evidence)

    arrival_lines: list[str] = []
    actor = scene.get("protagonist")
    for arrival in arrivals:
        if not isinstance(arrival, dict):
            continue
        move, location, people = arrival.get("move"), arrival.get("location"), arrival.get("present_people")
        if not (isinstance(move, dict) and isinstance(location, dict) and isinstance(people, list)):
            continue
        destination = move.get("to")
        if not isinstance(destination, str) or move.get("who") != actor:
            continue
        person_ids = [person["id"] for person in people
                      if isinstance(person, dict) and isinstance(person.get("id"), str)]
        if not person_ids:
            continue
        label = location.get("label") if isinstance(location.get("label"), str) else destination
        arrival_lines.append(
            f"- 若本回合 moves 同时使 {actor} 合法抵达 {destination}（{label}），抵达后可出现："
            + "、".join(person_ids) + "。"
        )
        arrival_lines.extend(evidence_lines(people, prefix=f"仅抵达 {destination} 后，人物 "))
    if arrival_lines:
        lines.append("【条件化抵达材料】以下不是行动开始时主角已知/已见的内容；未提交对应合法移动时不得写成已到达、已交谈或已获得信息。")
        lines.extend(arrival_lines)

    receipts = safe.get("communication_receipts", [])
    receipt_lines: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        receipt_id = receipt.get("id")
        recipient = receipt.get("recipient")
        statement = receipt.get("statement")
        when = receipt.get("when")
        if not all(isinstance(value, str) and value for value in (receipt_id, recipient, statement)):
            continue
        if not isinstance(when, dict) or when.get("kind") != "current_scene":
            continue
        receipt_lines.append(
            f"- 收据 {receipt_id}：宿主已明确选定主角对当前公开同场的 {recipient} 说原话“{statement}”。"
            "若记录该角色听到此句，只能提交逐字相同的 knowledge.heard_player（含此 receipt_id）；"
            "它不是世界事实，也不能扩展到其他人、心理活动或未选定的话。"
        )
    if receipt_lines:
        lines.append("【本回合明确交流授权】")
        lines.extend(receipt_lines)

    return "\n".join(lines)


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
    source_world = world
    capabilities = scene_capabilities(source_world, scene)
    observations = public_place_observations(source_world, scene)
    world = pov_world(source_world, scene)
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
                '发生付款/获得/消耗时，按原有字段记入 facts，并同步本人 knowledge；不得用近义字段逃避原账本。'
                '未完成的行动不能描述为已完成；叙事中的数值与提交后的余额必须一致。')

    # This is a capability contract, not additional canonical context.  It
    # makes the same action-start exits used by GUI and publish validation
    # explicit to authors, avoiding a map that merely looks authoritative.
    base += ("\n\n" if base else "") + capabilities.prompt_fragment()

    if observations:
        observation_lines = ["【公开地点观察·仅限已知当前/可达地点】"]
        for item in observations:
            stability = "稳定布局" if item["kind"] == "stable_layout" else "一次观察"
            observation_lines.append(
                f"- {item['place']}｜{stability}｜范围：{item['range']}｜"
                f"第{item['day']}天/turn {item['turn']}：{item['summary']}"
            )
        base += "\n" + "\n".join(observation_lines)

    # Store-owning entry points pre-render a bounded, redacted supplement.
    # This deliberately keeps raw EventStore rows out of the ordinary scene,
    # provider tool closures, action fingerprints, and persistence metadata.
    authoring_handoff = scene.get("_safe_authoring_handoff")
    if not isinstance(authoring_handoff, str):
        authoring_handoff = safe_authoring_handoff(source_world, scene)
    if authoring_handoff:
        base += ("\n\n" if base else "") + authoring_handoff

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

    result = "\n".join(parts)
    log.debug("assemble_context: output length=%d chars", len(result))
    return result
