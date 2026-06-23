"""Load a player-authored genesis blueprint file into a normalized GenesisSpec.

.json -> stdlib json; .yaml/.yml -> pyyaml if installed. The file may specify any
subset of any spec part; absent parts are model-filled at bootstrap.
"""
from __future__ import annotations

import json
from pathlib import Path

from loop.genesis_spec import normalize


class BlueprintError(Exception):
    """Raised when a blueprint file cannot be read or parsed."""


def load_blueprint(path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise BlueprintError(f"genesis blueprint not found: {path}")
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as e:
                raise BlueprintError(
                    f"{path} is YAML but pyyaml is not installed; "
                    f"use a .json blueprint or `pip install pyyaml`"
                ) from e
            raw = yaml.safe_load(text)
        else:
            raw = json.loads(text)
    except BlueprintError:
        raise
    except Exception as e:
        raise BlueprintError(f"failed to parse {path}: {e}") from e

    if not isinstance(raw, dict):
        raise BlueprintError(
            f"{path}: top-level must be an object/mapping, got {type(raw).__name__}")
    return normalize(raw)
