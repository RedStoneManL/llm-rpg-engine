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
         "source_event_ids": [str, ...],  # same ordering as raw
         "summary": str | None,  # filled when the scene ages out
         # All summaries retain summary_event_id; only bound ones have summary_source.
        },
    ],
    "super_summary": str | None,   # recursive summary-of-summaries
    "summarized_through_index": int,  # number of scene buckets summarized so far
    # After a recap: super_summary_event_id; new recaps also retain
    # super_summary_source (ordered summary references + previous recap link).
}

Constants (tunable — referenced as nmod.RECAP_RAW_SCENES etc.):
  RECAP_RAW_SCENES    = 2   (most recent N scene buckets kept verbatim)
  RECAP_SUMMARY_FANOUT = 6  (when >6 aged summaries, recompress into super_summary)

Rewind-safety: the entire slice folds from events.  /rewind N retracts
events >= turn N and re-projects; summaries of retracted scenes disappear.

inject() returns a SCENE-layer fragment with recent-N raw narration blocks and
older blocks still awaiting a saved summary. Failed compression never drops text.
The STABLE-layer summary block (super_summary + aged scene summaries) is emitted
by context/assembler.py's dedicated recap composition step (one system = one
fragment; the assembler handles the dual-layer split).
"""
from __future__ import annotations

import hashlib
import json

from kernel.contextsystem import ContextSystem, Fragment
from engine.log import get_logger

log = get_logger("systems.narrative")

# ---------------------------------------------------------------------------
# Module-level constants (tunable)
# ---------------------------------------------------------------------------

RECAP_RAW_SCENES: int = 2    # keep the last N scene buckets verbatim
RECAP_TURNS_PER_BUCKET: int = 6  # even a long conversation must eventually age out
RECAP_SUMMARY_FANOUT: int = 6  # recompress into super_summary when > N aged summaries


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def summary_source_ref(bucket: dict) -> dict:
    """Host-owned identity of the exact narration texts given to a summarizer.

    This is provenance, not proof that the summary is semantically faithful.
    Event IDs survive reopen and distinguish revisits/rewritten turn numbers;
    the digest also detects changed text under an unchanged ID. No seq fields
    are used, so staging and durable replay compute the same reference.
    """
    ids, raw = bucket.get('source_event_ids'), bucket.get('raw')
    if (not isinstance(ids, list) or not ids or not isinstance(raw, list)
            or len(ids) != len(raw)
            or any(not isinstance(value, str) or not value for value in ids)
            or len(set(ids)) != len(ids)
            or any(not isinstance(text, str) for text in raw)):
        raise ValueError('invalid summary source')
    payload = json.dumps(list(zip(ids, raw)), ensure_ascii=False, separators=(',', ':'))
    return {'version': 1, 'bucket_id': ids[0], 'event_ids': list(ids),
            'sha256': hashlib.sha256(payload.encode()).hexdigest()}


def _source_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def _source_hash(value) -> str:
    return hashlib.sha256(_source_json(value).encode()).hexdigest()


def recap_source_ref(ns: dict, through_index: int) -> dict:
    """Freeze the exact inputs for one recursive recap, before generation.

    Coverage advances only from the current frontier through a nonempty,
    contiguous sequence of saved summaries. References bind summary IDs/text
    and their narration provenance, plus the previous recap's ID/text/source.
    No seq or tunable compression threshold participates in durable identity.

    ``bound`` describes the immediate link, not semantic correctness or a
    fully verified ancestry: a bound recap may depend on legacy_unbound inputs.
    Legacy provenance is never inferred from the current bucket's raw texts.
    """
    buckets = ns.get('scenes', [])
    start = ns.get('summarized_through_index', 0)
    if (type(start) is not int or type(through_index) is not int
            or not 0 <= start < through_index <= len(buckets)):
        raise ValueError('invalid recap source coverage')

    previous = None
    previous_text = ns.get('super_summary')
    if previous_text is not None or start != 0:
        previous_id = ns.get('super_summary_event_id')
        if (not isinstance(previous_text, str) or not previous_text.strip()
                or not isinstance(previous_id, str) or not previous_id):
            raise ValueError('invalid recap source predecessor')
        previous = {
            'event_id': previous_id,
            'source_status': 'bound' if 'super_summary_source' in ns else 'legacy_unbound',
            'sha256': _source_hash([previous_text, ns.get('super_summary_source')]),
        }

    summaries = []
    for bucket in buckets[start:through_index]:
        ids = bucket.get('source_event_ids') or []
        summary_id, text = bucket.get('summary_event_id'), bucket.get('summary')
        if (not ids or not isinstance(ids[0], str) or not ids[0]
                or not isinstance(summary_id, str) or not summary_id
                or not isinstance(text, str) or not text.strip()):
            raise ValueError('invalid recap source summary')
        summaries.append({
            'bucket_id': ids[0], 'event_id': summary_id,
            'source_status': 'bound' if 'summary_source' in bucket else 'legacy_unbound',
            'sha256': _source_hash([bucket['scene'], text, bucket.get('summary_source')]),
        })
    return {'version': 1, 'from_index': start, 'through_index': through_index,
            'previous': previous, 'summaries': summaries}


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
        return {"narration_recorded", "scene_summarized", "recap_recompressed", "variation_sampled"}

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

        if t == 'variation_sampled':
            ns['variations'] = (ns.get('variations', []) + [dict(d)])[-6:]
            return

        if t == "narration_recorded":
            scene = d.get("scene")
            text = d.get("text")
            if not text:
                log.warning("narrative.apply: narration_recorded missing text — skipped")
                return
            buckets = ns["scenes"]
            if (buckets and buckets[-1]["scene"] == scene
                    and 'summary_source' not in buckets[-1]
                    and len(buckets[-1]['raw']) < RECAP_TURNS_PER_BUCKET):
                # Append to current scene bucket
                buckets[-1]["raw"].append(text)
                buckets[-1]['source_event_ids'].append(event.get('id'))
            else:
                # New scene → new bucket
                buckets.append({"scene": scene, "raw": [text], "summary": None,
                                'source_event_ids': [event.get('id')]})
            log.debug("narrative.apply: narration_recorded scene=%s text_len=%d", scene, len(text))

        elif t == "scene_summarized":
            scene = d.get("scene")
            summary = d.get("summary")
            buckets = ns["scenes"]
            if 'source' in d:
                source = d['source']
                if (not isinstance(source, dict) or type(source.get('version')) is not int
                        or source.get('version') != 1):
                    raise ValueError('invalid summary source')
                bucket = next((b for b in buckets
                               if b['source_event_ids'][0] == source.get('bucket_id')), None)
                if (bucket is None or bucket['scene'] != scene
                        or bucket.get('summary') is not None
                        or source != summary_source_ref(bucket)
                        or not isinstance(summary, str) or not summary.strip()):
                    raise ValueError('invalid summary source')
                bucket['summary'] = summary
                bucket['summary_source'] = summary_source_ref(bucket)
                bucket['summary_event_id'] = event.get('id')
                return
            # Compatibility only: do not rewrite old records or infer that an
            # unbound historical summary has verified source coverage.
            # Find the FIRST bucket for this scene whose summary is None and set it
            for bucket in buckets:
                if bucket["scene"] == scene and bucket.get("summary") is None:
                    bucket["summary"] = summary
                    bucket['summary_event_id'] = event.get('id')
                    log.debug("narrative.apply: scene_summarized scene=%s", scene)
                    return
            log.debug("narrative.apply: scene_summarized scene=%s — no unsummarized bucket found", scene)

        elif t == "recap_recompressed":
            if 'source' in d:
                source = d['source']
                text = d.get('super_summary')
                if (not isinstance(source, dict) or type(source.get('version')) is not int
                        or source.get('version') != 1
                        or not isinstance(text, str) or not text.strip()):
                    raise ValueError('invalid recap source')
                expected = recap_source_ref(ns, d.get('summarized_through_index'))
                # JSON equality is type-strict (True/1/1.0 must not alias).
                if _source_json(source) != _source_json(expected):
                    raise ValueError('invalid recap source')
                ns['super_summary_source'] = expected
            else:
                # Replay old index-only events unchanged, without inheriting
                # a verified descriptor from an earlier bound recap.
                ns.pop('super_summary_source', None)
            if "super_summary" in d:
                ns["super_summary"] = d["super_summary"]
            if 'super_summary' in d and d.get('summarized_through_index') is not None:
                ns['super_summary_event_id'] = event.get('id')
            else:
                # A legacy partial update has no single event containing both
                # inputs. Preserve replay, but do not fabricate a predecessor.
                ns.pop('super_summary_event_id', None)
            idx = d.get("summarized_through_index")
            if idx is not None:
                ns["summarized_through_index"] = idx
            log.debug("narrative.apply: recap_recompressed super_summary set, through_index=%s", idx)

    # ------------------------------------------------------------------
    # Inject: force-render recent-N raw narration (scene layer)
    # ------------------------------------------------------------------

    def inject(self, scene: dict, world: dict) -> "Fragment | None":
        """Render recent narration and any older, not-yet-compressed backlog.

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

        cutoff = max(0, len(buckets) - RECAP_RAW_SCENES)
        through = ns.get("summarized_through_index", 0)
        pending = [bucket for idx, bucket in enumerate(buckets[:cutoff])
                   if idx >= through and not bucket.get("summary")]
        lines = []
        # Age alone is not evidence that compression succeeded. Keep the
        # public source text until a durable summary (or super-summary) covers
        # it. This is a read projection: no retry, synthetic summary or write.
        # A prolonged recap outage may grow this backlog; silently truncating
        # it would erase the only available representation of old promises.
        if any(bucket.get("raw") for bucket in pending):
            lines.append("【待摘要剧情·原文】（摘要尚未保存，保留此前经过）")
            for bucket in pending:
                if bucket.get("raw"):
                    lines.append(f"〔{bucket['scene']}〕" + "\n".join(bucket["raw"]))

        recent = buckets[cutoff:]
        lines.append("【最近剧情·原文】（延续性，每回合必看）")
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
