"""NarrativeSystem — Event-sourced recency-tiered recap.

Owns three harness-authored event types (no commit section; the narrator
never writes to this system directly):

  narration_recorded  — appended by digest_fleet every turn; carries the
                        verbatim prose for that turn keyed by scene.
  scene_summarized    — appended by digest_fleet when a scene ages out of
                        the recent-N window; cheap-model summary.
  recap_recompressed  — appended when the summary count exceeds RECAP_SUMMARY_FANOUT;
                        recursive summary-of-summaries.

World slice: world["systems"]["narrative"] = {
    "scenes": [               # append-order list of scene buckets
        {"scene": str,
         "raw":  [str, ...],  # one entry per turn's narration in that scene
         "summary": str | None},  # filled when the scene ages out
    ],
    "super_summary": str | None,   # recursive summary-of-summaries
    "summarized_through_index": int,  # number of scene buckets summarized so far
}

Constants (tunable — referenced as nmod.RECAP_RAW_SCENES etc.):
  RECAP_RAW_SCENES    = 2   (most recent N scene buckets kept verbatim)
  RECAP_SUMMARY_FANOUT = 6  (when >6 aged summaries, recompress into super_summary)

Rewind-safety: the entire slice folds from events.  /rewind N retracts
events >= turn N and re-projects; summaries of retracted scenes disappear.

inject() returns a SCENE-layer fragment with the recent-N raw narration blocks.
The STABLE-layer summary block (super_summary + aged scene summaries) is emitted
by context/assembler.py's dedicated recap composition step (one system = one
fragment; the assembler handles the dual-layer split).
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem, Fragment
from engine.log import get_logger

log = get_logger("systems.narrative")

# ---------------------------------------------------------------------------
# Module-level constants (tunable)
# ---------------------------------------------------------------------------

RECAP_RAW_SCENES: int = 2    # keep the last N scene buckets verbatim
RECAP_SUMMARY_FANOUT: int = 6  # recompress into super_summary when > N aged summaries


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def aged_out_scene(ns: dict) -> str | None:
    """Return the scene id of the oldest unsummarized bucket beyond the raw window.

    A bucket is "aged out" when its index < len(buckets) - RECAP_RAW_SCENES
    AND its summary is None.  Returns the scene id of the FIRST such bucket
    (oldest unsummarized), or None if all beyond-window buckets are summarized
    or the total count is within the window.
    """
    buckets = ns.get("scenes", [])
    cutoff = len(buckets) - RECAP_RAW_SCENES
    for idx, bucket in enumerate(buckets):
        if idx < cutoff and bucket.get("summary") is None:
            return bucket["scene"]
    return None


# ---------------------------------------------------------------------------
# NarrativeSystem
# ---------------------------------------------------------------------------

class NarrativeSystem(ContextSystem):
    """Event-sourced recency-tiered recap; harness-authored only (no commit section)."""

    name = "narrative"

    def requires(self) -> set[str]:
        return set()

    def event_types(self) -> set[str]:
        return {"narration_recorded", "scene_summarized", "recap_recompressed"}

    def commit_sections(self) -> set[str]:
        return set()   # harness-authored only; narrator never writes to this

    def empty_state(self) -> dict:
        return {
            "scenes": [],
            "super_summary": None,
            "summarized_through_index": 0,
        }

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def apply(self, world: dict, event: dict) -> None:
        """Fold one narrative event into the recap slice."""
        ns = world["systems"][self.name]
        d = event.get("deltas", {})
        t = event["type"]

        if t == "narration_recorded":
            scene = d.get("scene")
            text = d.get("text")
            if not text:
                log.warning("narrative.apply: narration_recorded missing text — skipped")
                return
            buckets = ns["scenes"]
            if buckets and buckets[-1]["scene"] == scene:
                # Append to current scene bucket
                buckets[-1]["raw"].append(text)
            else:
                # New scene → new bucket
                buckets.append({"scene": scene, "raw": [text], "summary": None})
            log.debug("narrative.apply: narration_recorded scene=%s text_len=%d", scene, len(text))

        elif t == "scene_summarized":
            scene = d.get("scene")
            summary = d.get("summary")
            buckets = ns["scenes"]
            # Find the FIRST bucket for this scene whose summary is None and set it
            for bucket in buckets:
                if bucket["scene"] == scene and bucket.get("summary") is None:
                    bucket["summary"] = summary
                    log.debug("narrative.apply: scene_summarized scene=%s", scene)
                    return
            log.debug("narrative.apply: scene_summarized scene=%s — no unsummarized bucket found", scene)

        elif t == "recap_recompressed":
            if "super_summary" in d:
                ns["super_summary"] = d["super_summary"]
            idx = d.get("summarized_through_index")
            if idx is not None:
                ns["summarized_through_index"] = idx
            log.debug("narrative.apply: recap_recompressed super_summary set, through_index=%s", idx)

    # ------------------------------------------------------------------
    # Inject: force-render recent-N raw narration (scene layer)
    # ------------------------------------------------------------------

    def inject(self, scene: dict, world: dict) -> "Fragment | None":
        """Force-render the recent-N verbatim narration blocks into the scene layer.

        The stable-layer summary block (aged summaries + super_summary) is rendered
        by assemble_context directly from world["systems"]["narrative"] so that
        one system contributes one inject fragment (scene) while the stable block
        comes from the assembler's dedicated recap composition step.

        Returns None when no narration has been recorded yet.
        """
        ns = (world.get("systems", {}).get(self.name) or {})
        buckets = ns.get("scenes", [])
        if not buckets:
            return None

        recent = buckets[-RECAP_RAW_SCENES:]
        lines = ["【最近剧情·原文】（延续性，每回合必看）"]
        for bucket in recent:
            raw_texts = bucket.get("raw", [])
            if raw_texts:
                lines.append(f"〔{bucket['scene']}〕" + "".join(raw_texts))

        if len(lines) == 1:
            # Header only — no actual raw text (all recent buckets empty)
            return None

        text = "\n".join(lines)
        return Fragment(
            system="narrative",
            layer="scene",
            text=text,
            affordance="",
        )
