"""loop.compare — run_compare: produce 甲+丙 on the same pre-turn snapshot.

run_compare(registry, world, scene, player_input, *, provider, embedder=None,
            max_repairs=3) -> dict[str, tuple[TurnCommit, int, list[str]]]:
    Runs both AuthorStrategy (甲) and HybridStrategy (丙) via produce_turn
    against the SAME pre-turn world snapshot.  Neither is applied to the store.
    Returns {"甲": (commit, attempts, dropped), "丙": (commit, attempts, dropped)}.
    The caller selects one and calls apply_turn to commit it.
"""
from __future__ import annotations

from kernel.registry import Registry
from engine.log import get_logger
from loop.strategy import AuthorStrategy, HybridStrategy
from loop.turn import produce_turn

log = get_logger("loop.compare")


def run_compare(
    registry: Registry,
    world: dict,
    scene: dict,
    player_input: str,
    *,
    provider,
    embedder=None,
    max_repairs: int = 3,
    required_sections: frozenset = frozenset(),
) -> dict:
    """Run both 甲 (AuthorStrategy) and 丙 (HybridStrategy) on the same snapshot.

    Args:
        registry:     Kernel registry.
        world:        Current projected world dict (unchanged by this call).
        scene:        Scene dict with keys protagonist/present/day/location/(id).
        player_input: Raw player action string.
        provider:     LLMProvider (shared for both strategies).
        embedder:     Optional embedder for recall ranking.
        max_repairs:  Maximum repair attempts per strategy.

    Returns:
        {"甲": (commit, attempts, dropped), "丙": (commit, attempts, dropped)}
        Neither candidate is written to any store.
    """
    log.debug("run_compare: producing 甲+丙 on same world snapshot")

    # Strategy 甲: AuthorStrategy (one complete_json call)
    jia_commit, jia_attempts, jia_dropped = produce_turn(
        registry, world, scene, player_input,
        strategy=AuthorStrategy(),
        provider=provider,
        embedder=embedder,
        max_repairs=max_repairs,
        required_sections=required_sections,
    )
    log.debug("run_compare: 甲 done narration=%r attempts=%d dropped=%s",
              jia_commit.narration[:40], jia_attempts, jia_dropped)

    # Strategy 丙: HybridStrategy (free prose + grounded authoring of its structure)
    bing_commit, bing_attempts, bing_dropped = produce_turn(
        registry, world, scene, player_input,
        strategy=HybridStrategy(),
        provider=provider,
        embedder=embedder,
        max_repairs=max_repairs,
        required_sections=required_sections,
    )
    log.debug("run_compare: 丙 done narration=%r attempts=%d dropped=%s",
              str(bing_commit.narration)[:40], bing_attempts, bing_dropped)

    return {
        "甲": (jia_commit, jia_attempts, jia_dropped),
        "丙": (bing_commit, bing_attempts, bing_dropped),
    }
