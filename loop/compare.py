"""loop.compare — run_compare: produce 甲+丙 on the same pre-turn snapshot.

run_compare(registry, world, scene, player_input, *, provider, embedder=None,
            max_repairs=3) -> dict[str, tuple[TurnCommit, int, list[str]]]:
    Runs both AuthorStrategy (甲) and HybridStrategy (丙) via produce_turn
    against the SAME pre-turn world snapshot.  Neither is applied to the store.
    Returns {"甲": (commit, attempts, dropped), "丙": (commit, attempts, dropped)}.
    The caller selects one and uses run_turn with its host preparation to commit it.
"""
from __future__ import annotations

from kernel.registry import Registry
from engine.log import get_logger
from loop.strategy import AuthorStrategy, HybridStrategy
from loop.turn import produce_turn, TurnRejected
from loop.semantic_gate import finalize_candidate

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
    store=None, preparation=None,
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
    from loop.resources import registered_balances
    if preparation is None and store is not None:
        from loop.comparison_preparation import prepare_comparison
        preparation = prepare_comparison(registry, store, world, scene, player_input, provider)
    if preparation is not None:
        if store is None:
            raise TurnRejected('A prepared comparison requires its authoritative event store')
        from loop.comparison_preparation import restore_preparation
        world, scene = restore_preparation(registry, store, world, scene, player_input, preparation)
        scene = {**scene, '_comparison_preparation_digest': preparation.digest,
                 '_comparison_required_prefix': preparation.prefix_events}
    else:
        balances = registered_balances(world)
        if registry.owner_of_event('resources_resolved') is not None and any(
                owner == scene.get('protagonist') for owner, _ in balances):
            raise TurnRejected('Pass store= to prepare one shared resource result before comparison')
        scene = {**scene, '_resolved_values': balances}

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

    if jia_dropped or bing_dropped:
        raise TurnRejected('Comparison contains an invalid candidate; no alternatives are publishable')

    jia_commit._comparison_preparation = preparation
    bing_commit._comparison_preparation = preparation

    # Audit before showing either alternative. Selection reuses the host-bound
    # approval only while its source/context and complete candidate are intact.
    if not jia_dropped:
        jia_commit = finalize_candidate(registry, world, scene, player_input, jia_commit,
            provider=provider, revision=world.get('_revision'), required_sections=required_sections)
    if not bing_dropped:
        bing_commit = finalize_candidate(registry, world, scene, player_input, bing_commit,
            provider=provider, revision=world.get('_revision'), required_sections=required_sections)

    return {
        "甲": (jia_commit, jia_attempts, jia_dropped),
        "丙": (bing_commit, bing_attempts, bing_dropped),
    }
