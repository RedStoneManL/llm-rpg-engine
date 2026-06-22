from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.log import get_logger

log = get_logger("kernel.contextsystem")


@dataclass
class ValidationError:
    section: str          # turn-commit section the error is in
    field: str            # dotted path within the section, e.g. "[0].who"
    code: str             # "missing" | "dangling_ref" | "bad_enum" | "unknown_section" | ...
    hint: str             # preset, LLM-facing repair instruction


@dataclass
class Fragment:
    """A system's contribution to the assembled context."""
    system: str
    layer: str            # "stable" | "scene" | "volatile"
    text: str             # rendered context text
    affordance: str = ""  # "what you can declare this turn"


@dataclass
class RecallHit:
    system: str
    score: float
    text: str
    ref: dict = field(default_factory=dict)


class ContextSystem:
    """Base contract for a pluggable system. Subclasses override only what they need;
    every hook has an inert default so the kernel can call it unconditionally."""

    name: str = "unnamed"

    # --- ownership declarations -------------------------------------------
    def event_types(self) -> set[str]:
        return set()

    def commit_sections(self) -> set[str]:
        return set()

    def requires(self) -> set[str]:
        """Return the set of system names that must be registered before this one."""
        return set()

    # --- projection -------------------------------------------------------
    def empty_state(self) -> Any:
        return {}

    def apply(self, world: dict, event: dict) -> None:
        """Fold one owned event into the world. The system's own slice is
        world["systems"][self.name]; the shared fact-graph (if present) is
        world["systems"]["ontology"]. Mutate in place. Must be total over
        already-validated events."""

    # --- write path (turn-commit -> events) -------------------------------
    def validate(self, section: str, decl: Any, world: dict) -> list[ValidationError]:
        return []

    def to_events(self, section: str, decl: Any, *, turn: int, day: int, scene: str) -> list[dict]:
        return []

    def created_ids(self, section: str, decl: Any) -> set[str]:
        """Ids that THIS section introduces, so same-commit cross-references
        resolve during validation (e.g. a move to a place created in the same
        commit). Default: each item's 'id'. Override when 'id' is a reference
        (place materialize) or creation is conditional (cast op)."""
        out: set[str] = set()
        if isinstance(decl, list):
            for item in decl:
                if isinstance(item, dict) and item.get("id"):
                    out.add(item["id"])
        return out

    # --- read path --------------------------------------------------------
    def inject(self, scene: dict, world: dict) -> Fragment | None:
        return None

    def recall(self, query: str, world: dict) -> list[RecallHit]:
        return []

    # --- digest (strategy 乙) --------------------------------------------
    def digest_extract(self, prose: str, world: dict) -> dict:
        """Return {section_name: decl} extracted from narration prose."""
        return {}
