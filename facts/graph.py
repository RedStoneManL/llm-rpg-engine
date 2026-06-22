from __future__ import annotations
from typing import Any

from engine.log import get_logger
from facts.entity import Entity
from facts.fact import Fact, Relation

log = get_logger("facts.graph")


class FactGraph:
    """Bitemporal fact graph: entities, typed facts, and typed relation edges.
    All mutations are point-in-time; queries can target any past 'day'.

    Precondition: events must be applied in non-decreasing ``day`` order.
    Out-of-order / flashback asserts raise ValueError."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.facts: list[Fact] = []
        self.relations: list[Relation] = []

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def add_entity(self, id: str, etype: str, tier: str = "mentioned", **attrs: Any) -> Entity:
        e = Entity(id=id, etype=etype, tier=tier, attrs=dict(attrs))
        self.entities[id] = e
        log.debug("add_entity id=%s etype=%s tier=%s", id, etype, tier)
        return e

    def get_entity(self, id: str) -> Entity | None:
        return self.entities.get(id)

    def set_tier(self, id: str, tier: str) -> None:
        e = self.entities.get(id)
        if e is not None:
            e.tier = tier
            log.debug("set_tier id=%s tier=%s", id, tier)

    # ------------------------------------------------------------------
    # Facts (bitemporal, predicate-scoped supersession)
    # ------------------------------------------------------------------

    def assert_fact(
        self,
        subject: str,
        predicate: str,
        value: object,
        *,
        day: int,
        turn: int,
        source_event: str,
        secrecy: str | None = None,
    ) -> Fact:
        # Close the prior current fact for this (subject, predicate)
        for f in self.facts:
            if f.subject == subject and f.predicate == predicate and f.is_current():
                if f.event_time_start > day:
                    raise ValueError(
                        f"non-monotonic assert: day={day} < prior start={f.event_time_start}; "
                        "out-of-order/flashback not supported"
                    )
                f.event_time_end = day
                log.debug("supersede fact subject=%s predicate=%s at day=%d", subject, predicate, day)
                break
        new_fact = Fact(
            subject=subject,
            predicate=predicate,
            value=value,
            event_time_start=day,
            ingest_turn=turn,
            source_event=source_event,
            secrecy=secrecy,
        )
        self.facts.append(new_fact)
        log.debug("assert_fact subject=%s predicate=%s value=%s day=%d", subject, predicate, value, day)
        return new_fact

    def current_facts(self, subject: str) -> list[Fact]:
        """All currently-open facts for a subject (no event_time_end)."""
        return [f for f in self.facts if f.subject == subject and f.is_current()]

    def value_at(self, subject: str, predicate: str, day: int) -> object | None:
        """The value of the fact for (subject, predicate) valid at day, or None."""
        for f in self.facts:
            if f.subject == subject and f.predicate == predicate and f.valid_at(day):
                return f.value
        return None

    def fact_history(self, subject: str, predicate: str) -> list[Fact]:
        """All fact records for (subject, predicate), ordered by assertion time."""
        return [f for f in self.facts if f.subject == subject and f.predicate == predicate]

    # ------------------------------------------------------------------
    # Relations (bitemporal, (src, rel)-scoped supersession)
    # ------------------------------------------------------------------

    def add_relation(
        self,
        src: str,
        rel: str,
        dst: str,
        *,
        day: int,
        turn: int,
        source_event: str,
        supersede: bool = True,
        **attrs: Any,
    ) -> Relation:
        # Supersede prior current relation(s) when appropriate
        if supersede:
            # single-valued: close any current (src, rel) regardless of dst
            for r in self.relations:
                if r.src == src and r.rel == rel and r.is_current():
                    if r.event_time_start > day:
                        raise ValueError(
                            f"non-monotonic assert: day={day} < prior start={r.event_time_start}; "
                            "out-of-order/flashback not supported"
                        )
                    r.event_time_end = day
                    log.debug("supersede relation src=%s rel=%s at day=%d", src, rel, day)
                    break
        else:
            # multi-valued: close prior current relation for SAME (src, rel, dst) to avoid duplication
            for r in self.relations:
                if r.src == src and r.rel == rel and r.dst == dst and r.is_current():
                    if r.event_time_start > day:
                        raise ValueError(
                            f"non-monotonic assert: day={day} < prior start={r.event_time_start}; "
                            "out-of-order/flashback not supported"
                        )
                    r.event_time_end = day
                    log.debug("dedup-supersede relation src=%s rel=%s dst=%s at day=%d",
                              src, rel, dst, day)
                    break
        new_rel = Relation(
            src=src,
            rel=rel,
            dst=dst,
            event_time_start=day,
            ingest_turn=turn,
            source_event=source_event,
            attrs=dict(attrs),
        )
        self.relations.append(new_rel)
        log.debug("add_relation src=%s rel=%s dst=%s day=%d attrs=%s", src, rel, dst, day, attrs)
        return new_rel

    def relations_at(self, src: str, rel: str, day: int) -> list[Relation]:
        """All relations for (src, rel) valid at day."""
        return [r for r in self.relations if r.src == src and r.rel == rel and r.valid_at(day)]

    def neighbors(self, src: str, rel: str, day: int) -> list[str]:
        """Destination entity ids for (src, rel) valid at day."""
        return [r.dst for r in self.relations_at(src, rel, day)]

    def relation_attrs_at(self, src: str, rel: str, day: int) -> list[tuple[str, dict]]:
        """Return [(dst, attrs)] for all (src, rel) relations valid at day."""
        return [(r.dst, r.attrs) for r in self.relations_at(src, rel, day)]
