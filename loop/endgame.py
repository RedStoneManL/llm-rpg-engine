"""loop.endgame — complex-line endgame logic (L4).

Pure logic layer: no random/time calls, no LLM calls. Callers pass a seeded
Oracle for determinism and rewind-safety.

Public API:
  RESCUE_GRACE_STAGES   = 1     # stage_idx must be >= this before rescue rolls start
  RESCUE_BASE           = 10    # chance at stage 0 (%)
  RESCUE_RANGE          = 40    # additional chance spread over stages
  FINALE_RESCUE_CHANCE  = 60    # last-chance rescue % when pending_finale fires

  world_rescue_chance(stage_idx, n_stages) -> int
  roll_world_rescue(oracle, stage_idx, n_stages) -> bool
  rescue_summary(line) -> str
  catastrophe_summary(line, region) -> str
  build_catastrophe_events(line, world, *, day, scene, turn,
                           emit_world_change=True) -> list[dict]
"""
from __future__ import annotations

from kernel.events import kernel_event
from loop.density import region_scope
from engine.log import get_logger

log = get_logger("loop.endgame")

# ---------------------------------------------------------------------------
# Constants (all engine-decided, callers should not override)
# ---------------------------------------------------------------------------

RESCUE_GRACE_STAGES: int = 1   # no rescue roll below this stage_idx
RESCUE_BASE: int = 10           # % chance at stage 0
RESCUE_RANGE: int = 40          # additional % spread across stages
FINALE_RESCUE_CHANCE: int = 60  # last-chance % when pending_finale fires


# ---------------------------------------------------------------------------
# world_rescue_chance
# ---------------------------------------------------------------------------

def world_rescue_chance(stage_idx: int, n_stages: int) -> int:
    """Return rescue success threshold (1..100) for a given stage progress.

    Progressive low→high: RESCUE_BASE at stage 0, rising to
    RESCUE_BASE + RESCUE_RANGE at the last stage. Result is clamped to [0, 100].

    Formula:
        chance = RESCUE_BASE + round(stage_idx / max(1, n_stages - 1) * RESCUE_RANGE)
    """
    raw = RESCUE_BASE + round(stage_idx / max(1, n_stages - 1) * RESCUE_RANGE)
    return max(0, min(100, raw))


# ---------------------------------------------------------------------------
# roll_world_rescue
# ---------------------------------------------------------------------------

def roll_world_rescue(oracle, stage_idx: int, n_stages: int) -> bool:
    """Roll d100 against world_rescue_chance; return True on success.

    Args:
        oracle: A seeded Oracle instance (caller is responsible for seeding).
        stage_idx: Current stage index of the complex line.
        n_stages: Total number of stages in the line.

    Returns:
        True  → world rescue succeeded (emit quest_world_resolved).
        False → no rescue this checkpoint.
    """
    chance = world_rescue_chance(stage_idx, n_stages)
    roll = oracle.d100()
    result = roll <= chance
    log.debug("roll_world_rescue: stage_idx=%d n_stages=%d chance=%d roll=%d → %s",
              stage_idx, n_stages, chance, roll, result)
    return result


# ---------------------------------------------------------------------------
# Summary templates
# ---------------------------------------------------------------------------

def rescue_summary(line: dict) -> str:
    """Template summary for a world-rescue resolution.

    Format: 【世界自行了结】<about>：外力介入，事态平息
    """
    about = line.get("about", "")
    return f"【世界自行了结】{about}：外力介入，事态平息"


def catastrophe_summary(line: dict, region: str) -> str:
    """Template summary for a catastrophe resolution.

    Format: 【终局】<about>失控，<secret>，波及<region>
    """
    about = line.get("about", "")
    secret = line.get("secret", "")
    return f"【终局】{about}失控，{secret}，波及{region}"


# ---------------------------------------------------------------------------
# build_catastrophe_events
# ---------------------------------------------------------------------------

def build_catastrophe_events(
    line: dict,
    world: dict,
    *,
    day: int,
    scene: str,
    turn: int,
    emit_world_change: bool = True,
) -> list[dict]:
    """Build the events for a catastrophe resolution of a complex 暗 line.

    Returns a list of event dicts (NOT appended to any store — caller does that):
      1. quest_catastrophe  — resolves the line (LoreSystem apply sets 了结).
      2. world_change       — anchored at region_scope(anchor) with level=1
                              (only included if emit_world_change=True).

    The world_change shape matches what CascadeSystem.apply expects:
      deltas = {"place": <region_id>, "level": <int>, "summary": <str>}

    No random/time calls — caller provides seeded Oracle if needed for the roll;
    this function only constructs the event dicts deterministically.
    """
    anchor = line.get("anchor")
    region = region_scope(world, anchor, day) if anchor else (anchor or "")

    summary = catastrophe_summary(line, region)
    lid = line.get("id", "")

    # 1. quest_catastrophe event — LoreSystem apply will set state=了结
    cat_ev = kernel_event(
        "quest_catastrophe",
        day=day, scene=scene,
        summary=summary,
        deltas={
            "id": lid,
            "summary": summary,
            "anchor": region,
        },
        turn=turn,
    )

    events: list[dict] = [cat_ev]

    if emit_world_change:
        # 2. world_change — cascade trigger; shape: {place, level, summary}
        wc_ev = kernel_event(
            "world_change",
            day=day, scene=scene,
            summary=summary,
            deltas={
                "place": region,
                "level": 1,
                "summary": summary,
            },
            turn=turn,
        )
        events.append(wc_ev)

    log.debug(
        "build_catastrophe_events: line=%s anchor=%s → region=%s emit_world_change=%s",
        lid, anchor, region, emit_world_change,
    )
    return events
