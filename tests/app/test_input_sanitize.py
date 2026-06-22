"""Tests for _sanitize_input and its integration in play_loop (issue #7).

Terminal control sequences (arrow keys, tmux prefix, ANSI escapes) must be
stripped before dispatch.  Pure-noise lines are skipped entirely (no turn
consumed); mixed lines ("\\x1b[B赶紧进去") are salvaged and run as turns.
"""
from __future__ import annotations

import pytest
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Unit tests for _sanitize_input (pure function)
# ---------------------------------------------------------------------------

def test_sanitize_arrow_keys_only():
    """A line of only arrow-key CSI escapes → empty string."""
    from app.play import _sanitize_input

    assert _sanitize_input("\x1b[B\x1b[A\x1b[B") == ""


def test_sanitize_mixed_escape_and_text():
    """Escape prefix before real text → real text returned."""
    from app.play import _sanitize_input

    assert _sanitize_input("\x1b[B赶紧进去") == "赶紧进去"


def test_sanitize_normal_line_unchanged():
    """Plain Chinese text has nothing to strip → identical."""
    from app.play import _sanitize_input

    assert _sanitize_input("我环顾四周") == "我环顾四周"


def test_sanitize_ctrl_b_tmux_prefix():
    """Ctrl-b (0x02) is a C0 control char and is stripped.

    The '[' that follows is plain ASCII and is NOT a control character, so it
    survives sanitization.  The real tmux copy-mode escape that travels over
    the wire as ANSI is ESC [ (0x1b 0x5b), which *is* a CSI prefix and gets
    fully stripped.  The raw two-byte sequence \x02\x5b (\x02[) strips only
    the control byte, leaving '['.
    """
    from app.play import _sanitize_input

    # \x02 stripped, '[' preserved → "[" (a single bracket, not empty)
    assert _sanitize_input("\x02[") == "["


def test_sanitize_bare_escape():
    """A lone ESC byte → empty."""
    from app.play import _sanitize_input

    assert _sanitize_input("\x1b") == ""


def test_sanitize_csi_with_params():
    """CSI sequences with numeric params (colour codes etc.) are stripped."""
    from app.play import _sanitize_input

    # ESC[32m (green) + text + ESC[0m (reset)
    assert _sanitize_input("\x1b[32mhello\x1b[0m") == "hello"


def test_sanitize_multiple_csi_sequences():
    """Multiple consecutive CSI sequences → all removed."""
    from app.play import _sanitize_input

    result = _sanitize_input("\x1b[A\x1b[B\x1b[C\x1b[D")
    assert result == ""


def test_sanitize_ooc_slash_with_leading_escape():
    """A leading stray escape before '/quit' is stripped → '/quit' remains."""
    from app.play import _sanitize_input

    assert _sanitize_input("\x1b[B/quit") == "/quit"


def test_sanitize_preserves_ascii_and_whitespace():
    """Normal ASCII with spaces is returned stripped but otherwise intact."""
    from app.play import _sanitize_input

    assert _sanitize_input("  look around  ") == "look around"


def test_sanitize_preserves_tab():
    """Tab character (HT / 0x09) is kept (user might paste with tab)."""
    from app.play import _sanitize_input

    result = _sanitize_input("search\there")
    assert "\t" in result


# ---------------------------------------------------------------------------
# Helpers for integration tests
# ---------------------------------------------------------------------------

def _make_canned_provider():
    return FakeLLMProvider(json_responses=[
        {
            "narration": "你环顾四周，发现这是一片宁静的旷野。",
            "clock": [{"advance": False, "days": 0, "bands": 0,
                        "reason": "本回合时间未推进"}],
        }
    ])


def _build_engine(tmp_path, provider=None):
    from app.engine import build_engine, new_game
    if provider is None:
        provider = _make_canned_provider()
    engine = build_engine(tmp_path, provider=provider)
    new_game(engine)
    return engine


# ---------------------------------------------------------------------------
# Integration tests: play_loop skips / salvages noise
# ---------------------------------------------------------------------------

def test_play_loop_pure_arrow_keys_skipped_no_turn(tmp_path):
    """A line of pure arrow-key CSI escapes must NOT run a turn.

    The provider would be called if a turn ran; since it has only one canned
    response, a second call would raise — asserting no turn ran we just
    check that no narration text appeared in out and turn_no stays at 0.
    We track this by verifying no narration is emitted.
    """
    from app.play import play_loop

    provider = _make_canned_provider()
    engine = _build_engine(tmp_path, provider=provider)
    engine.provider = _make_canned_provider()

    collected = []
    # Feed only noise + /quit — no real turn should run
    play_loop(engine, inputs=["\x1b[B\x1b[A\x1b[B", "/quit"],
              out=collected.append)

    combined = "\n".join(collected)
    # The canned narration must NOT appear — no turn was run
    assert "你环顾四周" not in combined, (
        f"Noise-only line triggered a game turn (narration appeared): {combined!r}"
    )


def test_play_loop_mixed_escape_plus_text_runs_turn(tmp_path):
    """A line with a leading escape then real text is salvaged and runs a turn."""
    from app.play import play_loop

    provider = _make_canned_provider()
    engine = _build_engine(tmp_path, provider=provider)
    engine.provider = _make_canned_provider()

    collected = []
    play_loop(engine, inputs=["\x1b[B赶紧进去", "/quit"],
              out=collected.append)

    combined = "\n".join(collected)
    # The narration proves a turn ran with the cleaned text
    assert "你环顾四周" in combined, (
        f"Salvaged text did not run a turn: {combined!r}"
    )


def test_play_loop_normal_line_runs_turn(tmp_path):
    """A clean Chinese input line runs a normal turn unchanged."""
    from app.play import play_loop

    provider = _make_canned_provider()
    engine = _build_engine(tmp_path, provider=provider)
    engine.provider = _make_canned_provider()

    collected = []
    play_loop(engine, inputs=["我环顾四周", "/quit"],
              out=collected.append)

    combined = "\n".join(collected)
    assert "你环顾四周" in combined, (
        f"Normal line did not run a turn: {combined!r}"
    )


def test_play_loop_ooc_with_leading_escape_still_dispatched(tmp_path):
    """A leading stray escape before '/quit' is stripped → dispatched as OOC /quit."""
    from app.play import play_loop

    engine = _build_engine(tmp_path)

    collected = []
    play_loop(engine, inputs=["\x1b[B/quit", "这行不应被处理"],
              out=collected.append)

    combined = "\n".join(collected)
    # /quit must have fired — the trailing line must not have run
    assert "这行不应被处理" not in combined
    # `\x1b[B/quit` sanitizes to `/quit` → quit fires → game-end message printed
    assert "游戏结束" in combined


def test_play_loop_pure_c0_control_skipped(tmp_path):
    """A line composed entirely of C0 control characters → empty after sanitize → skipped.

    Example: the raw bytes that arrive when a user types Ctrl-b Ctrl-c in tmux
    (\x02\x03) consist solely of C0 controls — both are stripped, result is "".
    """
    from app.play import play_loop

    engine = _build_engine(tmp_path)
    engine.provider = _make_canned_provider()

    collected = []
    # \x02 = Ctrl-b, \x03 = Ctrl-c — both C0 controls, both stripped → ""
    play_loop(engine, inputs=["\x02\x03", "/quit"],
              out=collected.append)

    combined = "\n".join(collected)
    assert "你环顾四周" not in combined, (
        f"Pure C0 control noise triggered a game turn: {combined!r}"
    )


def test_play_loop_multiple_noise_lines_then_real_turn(tmp_path):
    """Multiple noise lines are all skipped; the first real line still runs."""
    from app.play import play_loop

    provider = _make_canned_provider()
    engine = _build_engine(tmp_path, provider=provider)
    engine.provider = _make_canned_provider()

    collected = []
    noise_lines = [
        "\x1b[B\x1b[A",          # arrow keys (CSI) → ""
        "\x02\x03",              # pure C0 controls (Ctrl-b Ctrl-c) → ""
        "\x1b[B\x1b[A\x1b[B",   # more CSI arrows → ""
    ]
    play_loop(engine, inputs=[*noise_lines, "我环顾四周", "/quit"],
              out=collected.append)

    combined = "\n".join(collected)
    assert "你环顾四周" in combined, (
        f"Turn after noise lines did not run: {combined!r}"
    )
