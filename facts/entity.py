from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Entity:
    id: str
    etype: str                      # Person | Place | Object | Faction | Thread
    tier: str = "mentioned"         # tracked | mentioned | retired
    attrs: dict[str, Any] = field(default_factory=dict)
