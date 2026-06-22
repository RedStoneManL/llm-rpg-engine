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
from llm.provider import FakeLLMProvider, make_provider
from engine.embed import get_embedder
from engine.log import get_logger

log = get_logger("app.engine")

# Protagonist id — reused by loop.bootstrap.bootstrap_world
_PROTAGONIST_ID = "protagonist"


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


def _derive_campaign_seed(campaign_dir: Path) -> int:
    """Deterministic seed from the campaign dir NAME (rewind-safe: replays identically)."""
    name = campaign_dir.name or "campaign"
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)


def build_engine(
    campaign_dir,
    *,
    provider=None,
    embedder=None,
) -> Engine:
    """Wire all 6 systems + store + provider + embedder; project initial world.

    Args:
        campaign_dir: Path (or str) to the campaign directory.
        provider:     Optional LLMProvider. If None, attempts make_provider from
                      env, falling back to FakeLLMProvider.
        embedder:     Optional embedder. If None, uses get_embedder() (checks env).

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

    log.debug("build_engine: registered %d systems", len(registry.systems))

    # Open the event store
    db_path = campaign_dir / "events.db"
    jsonl_path = campaign_dir / "events.jsonl"
    store = open_store(db_path, jsonl_path, registry.event_types())

    # Resolve provider
    if provider is None:
        try:
            # Try to build from env — will use whatever provider keys are set
            import os
            kind = os.environ.get("RPG_PROVIDER", "fake")
            model = os.environ.get("RPG_MODEL")
            base_url = os.environ.get("RPG_BASE_URL")
            provider = make_provider(kind, model=model, base_url=base_url)
        except Exception:
            log.debug("build_engine: falling back to FakeLLMProvider")
            provider = FakeLLMProvider()

    # Resolve embedder
    if embedder is None:
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

    # Project world from existing events
    world = project(registry, store.iter_events())

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
    )


def last_turn(engine: Engine) -> int:
    """Return the maximum turn number across all (non-retracted) events, or 0 if none."""
    max_t = 0
    for ev in engine.store.iter_events():
        t = ev.get("turn") or 0
        if t > max_t:
            max_t = t
    return max_t


def rewind(engine: Engine, turn: int) -> dict:
    """Retract all events with turn >= `turn` and re-project the world.

    Uses engine.store.retract_from_turn (soft-retracts, SQLite authoritative).
    Re-projects from the surviving events via kernel.projection.project.

    Returns {"retracted": N, "turn": T} where N = number of events retracted.
    """
    n = engine.store.retract_from_turn(turn)
    engine.world = project(engine.registry, engine.store.iter_events())
    log.debug("rewind: turn=%d retracted=%d", turn, n)
    return {"retracted": n, "turn": turn}


def new_game(engine: Engine, pitch: str = "") -> dict:
    """Seed genesis via the real bootstrap pipeline.

    Delegates entirely to ``loop.bootstrap.bootstrap_world``, which runs the
    full 9-step world-generation pipeline (frame → regions → local map →
    protagonist → factions → NPCs → threads → opening narration).

    Args:
        engine: The wired engine (from build_engine).
        pitch:  Optional world-background keyword(s) / theme string supplied by
                the player.  Defaults to empty string (bootstrap falls back to
                its own oracle-rolled genre).

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
    log.debug("new_game: delegating to bootstrap_world pitch=%r", pitch)
    result = bootstrap_world(engine, pitch)
    log.debug("new_game: bootstrap complete, world reprojected")
    return result
