"""Loopback-only player and explicitly opted-in GM dashboards for the offline RPG demo.

The default is intentionally a *player* surface, not a GM/editor console.  It
never serializes the event store or canonical world.  Every game-facing graph
read is made from ``context.access.pov_world`` and the only prose catalogue
exposed is the public CodexSystem index.  ``--gm`` starts a separate, read-only
server mode; the player server does not have GM routes or DTOs at all.

Run it locally with::

    python -m app.gui --campaign ./campaigns/gui-demo --demo

The server always binds to 127.0.0.1.  The demo uses a deterministic local
strategy and FakeLLMProvider; it makes no network or model request.
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, make_server

from app.engine import (build_engine, last_turn, projected_revision,
                        refresh_engine_world_if_advanced, reproject_engine_world, rewind)
from app.play import _build_scene, _previous_scene_before_latest_action
from app.player_recall import player_recall
from app.player_objectives import player_objectives
from app.player_character import player_character_facts
from engine.store import RevisionConflict
from context.access import pov_world
from context.capabilities import scene_capabilities
from context.native_authoring import host_player_statement_receipt, native_authoring_context
from kernel.clock import band_name
from kernel.events import kernel_event
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.turn import REQUIRED_SECTIONS, run_turn
from loop.strategy import AuthorStrategy


log = logging.getLogger(__name__)
_MAX_ACTION_BYTES = 2_000
_MAX_RECALL_QUERY_BYTES = 320


class GuiRequestError(ValueError):
    """A safe, player-facing request error (never contains provider/store text)."""


class GuiRecoveryError(RuntimeError):
    """A committed action cannot be safely reconstructed; never a confirmed rejection."""


class OfflineDemoStrategy:
    """Deterministic, local narrator used by the GUI demo.

    It deliberately exercises the normal turn publication path, but does not
    pretend to be an AI narrator.  Each action moves the protagonist to the
    next visible adjacent place when one exists, so the map has observable,
    offline behaviour without a remote provider.
    """

    def produce(self, registry, world, scene, player_input, *, provider, embedder=None, repair=None):
        # The demo has no special authority: its movement proposal must use
        # the same known-exit view that is serialized to the player, never the
        # backstage/canonical adjacency graph.
        capability = scene_capabilities(world, scene)
        destination = capability.exits[0] if capability.exits else None
        moves: list[dict[str, str]] = []
        if destination and scene.get("protagonist"):
            moves.append({"who": scene["protagonist"], "to": destination.id})
        if destination:
            narration = f"离线演示已保存你的行动：{player_input}。你沿着已知道路来到 {destination.label}。"
        else:
            narration = f"离线演示已保存你的行动：{player_input}。这里暂时没有已知出口。"
        return TurnCommit(narration=narration, sections={
            "moves": moves,
            "places": [],
            "cast": [],
            "facts": [],
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "离线演示中的即时行动"}],
        })

    def repair_sections(self, *args, **kwargs):  # pragma: no cover - demo output is already valid
        return {}

    def commit_to_thread(self, narration: str) -> None:
        return None


def _seed_demo_campaign(engine) -> None:
    """Seed a small existing-engine world only when the campaign is empty."""
    if any(True for _ in engine.store.iter_events()):
        return
    events = [
        kernel_event("character_created", day=1, scene="s1", turn=0,
                     summary="离线演示主角", deltas={
                         "id": "protagonist", "sketch": "一位初来石桥镇的旅人",
                         "goal": "在黎明前找到旧塔的线索", "tier": "tracked",
                     }),
        kernel_event("place_created", day=1, scene="s1", turn=0,
                     summary="石桥客栈", deltas={"id": "stonebridge_inn", "level": 2,
                                                   "kind": "venue", "label": "石桥客栈",
                                                   "seed": "石桥镇边的暖灯客栈", "tier": "tracked"}),
        kernel_event("place_created", day=1, scene="s1", turn=0,
                     summary="石桥集市", deltas={"id": "stonebridge_market", "level": 2,
                                                   "kind": "settlement", "label": "石桥集市",
                                                   "seed": "清晨刚开张的石桥集市", "tier": "tracked"}),
        kernel_event("place_linked", day=1, scene="s1", turn=0,
                     summary="客栈与集市相连", deltas={"a": "stonebridge_inn", "b": "stonebridge_market", "travel_cost": 1}),
        # The player POV deliberately cannot see this Place or its edge.  GM
        # mode reads the canonical graph so a local referee can inspect the
        # full map without feeding it back into player context or narration.
        kernel_event("entity_created", day=1, scene="s1", turn=0,
                     summary="北岸旧塔遗址（隐藏地点）", deltas={"id": "north_bank_old_tower", "etype": "Place", "tier": "tracked",
                     "attrs": {"label": "北岸旧塔遗址", "kind": "ruin", "visibility": "hidden"}}),
        kernel_event("place_linked", day=1, scene="s1", turn=0,
                     summary="集市通向北岸旧塔", deltas={"a": "stonebridge_market", "b": "north_bank_old_tower", "travel_cost": 2}),
        kernel_event("entity_moved", day=1, scene="s1", turn=0,
                     summary="主角在客栈", deltas={"who": "protagonist", "to": "stonebridge_inn"}),
        kernel_event("character_created", day=1, scene="s1", turn=0,
                     summary="旧塔守望者", deltas={"id": "tower_warden", "sketch": "守望北岸旧塔的沉默人影",
                     "goal": "不让封印裂痕扩散", "past": "曾参与封印仪式", "tier": "tracked"}),
        kernel_event("entity_moved", day=1, scene="s1", turn=0,
                     summary="守望者在旧塔", deltas={"who": "tower_warden", "to": "north_bank_old_tower"}),
        kernel_event("object_created", day=1, scene="s1", turn=0,
                     summary="旧铜灯", deltas={"id": "old_brass_lantern", "tier": "tracked", "label": "旧铜灯"}),
        kernel_event("item_transferred", day=1, scene="s1", turn=0,
                     summary="主角持有旧铜灯", deltas={"item": "old_brass_lantern", "to": "protagonist"}),
        # Deliberately retained only in the canonical store.  The browser
        # acceptance test proves this never crosses the player DTO boundary.
        kernel_event("fact_asserted", day=1, scene="s1", turn=0,
                     summary="未公开的演示秘密", deltas={"subject": "protagonist", "predicate": "hidden",
                                                          "value": "GUI_DEMO_PRIVATE_SENTINEL", "secrecy": "secret"}),
        kernel_event("world_change", day=1, scene="s1", turn=0,
                     summary="北岸旧塔的封印出现裂痕", deltas={"place": "north_bank_old_tower", "level": 2,
                                                         "summary": "北岸旧塔的封印出现裂痕"}),
        kernel_event("codex_entry_added", day=1, scene="s1", turn=0,
                     summary="石桥镇设定", deltas={"id": "stonebridge", "kind": "地理志",
                                                    "title": "石桥镇", "body": "河谷中的商旅小镇，旧塔耸立在北岸。"}),
        kernel_event("narration_recorded", day=1, scene="s1", turn=0,
                     summary="离线演示开场", deltas={"scene": "s1", "text": "晨雾笼罩石桥镇。你的旧铜灯仍在手边，客栈外通往集市的小路已经醒来。"}),
    ]
    engine.store.append_many(events)
    # Projection through build_engine's configured registry keeps this seed in
    # the same store/replay format as normal campaigns.
    from kernel.projection import project
    engine.world = project(engine.registry, engine.store.iter_events())
    engine.world["_revision"] = engine.store.revision


def _safe_value(value: Any) -> str:
    """Make a bounded plain-text display value; never pass structured objects through."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:500]


def _public_codex(world: dict[str, Any]) -> list[dict[str, str]]:
    """Narrow public Codex DTO shared by player and GM read-only browsers."""
    entries = (world.get("systems", {}).get("codex", {}) or {}).get("entries", [])
    return [{"id": str(item.get("id", "")), "kind": str(item.get("kind", "")),
             "title": str(item.get("title") or item.get("id", "")), "body": str(item.get("body", ""))}
            for item in entries if isinstance(item, dict)]


def _player_goal_and_quests(view: dict[str, Any], protagonist: str | None) -> dict[str, Any]:
    """Return the small, player-facing long-horizon slice for the dashboard.

    ``view`` is already the protagonist's POV projection.  The goal is read
    from that projected protagonist only; tasks deliberately expose just the
    LoreSystem's already-public 明-state one-line ledger, never a lore line's
    hidden fields, stages, or raw event provenance.
    """
    return player_objectives(view, protagonist)


def build_player_state(engine) -> dict[str, Any]:
    """Build the narrow, player-safe JSON DTO used by the browser.

    Do not return a graph/world here.  Keeping this hand-shaped makes accidental
    additions of EventStore, private interpretations, or DM-only state visible
    during code review and tests.
    """
    scene = _build_scene(engine)
    view = pov_world(engine.world, scene, pov=scene.get("protagonist"), redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    day = scene.get("day") or 1
    band = int((engine.world.get("meta", {}) or {}).get("band") or 0)
    protagonist = scene.get("protagonist")

    character_facts: list[dict[str, str]] = player_character_facts(view, protagonist)
    inventory: list[dict[str, str]] = []
    places: dict[str, dict[str, str]] = {}
    exits: list[dict[str, str]] = []
    capability = scene_capabilities(engine.world, scene)
    map_data = capability.map_dto()
    exit_costs: dict[str, str] = {}
    for route in capability.routes:
        if route.source == scene.get("location"):
            exit_costs[route.destination] = route.cost
        elif route.destination == scene.get("location"):
            exit_costs[route.source] = route.cost
    if graph is not None:
        for entity_id, entity in graph.entities.items():
            if entity.etype == "Object" and protagonist in graph.neighbors(entity_id, "held_by", day):
                inventory.append({"id": entity_id, "label": str(entity.attrs.get("label") or entity_id)})
        places = {node["id"]: node for node in map_data["nodes"]}
        exits.extend({"id": place.id, "label": place.label, "kind": place.kind}
                     for place in capability.exits)

    narrations: list[str] = []
    narration_unavailable = False
    try:
        history = engine.public_history()
    except ValueError:
        # A public row that explicitly references private memory must remain
        # fail-closed, but it should not make the rest of the read-only player
        # state (map, inventory, tasks) unavailable.
        history = []
        narration_unavailable = True
    for event in history:
        # ``public_history`` is a privacy DTO, not an active-history DTO: it
        # intentionally retains the retracted marker for callers that need an
        # audit view.  The player transcript must instead follow the projected
        # world and never resurrect a rewound line on refresh/reopen.
        if not event.get("retracted") and event.get("type") == "narration_recorded":
            text = (event.get("deltas") or {}).get("text")
            if isinstance(text, str):
                narrations.append(text)
    codex = _public_codex(engine.world)
    objectives = _player_goal_and_quests(view, protagonist)
    # This is deliberately the same redacted current-scene projection used to
    # decide whether a host may issue a ``heard_player`` receipt.  It exposes
    # no arbitrary NPC beliefs and never includes arrival-only recipients.
    authoring = native_authoring_context(engine.world, scene)
    speech_recipients = []
    for person in authoring.get("present_people", []):
        if not isinstance(person, dict) or not isinstance(person.get("id"), str):
            continue
        observable = person.get("observable") if isinstance(person.get("observable"), dict) else {}
        label = observable.get("sketch") if isinstance(observable.get("sketch"), str) else person["id"]
        speech_recipients.append({"id": person["id"], "label": label})
    return {
        "revision": projected_revision(engine),
        "turn": last_turn(engine),
        "scene": {"day": day, "time": {"day": day, "band": band_name(band)},
                  "location": places.get(scene.get("location")),
                  "exits": exits, "exit_costs": exit_costs},
        "map": map_data,
        "character": {"id": protagonist, "facts": character_facts},
        "objectives": objectives,
        "inventory": inventory,
        "narration": narrations[-8:],
        "narration_unavailable": narration_unavailable,
        "codex": codex,
        "speech_recipients": speech_recipients,
    }


def _gm_entity_label(entity) -> str:
    """Prefer an authored label without serializing arbitrary entity attrs."""
    return str(entity.attrs.get("label") or entity.attrs.get("name") or entity.id)


def _gm_current_facts(graph, entity_id: str) -> list[dict[str, str | None]]:
    return [
        {
            "predicate": str(fact.predicate),
            "value": _safe_value(fact.value),
            "secrecy": str(fact.secrecy) if fact.secrecy else None,
        }
        for fact in graph.current_facts(entity_id)
    ]


def _gm_timeline_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return an intentionally narrow, memory-free GM timeline row.

    The GM is allowed canonical *world* facts, including secrets, but a private
    character interpretation remains an actor-scoped record rather than GM
    observability.  We therefore expose only event metadata plus the one
    reader-facing body for narration/recap events.
    """
    event_type = str(event.get("type") or "")
    deltas = event.get("deltas") or {}
    category = "objective"
    detail = str(event.get("summary") or "")
    if event_type == "narration_recorded":
        category = "public_narrative"
        detail = str(deltas.get("text") or detail)
    elif event_type in {"scene_summarized", "recap_recompressed"}:
        category = "recap"
        detail = str(deltas.get("summary") or deltas.get("super_summary") or detail)
    return {
        "id": str(event.get("id") or ""),
        "type": event_type,
        "category": category,
        "day": int(event.get("day") or 0),
        "scene": str(event.get("scene") or ""),
        "turn": int(event.get("turn") or 0),
        "summary": detail,
        "retracted": bool(event.get("retracted")),
    }


def build_gm_state(engine) -> dict[str, Any]:
    """Build a read-only, canonical-world GM DTO.

    This is deliberately a different DTO from ``build_player_state``.  It
    reads the projected ontology and a sanitized event timeline, not the player
    POV projection or raw EventStore documents.  Private interpretation rows
    are always omitted, even when the host happened to open an experimental
    memory-enabled campaign.
    """
    scene = _build_scene(engine)
    day = scene.get("day") or 1
    graph = engine.world.get("systems", {}).get("ontology")
    people: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    places: list[dict[str, Any]] = []
    # GM reads the canonical graph explicitly.  It reuses the same DTO shape
    # as the player capability but never feeds it into a player controller.
    map_data = scene_capabilities(engine.world, scene, canonical=True).map_dto()
    if graph is not None:
        for entity_id, entity in sorted(graph.entities.items()):
            base = {
                "id": entity_id,
                "label": _gm_entity_label(entity),
                "tier": str(entity.tier),
                "facts": _gm_current_facts(graph, entity_id),
            }
            if entity.etype == "Person":
                people.append({**base, "location": next(iter(graph.neighbors(entity_id, "located_in", day)), None)})
            elif entity.etype == "Object":
                objects.append({**base, "holder": next(iter(graph.neighbors(entity_id, "held_by", day)), None)})
            elif entity.etype == "Place":
                places.append({**base, "kind": str(entity.attrs.get("kind") or "地点"),
                               "visibility": str(entity.attrs.get("visibility") or "public")})
    # Reuse the existing public-history boundary rather than merely dropping
    # private rows ourselves.  In particular it fails closed when an otherwise
    # ordinary narration/summary row references a private-memory event through
    # thread_refs or chunk_ids.  Do not turn that history-only defect into a
    # whole GM dashboard outage: map and canonical world state are independent
    # read-only DTOs, while both prose histories are withheld as one slice.
    history_unavailable = False
    try:
        timeline = [_gm_timeline_event(event) for event in engine.public_history()]
    except ValueError:
        timeline = []
        history_unavailable = True
    recaps: list[dict[str, str]] = []
    if not history_unavailable:
        narrative = engine.world.get("systems", {}).get("narrative", {}) or {}
        recaps = [
            {"scene": str(bucket.get("scene") or ""), "summary": str(bucket["summary"])}
            for bucket in narrative.get("scenes", [])
            if isinstance(bucket, dict) and isinstance(bucket.get("summary"), str)
        ]
        if isinstance(narrative.get("super_summary"), str):
            recaps.insert(0, {"scene": "总览", "summary": narrative["super_summary"]})
    return {
        "mode": "gm_read_only",
        "revision": projected_revision(engine),
        "day": int(day),
        "turn": last_turn(engine),
        "people": people,
        "objects": objects,
        "places": places,
        "map": map_data,
        "timeline": timeline[-160:],
        "recaps": recaps,
        "history_unavailable": history_unavailable,
        "codex": _public_codex(engine.world),
    }


@dataclass
class _RequestContext:
    player_input: str
    expected_revision: int | None
    scene: dict[str, Any]
    prev_scene: dict[str, Any] | None
    recipient: str | None
    statement: str | None
    trusted_player_statement_receipts: tuple[dict[str, Any], ...]


class PlayerDashboard:
    """In-process player UI controller; all mutations stay in ``run_turn``."""

    def __init__(self, engine, *, strategy=None, max_repairs: int = 6, demo: bool = False):
        self.engine = engine
        self.strategy = strategy if strategy is not None else OfflineDemoStrategy()
        self.max_repairs = max_repairs
        self.demo = demo
        # Unlike a fresh genesis, an existing campaign may have just been
        # reopened between two player actions.  Recover the prior durable
        # action-start scene so catch-up preserves its enter-scope semantics
        # instead of treating every stale co-located NPC as newly present.
        self.previous_scene: dict[str, Any] | None = _previous_scene_before_latest_action(engine)
        # This is deliberately controller-session state, not world state.  It
        # may guide the next author prompt, but must never become part of a
        # player action receipt or its recovery envelope.
        self.steering: str | None = None
        self.requests: dict[str, _RequestContext] = {}
        self.lock = threading.RLock()

    def state(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_if_store_advanced()
            state = build_player_state(self.engine)
            state["session"] = {"soft_direction": self.steering}
            return state

    def _refresh_authoritative_world(self) -> None:
        """Rebuild controller-only recovery state after a known stale revision.

        This is deliberately read-only: a peer action already changed the
        durable store, while the controller's cached projection and catch-up
        anchor describe an older world.  Keep it shared by normal action and
        revision-bound undo rejection so `/api/state` immediately represents
        the authoritative history that the player is told to refresh.
        """
        reproject_engine_world(self.engine)
        self._rebuild_controller_after_refresh()

    def _rebuild_controller_after_refresh(self) -> None:
        """Refresh controller-only caches after ``engine.world`` changed."""
        self.previous_scene = _previous_scene_before_latest_action(self.engine)
        if hasattr(self.strategy, "reset"):
            self.strategy.reset()

    def _refresh_if_store_advanced(self) -> None:
        """Observe peer publishes before serving another player operation."""
        if refresh_engine_world_if_advanced(self.engine):
            self._rebuild_controller_after_refresh()

    def recall(self, query: str) -> dict[str, Any]:
        """Run the shared, read-only player POV recall query for this session."""
        if not isinstance(query, str) or not query.strip():
            raise GuiRequestError("请输入要回忆的关键词。")
        cleaned = query.strip()
        if len(cleaned.encode("utf-8")) > _MAX_RECALL_QUERY_BYTES:
            raise GuiRequestError("回忆关键词过长，请缩短后重试。")
        with self.lock:
            self._refresh_if_store_advanced()
            scene = _build_scene(self.engine)
            return {
                "hits": player_recall(self.engine.registry, self.engine.world, scene, cleaned),
                "revision": projected_revision(self.engine),
            }

    def set_steering(self, text: str | None) -> dict[str, Any]:
        """Set a local authoring preference without changing the campaign.

        The CLI has the same intentionally non-durable ``//steer`` facility.
        Resetting a conversational strategy is essential here: otherwise a
        cleared preference could survive in its provider-side conversation
        cache even though it is no longer present in the next scene.
        """
        if text is not None and not isinstance(text, str):
            raise GuiRequestError("软方向格式无效，请重新填写。")
        requested = text.strip() if isinstance(text, str) else None
        if not requested:
            requested = None
        if requested is not None and len(requested) > 500:
            raise GuiRequestError("软方向请控制在 500 个字符内。")
        with self.lock:
            if requested != self.steering:
                self.steering = requested
                if hasattr(self.strategy, "reset"):
                    self.strategy.reset()
            return {"ok": True, "soft_direction": self.steering}

    def _stored_context(self, action_id: str) -> _RequestContext | None:
        """Recover only the host-written original GUI envelope; never trust client scene data."""
        receipt = self.engine.store.get_action_receipt(action_id)
        if receipt is None:
            return None
        try:
            metadata = self.engine.store.get_action_result_metadata(action_id)
            envelope = metadata.get("host_envelope") if isinstance(metadata, dict) else None
            scene = envelope.get("scene") if isinstance(envelope, dict) else None
            prev_scene = envelope.get("prev_scene") if isinstance(envelope, dict) else None
            saved_recipient = envelope.get("speak_recipient") if isinstance(envelope, dict) else None
            saved_statement = envelope.get("speak_statement") if isinstance(envelope, dict) else None
            saved_receipts = envelope.get("trusted_player_statement_receipts", []) if isinstance(envelope, dict) else []
            if (not isinstance(scene, dict) or (prev_scene is not None and not isinstance(prev_scene, dict))
                    or saved_recipient is not None and not isinstance(saved_recipient, str)
                    or saved_statement is not None and not isinstance(saved_statement, str)
                    or (saved_recipient is None) != (saved_statement is None)
                    or not isinstance(saved_receipts, list) or len(saved_receipts) > 1
                    or any(not isinstance(item, dict) for item in saved_receipts)):
                raise ValueError
            if bool(saved_receipts) != bool(saved_recipient):
                raise ValueError
            return _RequestContext("", None, scene, prev_scene, saved_recipient, saved_statement,
                                   tuple(saved_receipts))
        except Exception:
            raise GuiRecoveryError("已保存行动无法安全恢复；请刷新查看当前世界状态。")

    def action(self, *, player_input: str, action_id: str, expected_revision: int | None,
               recipient: str | None = None, statement: str | None = None) -> dict[str, Any]:
        if not isinstance(player_input, str) or not player_input.strip():
            raise GuiRequestError("请输入一个行动。")
        if len(player_input.encode("utf-8")) > _MAX_ACTION_BYTES:
            raise GuiRequestError("行动过长，请缩短后再试。")
        if not isinstance(action_id, str) or not action_id or len(action_id) > 128:
            raise GuiRequestError("请求标识无效，请刷新后重试。")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise GuiRequestError("页面状态无效，请刷新后重试。")
        if (recipient is None) != (statement is None):
            raise GuiRequestError("请同时明确选择当前同场收件人并填写逐字原话。")
        if recipient is not None:
            if (not isinstance(recipient, str) or not recipient or len(recipient) > 128
                    or not isinstance(statement, str) or not statement.strip()
                    or len(statement.encode("utf-8")) > _MAX_ACTION_BYTES):
                raise GuiRequestError("收件人或逐字原话无效，请重新选择当前同场角色。")
        with self.lock:
            self._refresh_if_store_advanced()
            existing = self.requests.get(action_id)
            if (existing is not None and existing.player_input == player_input
                    and existing.expected_revision == expected_revision
                    and existing.recipient == recipient and existing.statement == statement):
                context = existing
            else:
                stored = self._stored_context(action_id)
                if stored is not None:
                    if stored.recipient != recipient or stored.statement != statement:
                        # A receipt already exists, so this is not a request that
                        # the browser can safely reject and discard.  The original
                        # committed turn may be recoverable only with its original
                        # envelope; keep the client in its explicit recovery path.
                        raise GuiRecoveryError("已保存行动无法安全恢复；请刷新查看当前世界状态。")
                    context = _RequestContext(player_input, expected_revision, stored.scene, stored.prev_scene,
                                              stored.recipient, stored.statement,
                                              stored.trusted_player_statement_receipts)
                else:
                    scene = _build_scene(self.engine)
                    try:
                        receipts = (() if recipient is None else (
                            host_player_statement_receipt(
                                self.engine.world, scene, recipient=recipient, statement=statement,
                            ),
                        ))
                    except ValueError as exc:
                        raise GuiRequestError("收件人必须是当前公开同场角色，且原话不能为空。") from exc
                    context = _RequestContext(player_input, expected_revision, scene, self.previous_scene,
                                              recipient, statement, receipts)
                if existing is None:
                    self.requests[action_id] = context
            scene_for_turn = context.scene
            if self.steering:
                scene_for_turn = {**context.scene, "_player_steer": self.steering}
            try:
                result = run_turn(
                    self.engine.registry, self.engine.store, self.engine.world, scene_for_turn, player_input,
                    strategy=self.strategy, provider=self.engine.provider, embedder=self.engine.embedder,
                    max_repairs=self.max_repairs, required_sections=REQUIRED_SECTIONS, cascade_provider=self.engine.cascade_provider,
                    catchup_provider=self.engine.cascade_provider,
                    prev_scene=context.prev_scene, action_id=action_id, expected_revision=expected_revision,
                    host_envelope={
                        "scene": context.scene, "prev_scene": context.prev_scene,
                        "speak_recipient": context.recipient, "speak_statement": context.statement,
                        "trusted_player_statement_receipts": list(context.trusted_player_statement_receipts),
                    },
                    enforce_player_travel=True,
                    enforce_player_knowledge_authority=True,
                    enforce_player_world_authority=True,
                    enforce_player_fact_authority=True,
                    enforce_player_entity_authority=True,
                    enforce_player_faction_authority=True,
                    enforce_player_place_authority=True,
                    enforce_player_item_authority=True,
                    enforce_player_quest_authority=True,
                    trusted_player_statement_receipts=context.trusted_player_statement_receipts,
                )
            except RevisionConflict as exc:
                # If this id already names a durable receipt, the browser may
                # be looking at a committed action with a mismatched recovery
                # envelope.  It is not safe to tell it the request was merely
                # rejected and let it discard that recovery state.
                if self.engine.store.get_action_receipt(action_id) is not None:
                    raise GuiRecoveryError("已保存行动无法安全恢复；请刷新查看当前世界状态。") from exc
                # Otherwise the optimistic publish did not commit.  Reproject
                # the authoritative store, rebuild the same action-start scope
                # used by CLI/cold-reopen/undo recovery, and clear only this
                # rejected in-memory envelope before giving the browser a
                # confirmed rejection it can safely discard.
                self._refresh_authoritative_world()
                self.requests.pop(action_id, None)
                raise GuiRequestError("世界状态已更新；此行动未提交。请刷新后重试。") from exc
            self.engine.world = result.world
            if not bool(getattr(result, "receipt", {}).get("duplicate")):
                self.previous_scene = context.scene
            return {
                "ok": True,
                "duplicate": bool(getattr(result, "receipt", {}).get("duplicate")),
                "narration": result.narration,
                "revision": result.receipt.get("revision"),
                "recap_status": result.recap_status,
                "state": self.state(),
            }

    def undo(self, *, expected_revision: int | None) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise GuiRequestError("撤销请求无效，请刷新后重试。")
        with self.lock:
            if expected_revision != self.engine.store.revision:
                self._refresh_authoritative_world()
                raise GuiRequestError("世界已变化；已刷新本地状态，请重新决定是否撤销。")
            turn = last_turn(self.engine)
            if turn <= 0:
                raise GuiRequestError("当前没有可撤销的玩家回合。")
            try:
                rewind(self.engine, turn, expected_revision=expected_revision)
            except RevisionConflict as exc:
                self._refresh_authoritative_world()
                raise GuiRequestError("世界已变化；已刷新本地状态，请重新决定是否撤销。") from exc
            # The remaining durable history still has an action-start scope.
            # Rebuild it exactly as a cold reopen would, rather than making
            # the next action treat every stale co-located entity as newly
            # arrived merely because the user rewound the latest turn.
            self.previous_scene = _previous_scene_before_latest_action(self.engine)
            # A rewind changes canonical history but the strategy's multi-turn
            # thread is only an in-memory prompt cache.  Keeping it would let
            # the next normal GUI action carry a retracted player exchange to
            # the author even though the replayed world no longer contains it.
            # This mirrors CLI OOC undo/rewind handling.
            if hasattr(self.strategy, "reset"):
                self.strategy.reset()
            return {"ok": True, "message": "已撤销最后一回合。", "state": self.state()}


class GmDashboard:
    """Read-only controller for a separately started local GM server."""

    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.RLock()

    def state(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_if_store_advanced()
            return build_gm_state(self.engine)

    def _refresh_if_store_advanced(self) -> None:
        """Reproject a peer's durable publish before serving the GM GET view.

        GM mode has no mutation endpoint or revision-bound operation that could
        otherwise cause an in-memory engine opened at startup to catch up.  The
        comparison and projection are read-only: they neither acknowledge nor
        create player actions, and still leave ``build_gm_state`` responsible
        for excluding private interpretation records from its DTO.
        """
        refresh_engine_world_if_advanced(self.engine)


_INDEX = """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>LLM RPG · 离线玩家仪表盘</title><link rel=\"stylesheet\" href=\"/app.css\"></head><body><main><header><p class=\"eyebrow\">LOCAL · PLAYER VIEW · OFFLINE DEMO</p><h1>石桥镇冒险</h1><p id=\"status\" role=\"status\">正在读取已保存的世界…</p></header><section class=\"panel story\"><h2>叙事</h2><div id=\"narration\" class=\"narration\"></div><form id=\"action-form\"><label for=\"action-input\">你的行动</label><textarea id=\"action-input\" maxlength=\"2000\" placeholder=\"例如：走向集市，向路人打听旧塔。\" required></textarea><div class=\"actions\"><button id=\"submit-action\" type=\"submit\">保存行动</button><button id=\"undo\" type=\"button\" class=\"subtle\">撤销上一回合</button></div></form></section><section class=\"grid\"><article class=\"panel\"><h2>角色状态</h2><dl id=\"character\"></dl></article><article class=\"panel\"><h2>背包</h2><ul id=\"inventory\"></ul></article><article class=\"panel\"><h2>当前位置与地图</h2><div id=\"map\"></div></article><article class=\"panel codex\"><h2>公开世界设定典</h2><div id=\"codex\"></div></article></section><footer>这是离线玩家试用界面：不会显示 GM、原始事件、其他角色私有解释或编辑功能。</footer></main><script src=\"/app.js\"></script></body></html>"""


def _player_index(demo: bool) -> str:
    """Render an honest local-player shell without changing any game DTO."""
    mode = "OFFLINE DEMO" if demo else "SAVED CAMPAIGN"
    footer = (
        "这是固定离线演示：不会显示 GM、原始事件、其他角色私有解释或编辑功能。"
        if demo else
        "这是本地玩家视图：读取已保存战役，不会显示 GM、原始事件、其他角色私有解释或编辑功能。"
    )
    return (_INDEX
            .replace("LLM RPG · 离线玩家仪表盘", "LLM RPG · 本地玩家仪表盘")
            .replace("OFFLINE DEMO", mode)
            .replace("这是离线玩家试用界面：不会显示 GM、原始事件、其他角色私有解释或编辑功能。", footer))

_CSS = """*{box-sizing:border-box}body{margin:0;background:#10151d;color:#e9edf2;font:16px/1.55 system-ui,-apple-system,sans-serif}main{max-width:1120px;margin:auto;padding:26px 18px 42px}.eyebrow{color:#8ec5ff;letter-spacing:.08em;font-size:.78rem}h1{margin:.1rem 0}h2{font-size:1.08rem;margin-top:0}.panel{background:#19222e;border:1px solid #2d3c4e;border-radius:12px;padding:18px;box-shadow:0 4px 18px #0003}.story{margin:18px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.narration p{white-space:pre-wrap;border-left:3px solid #75b8ee;padding-left:12px}.actions{display:flex;gap:10px;margin-top:10px}textarea{width:100%;min-height:88px;margin-top:7px;padding:10px;background:#0d131b;border:1px solid #405469;border-radius:8px;color:inherit}button{border:0;border-radius:8px;background:#75b8ee;color:#09233a;padding:9px 13px;font-weight:700;cursor:pointer}.subtle{background:#33465a;color:#eef5fa}button:disabled{opacity:.55;cursor:wait}dt{color:#9eb1c5}dd{margin:0 0 10px}ul{padding-left:20px}.place{font-weight:700}.empty{color:#9eb1c5}details{border-top:1px solid #304052;padding:8px 0}summary{cursor:pointer;color:#b7dcfb}footer{margin-top:24px;color:#9eb1c5;font-size:.9rem}@media(max-width:700px){main{padding:16px 12px}.grid{grid-template-columns:1fr}.actions{flex-direction:column}button{width:100%}}"""

_JS = """(() => {let state=null;const $=id=>document.getElementById(id);const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};function clear(n){n.replaceChildren()}function setStatus(t,err=false){const n=$('status');n.textContent=t;n.style.color=err?'#ffadb0':'#e9edf2'}function render(s){state=s;clear($('narration'));(s.narration.length?s.narration:['尚无公开叙事。']).forEach(t=>$('narration').append(el('p',t)));clear($('character'));if(!s.character.facts.length){$('character').append(el('p','暂无可展示的角色状态。'))}s.character.facts.forEach(f=>{const dt=el('dt',f.name),dd=el('dd',f.value);$('character').append(dt,dd)});clear($('inventory'));if(!s.inventory.length)$('inventory').append(el('li','背包为空。'));s.inventory.forEach(x=>$('inventory').append(el('li',x.label)));clear($('map'));const loc=s.scene.location;$('map').append(el('p',loc?`当前位置：${loc.label}`:'当前位置尚未确定。'));const exits=el('ul');if(!s.scene.exits.length)exits.append(el('li','没有已知相邻地点。'));s.scene.exits.forEach(x=>exits.append(el('li',`→ ${x.label}（${x.kind}）`)));$('map').append(exits);clear($('codex'));if(!s.codex.length)$('codex').append(el('p','暂无公开设定条目。'));s.codex.forEach(x=>{const d=el('details'),sum=el('summary',`${x.kind?`[${x.kind}] `:''}${x.title}`),p=el('p',x.body);d.append(sum,p);$('codex').append(d)});setStatus(`第 ${s.turn} 回合 · 版本 ${s.revision} · 已从本地保存读取。`)}async function api(url,options){const r=await fetch(url,options);const data=await r.json().catch(()=>({message:'响应格式错误，请刷新页面。'}));if(!r.ok)throw new Error(data.message||'请求未完成，请刷新后重试。');return data}async function load(){try{render(await api('/api/state'))}catch(e){setStatus(e.message,true)}}$('action-form').addEventListener('submit',async e=>{e.preventDefault();if(!state)return;const input=$('action-input'),button=$('submit-action'),text=input.value.trim();if(!text)return;button.disabled=true;$('undo').disabled=true;setStatus('正在保存行动…');const actionId=crypto.randomUUID?crypto.randomUUID():`gui-${Date.now()}-${Math.random()}`;try{const d=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,action_id:actionId,expected_revision:state.revision})});render(d.state);input.value='';setStatus(d.duplicate?'已恢复此前保存的行动。':`行动已保存（版本 ${d.revision}）。`)}catch(err){setStatus(err.message,true)}finally{button.disabled=false;$('undo').disabled=false}});$('undo').addEventListener('click',async()=>{try{setStatus('正在撤销…');const d=await api('/api/undo',{method:'POST'});render(d.state);setStatus(d.message)}catch(e){setStatus(e.message,true)}});load()})();"""

# Static text is kept separate from all game data.  The replacement adds an
# explicit recovery action without interpolating a player/world string into the
# HTML document.
_INDEX = _INDEX.replace(
    '<button id="undo" type="button" class="subtle">撤销上一回合</button>',
    '<button id="undo" type="button" class="subtle">撤销上一回合</button><button id="resume-action" type="button" class="subtle" hidden>恢复上次请求</button>',
)
_INDEX = _INDEX.replace('<button id="resume-action" type="button" class="subtle" hidden>恢复上次请求</button>', '<button id="resume-action" type="button" class="subtle" hidden>恢复上次请求</button><button id="end-recovery" type="button" class="subtle" hidden>查看状态后结束恢复</button>')
_INDEX = _INDEX.replace('<div id="codex"></div>', '<label for="codex-search">搜索公开设定（标题、类别、正文）</label><input id="codex-search" type="search" placeholder="例如：地理、旧塔"><div id="codex"></div>')
_INDEX = _INDEX.replace(
    '<article class="panel codex">',
    '<article class="panel recall"><h2>回忆已知线索</h2><form id="recall-form"><label for="recall-query">只搜索当前主角已知的公开资料</label><input id="recall-query" type="search" maxlength="320" placeholder="例如：石桥、封门"><button id="search-recall" type="submit" class="subtle">查询回忆</button></form><ul id="recall-results"><li>输入关键词以查询已知线索。</li></ul></article><article class="panel codex">',
)
_INDEX = _INDEX.replace('<article class="panel"><h2>背包</h2><ul id="inventory"></ul></article>', '<article class="panel"><h2>背包</h2><ul id="inventory"></ul></article><article class="panel"><h2>目标与任务</h2><p id="long-term-goal"></p><h3>进行中</h3><ul id="active-quests"></ul><h3>已完成</h3><ul id="completed-quests"></ul></article>')
_INDEX = _INDEX.replace('<article class="panel"><h2>角色状态</h2><dl id="character"></dl></article>', '<article class="panel"><h2>角色状态</h2><dl id="character"></dl></article><article class="panel"><h2>当前可交谈角色</h2><ul id="present-people"></ul></article>')
_INDEX = _INDEX.replace(
    '</textarea><div class="actions">',
    '</textarea><fieldset class="speech"><legend>明确说出原话（可选）</legend><label for="speech-recipient">当前公开同场收件人</label><select id="speech-recipient"><option value="">不记录对话</option></select><label for="speech-statement">逐字原话</label><textarea id="speech-statement" maxlength="2000" placeholder="只填写愿意让所选角色准确记住的一句话。"></textarea><p class="hint">只有同时明确选择收件人和原话时，宿主才会签发本回合的单一通信收据；普通行动文字不会自动变成对话。</p></fieldset><div class="actions">',
)
_INDEX = _INDEX.replace(
    '<p class="hint">只有同时明确选择收件人和原话时，宿主才会签发本回合的单一通信收据；普通行动文字不会自动变成对话。</p></fieldset><div class="actions">',
    '<p class="hint">只有同时明确选择收件人和原话时，宿主才会签发本回合的单一通信收据；普通行动文字不会自动变成对话。</p></fieldset><fieldset class="steer"><legend>后续叙事方向（可选）</legend><label for="soft-direction">只影响本地会话中的后续叙事偏好</label><textarea id="soft-direction" maxlength="500" placeholder="例如：优先核实眼前公开线索，不预设远方结论。"></textarea><div class="steer-actions"><button id="save-steer" type="button" class="subtle">保存方向</button><button id="clear-steer" type="button" class="subtle">清除方向</button></div><p class="hint">它不是世界事实、路线或权限，也不会写入回合记录；清除后后续回合不再使用它。</p></fieldset><div class="actions">',
)
_JS = r"""(() => {
let state=null,busy=false; const pendingKey='llm-rpg.offline-player.pending-action.v1';
const $=id=>document.getElementById(id); const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
function clear(n){n.replaceChildren()} function setStatus(t,err=false){const n=$('status');n.textContent=t;n.style.color=err?'#ffadb0':'#e9edf2'}
function renderSpeechRecipients(items){const select=$('speech-recipient'),old=select.value;clear(select);select.append(el('option','不记录对话'));select.lastChild.value='';(items||[]).forEach(x=>{const option=el('option',x.label||x.id);option.value=x.id;select.append(option)});select.value=[...select.options].some(x=>x.value===old)?old:''}
function renderPresentPeople(items){const list=$('present-people');clear(list);const people=Array.isArray(items)?items:[];if(!people.length){list.append(el('li','当前没有可交谈角色。'));return}people.forEach(x=>list.append(el('li',x.label||x.id)))}
function pending(){try{const p=JSON.parse(sessionStorage.getItem(pendingKey)||'null');return p&&typeof p.text==='string'&&typeof p.action_id==='string'&&Number.isInteger(p.expected_revision)?p:null}catch(_){sessionStorage.removeItem(pendingKey);return null}}
function syncPending(){const p=pending();$('resume-action').hidden=!p;if(p&&!busy)setStatus('上次行动尚未得到确认；可恢复原请求，或先刷新查看当前状态。',true)}
function setBusy(next){busy=next;$('submit-action').disabled=next;$('undo').disabled=next;$('resume-action').disabled=next}
function render(s){state=s;renderSpeechRecipients(s.speech_recipients);renderPresentPeople(s.speech_recipients);clear($('narration'));const narration=Array.isArray(s.narration)?s.narration:[];(narration.length?narration:[s.narration_unavailable?'公开叙事暂不可用；请刷新查看当前状态。':'尚无公开叙事。']).forEach(t=>$('narration').append(el('p',t)));clear($('character'));if(!s.character.facts.length)$('character').append(el('p','暂无可展示的角色状态。'));s.character.facts.forEach(f=>$('character').append(el('dt',f.name),el('dd',f.value)));clear($('inventory'));if(!s.inventory.length)$('inventory').append(el('li','背包为空。'));s.inventory.forEach(x=>$('inventory').append(el('li',x.label)));const objectives=s.objectives||{},goal=$('long-term-goal'),quests=$('active-quests'),completed=$('completed-quests');goal.textContent=objectives.long_term_goal?`长期目标：${objectives.long_term_goal}`:'长期目标尚未记录。';clear(quests);const active=Array.isArray(objectives.active_quests)?objectives.active_quests:[];if(!active.length)quests.append(el('li','当前没有公开进行中的任务。'));active.forEach(x=>quests.append(el('li',`${x.summary}（${x.id}）`)));clear(completed);const done=Array.isArray(objectives.completed_quests)?objectives.completed_quests:[];if(!done.length)completed.append(el('li','尚无公开已完成任务。'));done.forEach(x=>completed.append(el('li',`${x.summary}（${x.id}）`)));clear($('map'));const loc=s.scene.location;$('map').append(el('p',loc?`当前位置：${loc.label}`:'当前位置尚未确定。'));const exits=el('ul');if(!s.scene.exits.length)exits.append(el('li','没有已知相邻地点。'));s.scene.exits.forEach(x=>exits.append(el('li',`→ ${x.label}（${x.kind}）`)));$('map').append(exits);clear($('codex'));if(!s.codex.length)$('codex').append(el('p','暂无公开设定条目。'));s.codex.forEach(x=>{const d=el('details');d.append(el('summary',`${x.kind?`[${x.kind}] `:''}${x.title}`),el('p',x.body));$('codex').append(d)});setStatus(`第 ${s.turn} 回合 · 版本 ${s.revision} · 已从本地保存读取。`)}
async function api(url,options){const r=await fetch(url,options);let data;try{data=await r.json()}catch(_){const e=new Error('响应格式错误，请刷新页面。');e.confirmedRejection=false;throw e}if(!r.ok){const e=new Error(data&&typeof data.message==='string'?data.message:'请求未完成，请刷新后重试。');e.confirmedRejection=r.status===400&&data&&data.confirmed_rejection===true;throw e}return data}
async function sendAction(envelope,recovering){if(busy)return;setBusy(true);setStatus(recovering?'正在恢复上次请求…':'正在保存行动…');try{const d=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(envelope)});if(!d||d.ok!==true||!d.state||!Number.isInteger(d.revision))throw new Error('响应未确认，请刷新页面后恢复上次请求。');render(d.state);sessionStorage.removeItem(pendingKey);syncPending();$('action-input').value='';$('speech-statement').value='';$('speech-recipient').value='';setStatus(d.duplicate?'已恢复此前保存的行动。':`行动已保存（版本 ${d.revision}）。`)}catch(err){if(err.confirmedRejection){sessionStorage.removeItem(pendingKey);syncPending();setStatus(`请求已被服务器拒绝，未提交。请修改后再试。${err.message}`,true)}else{syncPending();setStatus(`请求尚未得到确认；请刷新查看当前状态或恢复上次请求。${err.message}`,true)}}finally{setBusy(false)}}
async function load(){try{render(await api('/api/state'));syncPending()}catch(e){setStatus(e.message,true)}}
$('action-form').addEventListener('submit',e=>{e.preventDefault();if(!state||busy)return;const old=pending();if(old){setStatus('请先恢复或检查上次未确认的请求。',true);return}const text=$('action-input').value.trim(),recipient=$('speech-recipient').value,statement=$('speech-statement').value;if(!text)return;if((recipient&&!statement.trim())||(!recipient&&statement.trim())){setStatus('请同时明确选择当前同场收件人并填写逐字原话。',true);return}const action_id=crypto.randomUUID?crypto.randomUUID():`gui-${Date.now()}-${Math.random()}`,envelope={text,action_id,expected_revision:state.revision};if(recipient){envelope.recipient=recipient;envelope.statement=statement}sessionStorage.setItem(pendingKey,JSON.stringify(envelope));sendAction(envelope,false)});
$('resume-action').addEventListener('click',()=>{const p=pending();if(p)sendAction(p,true)});
$('undo').addEventListener('click',async()=>{if(!state||busy)return;setBusy(true);setStatus('正在撤销…');try{const d=await api('/api/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:state.revision})});render(d.state);setStatus(d.message)}catch(e){setStatus(e.message,true)}finally{setBusy(false)}});load()
})();"""
_JS = _JS.replace('let state=null,busy=false;', "let state=null,busy=false,recoveryUnavailable=false,codexQuery='',selectedMapId=null;")
_JS = _JS.replace("s.codex.forEach(x=>{", "s.codex.filter(x=>`${x.title} ${x.kind} ${x.body}`.toLowerCase().includes(codexQuery)).forEach(x=>{")
_JS = _JS.replace("if(!s.codex.length)$('codex').append(el('p','暂无公开设定条目。'));s.codex.filter(x=>`${x.title} ${x.kind} ${x.body}`.toLowerCase().includes(codexQuery)).forEach(x=>{",
                  "const visibleCodex=s.codex.filter(x=>`${x.title} ${x.kind} ${x.body}`.toLowerCase().includes(codexQuery));if(!visibleCodex.length)$('codex').append(el('p',codexQuery?'没有匹配的公开设定。':'暂无公开设定条目。'));visibleCodex.forEach(x=>{")
_JS = _JS.replace("$('resume-action').addEventListener('click',()=>{const p=pending();if(p)sendAction(p,true)});",
                  "$('resume-action').addEventListener('click',()=>{const p=pending();if(p)sendAction(p,true)});$('end-recovery').addEventListener('click',()=>{sessionStorage.removeItem(pendingKey);recoveryUnavailable=false;syncPending();setStatus('已结束恢复；当前显示的是已保存世界状态。')});$('codex-search').addEventListener('input',e=>{codexQuery=e.target.value.trim().toLowerCase();if(state)render(state)});")
_JS = _JS.replace("function syncPending(){const p=pending();$('resume-action').hidden=!p;if(p&&!busy)setStatus('上次行动尚未得到确认；可恢复原请求，或先刷新查看当前状态。',true)}", "function syncPending(){const p=pending();$('resume-action').hidden=!p||recoveryUnavailable;$('end-recovery').hidden=!p||!recoveryUnavailable;if(p&&!busy&&!recoveryUnavailable)setStatus('上次行动尚未得到确认；可恢复原请求，或先刷新查看当前状态。',true)}")
_JS = _JS.replace("if(!r.ok){const e=new Error(data&&typeof data.message==='string'?data.message:'请求未完成，请刷新后重试。');e.confirmedRejection=r.status===400&&data&&data.confirmed_rejection===true;throw e}", "if(!r.ok){const e=new Error(data&&typeof data.message==='string'?data.message:'请求未完成，请刷新后重试。');e.status=r.status;e.confirmedRejection=r.status===400&&data&&data.confirmed_rejection===true;throw e}")
_JS = _JS.replace("}else{syncPending();setStatus(`请求尚未得到确认；请刷新查看当前状态或恢复上次请求。${err.message}`,true)}}finally", "}else if(err.status===409){recoveryUnavailable=true;syncPending();setStatus('该行动已提交但无法安全恢复结果；请查看当前状态后明确结束恢复。',true)}else{syncPending();setStatus(`请求尚未得到确认；请刷新查看当前状态或恢复上次请求。${err.message}`,true)}}finally")

# Player and GM deliberately receive different map DTOs, but the player graph
# still deserves a first-class visual instead of a hand-written exit list.  All
# labels come from the already-redacted DTO and are assigned through
# ``textContent``; SVG nodes are read-only selectors, never navigation writes.
_CSS += ".panel{min-width:0}.speech,.steer{border:1px solid #405469;border-radius:8px;margin:12px 0;padding:10px}.speech label,.speech select,.steer label{display:block;width:100%;margin:5px 0}.speech select{background:#0d131b;border:1px solid #405469;border-radius:6px;color:inherit;padding:8px}.steer-actions{display:flex;gap:8px;margin-top:8px}.hint{color:#9eb1c5;font-size:.9rem}.recall form{display:grid;gap:8px}.recall input{width:100%;min-width:0;padding:8px;background:#0d131b;border:1px solid #405469;border-radius:6px;color:inherit}.recall button{justify-self:start}.recall li{overflow-wrap:anywhere}.player-map{min-height:232px;overflow:auto}.player-map svg{width:100%;min-width:420px;height:230px;background:#101820;border:1px solid #304052;border-radius:8px}.player-map .map-edge{stroke:#8094a8;stroke-width:2}.player-map .map-node{fill:#315779;stroke:#a6d4f5;stroke-width:1.5;cursor:pointer}.player-map .map-node.current{fill:#5c9c67;stroke:#d9f6bd;stroke-width:3}.player-map .map-node.selected{stroke:#ffcf74;stroke-width:4}.player-map .map-label{fill:#e9edf2;font-size:13px}.map-detail{color:#b7dcfb;font-size:.9rem}@media(max-width:700px){.player-map svg{min-width:360px;height:250px}.steer-actions{flex-direction:column}.recall button{width:100%}}"
_INDEX = _INDEX.replace('<div id="map"></div>', '<div id="map" class="player-map"></div>')
_JS = _JS.replace(
    "function render(s){state=s;",
    r"""function renderMap(data,location){const host=$('map');clear(host);const nodes=data&&Array.isArray(data.nodes)?data.nodes:[],edges=data&&Array.isArray(data.edges)?data.edges:[];host.append(el('p',location?`当前位置：${location.label}`:'当前位置尚未确定。'));if(!nodes.length){host.append(el('p','尚无已知地点。'));return}const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),count=nodes.length,positions={};nodes.forEach((node,i)=>{const angle=(Math.PI*2*i/count)-Math.PI/2;positions[node.id]={x:220+Math.cos(angle)*Math.min(86,36*count),y:112+Math.sin(angle)*76}});edges.forEach(edge=>{const a=positions[edge.from],b=positions[edge.to];if(!a||!b)return;const line=document.createElementNS(ns,'line');line.setAttribute('x1',a.x);line.setAttribute('y1',a.y);line.setAttribute('x2',b.x);line.setAttribute('y2',b.y);line.setAttribute('class','map-edge');svg.append(line)});nodes.forEach(node=>{const p=positions[node.id],g=document.createElementNS(ns,'g'),circle=document.createElementNS(ns,'circle'),label=document.createElementNS(ns,'text');g.setAttribute('role','button');g.setAttribute('tabindex','0');g.setAttribute('aria-label',`查看地点：${node.label}`);circle.setAttribute('cx',p.x);circle.setAttribute('cy',p.y);circle.setAttribute('r','19');circle.setAttribute('class',`map-node${location&&node.id===location.id?' current':''}${selectedMapId===node.id?' selected':''}`);label.setAttribute('x',p.x);label.setAttribute('y',p.y+35);label.setAttribute('text-anchor','middle');label.setAttribute('class','map-label');label.textContent=node.label;const select=()=>{selectedMapId=node.id;renderMap(data,location)};g.addEventListener('click',select);g.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select()}});g.append(circle,label);svg.append(g)});host.append(svg);const selected=nodes.find(node=>node.id===selectedMapId);if(selected){const detail=el('p',`${selected.label} · ${selected.kind}`);detail.className='map-detail';host.append(detail)}const exits=el('ul');const visible=state&&state.scene&&Array.isArray(state.scene.exits)?state.scene.exits:[];if(!visible.length)exits.append(el('li','没有已知相邻地点。'));visible.forEach(x=>exits.append(el('li',`→ ${x.label}（${x.kind}）`)));host.append(exits)}
function render(s){state=s;""",
)
_JS = _JS.replace(
    "function render(s){state=s;",
    "function render(s){state=s;const savedSteer=s.session&&typeof s.session.soft_direction==='string'?s.session.soft_direction:'';if(document.activeElement!==$('soft-direction'))$('soft-direction').value=savedSteer;",
)
_JS = _JS.replace(
    "function setBusy(next){busy=next;$('submit-action').disabled=next;$('undo').disabled=next;$('resume-action').disabled=next}",
    "function setBusy(next){busy=next;$('submit-action').disabled=next;$('undo').disabled=next;$('resume-action').disabled=next;$('save-steer').disabled=next;$('clear-steer').disabled=next}",
)
_JS = _JS.replace(
    "async function sendAction(envelope,recovering){",
    "async function saveSteering(clear){if(busy)return;try{const text=clear?null:$('soft-direction').value;const d=await api('/api/steer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});if(state)state.session={soft_direction:d.soft_direction};$('soft-direction').value=d.soft_direction||'';setStatus(d.soft_direction?'已保存会话软方向；它不改写世界、路线或权限。':'已清除软方向；后续回合只按行动和已保存世界推进。')}catch(e){setStatus(e.message,true)}}\nasync function sendAction(envelope,recovering){",
)
_JS = _JS.replace(
    "$('undo').addEventListener('click',async()=>{if(!state||busy)return;setBusy(true);setStatus('正在撤销…');try{const d=await api('/api/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:state.revision})});render(d.state);setStatus(d.message)}catch(e){setStatus(e.message,true)}finally{setBusy(false)}});load()",
    "$('undo').addEventListener('click',async()=>{if(!state||busy)return;setBusy(true);setStatus('正在撤销…');try{const d=await api('/api/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:state.revision})});render(d.state);setStatus(d.message)}catch(e){setStatus(e.message,true)}finally{setBusy(false)}});$('save-steer').addEventListener('click',()=>saveSteering(false));$('clear-steer').addEventListener('click',()=>saveSteering(true));load()",
)
_JS = _JS.replace(
    "clear($('map'));const loc=s.scene.location;$('map').append(el('p',loc?`当前位置：${loc.label}`:'当前位置尚未确定。'));const exits=el('ul');if(!s.scene.exits.length)exits.append(el('li','没有已知相邻地点。'));s.scene.exits.forEach(x=>exits.append(el('li',`→ ${x.label}（${x.kind}）`)));$('map').append(exits);",
    "renderMap(s.map,s.scene.location);",
)
_JS = _JS.replace(
    "setStatus(`第 ${s.turn} 回合 · 版本 ${s.revision} · 已从本地保存读取。`)",
    "const time=s.scene&&s.scene.time;const clock=time&&typeof time.day==='number'&&typeof time.band==='string'?`第 ${time.day} 天 · ${time.band} · `:'';setStatus(`${clock}第 ${s.turn} 回合 · 版本 ${s.revision} · 已从本地保存读取。`)",
)
_JS = _JS.replace(
    "function renderMap(data,location){",
    r"""function suggestExit(label){const input=$('action-input');if(!input)return;const suggestion=`前往${label}。`,existing=input.value.trim();input.value=existing?`${input.value.replace(/\s*$/,'')}\n${suggestion}`:suggestion;input.focus();setStatus(`已填入前往 ${label} 的建议；仍需编辑并保存行动。`)}function renderMap(data,location){""",
)
_JS = _JS.replace(
    "async function load(){try{render(await api('/api/state'));syncPending()}catch(e){setStatus(e.message,true)}}",
    "function renderRecall(hits){const list=$('recall-results');clear(list);const items=Array.isArray(hits)?hits:[];if(!items.length){list.append(el('li','没有匹配的已知线索。'));return}items.forEach(text=>list.append(el('li',text)))}async function submitRecall(){const query=$('recall-query').value.trim(),button=$('search-recall');if(!query){renderRecall([]);setStatus('请输入要回忆的关键词。',true);return}button.disabled=true;try{const data=await api(`/api/recall?q=${encodeURIComponent(query)}`);renderRecall(data.hits);setStatus(data.hits&&data.hits.length?'已查询当前主角已知线索。':'没有匹配的已知线索。')}catch(e){setStatus(e.message,true)}finally{button.disabled=false}}async function load(){try{render(await api('/api/state'));syncPending()}catch(e){setStatus(e.message,true)}}",
)
_JS = _JS.replace(
    "$('save-steer').addEventListener('click',()=>saveSteering(false));$('clear-steer').addEventListener('click',()=>saveSteering(true));load()",
    "$('save-steer').addEventListener('click',()=>saveSteering(false));$('clear-steer').addEventListener('click',()=>saveSteering(true));$('recall-form').addEventListener('submit',event=>{event.preventDefault();submitRecall()});load()",
)
_JS = _JS.replace(
    "visible.forEach(x=>exits.append(el('li',`→ ${x.label}（${x.kind}）`)));host.append(exits)}",
    r"""const exitCosts=state&&state.scene&&state.scene.exit_costs&&typeof state.scene.exit_costs==='object'?state.scene.exit_costs:{};visible.forEach(x=>{const item=el('li'),button=el('button','填入行动'),cost=typeof exitCosts[x.id]==='string'?` · 移动成本 ${exitCosts[x.id]}`:'';button.type='button';button.className='route-suggestion';button.addEventListener('click',()=>suggestExit(x.label));item.append(document.createTextNode(`→ ${x.label}（${x.kind}${cost}） `),button);exits.append(item)});host.append(exits)}""",
)
_CSS += ".route-suggestion{margin-left:6px;padding:3px 7px;font-size:.8rem}.player-map li{margin:6px 0}"

_GM_INDEX = """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>LLM RPG · 本地 GM 世界总览</title><link rel=\"stylesheet\" href=\"/app.css\"></head><body><main><header><p class=\"eyebrow gm-mode\">LOCAL · GM READ-ONLY · CANONICAL WORLD</p><h1>世界总览</h1><p id=\"status\" role=\"status\">正在读取已保存的世界…</p><p class=\"notice\">这是启动时通过 <code>--gm</code> 显式开启的本地只读模式。切换玩家/GM 视角需要重启服务；这里没有编辑、推进或玩家行动按钮。</p></header><section class=\"grid gm-grid\"><article class=\"panel map-panel\"><h2>全局地点连接图</h2><p class=\"hint\">节点和连线直接来自当前地点实体与 <code>adjacent_to</code> 关系。</p><div id=\"map\" class=\"map-graph\"></div></article><article class=\"panel\"><h2>地点系统</h2><div id=\"places\"></div></article><article class=\"panel\"><h2>角色系统</h2><div id=\"people\"></div></article><article class=\"panel\"><h2>物品与持有关系</h2><div id=\"objects\"></div></article><article class=\"panel\"><h2>摘要 / recap</h2><div id=\"recaps\"></div></article><article class=\"panel timeline\"><h2>世界事件时间线</h2><p class=\"hint\">标明客观事实、公开叙事、摘要以及已撤回历史。私有角色解释不会在此出现。</p><div id=\"timeline\"></div></article></section><footer>GM 全局可见不等于玩家可见，也不会写回玩家响应、模型上下文或私有解释记忆。</footer></main><script src=\"/app.js\"></script></body></html>"""
_GM_INDEX = _GM_INDEX.replace('<article class="panel"><h2>摘要 / recap</h2>', '<article class="panel codex"><h2>世界设定典（只读）</h2><label for="codex-search">搜索标题、类别或正文</label><input id="codex-search" type="search" placeholder="例如：地理、旧塔"><div id="codex"></div></article><article class="panel"><h2>摘要 / recap</h2>')

_GM_CSS = _CSS + ".gm-mode{color:#ffcf74}.notice{border-left:3px solid #d79b3a;padding-left:12px;color:#d8c49d}.hint{color:#9eb1c5;font-size:.9rem}.gm-grid{align-items:start}.map-panel{grid-column:span 2}.map-graph{min-height:250px;overflow:auto}.map-graph svg{width:100%;min-width:520px;height:270px;background:#101820;border:1px solid #304052;border-radius:8px}.map-edge{stroke:#8094a8;stroke-width:2}.map-node{fill:#315779;stroke:#a6d4f5;stroke-width:1.5}.map-label{fill:#e9edf2;font-size:13px}.row{border-top:1px solid #304052;padding:9px 0}.row:first-child{border-top:0}.meta{color:#9eb1c5;font-size:.85rem}.badge{display:inline-block;border-radius:999px;padding:1px 7px;margin-right:5px;font-size:.78rem;background:#33465a}.badge.objective{background:#4b5d78}.badge.public_narrative{background:#3f6b68}.badge.recap{background:#6a587e}.badge.retracted{background:#833d49;color:#fff2f2}.facts{margin:6px 0 0;padding-left:18px}.facts li{margin:2px 0}@media(max-width:700px){.map-panel{grid-column:auto}.map-graph svg{min-width:440px;height:310px}}"

_GM_JS = r"""(() => {
const $=id=>document.getElementById(id),el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
function clear(n){n.replaceChildren()} function row(title,meta,details){const n=el('div');n.className='row';n.append(el('strong',title),el('div',meta));n.lastChild.className='meta';if(details)n.append(details);return n}
function facts(items){if(!items.length)return null;const list=el('ul');list.className='facts';items.forEach(f=>{const text=`${f.predicate}: ${f.value}${f.secrecy?`（${f.secrecy}）`:''}`;list.append(el('li',text))});return list}
function entityRows(id,items,kind){const out=$(id);clear(out);if(!items.length){out.append(el('p','暂无记录。'));return}items.forEach(x=>{let meta=`${x.id} · ${x.tier}`;if(kind==='place')meta+=` · ${x.kind} · ${x.visibility}`;if(kind==='person'&&x.location)meta+=` · 位于 ${x.location}`;if(kind==='object'&&x.holder)meta+=` · 持有者 ${x.holder}`;out.append(row(x.label,meta,facts(x.facts)))})}
function map(data){const host=$('map');clear(host);if(!data.nodes.length){host.append(el('p','暂无地点节点。'));return}const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),count=data.nodes.length,positions={};data.nodes.forEach((node,i)=>{const angle=(Math.PI*2*i/count)-Math.PI/2;positions[node.id]={x:260+Math.cos(angle)*Math.min(100,38*count),y:135+Math.sin(angle)*95}});data.edges.forEach(edge=>{const a=positions[edge.from],b=positions[edge.to];if(!a||!b)return;const line=document.createElementNS(ns,'line');line.setAttribute('x1',a.x);line.setAttribute('y1',a.y);line.setAttribute('x2',b.x);line.setAttribute('y2',b.y);line.setAttribute('class','map-edge');svg.append(line)});data.nodes.forEach(node=>{const p=positions[node.id],g=document.createElementNS(ns,'g'),c=document.createElementNS(ns,'circle'),t=document.createElementNS(ns,'text');c.setAttribute('cx',p.x);c.setAttribute('cy',p.y);c.setAttribute('r','20');c.setAttribute('class','map-node');t.setAttribute('x',p.x);t.setAttribute('y',p.y+38);t.setAttribute('text-anchor','middle');t.setAttribute('class','map-label');t.textContent=node.label;g.append(c,t);svg.append(g)});host.append(svg);const list=el('ul');data.edges.forEach(e=>list.append(el('li',`${e.from} ↔ ${e.to}（移动成本 ${e.cost}）`)));host.append(list)}
function timeline(items,unavailable){const out=$('timeline');clear(out);if(unavailable){out.append(el('p','公开历史暂不可用；请查看当前世界状态。'));return}items.slice().reverse().forEach(x=>{const tags=el('div');for(const name of [x.category,x.retracted?'retracted':null])if(name){const b=el('span',name==='objective'?'客观事实':name==='public_narrative'?'公开叙事':name==='recap'?'摘要':'已撤回');b.className=`badge ${name}`;tags.append(b)}const d=el('div',x.summary);out.append(row(`${x.type} · 第 ${x.day} 天 / 回合 ${x.turn}`,`${x.scene} · ${x.id}`,tags));out.lastChild.append(d)})}
function recaps(items,unavailable){const out=$('recaps');clear(out);if(unavailable){out.append(el('p','公开历史暂不可用；请查看当前世界状态。'));return}if(!items.length){out.append(el('p','尚无已保存摘要。'));return}items.forEach(x=>out.append(row(x.scene,'已保存 recap',el('div',x.summary))));}
function codex(items){const out=$('codex'),q=$('codex-search').value.trim().toLowerCase();clear(out);const found=items.filter(x=>`${x.title} ${x.kind} ${x.body}`.toLowerCase().includes(q));if(!found.length){out.append(el('p','没有匹配的公开设定。'));return}found.forEach(x=>{const d=el('details'),s=el('summary',`${x.kind?`[${x.kind}] `:''}${x.title}`);d.append(s,el('p',x.body));out.append(d)})}
async function load(){try{const r=await fetch('/api/gm/state');const state=await r.json();if(!r.ok)throw new Error(state.message||'读取失败');entityRows('places',state.places,'place');entityRows('people',state.people,'person');entityRows('objects',state.objects,'object');map(state.map);timeline(state.timeline,state.history_unavailable===true);recaps(state.recaps,state.history_unavailable===true);codex(state.codex);$('codex-search').addEventListener('input',()=>codex(state.codex));$('status').textContent=`GM 只读视图 · 第 ${state.day} 天 · 回合 ${state.turn} · 版本 ${state.revision}`;}catch(err){$('status').textContent='GM 世界总览暂时不可用；请检查本地服务。'}}load();
})();"""


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        log.info("gui http %s", format % args)


def _json_response(start_response, status: str, payload: dict[str, Any]):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    start_response(status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(raw))),
                            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")])
    return [raw]


def _require_same_origin_json(environ) -> None:
    """Reject cross-site/simple-form writes before they reach a controller.

    Loopback binding prevents remote network peers, but browsers can still send
    a cross-site form to localhost.  This single-player server has no accounts,
    so strict expected Host + Origin + JSON is the appropriate narrow guard.
    """
    host = environ.get("HTTP_HOST")
    # wsgiref's SERVER_PORT is not reliable when the listener is wrapped by
    # the local execution sandbox, so validate the actual Host supplied by the
    # browser rather than comparing it with that internal default.  It must be
    # an explicit numeric 127.0.0.1 authority, and Origin (when supplied) must
    # match it exactly.
    valid_loopback_host = isinstance(host, str) and host.startswith("127.0.0.1:") and host[10:].isdigit()
    expected_origin = f"http://{host}" if valid_loopback_host else None
    content_type = (environ.get("CONTENT_TYPE") or "").split(";", 1)[0].strip().lower()
    # Same-origin ``fetch`` is permitted to omit Origin by browsers.  A
    # cross-site script sends it, while a cross-site form cannot manufacture
    # application/json; Host remains exact in both cases.
    origin = environ.get("HTTP_ORIGIN")
    if not valid_loopback_host or origin not in (None, expected_origin) or content_type != "application/json":
        raise GuiRequestError("此本地页面不能接受跨站或非 JSON 写入请求。")


def _read_json_request(environ, *, minimum: int, maximum: int, error_message: str) -> dict[str, Any]:
    """Parse only the request body, mapping syntax/size failures to an explicit 400.

    Keep this boundary intentionally narrow.  A ``TypeError`` or ``ValueError``
    raised later by turn publication, DTO construction, or response rendering is
    an unknown publication outcome, not proof that no action committed.
    """
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError) as exc:
        raise GuiRequestError(error_message) from exc
    if size < minimum or size > maximum:
        raise GuiRequestError(error_message)
    try:
        data = json.loads(environ["wsgi.input"].read(size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuiRequestError(error_message) from exc
    if not isinstance(data, dict):
        raise GuiRequestError(error_message)
    return data


def make_app(dashboard: PlayerDashboard | GmDashboard, *, mode: str = "player"):
    """Return a closed WSGI app for one startup-selected presentation mode.

    Mode selection happens before the server is made.  It is not a browser
    toggle: the player app has no GM endpoint and the GM app has no mutation
    routes, so hidden world data cannot be obtained by changing front-end code.
    """
    if mode not in {"player", "gm"}:
        raise ValueError("mode must be player or gm")

    def app(environ, start_response):
        path, method = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "GET")
        try:
            if mode == "gm":
                if method == "GET" and path == "/":
                    body = _GM_INDEX.encode("utf-8")
                    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
                                                ("Cache-Control", "no-store"), ("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
                                                ("X-Content-Type-Options", "nosniff")])
                    return [body]
                if method == "GET" and path == "/app.css":
                    body = _GM_CSS.encode("utf-8"); start_response("200 OK", [("Content-Type", "text/css; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")]); return [body]
                if method == "GET" and path == "/app.js":
                    body = _GM_JS.encode("utf-8"); start_response("200 OK", [("Content-Type", "application/javascript; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]); return [body]
                if method == "GET" and path == "/api/gm/state":
                    return _json_response(start_response, "200 OK", dashboard.state())
                return _json_response(start_response, "404 Not Found", {"message": "找不到此页面。"})
            if method == "GET" and path == "/":
                body = _player_index(dashboard.demo).encode("utf-8")
                start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
                                            ("Cache-Control", "no-store"), ("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
                                            ("X-Content-Type-Options", "nosniff")])
                return [body]
            if method == "GET" and path == "/app.css":
                body = _CSS.encode("utf-8"); start_response("200 OK", [("Content-Type", "text/css; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")]); return [body]
            if method == "GET" and path == "/app.js":
                body = _JS.encode("utf-8"); start_response("200 OK", [("Content-Type", "application/javascript; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]); return [body]
            if method == "GET" and path == "/api/state":
                return _json_response(start_response, "200 OK", dashboard.state())
            if method == "GET" and path == "/api/recall":
                query = parse_qs(environ.get("QUERY_STRING") or "", keep_blank_values=True).get("q", [])
                if len(query) != 1:
                    raise GuiRequestError("请输入要回忆的关键词。")
                return _json_response(start_response, "200 OK", dashboard.recall(query[0]))
            if method == "POST" and path == "/api/action":
                _require_same_origin_json(environ)
                data = _read_json_request(environ, minimum=0, maximum=(_MAX_ACTION_BYTES * 2) + 1024,
                                          error_message="请求格式无效，请刷新后重试。")
                return _json_response(start_response, "200 OK", dashboard.action(
                    player_input=data.get("text"), action_id=data.get("action_id"), expected_revision=data.get("expected_revision"),
                    recipient=data.get("recipient"), statement=data.get("statement")))
            if method == "POST" and path == "/api/steer":
                _require_same_origin_json(environ)
                data = _read_json_request(environ, minimum=2, maximum=4096,
                                          error_message="软方向格式无效，请重新填写。")
                return _json_response(start_response, "200 OK", dashboard.set_steering(data.get("text")))
            if method == "POST" and path == "/api/undo":
                _require_same_origin_json(environ)
                data = _read_json_request(environ, minimum=2, maximum=256,
                                          error_message="撤销请求无效，请刷新后重试。")
                return _json_response(start_response, "200 OK", dashboard.undo(
                    expected_revision=data.get("expected_revision")))
            return _json_response(start_response, "404 Not Found", {"message": "找不到此页面。"})
        except GuiRequestError as exc:
            return _json_response(start_response, "400 Bad Request", {
                "message": str(exc), "confirmed_rejection": True,
            })
        except GuiRecoveryError:
            return _json_response(start_response, "409 Conflict", {"message": "已保存行动无法安全恢复；请刷新查看当前世界状态。"})
        except Exception:
            # The failure can occur after a turn committed.  Do not emit the
            # exception text/trace here: it may contain private input or an
            # implementation detail and the browser has no safe outcome to
            # infer from it.
            log.error("player gui request failed")
            return _json_response(start_response, "500 Internal Server Error", {"message": "请求结果未能显示；请刷新查看当前状态。"})
    return app


def build_dashboard(campaign_dir: str | Path, *, demo: bool = False, provider=None,
                    strategy=None, max_repairs: int = 6) -> PlayerDashboard:
    """Build either explicit offline demo or the normal CLI-compatible play path.

    Normal mode never swaps an unavailable provider for the demo strategy.  The
    caller must supply a resolved CLI provider, and an empty campaign must be
    initialized through the existing ``python -m app`` genesis workflow.
    """
    if demo:
        engine = build_engine(campaign_dir, provider=FakeLLMProvider(responses=["1"]), embedder=None,
                              enable_interpretive_memory=False)
        _seed_demo_campaign(engine)
        return PlayerDashboard(engine, strategy=OfflineDemoStrategy(), max_repairs=max_repairs, demo=True)
    if provider is None:
        raise GuiRequestError("正常 GUI 模式需要已配置的 provider；请传 --provider，或显式使用 --demo。")
    engine = build_engine(campaign_dir, provider=provider, enable_interpretive_memory=False)
    if not any(engine.store.iter_events()):
        engine.store.close()
        raise GuiRequestError("当前 campaign 尚无世界；请先用 python -m app 初始化，或显式使用 --demo。")
    return PlayerDashboard(engine, strategy=strategy or AuthorStrategy(), max_repairs=max_repairs, demo=False)


def build_gm_dashboard(campaign_dir: str | Path, *, demo: bool = False) -> GmDashboard:
    """Open the same local campaign for a canonical-world, read-only GM view."""
    engine = build_engine(campaign_dir, provider=FakeLLMProvider(responses=["1"]), embedder=None,
                          enable_interpretive_memory=False)
    if demo:
        _seed_demo_campaign(engine)
    return GmDashboard(engine)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地离线 RPG 仪表盘（玩家默认；GM 需显式只读启用）")
    parser.add_argument("--campaign", required=True, help="启动时选择的本地 campaign 目录")
    parser.add_argument("--demo", action="store_true", help="仅在空 campaign 中写入固定离线演示世界")
    parser.add_argument("--gm", action="store_true", help="显式启动本地只读 GM 世界总览（切换模式需重启）")
    parser.add_argument("--provider", default="fake", choices=["fake", "openai", "deepseek", "zhipu", "anthropic"],
                        help="正常玩家模式的 CLI provider；--demo 始终使用固定离线替身")
    parser.add_argument("--model", default=None, help="正常玩家模式的模型名")
    parser.add_argument("--base-url", default=None, dest="base_url", help="正常玩家模式 provider 的 base URL")
    parser.add_argument("--max-tokens", default=None, type=int, dest="max_tokens", help="正常玩家模式的输出 token 上限")
    parser.add_argument("--max-repairs", default=6, type=int, dest="max_repairs", help="每回合结构修复上限")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"], help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help="本机端口（0 可让系统分配）")
    args = parser.parse_args(argv)
    if not (0 <= args.port <= 65535):
        parser.error("--port 必须在 0 到 65535 之间")
    if args.max_repairs < 0:
        parser.error("--max-repairs 必须为非负整数")
    mode = "gm" if args.gm else "player"
    if args.gm:
        dashboard = build_gm_dashboard(args.campaign, demo=args.demo)
    elif args.demo:
        dashboard = build_dashboard(args.campaign, demo=True, max_repairs=args.max_repairs)
    else:
        try:
            from llm.provider import make_provider
            provider = make_provider(args.provider, model=args.model, base_url=args.base_url, max_tokens=args.max_tokens)
            dashboard = build_dashboard(args.campaign, provider=provider, max_repairs=args.max_repairs)
        except GuiRequestError as exc:
            parser.error(str(exc))
        except Exception:
            parser.error("无法初始化所选 provider；请检查本机既有 provider 配置，或显式使用 --demo。")
    server = make_server("127.0.0.1", args.port, make_app(dashboard, mode=mode), handler_class=_QuietHandler)
    label = "GM 只读世界总览" if args.gm else "玩家仪表盘"
    print(f"{label}已在 http://127.0.0.1:{server.server_port} 启动（Ctrl-C 退出；切换模式需重启）。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        dashboard.engine.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
