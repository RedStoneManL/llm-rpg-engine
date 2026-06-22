"""Tests for app/trace.py — the rpg-trace viewer.

TDD: tests are written first against the expected contract.
Pure-function imports are preferred for speed; at least one subprocess invocation
covers the entry-point to satisfy the end-to-end requirement.
"""

import json
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write(path, recs):
    """Write a list of dicts as JSONL to path."""
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )


def _run(path, *args, env=None):
    """Invoke python -m app.trace via subprocess (end-to-end)."""
    import os
    e = dict(os.environ)
    e["PYTHONPATH"] = "/root/rpg-engine-app"
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "app.trace", str(path), *args],
        capture_output=True,
        text=True,
        cwd="/root/rpg-engine-app",
        env=e,
    )


FIXTURE_RECS = [
    # seq 1 — span_start for turn:3
    {
        "run": "r", "seq": 1, "ts": 1000.0,
        "type": "span_start", "name": "turn", "path": "turn:3",
        "parent_seq": None, "attrs": {"turn": 3},
    },
    # seq 2 — span_start for cascade inside turn:3
    {
        "run": "r", "seq": 2, "ts": 1000.1,
        "type": "span_start", "name": "cascade", "path": "turn:3▸cascade",
        "parent_seq": 1, "attrs": None,
    },
    # seq 3 — gen inside cascade
    {
        "run": "r", "seq": 3, "ts": 1000.2,
        "type": "gen", "name": "llm", "path": "turn:3▸cascade▸llm",
        "parent_seq": 2,
        "input": [{"role": "user", "content": "AAA long prompt here that is detailed"}],
        "output": "BBBBB response text",
        "usage": {"input": 10, "output": 5},
        "dur_ms": 8100,
        "attrs": {"model": "glm", "max_tokens": 512},
    },
    # seq 4 — event inside cascade
    {
        "run": "r", "seq": 4, "ts": 1000.3,
        "type": "event", "name": "note", "path": "turn:3▸cascade▸note",
        "parent_seq": 2, "attrs": {"text": "something interesting"},
    },
    # seq 5 — span_end for cascade
    {
        "run": "r", "seq": 5, "ts": 1001.0,
        "type": "span_end", "name": "cascade", "path": "turn:3▸cascade",
        "parent_seq": 1, "ref_seq": 2, "dur_ms": 900,
    },
    # seq 6 — span_end for turn:3
    {
        "run": "r", "seq": 6, "ts": 1001.1,
        "type": "span_end", "name": "turn", "path": "turn:3",
        "parent_seq": None, "ref_seq": 1, "dur_ms": 1100,
    },
    # seq 7 — genesis span_start (different prefix)
    {
        "run": "r", "seq": 7, "ts": 900.0,
        "type": "span_start", "name": "genesis", "path": "genesis",
        "parent_seq": None, "attrs": None,
    },
    # seq 8 — gen inside genesis
    {
        "run": "r", "seq": 8, "ts": 900.1,
        "type": "gen", "name": "gen_frame", "path": "genesis▸gen_frame",
        "parent_seq": 7,
        "input": [{"role": "system", "content": "ZZZ genesis prompt"}],
        "output": "genesis output",
        "usage": {"input": 8, "output": 4},
        "dur_ms": 2000,
        "attrs": {"model": "glm"},
    },
]


# ---------------------------------------------------------------------------
# Import pure functions (fast path)
# ---------------------------------------------------------------------------

def _import_trace():
    import importlib
    import sys as _sys
    # ensure /root/rpg-engine-app is on the path
    if "/root/rpg-engine-app" not in _sys.path:
        _sys.path.insert(0, "/root/rpg-engine-app")
    return importlib.import_module("app.trace")


# ---------------------------------------------------------------------------
# Test: default compact index
# ---------------------------------------------------------------------------

class TestDefaultIndex:
    def test_index_contains_seq_and_path(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        lines = [m.index_line(r) for r in recs]
        # seq 3 line must contain its seq and path
        line3 = next(l for l in lines if "3" in l and "cascade▸llm" in l)
        assert "turn:3▸cascade▸llm" in line3

    def test_index_does_not_contain_full_input(self, tmp_path):
        """Token-lean contract: full prompt must NOT appear in index."""
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        all_lines = "\n".join(m.index_line(r) for r in recs)
        # The raw full prompt content should not appear verbatim
        assert "AAA long prompt here that is detailed" not in all_lines

    def test_index_line_has_summary_truncated(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        gen_rec = next(r for r in recs if r["seq"] == 3)
        line = m.index_line(gen_rec)
        # summary must be present but truncated to <= 80 chars at end
        # split after the fixed columns and check last field length
        parts = line.split()
        # should contain "BBBBB" (start of output) but not an unbounded string
        assert "BBBBB" in line
        # ensure summary portion is truncated (<=80 chars)
        summary = line.split("  ")[-1].strip()
        assert len(summary) <= 80

    def test_index_line_format_fields(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        gen_rec = next(r for r in recs if r["seq"] == 3)
        line = m.index_line(gen_rec)
        # dur_ms 8100 -> "8.1s"
        assert "8.1s" in line
        # token count
        assert "5" in line

    def test_subprocess_index_end_to_end(self, tmp_path):
        """At least one end-to-end subprocess call."""
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        result = _run(f)
        assert result.returncode == 0
        out = result.stdout
        assert "turn:3▸cascade▸llm" in out
        assert "3" in out
        # full prompt must NOT be in the output
        assert "AAA long prompt here that is detailed" not in out


# ---------------------------------------------------------------------------
# Test: --show <seq>
# ---------------------------------------------------------------------------

class TestShow:
    def test_show_prints_full_input_and_output(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        import io
        buf = io.StringIO()
        m.show_record(next(r for r in recs if r["seq"] == 3), out=buf)
        text = buf.getvalue()
        assert "AAA long prompt here that is detailed" in text
        assert "BBBBB response text" in text
        assert "turn:3▸cascade▸llm" in text

    def test_show_via_subprocess(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        result = _run(f, "--show", "3")
        assert result.returncode == 0
        assert "AAA long prompt here that is detailed" in result.stdout
        assert "BBBBB response text" in result.stdout

    def test_show_missing_seq_exits_error(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        result = _run(f, "--show", "999")
        assert result.returncode != 0 or "not found" in result.stderr.lower() or "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test: filters --turn, --phase, --type
# ---------------------------------------------------------------------------

class TestFilters:
    def test_turn_filter(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, turn=3)
        paths = [r["path"] for r in filtered]
        assert all(p.startswith("turn:3") for p in paths)
        # genesis records should be excluded
        assert not any("genesis" in p for p in paths)

    def test_phase_filter(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, phase="cascade")
        # all filtered records must have phase==cascade
        for r in filtered:
            assert m._phase_of(r) == "cascade"

    def test_type_filter_gen(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, type_filter="gen")
        assert all(r["type"] == "gen" for r in filtered)
        assert len(filtered) == 2  # seq 3 and seq 8

    def test_type_filter_span_matches_both(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, type_filter="span")
        types = {r["type"] for r in filtered}
        assert types == {"span_start", "span_end"}

    def test_type_filter_span_start_only(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, type_filter="span_start")
        assert all(r["type"] == "span_start" for r in filtered)


# ---------------------------------------------------------------------------
# Test: --grep
# ---------------------------------------------------------------------------

class TestGrep:
    def test_grep_finds_gen_by_output(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        matched = m.apply_grep(recs, "BBB")
        seqs = [r["seq"] for r in matched]
        assert 3 in seqs

    def test_grep_finds_gen_by_input(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        matched = m.apply_grep(recs, "AAA")
        seqs = [r["seq"] for r in matched]
        assert 3 in seqs

    def test_grep_no_match(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        matched = m.apply_grep(recs, "XYZZY_NOMATCH")
        assert matched == []

    def test_grep_via_subprocess(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        result = _run(f, "--grep", "BBB")
        assert result.returncode == 0
        assert "3" in result.stdout
        # genesis record (seq 8) should not appear
        assert "genesis" not in result.stdout or "8" not in result.stdout


# ---------------------------------------------------------------------------
# Test: --tree
# ---------------------------------------------------------------------------

class TestTree:
    def test_tree_indentation_increases_with_depth(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        import io
        buf = io.StringIO()
        m.render_tree(recs, out=buf)
        text = buf.getvalue()
        lines = [l for l in text.splitlines() if l.strip()]
        # turn:3 is depth 0 (0 ▸), cascade is depth 1 (1 ▸), llm is depth 2 (2 ▸)
        # find the line with cascade▸llm  — it should be indented more than turn:3
        turn_lines = [l for l in lines if "turn:3" in l and "▸" not in l.replace("turn:3", "")]
        cascade_lines = [l for l in lines if "cascade" in l and "llm" not in l]
        llm_lines = [l for l in lines if "llm" in l]
        if turn_lines and llm_lines:
            turn_indent = len(turn_lines[0]) - len(turn_lines[0].lstrip())
            llm_indent = len(llm_lines[0]) - len(llm_lines[0].lstrip())
            assert llm_indent > turn_indent

    def test_tree_contains_seqs(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        import io
        buf = io.StringIO()
        m.render_tree(recs, out=buf)
        text = buf.getvalue()
        # all seqs should appear
        for seq in range(1, 9):
            assert str(seq) in text


# ---------------------------------------------------------------------------
# Test: --stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_sums_dur_and_tokens_per_phase(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        stats = m.compute_stats(recs)
        # cascade phase: gen dur_ms=8100, span_end dur_ms=900
        cascade = stats.get("cascade")
        assert cascade is not None
        assert cascade["dur_ms"] >= 8100   # at least the gen dur
        assert cascade["tokens"] == 5      # usage.output of seq 3
        assert cascade["gens"] == 1

    def test_stats_genesis_phase(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        stats = m.compute_stats(recs)
        # genesis▸gen_frame → phase is "gen_frame"
        gen_frame = stats.get("gen_frame")
        assert gen_frame is not None
        assert gen_frame["gens"] == 1
        assert gen_frame["tokens"] == 4  # usage.output of seq 8

    def test_stats_via_subprocess(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        result = _run(f, "--stats")
        assert result.returncode == 0
        assert "cascade" in result.stdout


# ---------------------------------------------------------------------------
# Test: --json
# ---------------------------------------------------------------------------

class TestJson:
    def test_json_roundtrips_all_records(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        import io
        buf = io.StringIO()
        m.render_json(recs, out=buf)
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        parsed = [json.loads(l) for l in lines]
        assert len(parsed) == len(recs)
        assert parsed[0]["seq"] == 1

    def test_json_filtered_by_turn(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, turn=3)
        import io
        buf = io.StringIO()
        m.render_json(filtered, out=buf)
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        parsed = [json.loads(l) for l in lines]
        assert all(p["path"].startswith("turn:3") for p in parsed)

    def test_json_via_subprocess(self, tmp_path):
        f = tmp_path / "t.jsonl"
        _write(f, FIXTURE_RECS)
        result = _run(f, "--json")
        assert result.returncode == 0
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        parsed = [json.loads(l) for l in lines]
        assert len(parsed) == len(FIXTURE_RECS)
        seqs = [p["seq"] for p in parsed]
        assert sorted(seqs) == list(range(1, 9))


# ---------------------------------------------------------------------------
# Test: _phase_of
# ---------------------------------------------------------------------------

class TestPhaseOf:
    def test_phase_of_turn_path(self):
        m = _import_trace()
        rec = {"path": "turn:3▸cascade▸llm"}
        assert m._phase_of(rec) == "cascade"

    def test_phase_of_genesis_path(self):
        m = _import_trace()
        rec = {"path": "genesis▸gen_frame"}
        assert m._phase_of(rec) == "gen_frame"

    def test_phase_of_top_level_returns_none(self):
        m = _import_trace()
        rec = {"path": "turn:3"}
        assert m._phase_of(rec) is None

    def test_phase_of_top_level_genesis(self):
        m = _import_trace()
        rec = {"path": "genesis"}
        assert m._phase_of(rec) is None


# ---------------------------------------------------------------------------
# Test: load tolerates bad lines
# ---------------------------------------------------------------------------

class TestLoad:
    def test_load_skips_blank_lines(self, tmp_path):
        f = tmp_path / "t.jsonl"
        f.write_text(
            '{"seq":1,"type":"event","name":"x","path":"turn:1▸x"}\n\n{"seq":2,"type":"event","name":"y","path":"turn:1▸y"}\n',
            encoding="utf-8",
        )
        m = _import_trace()
        recs = m.load(str(f))
        assert len(recs) == 2

    def test_load_skips_invalid_json(self, tmp_path):
        f = tmp_path / "t.jsonl"
        f.write_text(
            '{"seq":1,"type":"event","name":"x","path":"turn:1▸x"}\nnot-json\n{"seq":2,"type":"event","name":"y","path":"turn:1▸y"}\n',
            encoding="utf-8",
        )
        m = _import_trace()
        recs = m.load(str(f))
        assert len(recs) == 2

    def test_load_missing_file_returns_empty(self):
        m = _import_trace()
        recs = m.load("/nonexistent/path/trace.jsonl")
        assert recs == []


# ---------------------------------------------------------------------------
# Test: base-name phase matching (Fix 1)
# ---------------------------------------------------------------------------

# Records whose phase segment includes a turn suffix (real on-disk format).
ENRICHED_PHASE_RECS = [
    # turn:3 root span
    {
        "run": "r", "seq": 1, "ts": 1000.0,
        "type": "span_start", "name": "turn", "path": "turn:3",
        "parent_seq": None, "attrs": {"turn": 3},
    },
    # cascade:3 span — enriched with turn number, as produced by span("cascade", turn=3)
    {
        "run": "r", "seq": 2, "ts": 1000.1,
        "type": "span_start", "name": "cascade", "path": "turn:3▸cascade:3",
        "parent_seq": 1, "attrs": {"turn": 3},
    },
    {
        "run": "r", "seq": 3, "ts": 1000.5,
        "type": "span_end", "name": "cascade", "path": "turn:3▸cascade:3",
        "parent_seq": 1, "ref_seq": 2, "dur_ms": 50,
    },
    # turn:3 root span_end
    {
        "run": "r", "seq": 4, "ts": 1001.0,
        "type": "span_end", "name": "turn", "path": "turn:3",
        "parent_seq": None, "ref_seq": 1, "dur_ms": 1000,
    },
    # turn:5 root span
    {
        "run": "r", "seq": 5, "ts": 2000.0,
        "type": "span_start", "name": "turn", "path": "turn:5",
        "parent_seq": None, "attrs": {"turn": 5},
    },
    # cascade:5 span — enriched with turn number 5
    {
        "run": "r", "seq": 6, "ts": 2000.1,
        "type": "span_start", "name": "cascade", "path": "turn:5▸cascade:5",
        "parent_seq": 5, "attrs": {"turn": 5},
    },
    {
        "run": "r", "seq": 7, "ts": 2000.4,
        "type": "gen", "name": "llm", "path": "turn:5▸cascade:5▸llm",
        "parent_seq": 6,
        "input": [{"role": "user", "content": "cascade gen prompt"}],
        "output": "cascade gen output",
        "usage": {"input": 15, "output": 8},
        "dur_ms": 300,
        "attrs": {"model": "test"},
    },
    {
        "run": "r", "seq": 8, "ts": 2000.8,
        "type": "span_end", "name": "cascade", "path": "turn:5▸cascade:5",
        "parent_seq": 5, "ref_seq": 6, "dur_ms": 70,
    },
    # turn:5 root span_end
    {
        "run": "r", "seq": 9, "ts": 2001.0,
        "type": "span_end", "name": "turn", "path": "turn:5",
        "parent_seq": None, "ref_seq": 5, "dur_ms": 1000,
    },
    # density:3 span — enriched with turn number, as produced by span("density", turn=3)
    {
        "run": "r", "seq": 10, "ts": 3000.0,
        "type": "span_start", "name": "density", "path": "turn:3▸density:3",
        "parent_seq": 1, "attrs": {"turn": 3},
    },
    {
        "run": "r", "seq": 11, "ts": 3000.5,
        "type": "span_end", "name": "density", "path": "turn:3▸density:3",
        "parent_seq": 1, "ref_seq": 10, "dur_ms": 60,
    },
    # repair:1 span (no turn enrichment — uses attempt=1 so segment is repair:1)
    {
        "run": "r", "seq": 12, "ts": 4000.0,
        "type": "span_start", "name": "repair", "path": "turn:3▸repair:1",
        "parent_seq": 1, "attrs": {"attempt": 1},
    },
    {
        "run": "r", "seq": 13, "ts": 4000.5,
        "type": "span_end", "name": "repair", "path": "turn:3▸repair:1",
        "parent_seq": 1, "ref_seq": 12, "dur_ms": 40,
    },
]


class TestBaseNamePhaseMatching:
    """Fix 1: --phase cascade matches cascade:3, cascade:5 etc. via base-name."""

    def test_phase_cascade_matches_enriched_segment(self, tmp_path):
        """--phase cascade must match records whose segment is cascade:3."""
        f = tmp_path / "enriched.jsonl"
        _write(f, ENRICHED_PHASE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, phase="cascade")
        # Should match cascade:3 (seqs 2,3) and cascade:5 (seqs 6,7,8)
        seqs = {r["seq"] for r in filtered}
        assert 2 in seqs, "cascade:3 span_start should match --phase cascade"
        assert 3 in seqs, "cascade:3 span_end should match --phase cascade"
        assert 6 in seqs, "cascade:5 span_start should match --phase cascade"
        assert 7 in seqs, "cascade:5 gen (llm child) should match --phase cascade"
        assert 8 in seqs, "cascade:5 span_end should match --phase cascade"
        # turn root records have no phase, must not appear
        assert 1 not in seqs, "turn:3 root record must not match --phase cascade"
        assert 4 not in seqs, "turn:3 span_end root must not match --phase cascade"

    def test_phase_cascade_exact_still_works(self, tmp_path):
        """--phase cascade:3 (exact) should still match only cascade:3 records."""
        f = tmp_path / "enriched.jsonl"
        _write(f, ENRICHED_PHASE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, phase="cascade:3")
        seqs = {r["seq"] for r in filtered}
        assert 2 in seqs, "cascade:3 span_start should match exact --phase cascade:3"
        assert 3 in seqs, "cascade:3 span_end should match exact --phase cascade:3"
        assert 6 not in seqs, "cascade:5 must NOT match --phase cascade:3"

    def test_phase_density_matches_enriched_segment(self, tmp_path):
        """--phase density matches density:3 records."""
        f = tmp_path / "enriched.jsonl"
        _write(f, ENRICHED_PHASE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, phase="density")
        seqs = {r["seq"] for r in filtered}
        assert 10 in seqs, "density:3 span_start should match --phase density"
        assert 11 in seqs, "density:3 span_end should match --phase density"

    def test_phase_repair_matches_attempt_enriched_segment(self, tmp_path):
        """--phase repair matches repair:1 records (attempt enrichment)."""
        f = tmp_path / "enriched.jsonl"
        _write(f, ENRICHED_PHASE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        filtered = m.apply_filters(recs, phase="repair")
        seqs = {r["seq"] for r in filtered}
        assert 12 in seqs, "repair:1 span_start should match --phase repair"
        assert 13 in seqs, "repair:1 span_end should match --phase repair"

    def test_stats_aggregates_cascade_under_one_row(self, tmp_path):
        """--stats must collapse cascade:3 and cascade:5 into a single 'cascade' row."""
        f = tmp_path / "enriched.jsonl"
        _write(f, ENRICHED_PHASE_RECS)
        m = _import_trace()
        recs = m.load(str(f))
        stats = m.compute_stats(recs)
        # There must be a single 'cascade' key, not cascade:3 and cascade:5 separately
        assert "cascade" in stats, f"Expected 'cascade' key in stats, got: {list(stats.keys())}"
        assert "cascade:3" not in stats, "cascade:3 must be collapsed into 'cascade'"
        assert "cascade:5" not in stats, "cascade:5 must be collapsed into 'cascade'"
        # gen from cascade:5 (seq 7) contributes tokens=8 and dur_ms=300
        # span_end from cascade:3 (seq 3) contributes dur_ms=50
        # span_end from cascade:5 (seq 8) contributes dur_ms=70
        cascade_stats = stats["cascade"]
        assert cascade_stats["gens"] == 1, f"Expected 1 gen in cascade, got {cascade_stats['gens']}"
        assert cascade_stats["tokens"] == 8, f"Expected 8 tokens in cascade, got {cascade_stats['tokens']}"
        assert cascade_stats["dur_ms"] >= 350, f"Expected >=350 dur_ms in cascade, got {cascade_stats['dur_ms']}"

    def test_stats_via_subprocess_shows_single_cascade_row(self, tmp_path):
        """End-to-end: --stats must show 'cascade' not 'cascade:3' or 'cascade:5'."""
        f = tmp_path / "enriched.jsonl"
        _write(f, ENRICHED_PHASE_RECS)
        result = _run(f, "--stats")
        assert result.returncode == 0
        lines = result.stdout.splitlines()
        # There should be exactly one line containing 'cascade' as the phase label
        cascade_rows = [l for l in lines if l.strip().startswith("cascade")]
        assert len(cascade_rows) == 1, \
            f"Expected exactly one 'cascade' row in --stats, got: {cascade_rows}"
        # Neither 'cascade:3' nor 'cascade:5' should appear as a separate row
        assert not any("cascade:3" in l for l in lines), \
            "cascade:3 must be collapsed, not a separate row"
        assert not any("cascade:5" in l for l in lines), \
            "cascade:5 must be collapsed, not a separate row"
