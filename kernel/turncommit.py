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
    # Host-only signal; model from_dict/to_dict never read or emit this field.
    narration_rewrite_required: bool = field(default=False, init=False, repr=False, compare=False)

    semantic_audit_required: bool = field(default=False, init=False, repr=False, compare=False)
    _semantic_context: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _semantic_approval: Any = field(default=None, init=False, repr=False, compare=False)
    semantic_audit_log: list = field(default_factory=list, init=False, repr=False, compare=False)
    _comparison_preparation: Any = field(default=None, init=False, repr=False, compare=False)

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
        # Drop any legacy top-level `reasons` map (the pre-I1 anti-laziness escape):
        # it no longer drives validation, so discard it here rather than let it be
        # mistaken for a commit section.
        d.pop("reasons", None)
        return cls(narration=narration, sections=d)

    def to_dict(self) -> dict:
        return {"narration": self.narration, **self.sections}
