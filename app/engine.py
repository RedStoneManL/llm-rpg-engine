"""app.engine — build_engine + new_game genesis.

build_engine(campaign_dir, *, provider=None, embedder=None) -> Engine
    Wires all 6 systems into the registry (ontology first), opens the event
    store, resolves the provider and embedder, projects the current world,
    and returns an Engine dataclass holding everything.

new_game(engine, pitch="") -> None
    Delegates to loop.bootstrap.bootstrap_world: a deterministic, reroll-able
    world genesis (macro region skeleton + local map + factions + NPCs-with-
    secrets + campaign threads + opening narration), appended as genesis events
    (turn=0) with engine.world re-projected. `pitch` biases the world tone/genre.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kernel.registry import Registry
from kernel.projection import project
from kernel.events import kernel_event, open_store
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.object import ObjectSystem
from systems.faction import FactionSystem
from systems.knowledge import KnowledgeSystem
from systems.director import DirectorSystem
from systems.cascade import CascadeSystem
from systems.time import TimeSystem
from systems.narrative import NarrativeSystem
from systems.scene import SceneSystem
from systems.lore import LoreSystem
from systems.codex import CodexSystem
from systems.resources import ResourceSystem
from llm.provider import FakeLLMProvider, make_provider
from engine.embed import get_embedder
from engine.log import get_logger

log = get_logger("app.engine")

# Protagonist id — reused by loop.bootstrap.bootstrap_world
_PROTAGONIST_ID = "protagonist"
_DEFAULT_EMBEDDER = object()

# This is deliberately a fixed, local-only experiment, rather than a model
# prompt or a user-supplied instruction.  It exercises the private-record
# lifecycle without granting the narrator, tools, recall, or recap a new
# source of context.
OFFLINE_INTERPRETIVE_MEMORY_TEXT = (
    "离线实验解释：这只是主角的私有感受，不是世界事实，也不改变任何权限。"
)


@dataclass
class Engine:
    """All wired components for a running RPG engine session."""
    registry: Registry
    store: Any          # EventStore
    provider: Any       # LLMProvider
    embedder: Any       # Embedder or None
    world: dict = field(default_factory=dict)
    campaign_seed: int = 0
    cascade_provider: Any = None  # Optional cheap LLMProvider for cascade node calls
    interpretive_memory_enabled: bool = False
    interpretive_memory_generator: Any = None

    def public_history(self) -> list[dict]:
        """Public DTO history; raw EventStore access remains host-internal."""
        from systems.interpretive_memory import public_event_history
        return public_event_history(self.store.iter_events(include_retracted=True))


def _derive_campaign_seed(campaign_dir: Path) -> int:
    """Deterministic seed from the campaign dir NAME (rewind-safe: replays identically)."""
    name = campaign_dir.name or "campaign"
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)


def build_engine(
    campaign_dir,
    *,
    provider=None,
    embedder=_DEFAULT_EMBEDDER,
    enable_interpretive_memory: bool = False,
) -> Engine:
    """Wire all 6 systems + store + provider + embedder; project initial world.

    Args:
        campaign_dir: Path (or str) to the campaign directory.
        provider:     Optional LLMProvider. If None, attempts make_provider from
                      env (default fake); an explicitly configured provider failure raises.
        embedder:     An embedder, or explicit ``None`` to disable semantic
                      embedding. When omitted, uses get_embedder() (checks env).

    Returns:
        Engine with registry, store, provider, embedder, world.
    """
    campaign_dir = Path(campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)

    # Build registry — ontology must be first (all others require it)
    registry = Registry()
    registry.register(OntologySystem())
    registry.register(PlaceSystem())
    registry.register(CharacterSystem())
    registry.register(ObjectSystem())
    registry.register(FactionSystem())
    registry.register(KnowledgeSystem())
    registry.register(DirectorSystem())
    registry.register(CascadeSystem())
    registry.register(TimeSystem())
    registry.register(NarrativeSystem())
    registry.register(SceneSystem())
    registry.register(LoreSystem())
    registry.register(CodexSystem())
    registry.register(ResourceSystem())
    from systems.challenge import ChallengeSystem
    registry.register(ChallengeSystem())
    memory_generator = None
    if enable_interpretive_memory:
        # Opt-in must happen before opening/projecting the store, so a reopened
        # experimental campaign can replay its private slice.  Normal app
        # construction deliberately never registers this system.
        from systems.interpretive_memory import (
            InterpretiveMemorySystem, OfflineInterpretationGenerator,
        )
        registry.register(InterpretiveMemorySystem())
        memory_generator = OfflineInterpretationGenerator(OFFLINE_INTERPRETIVE_MEMORY_TEXT)

    log.debug("build_engine: registered %d systems", len(registry.systems))

    # Open the event store
    db_path = campaign_dir / "events.db"
    jsonl_path = campaign_dir / "events.jsonl"
    store = open_store(db_path, jsonl_path, registry.event_types())
    store.preflight = lambda events: project(registry, events)

    # Resolve provider
    if provider is None:
        import os
        kind = os.environ.get("RPG_PROVIDER", "fake")
        model = os.environ.get("RPG_MODEL")
        base_url = os.environ.get("RPG_BASE_URL")
        try:
            provider = make_provider(kind, model=model, base_url=base_url)
        except Exception:
            store.close()
            raise  # A configured real provider must never silently become a stub.

    # Resolve embedder
    if embedder is _DEFAULT_EMBEDDER:
        embedder = get_embedder()

    # Resolve optional cheap cascade provider (env-driven, no CLI/run.sh change needed)
    import os as _os
    cascade_provider = None
    cascade_model = _os.environ.get("RPG_CASCADE_MODEL") or _os.environ.get("GLM_CASCADE_MODEL")
    if cascade_model and hasattr(provider, "model") and hasattr(provider, "api_key"):
        try:
            cascade_provider = type(provider)(
                model=cascade_model,
                api_key=provider.api_key,
                base_url=getattr(provider, "base_url", None),
                max_tokens=getattr(provider, "max_tokens", None),
            )
            log.debug("build_engine: cascade_provider built model=%s", cascade_model)
        except Exception:
            log.debug("build_engine: cascade_provider construction failed; falling back to main")
            cascade_provider = None

    # Project one consistent opening snapshot.  A campaign may also be open
    # in another local controller, so never label a projection with a revision
    # read from a different point in the event stream.
    revision, history = store.snapshot()
    world = project(registry, history)
    world['_revision'] = revision

    campaign_seed = world.get('meta', {}).get('campaign_seed')
    if campaign_seed is None:
        campaign_seed = _derive_campaign_seed(campaign_dir)

    log.debug("build_engine: done campaign_dir=%s", campaign_dir)
    return Engine(
        registry=registry,
        store=store,
        provider=provider,
        embedder=embedder,
        world=world,
        campaign_seed=campaign_seed,
        cascade_provider=cascade_provider,
        interpretive_memory_enabled=enable_interpretive_memory,
        interpretive_memory_generator=memory_generator,
    )


def last_turn(engine: Engine) -> int:
    """Return the maximum turn number across all (non-retracted) events, or 0 if none."""
    return engine.store.next_turn() - 1


def last_turn_and_revision(engine: Engine) -> tuple[int, int]:
    """Return the active player-turn frontier and its store revision together.

    An optimistic undo needs both values to describe the same durable branch.
    Reading the last turn first and revision later lets a peer publish in the
    gap, which can make the stale turn look approved for the peer's revision.
    ``snapshot`` includes retracted history, so filter it here exactly as
    ``last_turn`` filters EventStore's default active iterator.
    """
    revision, history = engine.store.snapshot()
    max_turn = max(
        (event.get("turn") or 0 for event in history if not event.get("retracted")),
        default=0,
    )
    return max_turn, revision


def projected_revision(engine: Engine) -> int:
    """Return the revision that labels ``engine.world`` without a fresh read.

    Player-facing DTOs must not pair an existing projection with a newer store
    revision that a peer committed immediately afterwards.  Engines built by
    this module always carry an integer marker; retain a defensive fallback
    for narrow test doubles and legacy callers that construct an Engine by
    hand.
    """
    revision = engine.world.get("_revision")
    return revision if type(revision) is int else engine.store.revision


def _replace_world_from_snapshot(engine: Engine, revision: int, history) -> None:
    engine.world = project(engine.registry, history)
    engine.world["_revision"] = revision


def reproject_engine_world(engine: Engine) -> int:
    """Replace ``engine.world`` from one read-only store snapshot.

    Controllers use the projected revision as their cache identity.  Reading
    that revision separately from the event iterator can otherwise label an
    older projection as a peer's newer commit.  ``EventStore.snapshot`` keeps
    both values in one short SQLite read transaction; retracted rows are kept
    in the history for deterministic replay and ignored by projection as usual.
    """
    revision, history = engine.store.snapshot()
    _replace_world_from_snapshot(engine, revision, history)
    return revision


def refresh_engine_world_if_advanced(engine: Engine) -> bool:
    """Refresh only when one store snapshot is newer than this projection.

    The comparison must use the same snapshot as any replacement world.  A
    separate revision read can race with a peer publish and incorrectly claim
    that an older projection is already current.  If a peer publishes after
    this snapshot, retaining the older, correctly labelled projection is safe;
    the next read will observe the newer complete snapshot.
    """
    revision, history = engine.store.snapshot(if_changed_from=projected_revision(engine))
    if history is None:
        return False
    _replace_world_from_snapshot(engine, revision, history)
    return True


def rewind(engine: Engine, turn: int, *, expected_revision: int | None = None) -> dict:
    """Retract all events with turn >= `turn` and re-project the world.

    Uses engine.store.retract_from_turn (soft-retracts, SQLite authoritative).
    Re-projects from the surviving events via kernel.projection.project.

    Returns {"retracted": N, "turn": T} where N = number of events retracted.
    """
    if type(turn) is not int or turn < 1:
        raise ValueError("rewind turn must be a positive player turn")
    n = engine.store.retract_from_turn(turn, expected_revision=expected_revision)
    reproject_engine_world(engine)
    log.debug("rewind: turn=%d retracted=%d", turn, n)
    return {"retracted": n, "turn": turn}


def new_game(engine: Engine, pitch: str = "", *, spec=None, progress=None, flavor: str = "classic") -> dict:
    """Seed genesis via the real bootstrap pipeline.

    Delegates entirely to ``loop.bootstrap.bootstrap_world``, which runs the
    full 9-step world-generation pipeline (frame → regions → local map →
    protagonist → factions → NPCs → threads → opening narration).

    Args:
        engine:   The wired engine (from build_engine).
        pitch:    Optional world-background keyword(s) / theme string supplied by
                  the player.  Defaults to empty string (bootstrap falls back to
                  its own oracle-rolled genre).
        progress: Optional callback(step_index, total_steps, label) forwarded to
                  bootstrap_world.  Default None → no-op (existing callers unaffected).

    Returns:
        The dict returned by bootstrap_world:
        ``{"summary": {...}, "_state": {...}, "_boundaries": {...}}``.

    Note:
        The old three-event placeholder genesis (starting_location + generic
        protagonist + entity_moved) has been removed.  All caller sites that
        previously invoked ``new_game(engine)`` continue to work because
        ``pitch`` defaults to ``""``.
    """
    from loop.bootstrap import bootstrap_world
    log.debug("new_game: delegating to bootstrap_world pitch=%r spec=%s", pitch, bool(spec))
    result = bootstrap_world(engine, pitch, spec=spec, progress=progress, flavor=flavor)
    log.debug("new_game: bootstrap complete, world reprojected")
    return result


def resolve_genesis_spec(provider, *, pitch="", blueprint_path=None,
                         world_book_path=None, card_path=None,
                         card_as="protagonist",
                         inputs=None, out=None, interactive=False) -> dict:
    """Resolve a GenesisSpec from all sources: pitch -> conversion -> file -> session-zero.

    Precedence (later wins): interactive > file > conversion > pitch >
    (model-fill at bootstrap). Seeding pitch as the base means a player who
    already gave a pitch is not re-asked for the premise by session-zero.
    """
    from loop.genesis_spec import merge, normalize
    from loop.genesis_blueprint import BlueprintError
    spec: dict = normalize({"world_premise": {"genre": pitch}}) if pitch else {}

    if world_book_path or card_path:
        from loop.import_sillytavern import convert_sillytavern
        try:
            wb = _read_json(world_book_path) if world_book_path else None
            card = _read_json(card_path) if card_path else None
        except (OSError, ValueError) as e:
            # Surface a bad import file as the same clean error class as a bad
            # blueprint (not a raw FileNotFoundError/JSONDecodeError traceback).
            raise BlueprintError(f"failed to read SillyTavern import file: {e}") from e
        spec = merge(spec, convert_sillytavern(
            provider, world_book=wb, character_card=card, card_as=card_as))

    if blueprint_path:
        from loop.genesis_blueprint import load_blueprint
        spec = merge(spec, load_blueprint(blueprint_path))

    if interactive and inputs is not None:
        from app.session_zero import run_session_zero
        spec = run_session_zero(spec, inputs=inputs, out=(out or (lambda *_: None)),
                                interactive=True)

    return normalize(spec)


def validate_genesis_source_files(*, blueprint_path=None, world_book_path=None,
                                  card_path=None) -> None:
    """Check explicit genesis files before a new campaign opens its store.

    This is intentionally provider-free and does not normalize or translate a
    world book.  It exists for entry points that must reject an unreadable
    source without leaving behind an empty SQLite/JSONL campaign.  The normal
    resolver remains the single authority for merge precedence and is still
    called immediately before bootstrap.
    """
    from loop.genesis_blueprint import BlueprintError, load_blueprint

    try:
        if world_book_path:
            _read_json(world_book_path)
        if card_path:
            _read_json(card_path)
    except (OSError, ValueError) as e:
        raise BlueprintError(f"failed to read SillyTavern import file: {e}") from e
    if blueprint_path:
        load_blueprint(blueprint_path)


def _read_json(path):
    import json
    return json.loads(Path(path).read_text(encoding="utf-8"))
