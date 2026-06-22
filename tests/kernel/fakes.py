from __future__ import annotations

from kernel.contextsystem import ContextSystem, ValidationError, Fragment, RecallHit
from kernel.events import kernel_event


class FakeNoteSystem(ContextSystem):
    """A toy system owning a 'notes' section / 'note_added' event. Each note is
    a {'text': str}. Used to drive kernel tests with no game logic."""

    name = "notes"

    def event_types(self): return {"note_added"}
    def commit_sections(self): return {"notes"}
    def empty_state(self): return {"notes": []}

    def apply(self, world, event):
        world["systems"][self.name]["notes"].append(event["summary"])

    def validate(self, section, decl, world):
        errs = []
        for i, n in enumerate(decl or []):
            if not (isinstance(n, dict) and str(n.get("text", "")).strip()):
                errs.append(ValidationError("notes", f"[{i}].text", "missing",
                                            "每条 note 需要非空 text"))
        return errs

    def to_events(self, section, decl, *, turn, day, scene):
        return [kernel_event("note_added", day=day, scene=scene, summary=n["text"], turn=turn)
                for n in (decl or [])]

    def inject(self, scene, world):
        notes = world.get("systems", {}).get("notes", {}).get("notes", [])
        return Fragment("notes", "scene", "Notes: " + "; ".join(notes),
                        affordance="notes:[{text}] — 记一条便签")

    def recall(self, query, world):
        notes = world.get("systems", {}).get("notes", {}).get("notes", [])
        return [RecallHit("notes", 1.0, n) for n in notes if query in n]

    def digest_extract(self, prose, world):
        return {"notes": [{"text": prose[:24]}]} if prose.strip() else {}
