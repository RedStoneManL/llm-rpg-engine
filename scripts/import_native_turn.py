"""Import one local native-author TurnCommit JSON without external providers.

The canonical input is :meth:`TurnCommit.to_dict`: ``narration`` plus named
system sections at the top level.  The old ``{"narration", "sections"}``
wrapper remains readable only so retained review material does not rot.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# A direct ``python scripts/import_native_turn.py`` puts ``scripts/`` rather
# than the repository root on sys.path.  Keep this a local executable without
# requiring PYTHONPATH or any package installation.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.engine import build_engine
from app.play import _build_scene
from context.capabilities import host_route_discovery_grant
from context.native_authoring import NativeAuthoringPolicyError, native_authoring_policy_errors
from kernel.turncommit import TurnCommit
from llm.provider import FakeLLMProvider
from loop.turn import REQUIRED_SECTIONS, run_turn

_HOST_ONLY_FIELDS = frozenset({"trusted_discovery_keys", "host_envelope", "memory_role"})


def parse_native_commit(payload: object) -> TurnCommit:
    """Read a local author proposal without accepting host authority fields."""
    if not isinstance(payload, dict):
        raise ValueError("native proposal must be a JSON object")
    if any(field in payload for field in _HOST_ONLY_FIELDS):
        raise ValueError("native proposal may not set host-only authority fields")
    # Retain compatibility with the pre-schema-review review artifact.  Its
    # inner mapping is converted into exactly the public TurnCommit schema.
    if "sections" in payload:
        if set(payload) != {"narration", "sections"} or not isinstance(payload["sections"], dict):
            raise ValueError("legacy native wrapper must contain only narration and object sections")
        payload = {"narration": payload["narration"], **payload["sections"]}
    return TurnCommit.from_dict(payload)


class ImportedAuthor:
    """A one-shot local strategy; it does not call a model or repair provider."""
    def __init__(self, commit: TurnCommit):
        self.commit = commit

    def produce(self, *args, **kwargs):
        return self.commit

    def repair_sections(self, *args, **kwargs):
        return {}

    def commit_to_thread(self, narration):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("proposal", type=Path)
    parser.add_argument("player_input")
    parser.add_argument("--host-authorize-route", action="append", default=[], metavar="DESTINATION",
                        help="trusted host only: grant one existing undiscovered adjacent route for this import")
    args = parser.parse_args(argv)
    campaign = args.campaign
    proposal_path = args.proposal
    player_input = args.player_input
    payload = json.loads(proposal_path.read_text(encoding="utf-8"))
    commit = parse_native_commit(payload)

    # All three choices below are intentional safety boundaries: no external
    # provider, no environment-selected embedder, and no host discovery grants.
    engine = build_engine(campaign, provider=FakeLLMProvider(), embedder=None)
    try:
        scene = _build_scene(engine)
        route_options = [host_route_discovery_grant(engine.world, scene, destination)
                         for destination in args.host_authorize_route]
        trusted_discovery_keys = frozenset(
            key for option in route_options
            for key in (option["place_discovery_key"], option["route_discovery_key"])
        )
        policy_errors = native_authoring_policy_errors(
            engine.world, scene, commit,
            trusted_discovery_keys=trusted_discovery_keys,
        )
        if policy_errors:
            raise NativeAuthoringPolicyError(
                "native authoring policy rejected proposal: " + "; ".join(policy_errors)
            )
        result = run_turn(
            engine.registry, engine.store, engine.world, scene, player_input,
            strategy=ImportedAuthor(commit), provider=engine.provider,
            required_sections=REQUIRED_SECTIONS, enforce_player_travel=True,
            trusted_discovery_keys=trusted_discovery_keys,
        )
        print(json.dumps({"narration": result.narration, "revision": result.receipt["revision"]}, ensure_ascii=False))
    finally:
        engine.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
