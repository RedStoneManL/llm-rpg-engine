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
from context.capabilities import host_route_discovery_grant
from context.native_authoring import native_authoring_context
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


def build_native_turn_brief(engine, *, goal: str, player_input: str | None = None,
                            host_authorize_routes: tuple[str, ...] = ()) -> dict:
    """Return public player state plus bounded author-only grounding."""
    scene = _build_scene(engine)
    player = build_player_state(engine)
    route_options = [host_route_discovery_grant(engine.world, scene, destination)
                     for destination in host_authorize_routes]
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
        "authoring_context": native_authoring_context(
            engine.world, scene, player_input=player_input,
            host_authorized_routes=route_options,
        ),
        "free_action_goal": goal,
        "turncommit_schema": {
            "canonical": "TurnCommit.to_dict() (top-level narration plus system sections)",
            "shape": {
                "narration": "player-facing prose",
                "moves": [{"who": "protagonist", "to": "one available_exits id, or omit for no movement"}],
                "places": [], "links": [], "cast": [], "facts": [],
                "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "..."}],
            },
            "optional_sections": {
                "entities": [{"id": "new public non-person entity id", "etype": "Object|Place|..."}],
                "relations": [{"src": "new entity id", "rel": "relation", "dst": "existing or new entity id"}],
                "items": [{"op": "transfer", "item": "existing item id", "to": "existing entity/place id"}],
                "knowledge": [{"op": "told", "knower": "protagonist", "fact_key": "subject.predicate", "value": "learned value", "via": "source"}],
                "quests": [{"op": "open|surface|advance|resolve", "id": "quest id", "summary": "..."}],
                "world": [{"areas": ["existing place id"], "level": 1, "summary": "..."}],
            },
            "action_adjudication": {
                "clock": "required exactly once; use advance=false with days=0 and bands=0 for same-scene actions, never [].",
                "travel": "available_exits are the only legal protagonist destinations unless authoring_context.host_authorized_routes explicitly lists a one-turn host route option, or this turn creates a genuinely new public place with a globally fresh id and exactly one direct public link from the action-start location. The latter is new world creation, not discovery: it may not reuse any existing/hidden id, touch another existing place, or add discovery knowledge. Asking an NPC for directions may add only a source-bound reported:<topic> statement and never adds a map edge or a move. route cost is not a time conversion, so do not infer clock bands from it. A listed host route still requires matching discover_place and discover_route knowledge records; the author may not invent authorization or another destination.",
                "information": "Normal authors may create ordinary public scenes, people, and provenance-bearing statements. To persist a new statement, create its public speaker in cast (op=create), or use a currently visible speaker listed in authoring_context.present_people. Record one previously absent public facts [{subject:speaker_id,predicate:'reported:<topic>',value:'what that speaker reports',secrecy:'public'}], and knowledge.told the same fact/value to protagonist with via:speaker_id. It records ‘the speaker said this’, not an overwrite of unresolved existing-world truth. Existing entities may repeat exact supplied speakable_evidence, but may not overwrite an older reported:* key, alter other canonical facts, or write secret/restricted/hidden content.",
                "items": "only use items.transfer when canonical custody changes; temporary prose without an addressable destination leaves holder state unchanged.",
            },
            "forbidden_host_fields": ["trusted_discovery_keys", "host_envelope", "memory_role"],
        },
        "import": {
            "command": "python scripts/import_native_turn.py CAMPAIGN_DIR PROPOSAL.json PLAYER_INPUT",
            "host_behavior": "Importer uses FakeLLMProvider, embedder=None and enforce_player_travel=True. It grants no route by default; any host_authorized_routes option must be repeated by the trusted importer command.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path, help="existing local campaign directory")
    parser.add_argument("--goal", required=True, help="one free player-action goal for the author")
    parser.add_argument("--player-input", default=None,
                        help="optional current player wording; recorded as unresolved intent, not fact")
    parser.add_argument("--host-authorize-route", action="append", default=[], metavar="DESTINATION",
                        help="trusted host only: expose one existing undiscovered adjacent route for this brief")
    parser.add_argument("--output", type=Path, required=True, help="output JSON path")
    args = parser.parse_args(argv)

    # Explicit dependencies keep exporting offline even if ambient environment
    # variables name a real provider or embedder.
    engine = build_engine(args.campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        brief = build_native_turn_brief(
            engine, goal=args.goal, player_input=args.player_input,
            host_authorize_routes=tuple(args.host_authorize_route),
        )
    finally:
        engine.store.close()
    args.output.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
