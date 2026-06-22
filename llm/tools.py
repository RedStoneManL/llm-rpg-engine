"""llm.tools — POV tool surface for the narrator tool loop (P3a).

Implements Tool dataclass, ToolRegistry, and build_tool_registry for P3a.

Fog rule (DD4 / keystone invariant):
  - Structural topology (place edges / levels / paths) is PUBLIC — the protagonist
    can always see exits, containment, and navigate paths.
  - Place *state/detail* facts (e.g. "断桥.是否可通行") are fog-gated: only
    emitted if knows(graph, pov, "<place>.<predicate>", day) is not None.
    When known, the BELIEVED value is returned (never graph.value_at truth).
  - Recall hits whose ref carries a "fact_key" are dropped if the POV agent
    does not know that fact (knows(...) is None). Structural recall hits
    (ref carries "id" but no "fact_key") are always included.

POV agent (DD5):
  - Default: scene["protagonist"].
  - Optional "pov" arg: may be any entity in scene["present"] or the protagonist.
    An out-of-scene pov → {"error": "pov not in scene"}.

Read-only keystone: this module NEVER calls assert_fact, add_entity, add_relation,
or appends any event. Tools are pure read-over-graph functions.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from engine.log import get_logger
from systems.knowledge import knows
from systems.place import navigate
from systems.faction import members_of, member_rank
import kernel.recall as _kernel_recall

log = get_logger("llm.tools")

# Predicate prefixes that are considered internal/structural — not surfaced
# as fog-gated place facts (they are infrastructure predicates, not story facts).
_INTERNAL_PREDICATES: frozenset[str] = frozenset({
    "level", "kind", "seed", "detail", "density", "last_update", "tier",
})

# Predicate prefix patterns that are always fog-gated (never structural).
_ALWAYS_GATED_PREFIXES: tuple[str, ...] = (
    "knows:",   # knowledge system writes these — never a place state fact
    "trust:",
    "rank:",
    "group:",
    "hidden:",
)


# ---------------------------------------------------------------------------
# Tool dataclass
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    """A single callable tool exposed to the narrator model."""
    name: str
    description: str
    parameters: dict       # JSON-schema for the tool's arguments
    fn: Callable           # fn(**kwargs) -> JSON-serialisable dict

    def schema(self) -> dict:
        """Return the OpenAI function-calling schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Holds a set of Tool objects and dispatches execute() calls.

    execute() catches EVERY exception so a bad model argument can never
    crash the turn — it returns {"error": "<msg>"} as JSON (DD3).
    """

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    def schemas(self) -> list[dict]:
        """Return the OpenAI-style tools array for the model request body."""
        return [t.schema() for t in self._tools.values()]

    def execute(self, name: str, args: dict) -> str:
        """Dispatch a tool call and return a JSON string result.

        Unknown tool or any exception → {"error": "<msg>"} JSON (never raises).
        """
        tool = self._tools.get(name)
        if tool is None:
            log.warning("ToolRegistry.execute: unknown tool %r", name)
            return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)
        try:
            result = tool.fn(**args)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            log.warning("ToolRegistry.execute tool=%r raised: %s", name, exc)
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Fog helpers
# ---------------------------------------------------------------------------

def _resolve_pov(args: dict, scene: dict) -> tuple[str | None, dict | None]:
    """Resolve the pov agent from tool args and scene.

    Returns (pov_id, None) on success, (None, error_dict) on validation failure.
    DD5: pov defaults to protagonist; may be a present NPC; else → error.
    """
    protagonist = scene["protagonist"]
    present = scene.get("present", [])
    pov = args.get("pov") or protagonist
    if pov != protagonist and pov not in present:
        return None, {"error": "pov not in scene"}
    return pov, None


def _is_internal_predicate(predicate: str) -> bool:
    """True when a predicate is structural/internal and should not be fog-gated."""
    if predicate in _INTERNAL_PREDICATES:
        return True
    for prefix in _ALWAYS_GATED_PREFIXES:
        if predicate.startswith(prefix):
            return True
    return False


def _place_known_facts(graph, place_id: str, pov: str, day: int) -> dict[str, Any]:
    """Return the subset of facts on place_id that pov knows (believed values).

    Structural predicates (level, kind, seed, …) are omitted — they are returned
    as part of the public topology bundle, not as fog-gated facts.
    Internal predicates (knows:, trust:, …) are skipped entirely.
    """
    result: dict[str, Any] = {}
    for fact in graph.facts:
        if fact.subject != place_id:
            continue
        if not fact.valid_at(day):
            continue
        pred = fact.predicate
        if _is_internal_predicate(pred):
            continue
        # This is a story/state fact — fog-gate it.
        fact_key = f"{place_id}.{pred}"
        believed = knows(graph, pov, fact_key, day)
        if believed is not None:
            result[pred] = believed
    return result


# ---------------------------------------------------------------------------
# map_query POV tool implementation
# ---------------------------------------------------------------------------

def _map_query_fn(world: dict, scene: dict) -> Callable:
    """Return the map_query closure over (world, scene).

    Public topology: containment parent, adjacent exits + costs, level/kind.
    Fog-gated: place state/detail facts via knows().
    Supports optional path_to → navigate() result merged into output.
    """
    def fn(q: str, path_to: str | None = None, pov: str | None = None) -> dict:
        # DD5: resolve pov agent
        args = {}
        if pov is not None:
            args["pov"] = pov
        pov_id, err = _resolve_pov(args, scene)
        if err is not None:
            return err

        graph = world["systems"]["ontology"]
        day = scene.get("day", 1)

        # --- Topology search: find places whose id or seed contains q ---
        matches: list[str] = []
        for eid, entity in graph.entities.items():
            if entity.etype != "Place":
                continue
            seed = entity.attrs.get("seed", "")
            if q in eid or (seed and q in seed):
                matches.append(eid)

        if not matches:
            # Fall back: return the protagonist's current location if q matches nothing
            locs = graph.neighbors(pov_id, "located_in", day)
            cur_loc = locs[0] if locs else None
            result: dict[str, Any] = {"query": q, "matches": []}
            if path_to:
                src = cur_loc or pov_id
                nav = navigate(graph, src, path_to, day)
                result.update(nav)
            return result

        # Build info for each matched place
        place_results = []
        for place_id in matches:
            entity = graph.get_entity(place_id)
            attrs = entity.attrs if entity else {}

            place_info: dict[str, Any] = {"id": place_id}

            # Public topology: structural attrs
            for k in ("level", "kind", "seed"):
                if k in attrs:
                    place_info[k] = attrs[k]

            # Public topology: containment parent (public)
            parents = graph.neighbors(place_id, "contained_by", day)
            if parents:
                place_info["contained_by"] = parents[0]

            # Public topology: adjacent exits with costs
            exits = graph.relation_attrs_at(place_id, "adjacent_to", day)
            if exits:
                place_info["exits"] = [
                    {"to": dst, "travel_cost": a.get("travel_cost", 1)}
                    for dst, a in exits
                ]

            # Fog-gated: place state/detail facts (believes only, never truth)
            known_facts = _place_known_facts(graph, place_id, pov_id, day)
            if known_facts:
                place_info["known_facts"] = known_facts

            place_results.append(place_info)

        result = {"query": q, "matches": place_results}

        # Optional navigate path (Task 5 — path_to arg)
        if path_to is not None:
            # Use the protagonist's current location as navigation src
            locs = graph.neighbors(pov_id, "located_in", day)
            src = locs[0] if locs else (matches[0] if matches else pov_id)
            nav = navigate(graph, src, path_to, day)
            result["path"] = nav["path"]
            result["total_cost"] = nav["total_cost"]

        return result

    return fn


# ---------------------------------------------------------------------------
# recall_query POV tool implementation
# ---------------------------------------------------------------------------

def _recall_query_fn(registry, world: dict, scene: dict) -> Callable:
    """Return the recall_query closure over (registry, world, scene).

    Fog: hits referencing a fog-gated fact_key (ref["fact_key"]) are dropped
    if the POV agent does not know that fact. Structural hits (ref["id"] without
    fact_key) are always included (public topology).

    memory.recall.rank requires embeddings for full scoring — for P3a we use
    kernel.recall.recall (substring matching) which is deterministic and offline.
    Fog is applied post-recall: drop hits whose ref["fact_key"] is unknown to pov.
    """
    def fn(q: str, pov: str | None = None) -> dict:
        args = {}
        if pov is not None:
            args["pov"] = pov
        pov_id, err = _resolve_pov(args, scene)
        if err is not None:
            return err

        graph = world["systems"]["ontology"]
        day = scene.get("day", 1)
        present: list[str] = scene.get("present", [])

        # Fan-out recall across all registered systems
        hits = _kernel_recall.recall(registry, q, world)

        # Fog filter: drop hits the POV agent must not see.
        #
        # Two gating branches:
        #
        # (A) fact_key branch (forward guard):
        #     If ref["fact_key"] is set the hit is explicitly knowledge-gated.
        #     Drop it when pov does not knows() that fact_key.
        #
        # (B) Person-hit branch (C1 fix):
        #     CharacterSystem.recall emits Person hits as ref={"id": eid} with NO
        #     fact_key, baking sketch+goal into the text.  These are NOT structural
        #     public data — they are NPC secrets.  Gate them: drop the hit when ALL
        #     of the following hold:
        #       • the ref entity is a Person
        #       • pov does not know spy.sketch OR spy.goal
        #       • the NPC is not co-present (scene["present"] makes existence public)
        #     Keep (don't gate): Place/ontology hits (non-Person with ref["id"]) and
        #     hits with no ref at all — these are topology/flavor, always public.
        fog_filtered = []
        for hit in hits:
            ref = hit.ref or {}
            fact_key = ref.get("fact_key")

            # --- Branch (A): explicit fact_key gating ---
            if fact_key is not None:
                believed = knows(graph, pov_id, fact_key, day)
                if believed is None:
                    log.debug(
                        "recall_query fog-drop (fact_key) system=%s fact_key=%r pov=%s",
                        hit.system, fact_key, pov_id,
                    )
                    continue  # drop: pov doesn't know this fact

            # --- Branch (B): Person-hit gating ---
            else:
                eid = ref.get("id")
                if eid is not None:
                    try:
                        entity = graph.get_entity(eid)
                    except Exception:
                        entity = None
                    if entity is not None and entity.etype == "Person":
                        # Self-knowledge rule: pov ALWAYS knows recall hits about itself.
                        if eid == pov_id:
                            pass  # never drop a recall hit about the pov itself
                        # Co-presence makes the NPC visible — allow
                        elif eid not in present:
                            # Check if pov knows any facet of this NPC
                            sketch_known = knows(graph, pov_id, f"{eid}.sketch", day)
                            goal_known = knows(graph, pov_id, f"{eid}.goal", day)
                            if sketch_known is None and goal_known is None:
                                log.debug(
                                    "recall_query fog-drop (Person) system=%s eid=%r pov=%s",
                                    hit.system, eid, pov_id,
                                )
                                continue  # drop: pov has never met this NPC

            fog_filtered.append(hit)

        return {
            "query": q,
            "hits": [{"system": h.system, "text": h.text, "score": h.score}
                     for h in fog_filtered],
        }

    return fn


# ---------------------------------------------------------------------------
# characters_query POV tool implementation
# ---------------------------------------------------------------------------

# Predicates that are always fog-gated regardless of any knows() grant.
# The protagonist must explicitly receive a knowledge_set for these to be seen.
_ALWAYS_GATED_CHARACTER_PREDICATES: frozenset[str] = frozenset({
    "hidden",
})

# Predicate prefixes that are structural/internal to other systems and
# must never appear in a character facet bundle.
_CHARACTER_INTERNAL_PREFIXES: tuple[str, ...] = (
    "trust:",
    "knows:",
    "rank:",
    "group:",
)


def _characters_query_fn(world: dict, scene: dict) -> Callable:
    """Return the characters_query closure over (world, scene).

    Fog rules (DD4 — characters_query):
      - sketch/goal: gated via knows(graph, pov, f"{cid}.sketch", day) etc.
        Returns the BELIEVED value if known; omits the facet if not.
      - hidden: ALWAYS fog-gated — never surfaced to a POV tool.
      - trust:/rank:/group: structural to other systems — never surfaced.
      - Existence/location:
          * Co-present (NPC in scene["present"]) → existence is public (id visible).
          * Never-met (no knows on any facet, not co-present) → {"id": cid, "known": false}.
    """
    def fn(q: str, pov: str | None = None) -> dict:
        args = {}
        if pov is not None:
            args["pov"] = pov
        pov_id, err = _resolve_pov(args, scene)
        if err is not None:
            return err

        graph = world["systems"]["ontology"]
        day = scene.get("day", 1)
        present: list[str] = scene.get("present", [])

        # Find Person entities matching the query (substring on id)
        matches: list[str] = []
        for eid, entity in graph.entities.items():
            if entity.etype != "Person":
                continue
            if q in eid:
                matches.append(eid)

        if not matches:
            return {"query": q, "matches": []}

        results = []
        for cid in matches:
            # Self-knowledge rule: an agent ALWAYS knows itself.
            # When pov_id == cid, bypass the knows() gate entirely and return the
            # entity's own facets at their real (graph) values.  The only exclusions
            # are: the always-gated 'hidden' predicate (an unknown-even-to-self secret
            # stays hidden) and the internal-system prefixes (trust:/knows:/rank:/group:).
            if cid == pov_id:
                record: dict[str, Any] = {"id": cid}
                for fact in graph.facts:
                    if fact.subject != cid:
                        continue
                    if not fact.valid_at(day):
                        continue
                    pred = fact.predicate
                    if pred in _ALWAYS_GATED_CHARACTER_PREDICATES:
                        continue
                    if any(pred.startswith(pfx) for pfx in _CHARACTER_INTERNAL_PREFIXES):
                        continue
                    record[pred] = fact.value
                results.append(record)
                continue

            co_present = cid in present
            sketch_believed = knows(graph, pov_id, f"{cid}.sketch", day)
            goal_believed = knows(graph, pov_id, f"{cid}.goal", day)
            pov_knows_any = (sketch_believed is not None or goal_believed is not None)

            # Never-met: no knows on any facet AND not co-present → known:false
            if not pov_knows_any and not co_present:
                results.append({"id": cid, "known": False})
                continue

            # Build the character record with only known facets
            record = {"id": cid}
            if co_present:
                # Co-presence makes existence public — note it
                record["co_present"] = True

            if sketch_believed is not None:
                record["sketch"] = sketch_believed
            if goal_believed is not None:
                record["goal"] = goal_believed

            # Other non-hidden, non-internal facets gated individually
            for fact in graph.facts:
                if fact.subject != cid:
                    continue
                if not fact.valid_at(day):
                    continue
                pred = fact.predicate
                # Skip already-handled predicates
                if pred in ("sketch", "goal"):
                    continue
                # Always skip hidden and internal prefixes
                if pred in _ALWAYS_GATED_CHARACTER_PREDICATES:
                    continue
                if any(pred.startswith(pfx) for pfx in _CHARACTER_INTERNAL_PREFIXES):
                    continue
                # Gate the facet via knows
                fact_key = f"{cid}.{pred}"
                believed = knows(graph, pov_id, fact_key, day)
                if believed is not None:
                    record[pred] = believed

            results.append(record)

        return {"query": q, "matches": results}

    return fn


# ---------------------------------------------------------------------------
# factions_query POV tool implementation
# ---------------------------------------------------------------------------

def _factions_query_fn(world: dict, scene: dict) -> Callable:
    """Return the factions_query closure over (world, scene).

    Fog rules (DD4 — factions_query):
      - Membership/rank gated via knows(graph, pov, f"{member}.rank:{faction}", day).
        Only members whose rank in the faction is known to pov are surfaced.
      - Faction existence (id/seed) is public — the faction can be named.
      - Only relationships the pov knows are surfaced (membership list is gated).

    Fact key used for gating: "<member>.rank:<faction>"
    (Stored in graph as a Fact on member: predicate="rank:{faction}", value=rank_str;
     knowledge grant stored as predicate="knows:alice.rank:guild" on pov.)
    """
    def fn(q: str, pov: str | None = None) -> dict:
        args = {}
        if pov is not None:
            args["pov"] = pov
        pov_id, err = _resolve_pov(args, scene)
        if err is not None:
            return err

        graph = world["systems"]["ontology"]
        day = scene.get("day", 1)

        # Find Faction entities matching the query (substring on id or seed attr)
        matches: list[str] = []
        for eid, entity in graph.entities.items():
            if entity.etype != "Faction":
                continue
            seed = entity.attrs.get("seed", "")
            if q in eid or (seed and q in seed):
                matches.append(eid)

        if not matches:
            return {"query": q, "matches": []}

        results = []
        for faction_id in matches:
            entity = graph.get_entity(faction_id)
            attrs = entity.attrs if entity else {}

            # Public: faction existence, name/seed
            faction_record: dict[str, Any] = {"id": faction_id}
            if "seed" in attrs:
                faction_record["seed"] = attrs["seed"]

            # Fog-gated: members whose rank the pov knows
            all_members = members_of(graph, faction_id, day)
            known_members = []
            for member in all_members:
                fact_key = f"{member}.rank:{faction_id}"
                believed_rank = knows(graph, pov_id, fact_key, day)
                if believed_rank is not None:
                    known_members.append({
                        "id": member,
                        "rank": believed_rank,
                    })

            faction_record["known_members"] = known_members
            results.append(faction_record)

        return {"query": q, "matches": results}

    return fn


# ---------------------------------------------------------------------------
# ambient_query — PUBLIC tier (passerby / 街坊常识) — T9
# ---------------------------------------------------------------------------

_AMBIENT_SPLIT = re.compile(r"[\s，、。,.;；:：!！?？/]+")


def _ambient_match(q: str, *fields: Any) -> bool:
    """Fuzzy, GENEROUS match for the ambient/passerby lens.

    A real narrator passes a natural-language phrase ("石桥镇最近的风声 异常 街坊议论"),
    not a keyword — so a plain `q in field` substring test never matches a fact
    whose subject is just "石桥镇". Match if the whole query OR any of its tokens
    overlaps a field in EITHER direction. Loosening relevance is safe here: only
    secrecy=="public" facts are ever passed in, so over-matching can only surface
    things that are already public.
    """
    toks = [t for t in _AMBIENT_SPLIT.split(q or "") if t]
    for f in fields:
        fs = str(f) if f is not None else ""
        if not fs:
            continue
        if (q and (q in fs or fs in q)):
            return True
        for t in toks:
            if t in fs or fs in t:
                return True
    return False


def _ambient_query_fn(world: dict, scene: dict) -> Callable:
    """Return the ambient_query closure over (world, scene).

    The PUBLIC tier — what a random local / passerby / common rumor could relay.
    NO per-agent knows() gating (it is not anyone's private knowledge). The safe
    floor is STRUCTURAL: only ever surfaces

      - place/faction structural seeds (id/kind/level/seed — always public), and
      - facts EXPLICITLY marked secrecy=="public" by the narrator.

    Facts with secrecy None (unmarked) or restricted/secret are NEVER returned —
    so an un-tagged secret cannot leak even if the narrator forgets. Relevance /
    "would THIS passerby plausibly know it" is left to the LLM (read=floor,
    reach=trust-LLM); the engine only guarantees no hard secret escapes.
    """
    def fn(q: str) -> dict:
        graph = world["systems"]["ontology"]
        day = scene.get("day", 1)

        # Public structural: matching Place / Faction seeds (always public topology)
        public_places: list[dict[str, Any]] = []
        for eid, entity in graph.entities.items():
            if entity.etype not in ("Place", "Faction"):
                continue
            seed = entity.attrs.get("seed", "")
            if _ambient_match(q, eid, seed):
                rec: dict[str, Any] = {"id": eid, "etype": entity.etype}
                for k in ("level", "kind", "seed"):
                    if k in entity.attrs:
                        rec[k] = entity.attrs[k]
                public_places.append(rec)

        # Public facts ONLY (secrecy=="public"); match on subject/predicate/value.
        public_facts: list[dict[str, Any]] = []
        for fact in graph.facts:
            if fact.secrecy != "public":
                continue
            if not fact.valid_at(day):
                continue
            if _ambient_match(q, fact.subject, fact.predicate, fact.value):
                public_facts.append({
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "value": fact.value,
                })

        return {"query": q, "public_places": public_places,
                "public_facts": public_facts}

    return fn


# ---------------------------------------------------------------------------
# dm_world_query — DM / authoring ground-truth tier (dm=True only) — T9
# ---------------------------------------------------------------------------

def _dm_world_query_fn(world: dict, scene: dict) -> Callable:
    """Return the dm_world_query closure over (world, scene).

    The DM tier — full GROUND TRUTH for authoring/consistency. Bypasses BOTH the
    per-agent fog and the secrecy floor: returns every matching entity with its
    tier/attrs and ALL currently-valid facts (any secrecy) at their TRUE values.
    Never exposed in the player-facing POV registry — only when dm=True.
    """
    def fn(q: str) -> dict:
        graph = world["systems"]["ontology"]
        day = scene.get("day", 1)

        matches: list[dict[str, Any]] = []
        for eid, entity in graph.entities.items():
            seed = entity.attrs.get("seed", "")
            if not (q in eid or (seed and q in seed)):
                continue
            rec: dict[str, Any] = {
                "id": eid, "etype": entity.etype, "tier": entity.tier,
            }
            if entity.attrs:
                rec["attrs"] = dict(entity.attrs)
            facts = [
                {"predicate": f.predicate, "value": f.value, "secrecy": f.secrecy}
                for f in graph.facts
                if f.subject == eid and f.valid_at(day)
            ]
            if facts:
                rec["facts"] = facts
            matches.append(rec)

        return {"query": q, "matches": matches}

    return fn


# ---------------------------------------------------------------------------
# build_tool_registry — assembles the POV tool set (P3a) + tiers (T9)
# ---------------------------------------------------------------------------

def build_tool_registry(registry, world: dict, scene: dict, *, dm: bool = False) -> ToolRegistry:
    """Assemble the narrator tool registry for the given scene.

    dm=False (default): POV tool set (fog applied) + ambient_query (public tier).
    dm=True: the same set PLUS dm_world_query (ground-truth authoring lens).
             The POV tools stay knows-gated either way; dm only ADDS the
             ground-truth lens, it does not loosen the POV fog.

    Returns a ToolRegistry whose schemas() and execute() are the provider
    interface; execute() catches every exception (DD3).
    """
    tools: list[Tool] = []

    # --- map_query: geography / topology / fog-gated place facts ---
    tools.append(Tool(
        name="map_query",
        description=(
            "Query the map for places, exits, containment, adjacency, and known"
            " state facts. Topology (exits, parents) is always shown. Place state"
            " facts (e.g. '断桥.是否可通行') are filtered to what the POV agent knows."
            " Optional: path_to=<place_id> returns a navigate() path."
            " Optional: pov=<entity_id> shifts POV to a present NPC (defaults to protagonist)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Substring to search in place id or seed name.",
                },
                "path_to": {
                    "type": "string",
                    "description": "Optional destination place id; returns navigate() path + cost.",
                },
                "pov": {
                    "type": "string",
                    "description": (
                        "Optional POV agent id (must be protagonist or a present NPC)."
                        " Defaults to protagonist."
                    ),
                },
            },
            "required": ["q"],
        },
        fn=_map_query_fn(world, scene),
    ))

    # --- recall_query: cross-system substring recall with POV fog ---
    tools.append(Tool(
        name="recall_query",
        description=(
            "Search across all systems for entities or facts matching a query string."
            " Returns ranked hits (text + source system + score)."
            " Hits referencing knowledge-gated facts the POV agent doesn't know are dropped."
            " Optional: pov=<entity_id> shifts POV to a present NPC (defaults to protagonist)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Substring query to search across entities and facts.",
                },
                "pov": {
                    "type": "string",
                    "description": (
                        "Optional POV agent id (must be protagonist or a present NPC)."
                        " Defaults to protagonist."
                    ),
                },
            },
            "required": ["q"],
        },
        fn=_recall_query_fn(registry, world, scene),
    ))

    # --- characters_query: POV character facet lookup with fog ---
    tools.append(Tool(
        name="characters_query",
        description=(
            "Look up characters by id substring. For each match, returns ONLY the"
            " facets (sketch/goal/etc.) the POV agent knows via knowledge grants."
            " sketch and goal are gated via knows('<id>.sketch', '<id>.goal')."
            " The 'hidden' facet is ALWAYS fog-gated (never surfaced)."
            " A never-met character (no knowledge + not co-present) returns"
            " {\"id\": \"...\", \"known\": false}."
            " Co-present characters have their existence visible, but unknown facets remain gated."
            " Optional: pov=<entity_id> shifts POV to a present NPC (defaults to protagonist)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Substring to search in character id.",
                },
                "pov": {
                    "type": "string",
                    "description": (
                        "Optional POV agent id (must be protagonist or a present NPC)."
                        " Defaults to protagonist."
                    ),
                },
            },
            "required": ["q"],
        },
        fn=_characters_query_fn(world, scene),
    ))

    # --- factions_query: POV faction membership/rank lookup with fog ---
    tools.append(Tool(
        name="factions_query",
        description=(
            "Look up factions by id or name substring. Returns faction existence (always public)."
            " Membership and rank are fog-gated: only members whose rank in the faction"
            " the POV agent knows (via knows('<member>.rank:<faction>')) are surfaced."
            " Optional: pov=<entity_id> shifts POV to a present NPC (defaults to protagonist)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Substring to search in faction id or seed name.",
                },
                "pov": {
                    "type": "string",
                    "description": (
                        "Optional POV agent id (must be protagonist or a present NPC)."
                        " Defaults to protagonist."
                    ),
                },
            },
            "required": ["q"],
        },
        fn=_factions_query_fn(world, scene),
    ))

    # --- ambient_query: PUBLIC tier (passerby / common knowledge) — T9 ---
    # Always present (in both POV and DM registries): it carries no private data,
    # only structural seeds + secrecy=="public" facts.
    tools.append(Tool(
        name="ambient_query",
        description=(
            "Consult LOCAL COMMON KNOWLEDGE / what a random passerby could relay"
            " (use this for 找路人打听 / 街谈巷议 — when no specific tracked NPC is"
            " being asked). Returns public place/faction seeds and ONLY facts the"
            " world has marked as public knowledge. Hard secrets are structurally"
            " excluded — you decide how much THIS passerby plausibly knows and may"
            " add deniable rumor on top. No POV/knowledge gating."
        ),
        parameters={
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "What is being asked about (matches place/faction/fact text).",
                },
            },
            "required": ["q"],
        },
        fn=_ambient_query_fn(world, scene),
    ))

    # --- dm_world_query: DM ground-truth tier — only when dm=True (T9) ---
    if dm:
        tools.append(Tool(
            name="dm_world_query",
            description=(
                "AUTHORING ground truth: returns every matching entity with its"
                " tier/attrs and ALL facts (any secrecy) at their TRUE values,"
                " bypassing fog and the secrecy floor. For maintaining world"
                " consistency — never for player-facing fog-of-war."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Substring to search across entity ids and seeds.",
                    },
                },
                "required": ["q"],
            },
            fn=_dm_world_query_fn(world, scene),
        ))

    log.debug(
        "build_tool_registry tools=%s dm=%s scene_protagonist=%s",
        [t.name for t in tools], dm, scene.get("protagonist"),
    )
    return ToolRegistry(tools)
