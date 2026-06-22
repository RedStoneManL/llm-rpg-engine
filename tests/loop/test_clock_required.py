import os
import tempfile

from kernel.registry import Registry
from kernel.projection import empty_world
from kernel.events import open_store
from loop.turn import run_turn, REQUIRED_SECTIONS
from loop.strategy import AuthorStrategy, _SYSTEM_PROMPT, _SYSTEM_PROMPT_HYBRID
from llm.provider import FakeLLMProvider
from systems.ontology import OntologySystem
from systems.place import PlaceSystem
from systems.character import CharacterSystem
from systems.time import TimeSystem


def test_both_prompts_document_clock():
    assert "clock" in _SYSTEM_PROMPT
    assert "clock" in _SYSTEM_PROMPT_HYBRID


def _registry():
    r = Registry()
    for s in (OntologySystem(), PlaceSystem(), CharacterSystem(), TimeSystem()):
        r.register(s)
    return r


def _store(registry):
    d = tempfile.mkdtemp()
    return open_store(os.path.join(d, "e.db"), os.path.join(d, "e.jsonl"),
                      allowed_types=registry.event_types())


def test_missing_clock_is_repaired_via_gate():
    """A commit lacking `clock` is bounced by the required-sections gate; the
    repair attempt supplies it and the turn proceeds (forcing function)."""
    r = _registry()
    world = empty_world(r)
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "town", "location": "town"}

    # First attempt: every OTHER required section explained via reasons, but NO clock.
    no_clock = {"narration": "原地。",
                "reasons": {"moves": "未移动", "places": "无新地点",
                            "cast": "无人物变化", "facts": "无"}}
    # Repair attempt: now includes a no-advance clock.
    with_clock = {**no_clock,
                  "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "紧接上一刻"}]}

    provider = FakeLLMProvider(json_responses=[no_clock, with_clock])
    store = _store(r)
    try:
        result = run_turn(r, store, world, scene, "观察",
                          strategy=AuthorStrategy(), provider=provider,
                          required_sections=REQUIRED_SECTIONS)
    finally:
        store.close()
    assert result.repair_attempts >= 1
    assert "clock" not in result.dropped_sections
    assert result.world["meta"]["day"] == 1


def test_clock_via_reasons_only_is_safe_no_advance():
    """If the narrator answers every attempt with clock only in reasons (not as an
    array), the turn completes without raising and the clock does NOT advance.

    This documents that the reason-escape path is a safe no-advance, not a crash.
    The clock section is dropped (never a valid array) after max_repairs exhausted,
    and the turn proceeds with the pre-turn day/band unchanged.
    """
    r = _registry()
    world = empty_world(r)
    scene = {"protagonist": "hero", "present": [], "day": 1, "id": "town", "location": "town"}

    # Every attempt returns reasons for all required sections but NO clock array.
    # The gate will bounce it max_repairs times, then drop clock and proceed.
    clock_via_reasons_only = {
        "narration": "原地踌躇。",
        "reasons": {
            "moves": "未移动",
            "places": "无新地点",
            "cast": "无人物变化",
            "facts": "无新事实",
            "clock": "时间未流逝",
        },
    }

    # Provide enough responses to exhaust max_repairs (default 3) + 1 initial
    provider = FakeLLMProvider(json_responses=[clock_via_reasons_only] * 4)
    store = _store(r)
    try:
        result = run_turn(r, store, world, scene, "发呆",
                          strategy=AuthorStrategy(), provider=provider,
                          required_sections=REQUIRED_SECTIONS)
    finally:
        store.close()

    # Turn must complete without raising
    assert result is not None
    # Clock must NOT have advanced (no valid clock array was ever supplied).
    # empty_world starts with meta.day=None and band absent; both must be unchanged.
    day_after = result.world["meta"].get("day")
    band_after = result.world["meta"].get("band", 0)
    assert day_after is None or day_after == 1, (
        f"Clock advanced unexpectedly: day={day_after}"
    )
    assert band_after == 0, f"Band advanced unexpectedly: band={band_after}"
