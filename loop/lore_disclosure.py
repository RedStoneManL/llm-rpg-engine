"""loop.lore_disclosure — station-push ambient lore injection.

station_push_fragment(registry, world, scene) -> str | None

For the protagonist's current L3 venue, pushes lore lines' content into context
without a tool call (PUSH / station pattern):

  L1 detail (fetch_lore(line, 1)): lines whose l3_anchor matches the current L3.
  L0 index  (fetch_lore(line, 0)): lines whose anchor matches the current L2 town
                                    but l3_anchor is a different venue.

Returns None when no active lines are in range.
"""
from __future__ import annotations

from loop.lore import fetch_lore
from loop.graph_utils import ancestor_of_level
from engine.log import get_logger

log = get_logger("loop.lore_disclosure")


# ---------------------------------------------------------------------------
# Internal helper: walk contained_by edges to find the L2 ancestor
# ---------------------------------------------------------------------------

def _l2_ancestor(g, place_id: str, day: int) -> str | None:
    """Walk contained_by relations upward until we find a Place with level==2.

    Thin wrapper around loop.graph_utils.ancestor_of_level kept for backward
    compatibility (loop.turn imports this name directly).
    """
    return ancestor_of_level(g, place_id, day, 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def station_push_fragment(registry, world: dict, scene: dict) -> str | None:
    """Return the B-mode context text block for the protagonist's current L3, or None.

    Algorithm:
    1. Resolve protagonist's current L3 via located_in relation.
    2. Resolve the L2 ancestor (town) of that L3 via contained_by walk.
    3. For each active lore line:
       - l3_anchor == current_L3  →  render fetch_lore(line, 1)  [beat + clue]
       - anchor    == current_town →  render fetch_lore(line, 0)  [index only]
       - else                      →  skip
    4. Format into one compact text block.  Return None if nothing is in range.
    """
    g = (world.get("systems") or {}).get("ontology")
    if g is None:
        return None

    prot = scene.get("protagonist") if isinstance(scene, dict) else None
    if not prot:
        return None

    day = (world.get("meta") or {}).get("day") or 1

    # Step 1: current L3 venue
    locs = g.neighbors(prot, "located_in", day)
    if not locs:
        return None
    current_l3 = locs[0]

    # Step 2: L2 ancestor (town)
    current_town = _l2_ancestor(g, current_l3, day)

    # Step 3: classify lines
    lines = (world.get("systems", {}).get("lore") or {}).get("lines", {})

    l1_parts: list[str] = []   # at this exact venue → full L1
    l0_parts: list[str] = []   # same town, different venue → L0 index

    for lid, line in lines.items():
        if line.get("state") == "了结":
            continue
        # Ambient block is for 暗 lines ONLY; 明 lines live in the 明账 via LoreSystem.inject
        if line.get("state") != "暗":
            continue

        if line.get("l3_anchor") == current_l3:
            # L1: show [id] + beat + latest_clue
            data = fetch_lore(line, 1)
            beat = data.get("beat")
            clue = data.get("latest_clue")
            parts = []
            if beat:
                parts.append(beat)
            if clue and clue != beat:
                parts.append(clue)
            body = " / ".join(parts) if parts else ""
            l1_parts.append(f"·[{lid}] {body}" if body else f"·[{lid}]")
            log.debug("station_push: L1 line=%s", lid)

        elif current_town and line.get("anchor") == current_town:
            # L0: [id] + description
            data = fetch_lore(line, 0)
            desc = data.get("description") or lid
            l0_parts.append(f"·[{lid}] {desc}")
            log.debug("station_push: L0 line=%s", lid)

    if not l1_parts and not l0_parts:
        return None

    # Step 4: format
    sections: list[str] = ["〔本地暗线·环境可织入,勿点破为任务〕"]
    if l1_parts:
        sections.append("【就在此处】")
        sections.extend(l1_parts)
    if l0_parts:
        sections.append("【本镇其余风声】")
        sections.extend(l0_parts)

    return "\n".join(sections)
