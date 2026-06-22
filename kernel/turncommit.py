from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnCommit:
    """The structured output of a turn. `narration` is the player-facing prose;
    `sections` maps each owning system's section name to its declaration.
    NOTE: "narration" is reserved and must not be used as a commit section name."""
    narration: str = ""
    sections: dict[str, Any] = field(default_factory=dict)
    reasons: dict[str, Any] = field(default_factory=dict)  # {section: why it's empty this turn}

    @classmethod
    def from_dict(cls, d: dict) -> "TurnCommit":
        d = dict(d)
        narration = d.pop("narration", "")
        # Some models emit narration as an array of paragraphs (or a non-string);
        # coerce to a single display string so storage/display stay consistent.
        if isinstance(narration, list):
            narration = "\n\n".join(str(p) for p in narration)
        elif not isinstance(narration, str):
            narration = str(narration)
        reasons = d.pop("reasons", {})
        if not isinstance(reasons, dict):
            reasons = {}
        return cls(narration=narration, sections=d, reasons=reasons)

    def to_dict(self) -> dict:
        out = {"narration": self.narration, **self.sections}
        if self.reasons:
            out["reasons"] = self.reasons
        return out
