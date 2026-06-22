"""rpg-trace viewer — python -m app.trace <file> [flags]

Agent-friendly CLI over a trace JSONL produced by DebugTracer.

Default (no action flag): compact index, one terse line per record.
Action flags: --show SEQ, --grep RE, --tree, --stats, --json.
Filters: --turn N, --phase NAME, --type TYPE.

All core logic is in pure functions for easy unit testing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import IO, Dict, List, Optional


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load(path: str) -> List[dict]:
    """Read a JSONL file, skipping blank and malformed lines.

    Returns an empty list if the file is missing or unreadable.
    """
    records: List[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except (OSError, IOError):
        pass
    return records


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _phase_of(rec: dict) -> Optional[str]:
    """Return the path segment immediately after the top-level prefix.

    Examples:
      "turn:3▸cascade▸llm" → "cascade"
      "genesis▸gen_frame"  → "gen_frame"
      "turn:3"             → None  (top-level, no sub-phase)
      "genesis"            → None
    """
    path: str = rec.get("path", "") or ""
    parts = path.split("▸")
    # parts[0] is the top-level (turn:N or genesis or something else)
    if len(parts) >= 2:
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _dur_str(dur_ms) -> str:
    """Convert dur_ms to human string: e.g. 8100 → '8.1s', 200 → '200ms'."""
    if dur_ms is None:
        return ""
    try:
        ms = float(dur_ms)
    except (TypeError, ValueError):
        return ""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{int(ms)}ms"


def _tok_str(rec: dict) -> str:
    """Extract output token count from usage field."""
    usage = rec.get("usage")
    if not usage:
        return ""
    out = usage.get("output")
    if out is None:
        return ""
    return str(out)


def _summary(rec: dict, max_len: int = 80) -> str:
    """One-line terse summary of a record's content, truncated to max_len."""
    rtype = rec.get("type", "")
    text = ""
    if rtype == "gen":
        text = rec.get("output") or ""
        if not text:
            # Fall back to first input content snippet
            inp = rec.get("input") or []
            if inp and isinstance(inp, list):
                first = inp[0]
                if isinstance(first, dict):
                    text = first.get("content", "")
    elif rtype == "event":
        attrs = rec.get("attrs") or {}
        text = str(attrs.get("text", "") or attrs.get("msg", "") or rec.get("name", ""))
    else:
        # span_start / span_end
        text = rec.get("name", "")
    if len(text) > max_len:
        text = text[:max_len - 1] + "…"
    return text


def index_line(rec: dict) -> str:
    """Format one terse index line for a record.

    Format: seq  path  type  dur  tok  summary
    """
    seq = rec.get("seq", "?")
    path = rec.get("path", "")
    rtype = rec.get("type", "")
    dur = _dur_str(rec.get("dur_ms"))
    tok = _tok_str(rec)
    summary = _summary(rec, max_len=80)

    return f"{seq:>5}  {path:<34.34}  {rtype:<10}  {dur:<8}  {tok:<6}  {summary}"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def apply_filters(
    records: List[dict],
    turn: Optional[int] = None,
    phase: Optional[str] = None,
    type_filter: Optional[str] = None,
) -> List[dict]:
    """Apply --turn, --phase, --type filters and return matching records."""
    result = records

    if turn is not None:
        prefix = f"turn:{turn}"
        result = [r for r in result if (r.get("path") or "").startswith(prefix)]

    if phase is not None:
        def _phase_matches(rec: dict, wanted: str) -> bool:
            seg = _phase_of(rec)
            if seg is None:
                return False
            # Exact match first (e.g. --phase cascade:3), then base-name match
            # so --phase cascade matches cascade:3, cascade:5, etc.
            return seg == wanted or seg.split(":", 1)[0] == wanted
        result = [r for r in result if _phase_matches(r, phase)]

    if type_filter is not None:
        if type_filter == "span":
            result = [r for r in result if r.get("type") in ("span_start", "span_end")]
        else:
            result = [r for r in result if r.get("type") == type_filter]

    return result


def apply_grep(records: List[dict], pattern: str) -> List[dict]:
    """Return records whose serialised input+output matches the regex."""
    try:
        rx = re.compile(pattern)
    except re.error:
        return []
    matched = []
    for rec in records:
        inp = rec.get("input")
        out = rec.get("output") or ""
        blob = json.dumps(inp, ensure_ascii=False) if inp is not None else ""
        blob += out
        if rx.search(blob):
            matched.append(rec)
    return matched


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_index(records: List[dict], out: IO[str] = sys.stdout) -> None:
    """Print the compact index, one line per record."""
    for rec in records:
        print(index_line(rec), file=out)


def show_record(rec: dict, out: IO[str] = sys.stdout) -> None:
    """Print full details of a single record (drill-down)."""
    print(f"seq:  {rec.get('seq')}", file=out)
    print(f"path: {rec.get('path')}", file=out)
    print(f"type: {rec.get('type')}", file=out)
    attrs = rec.get("attrs")
    if attrs:
        print(f"attrs: {json.dumps(attrs, ensure_ascii=False)}", file=out)
    dur = rec.get("dur_ms")
    if dur is not None:
        print(f"dur:  {_dur_str(dur)} ({dur} ms)", file=out)
    usage = rec.get("usage")
    if usage:
        print(f"usage: {json.dumps(usage, ensure_ascii=False)}", file=out)
    inp = rec.get("input")
    if inp is not None:
        print("--- input ---", file=out)
        if isinstance(inp, list):
            for msg in inp:
                role = msg.get("role", "?") if isinstance(msg, dict) else "?"
                content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
                print(f"[{role}] {content}", file=out)
        else:
            print(str(inp), file=out)
    output = rec.get("output")
    if output is not None:
        print("--- output ---", file=out)
        print(str(output), file=out)


def render_tree(records: List[dict], out: IO[str] = sys.stdout) -> None:
    """Print an indented span tree, depth = number of ▸ in path."""
    sorted_recs = sorted(records, key=lambda r: r.get("seq", 0))
    for rec in sorted_recs:
        path = rec.get("path", "")
        depth = path.count("▸")
        indent = "  " * depth
        seq = rec.get("seq", "?")
        name = rec.get("name", "")
        summary = _summary(rec, max_len=60)
        print(f"{indent}{seq} {name} {summary}", file=out)


def compute_stats(records: List[dict]) -> Dict[str, dict]:
    """Aggregate per-phase stats: gens, dur_ms, tokens.

    Only gen and span_end records contribute dur_ms (to avoid double-counting
    with span_start which has no duration).
    """
    stats: Dict[str, dict] = {}

    def _ensure(phase: str):
        if phase not in stats:
            stats[phase] = {"gens": 0, "dur_ms": 0, "tokens": 0}

    for rec in records:
        phase = _phase_of(rec)
        if phase is None:
            phase = rec.get("path", "") or "(top)"
        # Normalise to base name so cascade:1, cascade:2 → cascade in the table
        phase = phase.split(":", 1)[0] if phase else phase
        _ensure(phase)
        rtype = rec.get("type", "")
        dur = rec.get("dur_ms")
        if rtype == "gen":
            stats[phase]["gens"] += 1
            if dur is not None:
                stats[phase]["dur_ms"] += int(dur)
            usage = rec.get("usage") or {}
            tok = usage.get("output")
            if tok is not None:
                stats[phase]["tokens"] += int(tok)
        elif rtype == "span_end":
            if dur is not None:
                stats[phase]["dur_ms"] += int(dur)

    return stats


def render_stats(records: List[dict], out: IO[str] = sys.stdout) -> None:
    """Print per-phase stats table."""
    stats = compute_stats(records)
    print(f"{'phase':<24}  {'gens':>5}  {'dur_ms':>8}  {'tokens':>7}", file=out)
    print("-" * 52, file=out)
    for phase, s in sorted(stats.items()):
        print(f"{phase:<24}  {s['gens']:>5}  {s['dur_ms']:>8}  {s['tokens']:>7}", file=out)


def render_json(records: List[dict], out: IO[str] = sys.stdout) -> None:
    """Print filtered records as JSON lines."""
    for rec in records:
        print(json.dumps(rec, ensure_ascii=False), file=out)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m app.trace",
        description="Agent-friendly viewer for RPG debug trace JSONL files.",
    )
    parser.add_argument("file", help="Path to the trace JSONL file")

    # Filters
    parser.add_argument("--turn", type=int, default=None,
                        help="Only records whose path starts with turn:N")
    parser.add_argument("--phase", default=None,
                        help="Only records with this phase (segment after turn:N or genesis)")
    parser.add_argument("--type", dest="type_filter", default=None,
                        choices=["gen", "event", "span_start", "span_end", "span"],
                        help="Filter by record type ('span' matches span_start + span_end)")

    # Actions (mutually exclusive display modes)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", type=int, default=None, metavar="SEQ",
                       help="Drill down: print full content of a single record by seq")
    group.add_argument("--grep", default=None, metavar="REGEX",
                       help="Index lines whose input+output match the regex")
    group.add_argument("--tree", action="store_true",
                       help="Print indented span tree")
    group.add_argument("--stats", action="store_true",
                       help="Print per-phase aggregate stats")
    group.add_argument("--json", dest="as_json", action="store_true",
                       help="Print filtered records as JSON lines")

    args = parser.parse_args(argv)

    records = load(args.file)

    # --show bypasses normal filters (look up by seq globally)
    if args.show is not None:
        rec = next((r for r in records if r.get("seq") == args.show), None)
        if rec is None:
            print(f"seq {args.show} not found in {args.file}", file=sys.stderr)
            sys.exit(1)
        show_record(rec)
        return

    # Apply filters
    filtered = apply_filters(
        records,
        turn=args.turn,
        phase=args.phase,
        type_filter=args.type_filter,
    )

    # Apply grep if requested
    if args.grep is not None:
        filtered = apply_grep(filtered, args.grep)

    # Dispatch to renderer
    if args.tree:
        render_tree(filtered)
    elif args.stats:
        render_stats(filtered)
    elif args.as_json:
        render_json(filtered)
    else:
        render_index(filtered)


if __name__ == "__main__":
    main()
