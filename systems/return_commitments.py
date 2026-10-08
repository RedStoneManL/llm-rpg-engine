"""Typed, immutable item-return promises backed by canonical physical transfers.

The host alone authorizes creation and supplies actual player-action evidence.
Exact quote matching is a provenance check, not proof of semantic entailment,
consent, legal ownership, or the eligibility of a proposed promise. A recipient
is only the agreed return destination (a Person or Place).

No legacy narrative ``promise_made``/``promise_kept`` events are owned here.
"""
from __future__ import annotations

import copy
import json

from context.access import pov_world
from kernel.contextsystem import ContextSystem, Fragment, ValidationError
from kernel.events import kernel_event


_CREATION_FIELDS = {"id", "item", "debtor", "recipient", "due", "evidence"}
_VISIBLE_FIELDS = _CREATION_FIELDS | {"status", "created_at", "created_turn", "fulfilled_at"}
_INITIAL_SOURCE = "initial_held_by_source_event"
_DEBTOR_KNOWN = "debtor_known_entities"


def _nonblank(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _integer(value, *, minimum=0, maximum=None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= minimum and (maximum is None or value <= maximum))


def _clock(day, band) -> tuple[int, int]:
    if not _integer(day, minimum=1) or not _integer(band, maximum=3):
        raise ValueError("return commitment clock requires day >= 1 and band in 0..3")
    return day, band


def normalized_deadline(due) -> tuple[int, int, str]:
    """Validate a deadline and compare legacy dates as inclusive, without edits.

    The original due mapping remains part of the immutable event and record;
    normalization is only a comparison key, never a replay migration.
    """
    if (not isinstance(due, dict)
            or set(due) not in ({"day", "band"}, {"day", "band", "boundary"})):
        raise ValueError("return commitment due requires day and band, with optional boundary")
    day, band = _clock(due["day"], due["band"])
    boundary = due.get("boundary", "inclusive")
    if boundary not in ("inclusive", "exclusive"):
        raise ValueError("return commitment due boundary must be inclusive or exclusive")
    return day, band, boundary


def _now(world, *, day=None) -> tuple[int, int]:
    meta = world.get("meta", {})
    current_day = day if day is not None else meta.get("day")
    if current_day is None:
        current_day = 1
    band = meta.get("band")
    return _clock(current_day, 0 if band is None else band)


def _records(world) -> dict:
    return world.get("systems", {}).get("return_commitments", {}).get("records", {})


def _turn(value) -> int:
    if value is None:
        return 0
    if not _integer(value):
        raise ValueError("return commitment turn must be a nonnegative integer")
    return value


def _known_to_debtor(world, data, day) -> list[str]:
    """Remember only identities visible in the debtor's actual creation POV.

    This is record-local continuity, not a knowledge grant or a change to the
    ontology. It cannot reveal facts, inventories, or previously hidden people.
    Derive co-presence from the graph, never an event summary/model declaration.
    """
    graph = world["systems"]["ontology"]
    locations = graph.neighbors(data["debtor"], "located_in", day)
    scene = {"protagonist": data["debtor"], "day": day, "present": []}
    if len(locations) == 1:
        scene["location"] = locations[0]
        scene["present"] = [entity.id for entity in graph.entities.values()
                            if entity.etype == "Person"
                            and locations[0] in graph.neighbors(entity.id, "located_in", day)]
    visible = pov_world(world, scene)["systems"]["ontology"]
    return [data[field] for field in ("item", "debtor", "recipient")
            if visible.get_entity(data[field]) is not None]


def _creation_record(world, event) -> dict:
    data = event.get("deltas")
    if not isinstance(data, dict) or set(data) != _CREATION_FIELDS:
        raise ValueError("item_return_promised requires exactly id, item, debtor, recipient, due, evidence")
    for field in ("id", "item", "debtor", "recipient"):
        if not _nonblank(data[field]):
            raise ValueError(f"return commitment {field} must be a nonempty string")
    if data["id"] in _records(world):
        raise ValueError("return commitment id already exists; records cannot be rewritten")
    graph = world.get("systems", {}).get("ontology")
    if graph is None:
        raise ValueError("return commitments require the ontology graph")
    for field, types in (("item", {"Object"}), ("debtor", {"Person"}),
                         ("recipient", {"Person", "Place"})):
        entity = graph.get_entity(data[field])
        if entity is None or entity.etype not in types:
            raise ValueError(f"return commitment {field} requires an existing typed entity")
    if data["debtor"] == data["recipient"]:
        raise ValueError("return commitment debtor and recipient must differ")
    due_day, due_band, boundary = normalized_deadline(data["due"])
    due_time = due_day, due_band
    created_time = _now(world, day=event.get("day"))
    current_time = max(_now(world), created_time)
    if due_time < current_time:
        raise ValueError("return commitment due cannot precede the current clock")
    if boundary == "exclusive" and due_time == current_time:
        raise ValueError("exclusive return commitment due must follow the current clock")
    evidence = data["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"player_actions", "quotes"}:
        raise ValueError("return commitment evidence requires player_actions and quotes")
    for field in ("player_actions", "quotes"):
        if (not isinstance(evidence[field], list) or not evidence[field]
                or any(not _nonblank(value) for value in evidence[field])):
            raise ValueError(f"return commitment evidence.{field} requires nonempty strings")
    # This preserves literal provenance only. The host authenticates the actions
    # and determines whether they actually authorize the extracted commitment.
    if any(not any(quote in action for action in evidence["player_actions"])
           for quote in evidence["quotes"]):
        raise ValueError("return commitment quotes must occur exactly in the supplied player actions")
    relations = graph.relations_at(data["item"], "held_by", created_time[0])
    if len(relations) > 1:
        raise ValueError("return commitment requires an unambiguous physical record")
    record = copy.deepcopy(data)
    record.update(status="open", created_at={"day": created_time[0], "band": created_time[1]},
                  created_turn=_turn(event.get("turn")),
                  initial_held_by_source_event=relations[0].source_event if relations else None,
                  debtor_known_entities=_known_to_debtor(world, data, created_time[0]))
    return record


def _return_proven(world, record, day) -> bool:
    """Check final physical state and post-creation provenance, never narration."""
    graph = world.get("systems", {}).get("ontology")
    if graph is None:
        return False
    item = graph.get_entity(record["item"])
    debtor = graph.get_entity(record["debtor"])
    recipient = graph.get_entity(record["recipient"])
    if (item is None or item.etype != "Object" or debtor is None or debtor.etype != "Person"
            or recipient is None or recipient.etype not in {"Person", "Place"}):
        return False
    relations = graph.relations_at(record["item"], "held_by", day)
    if len(relations) != 1:
        return False
    relation = relations[0]
    # A fresh receipt id is insufficient: legacy/direct projection can reassert
    # B -> B without an actual handover. Inspect insertion history, including
    # superseded same-day edges, to require a changed holder for this receipt.
    # No preceding held_by edge represents a genuine first placement.
    previous = None
    for candidate in graph.relations:
        if candidate is relation:
            break
        if candidate.src == record["item"] and candidate.rel == "held_by":
            previous = candidate
    if previous is not None and previous.dst == relation.dst:
        return False
    return (relation.is_current() and relation.dst == record["recipient"]
            and _nonblank(relation.source_event)
            and relation.source_event != record[_INITIAL_SOURCE]
            and _integer(relation.ingest_turn)
            and relation.ingest_turn >= record["created_turn"])


def visible_records(world, scene, *, commitment_id=None) -> list[dict]:
    """Copied canonical records for a party, after applying the shared POV gate.

    Knowing an id or being an onlooker grants no access. Every referenced item
    and party must be visible, or already seen at creation by this same debtor.
    Only the debtor sees their original action evidence; another party receives
    contract fields/status without raw player text or quotes. Internal physical
    provenance and identity-memory metadata are omitted from every view.
    ``overdue`` is derived from the clock, never persisted or inferred from prose.
    """
    actor = scene.get("protagonist")
    if not _nonblank(actor) or (commitment_id is not None and not _nonblank(commitment_id)):
        return []
    view = pov_world(world, scene)
    graph = view.get("systems", {}).get("ontology")
    if graph is None:
        return []
    now = _now(world)
    result = []
    for record in _records(world).values():
        if commitment_id is not None and record["id"] != commitment_id:
            continue
        if actor not in (record["debtor"], record["recipient"]):
            continue
        remembered = record.get(_DEBTOR_KNOWN, []) if actor == record["debtor"] else []
        if any(graph.get_entity(record[field]) is None and record[field] not in remembered
               for field in ("item", "debtor", "recipient")):
            continue
        exposed = {key: copy.deepcopy(value) for key, value in record.items()
                   if key in _VISIBLE_FIELDS and (key != "evidence" or actor == record["debtor"])}
        due_day, due_band, boundary = normalized_deadline(record["due"])
        due_time = due_day, due_band
        exposed["overdue"] = (record["status"] == "open"
                              and (now >= due_time if boundary == "exclusive" else now > due_time))
        result.append(exposed)
    return result


def completion_events(world, *, day, scene, turn) -> list[dict]:
    """Generate completions after all turn effects, without mutating the world.

    The caller must stage/apply these events transactionally after final item
    transfers. This uses the canonical world rather than a POV-filtered view.
    """
    _now(world, day=day)
    _turn(turn)
    return [kernel_event("item_return_fulfilled", day=day, scene=scene, turn=turn,
                         summary="物品归还承诺已按实际交接履行", deltas={"id": record["id"]})
            for record in _records(world).values()
            if record["status"] == "open" and _return_proven(world, record, day)]


def _declaration_errors(decl) -> list[ValidationError]:
    if not isinstance(decl, list):
        return [ValidationError("promises", "", "bad_shape", "promises 必须是数组")]
    errors = []
    for index, row in enumerate(decl):
        field = f"[{index}]"
        if not isinstance(row, dict):
            errors.append(ValidationError("promises", field, "bad_shape", "归还声明必须是对象"))
            continue
        if row.get("op") != "fulfill":
            errors.append(ValidationError("promises", field + ".op", "promise_op",
                "promises 仅允许 fulfill；创建或更改承诺须由引擎根据玩家行动确认"))
        if not _nonblank(row.get("id")):
            errors.append(ValidationError("promises", field + ".id", "missing", "归还声明必须含非空 id"))
        if set(row) - {"op", "id"}:
            errors.append(ValidationError("promises", field, "immutable",
                "fulfill 只能含 op 和 id，不能更改承诺内容"))
    return errors


class ReturnCommitmentSystem(ContextSystem):
    name = "return_commitments"

    def requires(self) -> set[str]:
        return {"ontology", "time", "object"}

    def event_types(self) -> set[str]:
        return {"item_return_promised", "item_return_fulfilled"}

    def commit_sections(self) -> set[str]:
        return {"promises"}

    def empty_state(self) -> dict:
        return {"records": {}}

    def created_ids(self, section, decl) -> set[str]:
        # Commitment ids are not graph entities; fulfill only references an id.
        return set()

    def apply(self, world, event) -> None:
        kind = event.get("type")
        if kind not in self.event_types():
            return
        if kind == "item_return_promised":
            record = _creation_record(world, event)
            world["systems"].setdefault(self.name, self.empty_state())["records"][record["id"]] = record
            return
        data = event.get("deltas")
        if not isinstance(data, dict) or set(data) != {"id"} or not _nonblank(data.get("id")):
            raise ValueError("item_return_fulfilled requires exactly a nonempty id")
        record = _records(world).get(data["id"])
        if record is None or record["status"] != "open":
            raise ValueError("return commitment is unavailable or no longer open")
        day, band = _now(world, day=event.get("day"))
        if (day, band) < (record["created_at"]["day"], record["created_at"]["band"]):
            raise ValueError("return completion cannot precede creation")
        if not _return_proven(world, record, day):
            raise ValueError("return completion requires a new canonical physical return")
        record["status"] = "fulfilled"
        record["fulfilled_at"] = {"day": day, "band": band}

    def validate(self, section, decl, world) -> list[ValidationError]:
        if section != "promises":
            return []
        errors = _declaration_errors(decl)
        if errors:
            return errors
        seen = set()
        for index, row in enumerate(decl):
            record = _records(world).get(row["id"])
            if record is None or record["status"] != "open" or row["id"] in seen:
                errors.append(ValidationError("promises", f"[{index}].id", "unavailable",
                    "承诺不存在、已结束或重复声明；只能引用当前可见的未完成承诺"))
            seen.add(row["id"])
        # Deliberately no holder checks here: items may transfer in this commit.
        return errors

    def to_events(self, section, decl, *, turn, day, scene) -> list[dict]:
        if section != "promises":
            return []
        errors = _declaration_errors(decl)
        if errors:
            raise ValueError(errors[0].hint)
        return [kernel_event("item_return_fulfilled", day=day, scene=scene, turn=turn,
                             summary="物品归还承诺履行待核验", deltas={"id": row["id"]})
                for row in decl]

    def inject(self, scene, world) -> Fragment | None:
        records = visible_records(world, scene)
        if not records:
            return None
        text = ("【物品归还承诺·引擎记录】\n" + json.dumps(records, ensure_ascii=False)
                + "\n以上当事人、期限与玩家原话以此记录为准，不因叙述摘要改写。"
                  "due.boundary=exclusive 表示须在指定 day/band 到来前归还，到达该时段即逾期；"
                  "inclusive（含未写 boundary 的旧记录）允许在指定时段内归还，超过该时段才逾期。"
                  "recipient 是约定归还目的地，不代表法定所有者；open/overdue 不推断道德责任或同意。")
        return Fragment(self.name, "scene", text,
                        'promises 仅可声明 [{"op":"fulfill","id":"已有承诺 id"}]；'
                        '实际归还须通过 items 完成交接，引擎核验最终 held_by。'
                        '不得自行创建、撤销、改期、更换当事人或物品。')
