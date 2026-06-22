"""CharacterSystem — Persons as entities in the shared FactGraph.

Architecture: Follows the system pattern from S1b (PlaceSystem). Owns its
event-types/sections; reads/writes the shared FactGraph at
world["systems"]["ontology"]. Requires OntologySystem to be registered before
CharacterSystem in the Registry.

Storage model — everything about a character except identity (id/etype/tier)
is a bitemporal Fact on the Person entity:
  • character_created  → add_entity + assert_fact for sketch, goal, and
                         OPTIONALLY past/hidden (only if present in deltas).
  • character_evolved  → assert_fact(id, predicate, value), superseding the
                         prior value for that predicate (arc preserved in
                         fact_history).
  • relationship_changed → assert_fact(id, "trust:<toward>", value).

Validation stance — 机械处严，创作处松:
  • create items: require id + sketch + goal; NEVER require past/hidden.
  • evolve/relationship items: require that the subject entity already exists
    in the graph (dangling_ref if not).
  • A minimal "纯粹之人" create (id+sketch+goal, no facets) must always pass
    clean.

Staleness detection (active-but-unchanged) and reflection (superseding sketch/
arc facts based on narration) are S4/S2 concerns — this system only records.
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event
from facts.graph import FactGraph
from engine.log import get_logger

log = get_logger("systems.character")

_RESERVED_PREFIXES = {"knows", "rank", "group", "trust"}


class CharacterSystem(ContextSystem):
    """ContextSystem that owns character events and writes to the shared FactGraph.

    Requires OntologySystem to be registered (its shared FactGraph is at
    world["systems"]["ontology"]).

    Commit sections:
        "cast"  → character_created / character_evolved / relationship_changed events
    """

    name = "character"

    def requires(self) -> set[str]:
        return {"ontology"}

    def event_types(self) -> set[str]:
        return {"character_created", "character_evolved", "relationship_changed"}

    def commit_sections(self) -> set[str]:
        return {"cast"}

    def empty_state(self) -> dict:
        """CharacterSystem owns no separate slice — Persons live in the shared graph."""
        return {}

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def apply(self, world: dict, event: dict) -> None:
        g: FactGraph = world["systems"]["ontology"]
        d = event.get("deltas", {})
        t = event["type"]

        if t == "character_created":
            pid = d.get("id")
            if not pid:
                log.warning("character_created event missing deltas.id — skipping")
                return
            sketch = d.get("sketch")
            goal = d.get("goal")
            if not sketch or not goal:
                log.warning(
                    "character_created id=%s missing sketch/goal — skipping", pid
                )
                return
            tier = d.get("tier", "mentioned")
            g.add_entity(pid, "Person", tier=tier)
            day = event["day"]
            turn = event.get("turn") or 0
            src = event["id"]
            g.assert_fact(pid, "sketch", sketch,
                          day=day, turn=turn, source_event=src)
            g.assert_fact(pid, "goal", goal,
                          day=day, turn=turn, source_event=src)
            # Optional facets — only assert if present in deltas
            if "past" in d:
                g.assert_fact(pid, "past", d["past"],
                              day=day, turn=turn, source_event=src)
            if "hidden" in d:
                g.assert_fact(pid, "hidden", d["hidden"],
                              day=day, turn=turn, source_event=src)
            # Phase D: stamp last_update for drift tracking
            g.get_entity(pid).attrs["last_update"] = day
            log.debug("character_created id=%s tier=%s", pid, tier)

        elif t == "character_evolved":
            pid = d.get("id")
            predicate = d.get("predicate")
            value = d.get("value")
            if not pid or not predicate or value is None:
                log.warning(
                    "character_evolved missing required field(s) id=%r predicate=%r value=%r — skipping",
                    pid, predicate, value,
                )
                return
            g.assert_fact(pid, predicate, value,
                          day=event["day"],
                          turn=event.get("turn") or 0,
                          source_event=event["id"])
            # Phase D: advance last_update
            e = g.get_entity(pid)
            if e is not None:
                e.attrs["last_update"] = event["day"]
            log.debug("character_evolved id=%s predicate=%s value=%s",
                      pid, predicate, value)

        elif t == "relationship_changed":
            pid = d.get("id")
            toward = d.get("toward")
            value = d.get("value")
            if not pid or not toward or value is None:
                log.warning(
                    "relationship_changed missing required field(s) id=%r toward=%r value=%r — skipping",
                    pid, toward, value,
                )
                return
            g.assert_fact(pid, f"trust:{toward}", value,
                          day=event["day"],
                          turn=event.get("turn") or 0,
                          source_event=event["id"])
            # Phase D: advance last_update
            e = g.get_entity(pid)
            if e is not None:
                e.attrs["last_update"] = event["day"]
            log.debug("relationship_changed id=%s toward=%s value=%s",
                      pid, toward, value)

    # ------------------------------------------------------------------
    # Write path: validate + to_events
    # ------------------------------------------------------------------

    def created_ids(self, section: str, decl: list) -> set[str]:
        # A cast item creates a character only when op is "create" (the default);
        # evolve / relationship reference an existing character.
        out: set[str] = set()
        if section == "cast" and isinstance(decl, list):
            for item in decl:
                if (isinstance(item, dict) and item.get("id")
                        and item.get("op", "create") == "create"):
                    out.add(item["id"])
        return out

    def validate(self, section: str, decl: list, world: dict) -> list[ValidationError]:
        """Validate cast declarations.

        机械处严：
          create items require id + sketch + goal; subject refs for
          evolve/relationship must already exist in the graph.
        创作处松：
          past/hidden facets are NEVER required; "纯粹之人" minimal creates pass
          clean.
        """
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        errs: list[ValidationError] = []

        if section != "cast":
            return errs

        for i, item in enumerate(decl or []):
            op = item.get("op", "create")

            if op == "create":
                if not item.get("id"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="角色声明必须包含 'id' 字段",
                    ))
                if not item.get("sketch"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].sketch",
                        code="missing",
                        hint="角色声明必须包含 'sketch'（人物素描）字段",
                    ))
                if not item.get("goal"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].goal",
                        code="missing",
                        hint="角色声明必须包含 'goal'（当前目标）字段",
                    ))
                # NOTE: past/hidden are NEVER required — this is the
                # anti-脸谱 guarantee; a minimal "纯粹之人" create is valid.

            elif op == "evolve":
                # Require id, predicate, value — to_events/apply dereference all three
                if not item.get("id"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="evolve 操作必须包含 'id' 字段以指定目标角色",
                    ))
                else:
                    pid = item.get("id")
                    if g and g.get_entity(pid) is None:
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].id",
                            code="dangling_ref",
                            hint=f"角色 '{pid}' 不存在于图中，请先通过 create 声明",
                        ))
                if not item.get("predicate"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].predicate",
                        code="missing",
                        hint="evolve 操作必须包含 'predicate' 字段以指定要更新的属性名",
                    ))
                else:
                    predicate = item.get("predicate", "")
                    prefix = predicate.split(":")[0]
                    if ":" in predicate and prefix in _RESERVED_PREFIXES:
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].predicate",
                            code="reserved",
                            hint="predicate 不可写入其它系统命名空间(含 ':')",
                        ))
                if "value" not in item:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].value",
                        code="missing",
                        hint="evolve 操作必须包含 'value' 字段以指定属性的新值",
                    ))

            elif op == "relationship":
                # Require id, toward, value — to_events/apply dereference all three
                if not item.get("id"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].id",
                        code="missing",
                        hint="relationship 操作必须包含 'id' 字段以指定发起方角色",
                    ))
                else:
                    pid = item.get("id")
                    if g and g.get_entity(pid) is None:
                        errs.append(ValidationError(
                            section=section,
                            field=f"[{i}].id",
                            code="dangling_ref",
                            hint=f"角色 '{pid}' 不存在于图中，请先通过 create 声明",
                        ))
                if not item.get("toward"):
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].toward",
                        code="missing",
                        hint="relationship 操作必须包含 'toward' 字段以指定关系目标方",
                    ))
                if "value" not in item:
                    errs.append(ValidationError(
                        section=section,
                        field=f"[{i}].value",
                        code="missing",
                        hint="relationship 操作必须包含 'value' 字段以描述信任关系内容",
                    ))

        return errs

    def to_events(
        self, section: str, decl: list, *, turn: int, day: int, scene: str
    ) -> list[dict]:
        """Map cast declarations to events by op field (default: "create")."""
        out = []
        if section != "cast":
            return out

        for item in decl:
            op = item.get("op", "create")
            if op == "create":
                out.append(kernel_event(
                    "character_created", day=day, scene=scene,
                    summary=f"{item.get('id', '?')} 登场",
                    deltas=item, turn=turn,
                ))
            elif op == "evolve":
                out.append(kernel_event(
                    "character_evolved", day=day, scene=scene,
                    summary=f"{item.get('id', '?')}.{item.get('predicate', '?')}={item.get('value', '?')}",
                    deltas=item, turn=turn,
                ))
            elif op == "relationship":
                out.append(kernel_event(
                    "relationship_changed", day=day, scene=scene,
                    summary=f"{item.get('id', '?')} 对 {item.get('toward', '?')} 的信任变化",
                    deltas=item, turn=turn,
                ))
        return out

    # ------------------------------------------------------------------
    # Read path: inject
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Read path: recall (M4)
    # ------------------------------------------------------------------

    def recall(self, query: str, world: dict) -> list[RecallHit]:
        """Return RecallHits for Person entities whose sketch or goal contains query.

        Substring matching only — semantic ranking happens in the assembler via S2.
        """
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        if not g:
            return []
        hits: list[RecallHit] = []
        for eid, entity in g.entities.items():
            if entity.etype != "Person":
                continue
            # Use current facts (no specific day needed here — grab current values)
            sketch = None
            goal = None
            for f in g.current_facts(eid):
                if f.predicate == "sketch":
                    sketch = str(f.value)
                elif f.predicate == "goal":
                    goal = str(f.value)
            matched = (
                (sketch and query in sketch) or
                (goal and query in goal)
            )
            if matched:
                text_parts = []
                if sketch:
                    text_parts.append(sketch)
                if goal:
                    text_parts.append(f"目标：{goal}")
                hits.append(RecallHit(
                    system="character",
                    score=1.0,
                    text=f"{eid}：" + " | ".join(text_parts),
                    ref={"id": eid},
                ))
                log.debug("recall hit person=%s query=%r", eid, query)
        return hits

    def inject(self, scene: dict, world: dict) -> Fragment | None:
        """Return a scene-layer Fragment with current-state cards for present characters.

        For each id in scene["present"] that is a Person entity, renders:
            {id}：{sketch} | 目标：{goal} | 此刻：{mood或'—'}
        using value_at(id, pred, day) to get bitemporal current values.

        Returns None if present is empty or absent.
        """
        g: FactGraph | None = world.get("systems", {}).get("ontology")
        if not g:
            return None
        present = scene.get("present", [])
        if not present:
            return None

        day = scene.get("day", 0)
        cards = []
        for pid in present:
            entity = g.get_entity(pid)
            if entity is None or entity.etype != "Person":
                continue
            sketch = g.value_at(pid, "sketch", day) or ""
            goal = g.value_at(pid, "goal", day) or ""
            mood = g.value_at(pid, "mood", day) or "—"
            cards.append(f"{pid}：{sketch} | 目标：{goal} | 此刻：{mood}")

        if not cards:
            return None

        return Fragment(
            system="character",
            layer="scene",
            text="\n".join(cards),
        )
