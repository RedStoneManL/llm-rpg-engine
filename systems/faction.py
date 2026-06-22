"""FactionSystem — Faction entities + membership in the shared FactGraph.

Architecture: Same system pattern (own events/sections; write
world["systems"]["ontology"]; require OntologySystem). Factions = entities
whose attrs define canonical ranks/groups once. Membership = multi-valued
member_of relation (supersede=False). A member's rank-in-a-faction = a Fact
rank:{faction} (predicate-scoped supersession → promotion supersedes cleanly
per faction).

Storage:
  faction_created  → add_entity("Faction", ..., ranks=list, groups=list, **attrs)
  member_changed   → add_relation(person,"member_of",faction, supersede=False)
                     + assert_fact(person, "rank:{faction}", rank)  if rank present
                     + assert_fact(person, "group:{faction}", group) if group present

Module helpers (for 认知 S1e audience resolution):
  members_of(graph, faction, day, *, min_rank=None, group=None) -> list[str]
  member_rank(graph, person, faction, day) -> str | None
"""
from __future__ import annotations

from typing import Any

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event
from facts.graph import FactGraph
from engine.log import get_logger

log = get_logger("systems.faction")


# ---------------------------------------------------------------------------
# Module-level audience-resolution helpers
# ---------------------------------------------------------------------------

def members_of(
    graph: FactGraph,
    faction: str,
    day: int,
    *,
    min_rank: str | None = None,
    group: str | None = None,
) -> list[str]:
    """Return ids of persons with a current member_of → faction relation at day.

    Optional filters:
      group    → keep only members whose value_at(person, f"group:{faction}", day)
                 equals group.
      min_rank → keep only members whose rank index in the faction's 'ranks' attr
                 is >= index of min_rank.  Members with no rank fact have index -1
                 (below any real rank) and are excluded when min_rank is set.
    """
    faction_entity = graph.get_entity(faction)
    ranks_list: list[str] = (
        faction_entity.attrs.get("ranks", []) if faction_entity else []
    )

    result: list[str] = []
    for eid, entity in graph.entities.items():
        # Check if this entity is a member of the faction at `day`
        factions_joined = graph.neighbors(eid, "member_of", day)
        if faction not in factions_joined:
            continue

        # group filter
        if group is not None:
            g_val = graph.value_at(eid, f"group:{faction}", day)
            if g_val != group:
                continue

        # min_rank filter
        if min_rank is not None:
            min_idx = ranks_list.index(min_rank) if min_rank in ranks_list else -1
            r_val = graph.value_at(eid, f"rank:{faction}", day)
            member_idx = ranks_list.index(r_val) if r_val in ranks_list else -1
            if member_idx < min_idx:
                continue

        result.append(eid)

    return result


def member_rank(
    graph: FactGraph,
    person: str,
    faction: str,
    day: int,
) -> str | None:
    """Return the person's rank in the faction at day, or None if not set."""
    return graph.value_at(person, f"rank:{faction}", day)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# FactionSystem
# ---------------------------------------------------------------------------

class FactionSystem(ContextSystem):
    """ContextSystem that owns faction events and writes to the shared FactGraph.

    Requires OntologySystem to be registered (its shared FactGraph is at
    world["systems"]["ontology"]).

    Commit sections:
        "factions"  → faction_created / member_changed events
    """

    name = "faction"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"faction_created", "member_changed"}

    def commit_sections(self) -> set[str]:
        return {"factions"}

    def empty_state(self) -> dict:
        """FactionSystem owns no separate slice — Factions live in the shared graph."""
        return {}

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def apply(self, world: dict, event: dict) -> None:
        g: FactGraph = world["systems"]["ontology"]
        d = event.get("deltas", {})
        t = event["type"]

        if t == "faction_created":
            # Defensive: skip if id is missing (should have been caught by validate)
            fid = d.get("id")
            if not fid:
                log.warning("faction_created event missing 'id' in deltas — skipping (event=%s)",
                            event.get("id"))
                return
            tier = d.get("tier", "mentioned")
            ranks = d.get("ranks", [])
            groups = d.get("groups", [])
            # Extra attrs: exclude structural keys
            attrs: dict[str, Any] = {
                k: v for k, v in d.items()
                if k not in ("id", "tier", "ranks", "groups")
            }
            g.add_entity(fid, "Faction", tier=tier, ranks=ranks, groups=groups, **attrs)
            log.debug("faction_created id=%s tier=%s ranks=%s groups=%s",
                      fid, tier, ranks, groups)

        elif t == "member_changed":
            # Defensive: skip if person or faction is missing (should have been caught by validate)
            person = d.get("person")
            faction = d.get("faction")
            if not person or not faction:
                log.warning(
                    "member_changed event missing 'person' or 'faction' in deltas — "
                    "skipping (event=%s, person=%r, faction=%r)",
                    event.get("id"), person, faction,
                )
                return
            day = event["day"]
            turn = event.get("turn") or 0
            src = event["id"]

            # member_of is multi-valued (supersede=False)
            g.add_relation(
                person, "member_of", faction,
                day=day, turn=turn, source_event=src,
                supersede=False,
            )

            # Optional rank: stored as fact rank:{faction} — supersedes per-faction
            if "rank" in d:
                g.assert_fact(person, f"rank:{faction}", d["rank"],
                              day=day, turn=turn, source_event=src)

            # Optional group: stored as fact group:{faction}
            if "group" in d:
                g.assert_fact(person, f"group:{faction}", d["group"],
                              day=day, turn=turn, source_event=src)

            log.debug("member_changed person=%s faction=%s rank=%s group=%s",
                      person, faction, d.get("rank"), d.get("group"))

    # ------------------------------------------------------------------
    # Write path: validate + to_events
    # ------------------------------------------------------------------

    def validate(self, section: str, decl: list, world: dict) -> list[ValidationError]:
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        errs: list[ValidationError] = []

        if section != "factions":
            return errs

        for i, item in enumerate(decl or []):
            op = item.get("op", "faction")

            if op == "faction":
                # apply() dereferences d["id"] — must be a non-empty string
                if not item.get("id") or not isinstance(item.get("id"), str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="势力声明必须包含非空字符串 'id' 字段",
                    ))

            elif op == "member":
                # apply() dereferences d["person"] and d["faction"] unconditionally
                person_id = item.get("person")
                faction_id = item.get("faction")

                if not person_id or not isinstance(person_id, str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].person",
                        code="missing",
                        hint="成员变更声明必须包含非空字符串 'person' 字段",
                    ))
                elif g and g.get_entity(person_id) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].person",
                        code="dangling_ref",
                        hint=f"成员 '{person_id}' 不存在于图中",
                    ))

                if not faction_id or not isinstance(faction_id, str):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].faction",
                        code="missing",
                        hint="成员变更声明必须包含非空字符串 'faction' 字段",
                    ))
                elif g and g.get_entity(faction_id) is None:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].faction",
                        code="dangling_ref",
                        hint=f"势力 '{faction_id}' 不存在于图中",
                    ))

        return errs

    def to_events(
        self, section: str, decl: list, *, turn: int, day: int, scene: str
    ) -> list[dict]:
        """Map factions declarations to events by op field."""
        out = []
        if section != "factions":
            return out

        for item in decl:
            op = item.get("op", "faction")
            if op == "faction":
                fid = item.get("id")
                if not fid:
                    log.warning(
                        "to_events: faction item missing 'id' — skipping item=%r", item
                    )
                    continue
                out.append(kernel_event(
                    "faction_created", day=day, scene=scene,
                    summary=f"{fid} 势力创建",
                    deltas=item, turn=turn,
                ))
            elif op == "member":
                person = item.get("person")
                faction = item.get("faction")
                if not person or not faction:
                    log.warning(
                        "to_events: member item missing 'person' or 'faction' — "
                        "skipping item=%r", item
                    )
                    continue
                out.append(kernel_event(
                    "member_changed", day=day, scene=scene,
                    summary=f"{person} 加入 {faction}",
                    deltas=item, turn=turn,
                ))
        return out

    # ------------------------------------------------------------------
    # Read path: inject (FactionSystem has no scene inject for now)
    # ------------------------------------------------------------------

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        """FactionSystem does not inject scene context directly."""
        return None
