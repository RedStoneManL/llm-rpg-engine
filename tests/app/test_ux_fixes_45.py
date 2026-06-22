"""Tests for UX fixes #4 (loading indicator) and #5 (player/DM framing).

Fix #4 — _Spinner:
  - Non-TTY path: emits exactly one plain-text indicator line via ``out``,
    no thread, deterministic.
  - TTY path (forced on): starts/stops without error; no spinner text leaks
    into a captured ``out`` collector.

Fix #5 — _echo_player / _print_dm_narration:
  - Non-TTY: player echoed with '> 你：' prefix; DM narration under '[DM]'
    header preceded by a plain dashes separator.  No ANSI codes.
  - TTY (forced on): player echo contains ANSI dim codes; DM framing uses
    Unicode separator + 【DM】.

Integration (play_loop, non-TTY):
  - After one turn: collected output contains '> 你：<input>', then '[DM]',
    then the narration text.  No spinner animation leaks.
  - Spinner plain-text line ('（DM 落笔中…）') appears BEFORE '[DM]'.
  - OOC commands still work (no double-echo, no DM header on OOC output).
  - Compare mode: player echo + spinner indicator + DM framing appear.
"""
from __future__ import annotations

import sys
import threading
import time
from unittest.mock import patch

import pytest
from llm.provider import FakeLLMProvider


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _canned_provider():
    return FakeLLMProvider(json_responses=[
        {
            "narration": "你环顾四周，发现这是一片宁静的旷野。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "本回合时间未推进"}],
        }
    ])


def _build_engine(tmp_path, provider=None):
    from app.engine import build_engine, new_game
    if provider is None:
        provider = _canned_provider()
    engine = build_engine(tmp_path, provider=provider)
    new_game(engine)
    return engine


# ---------------------------------------------------------------------------
# Unit: _Spinner — non-TTY path
# ---------------------------------------------------------------------------

class TestSpinnerNonTTY:
    """When stdout is NOT a tty, _Spinner must:
    - emit exactly one plain-text line via ``out``
    - NOT start a background thread
    - stop() must be a no-op (no error)
    """

    def _make_spinner(self, out):
        from app.play import _Spinner
        return _Spinner(out, _force_tty=False)

    def test_start_emits_one_plain_line(self):
        collected = []
        sp = self._make_spinner(collected.append)
        sp.start()
        # Exactly one line emitted
        assert len(collected) == 1, f"Expected 1 line, got {collected!r}"
        assert "落笔" in collected[0], f"Expected indicator text, got {collected[0]!r}"
        # No ANSI escape sequences
        assert "\x1b" not in collected[0], "Non-TTY spinner must not contain ANSI codes"

    def test_stop_is_safe_noop(self):
        collected = []
        sp = self._make_spinner(collected.append)
        sp.start()
        before = len(collected)
        sp.stop()
        # stop must not add more output on the non-TTY path
        assert len(collected) == before, "stop() must not emit additional lines on non-TTY"

    def test_no_thread_started(self):
        """Non-TTY spinner must not start a background thread."""
        collected = []
        sp = self._make_spinner(collected.append)
        threads_before = set(t.ident for t in threading.enumerate())
        sp.start()
        sp.stop()
        threads_after = set(t.ident for t in threading.enumerate())
        new_threads = threads_after - threads_before
        assert not new_threads, f"Non-TTY spinner must not start threads; got {new_threads}"

    def test_stop_before_start_does_not_raise(self):
        collected = []
        sp = self._make_spinner(collected.append)
        sp.stop()  # must not raise
        assert len(collected) == 0


# ---------------------------------------------------------------------------
# Unit: _Spinner — TTY path (forced)
# ---------------------------------------------------------------------------

class TestSpinnerTTY:
    """When stdout IS a tty (forced), _Spinner must:
    - start a daemon background thread
    - stop() must join it cleanly (no leaked threads, no output via ``out``)
    """

    def _make_spinner(self, out):
        from app.play import _Spinner
        return _Spinner(out, _force_tty=True)

    def test_start_stop_does_not_crash(self):
        """TTY spinner starts and stops without error."""
        collected = []
        sp = self._make_spinner(collected.append)
        # Patch sys.stdout.write/flush so the animation doesn't hit the real terminal
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = lambda s: None
            mock_stdout.flush = lambda: None
            sp.start()
            time.sleep(0.05)
            sp.stop()
        # No output should have been routed through ``out`` on the TTY path
        assert len(collected) == 0, (
            f"TTY spinner must not call out(); got {collected!r}"
        )

    def test_thread_joined_after_stop(self):
        """After stop(), the spinner thread must be joined (not alive)."""
        collected = []
        sp = self._make_spinner(collected.append)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = lambda s: None
            mock_stdout.flush = lambda: None
            sp.start()
            thread = sp._thread
            assert thread is not None, "TTY spinner must have started a thread"
            assert thread.is_alive(), "Spinner thread should be alive just after start"
            sp.stop()
        assert not thread.is_alive(), "Spinner thread must not be alive after stop()"


# ---------------------------------------------------------------------------
# Unit: _echo_player — non-TTY + TTY
# ---------------------------------------------------------------------------

class TestEchoPlayer:
    def test_non_tty_plain_prefix(self):
        from app.play import _echo_player
        collected = []
        _echo_player("我环顾四周", collected.append, _force_tty=False)
        assert len(collected) == 1
        line = collected[0]
        assert "> 你：" in line, f"Expected '> 你：' prefix, got {line!r}"
        assert "我环顾四周" in line
        assert "\x1b" not in line, "Non-TTY echo must not contain ANSI codes"

    def test_non_tty_no_ansi(self):
        from app.play import _echo_player
        collected = []
        _echo_player("测试输入", collected.append, _force_tty=False)
        assert "\x1b" not in collected[0]

    def test_tty_contains_ansi_dim(self):
        from app.play import _echo_player, _ANSI_DIM, _ANSI_RESET
        collected = []
        _echo_player("测试输入", collected.append, _force_tty=True)
        assert len(collected) == 1
        line = collected[0]
        assert _ANSI_DIM in line, f"TTY echo must contain dim code; got {line!r}"
        assert _ANSI_RESET in line
        assert "测试输入" in line
        assert "▶ 你：" in line or "你：" in line


# ---------------------------------------------------------------------------
# Unit: _print_dm_narration — non-TTY + TTY
# ---------------------------------------------------------------------------

class TestPrintDMNarration:
    def test_non_tty_plain_header_and_text(self):
        from app.play import _print_dm_narration
        collected = []
        _print_dm_narration("DM说了些话。", collected.append, _force_tty=False)
        combined = "\n".join(collected)
        # Must have plain separator (dashes)
        assert "-" * 10 in combined, f"Expected plain dash separator; got {combined!r}"
        # Must have plain [DM] marker
        assert "[DM]" in combined, f"Expected [DM] header; got {combined!r}"
        # Must contain the narration text
        assert "DM说了些话" in combined
        # Must not contain ANSI escape
        assert "\x1b" not in combined, "Non-TTY DM output must not contain ANSI codes"

    def test_non_tty_order_separator_then_header_then_text(self):
        from app.play import _print_dm_narration
        collected = []
        _print_dm_narration("叙述内容。", collected.append, _force_tty=False)
        # collected = [separator_line, "[DM]", narration]
        assert len(collected) == 3, f"Expected 3 items (sep, header, text); got {collected!r}"
        assert "-" in collected[0], f"First item should be separator; got {collected[0]!r}"
        assert "[DM]" in collected[1], f"Second item should be [DM]; got {collected[1]!r}"
        assert "叙述内容" in collected[2], f"Third item should be narration; got {collected[2]!r}"

    def test_tty_unicode_separator_and_header(self):
        from app.play import _print_dm_narration
        collected = []
        _print_dm_narration("TTY叙述。", collected.append, _force_tty=True)
        combined = "\n".join(collected)
        # Should have Unicode separator (─) and 【DM】
        assert "─" in combined or "【DM】" in combined, (
            f"TTY DM output should have Unicode markers; got {combined!r}"
        )
        assert "TTY叙述" in combined

    def test_non_tty_no_unicode_box_chars(self):
        from app.play import _print_dm_narration
        collected = []
        _print_dm_narration("内容", collected.append, _force_tty=False)
        combined = "\n".join(collected)
        # Non-TTY path must not emit the TTY-only Unicode box-drawing characters
        assert "─" not in combined, "Non-TTY must not emit Unicode box-drawing '─'"
        assert "【" not in combined, "Non-TTY must not emit 【DM】"


# ---------------------------------------------------------------------------
# Integration: play_loop with injected out= (non-TTY path)
# ---------------------------------------------------------------------------

class TestPlayLoopFraming:
    """play_loop with a collector ``out`` (non-TTY: sys.stdout.isatty()==False)."""

    def _run_one_turn(self, tmp_path, inputs=None):
        from app.play import play_loop
        if inputs is None:
            inputs = ["看看四周", "/quit"]
        engine = _build_engine(tmp_path, _canned_provider())
        engine.provider = _canned_provider()
        collected = []
        with patch.object(sys.stdout, "isatty", return_value=False):
            play_loop(engine, inputs=inputs, out=collected.append)
        return collected

    def test_player_input_echoed_with_marker(self, tmp_path):
        """The sanitised player input must appear with '> 你：' prefix in output."""
        collected = self._run_one_turn(tmp_path, ["看看四周", "/quit"])
        combined = "\n".join(collected)
        # Must contain the player echo marker
        assert "> 你：" in combined, (
            f"Expected '> 你：' player echo in output; got {combined!r}"
        )
        # The input text must follow the marker
        assert "看看四周" in combined

    def test_dm_narration_under_dm_header(self, tmp_path):
        """The DM narration must appear under a [DM] header."""
        collected = self._run_one_turn(tmp_path, ["看看四周", "/quit"])
        combined = "\n".join(collected)
        assert "[DM]" in combined, f"Expected [DM] header; got {combined!r}"
        assert "你环顾四周" in combined, f"Expected narration text; got {combined!r}"

    def test_player_echo_before_dm_header(self, tmp_path):
        """Player echo must appear before the [DM] header in output sequence."""
        collected = self._run_one_turn(tmp_path, ["看看四周", "/quit"])
        # Find positions
        echo_idx = next(
            (i for i, s in enumerate(collected) if "> 你：" in s), None
        )
        dm_idx = next(
            (i for i, s in enumerate(collected) if "[DM]" in s), None
        )
        assert echo_idx is not None, f"Player echo not found in {collected!r}"
        assert dm_idx is not None, f"[DM] header not found in {collected!r}"
        assert echo_idx < dm_idx, (
            f"Player echo (idx={echo_idx}) must come before [DM] (idx={dm_idx})"
        )

    def test_spinner_plain_line_before_dm_header(self, tmp_path):
        """The non-TTY spinner line must appear between player echo and [DM] header."""
        collected = self._run_one_turn(tmp_path, ["看看四周", "/quit"])
        spinner_idx = next(
            (i for i, s in enumerate(collected) if "落笔" in s), None
        )
        dm_idx = next(
            (i for i, s in enumerate(collected) if "[DM]" in s), None
        )
        assert spinner_idx is not None, (
            f"Spinner plain line not found in output: {collected!r}"
        )
        assert dm_idx is not None, f"[DM] header not found in {collected!r}"
        assert spinner_idx < dm_idx, (
            f"Spinner (idx={spinner_idx}) must appear before [DM] (idx={dm_idx})"
        )

    def test_no_ansi_in_non_tty_output(self, tmp_path):
        """No ANSI escape codes must appear in the collected non-TTY output."""
        collected = self._run_one_turn(tmp_path, ["看看四周", "/quit"])
        for line in collected:
            assert "\x1b" not in line, (
                f"ANSI code found in non-TTY output line: {line!r}"
            )

    def test_no_spinner_animation_in_collected_output(self, tmp_path):
        """The spinner animation frames (⏳ DM 落笔中...) must NOT appear in output.
        Only the single plain line is expected, not any 'cleared' animation artifact."""
        collected = self._run_one_turn(tmp_path, ["看看四周", "/quit"])
        # No frame should contain carriage return (\r)
        for line in collected:
            assert "\r" not in line, (
                f"Carriage return (animation artifact) found in collected output: {line!r}"
            )
        # Count spinner-related lines — should be exactly one (the plain indicator)
        spinner_lines = [s for s in collected if "落笔" in s]
        assert len(spinner_lines) == 1, (
            f"Expected exactly 1 spinner indicator line; got {spinner_lines!r}"
        )

    def test_ooc_quit_not_echoed_as_player_input(self, tmp_path):
        """OOC commands like /quit must NOT be echoed with '> 你：' prefix."""
        collected = self._run_one_turn(tmp_path, ["/quit"])
        for line in collected:
            assert not ("> 你：" in line and "/quit" in line), (
                f"OOC /quit must not be echoed as player input; found: {line!r}"
            )

    def test_ooc_help_no_dm_header(self, tmp_path):
        """/help output must not be wrapped in a [DM] header."""
        from app.play import play_loop
        engine = _build_engine(tmp_path)
        collected = []
        with patch.object(sys.stdout, "isatty", return_value=False):
            play_loop(engine, inputs=["/help", "/quit"], out=collected.append)
        combined = "\n".join(collected)
        # Help text should appear
        assert "/quit" in combined or "退出" in combined
        # But there should be no [DM] marker (no turn ran)
        assert "[DM]" not in combined, (
            f"/help output must not be wrapped in [DM]; got {combined!r}"
        )

    def test_two_turns_both_framed(self, tmp_path):
        """With two consecutive player inputs, each turn produces its own player echo
        and [DM] header — i.e. the framing is per-turn, not once-per-session."""
        from app.play import play_loop
        # Use the default canned provider (returns the same narration for both turns).
        # That's fine: we only care that the framing appears twice.
        engine = _build_engine(tmp_path)
        engine.provider = _canned_provider()
        collected = []
        with patch.object(sys.stdout, "isatty", return_value=False):
            play_loop(
                engine,
                inputs=["第一行动", "第二行动", "/quit"],
                out=collected.append,
            )
        combined = "\n".join(collected)
        # Both player echoes must be present
        assert "第一行动" in combined, f"First player echo missing; got {combined!r}"
        assert "第二行动" in combined, f"Second player echo missing; got {combined!r}"
        # Two player-echo lines with the '> 你：' prefix
        echo_lines = [s for s in collected if "> 你：" in s]
        assert len(echo_lines) == 2, (
            f"Expected 2 player echo lines; got {echo_lines!r}"
        )
        # Two [DM] header lines (one per turn)
        dm_count = sum(1 for s in collected if s.strip() == "[DM]")
        assert dm_count == 2, (
            f"Expected 2 [DM] headers for 2 turns; got {dm_count} in {collected!r}"
        )


# ---------------------------------------------------------------------------
# Integration: compare mode framing (non-TTY)
# ---------------------------------------------------------------------------

class TestCompareModeFix45:
    def test_compare_mode_player_echo_and_dm_framing(self, tmp_path):
        """In compare mode: player input is echoed + spinner runs + DM framing present."""
        from app.play import play_loop
        jia = {
            "narration": "甲策略叙述：你踏入小路。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "无"}],
        }
        bing = {
            "narration": "丙策略叙述：小路延伸。",
            "clock": [{"advance": False, "days": 0, "bands": 0, "reason": "无"}],
        }
        bing_prose = "丙策略叙述：小路延伸。"
        provider = FakeLLMProvider(
            responses=[bing_prose],
            json_responses=[jia, bing],
        )
        engine = _build_engine(tmp_path, provider)
        engine.provider = FakeLLMProvider(
            responses=[bing_prose],
            json_responses=[jia, bing],
        )
        collected = []
        with patch.object(sys.stdout, "isatty", return_value=False):
            play_loop(
                engine,
                inputs=["/compare on", "向前走", "/quit"],
                out=collected.append,
            )
        combined = "\n".join(collected)
        # Player echo
        assert "> 你：" in combined and "向前走" in combined, (
            f"Player echo missing; got {combined!r}"
        )
        # Spinner indicator
        assert "落笔" in combined, f"Spinner indicator missing; got {combined!r}"
        # DM framing
        assert "[DM]" in combined, f"[DM] header missing; got {combined!r}"
        # No ANSI
        assert "\x1b" not in combined, "No ANSI codes on non-TTY compare mode"
