"""Export one safe, offline native-author turn brief from an existing campaign.

The export is intentionally a player-POV document, not a serialised world or
EventStore dump.  It is suitable for a human/native author to draft a single
``TurnCommit`` which the separate importer will validate and publish.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.engine import build_engine
from app.gui import build_player_state
from app.play import _build_scene
from context.access import pov_world
from llm.provider import FakeLLMProvider


def _public_people(engine, scene: dict) -> list[dict[str, str]]:
    """Select only visible people and their observable public sketch."""
    view = pov_world(engine.world, scene, pov=scene.get("protagonist"), redact_facts=True)
    graph = view.get("systems", {}).get("ontology")
    day = scene.get("day") or 1
    protagonist = scene.get("protagonist")
    if graph is None:
        return []
    people = []
    for person_id, entity in sorted(graph.entities.items()):
        if entity.etype != "Person" or person_id == protagonist:
            continue
        sketch = graph.value_at(person_id, "sketch", day)
        people.append({"id": str(person_id), "public_background": str(sketch or person_id)})
    return people


def _public_narrative(engine) -> list[str]:
    """Use the public-history boundary, then expose only active narration text."""
    lines = []
    for event in engine.public_history():
        if event.get("retracted") or event.get("type") != "narration_recorded":
            continue
        text = (event.get("deltas") or {}).get("text")
        if isinstance(text, str):
            lines.append(text)
    return lines[-8:]


def build_native_turn_brief(engine, *, goal: str) -> dict:
    """Return the narrow, actual-player-context brief without host authority."""
    scene = _build_scene(engine)
    player = build_player_state(engine)
    protagonist_facts = [
        {"name": item["name"], "value": item["value"]}
        for item in player["character"]["facts"]
        if item["name"] in {"sketch", "goal", "name", "mood", "state"}
    ]
    return {
        "player_context": {
            "current_location": player["scene"]["location"],
            "protagonist": {"id": player["character"]["id"], "public_background": protagonist_facts},
            "known_people": _public_people(engine, scene),
            "known_items": player["inventory"],
            "known_events": _public_narrative(engine),
            "public_narrative": player["narration"],
            "available_exits": player["scene"]["exits"],
            "map": player["map"],
        },
        "free_action_goal": goal,
        "turncommit_schema": {
            "canonical": "TurnCommit.to_dict() (top-level narration plus system sections)",
            "shape": {
                "narration": "player-facing prose",
                "moves": [{"who": "protagonist", "to": "one available_exits id, or omit for no movement"}],
                "places": [], "links": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "..."}],
            },
            "forbidden_host_fields": ["trusted_discovery_keys", "host_envelope", "memory_role"],
        },
        "import": {
            "command": "python scripts/import_native_turn.py CAMPAIGN_DIR PROPOSAL.json PLAYER_INPUT",
            "host_behavior": "Importer uses FakeLLMProvider, embedder=None, enforce_player_travel=True, and no trusted discovery grants.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path, help="existing local campaign directory")
    parser.add_argument("--goal", required=True, help="one free player-action goal for the author")
    parser.add_argument("--output", type=Path, required=True, help="output JSON path")
    args = parser.parse_args(argv)

    # Explicit dependencies keep exporting offline even if ambient environment
    # variables name a real provider or embedder.
    engine = build_engine(args.campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        brief = build_native_turn_brief(engine, goal=args.goal)
    finally:
        engine.store.close()
    args.output.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
