"""Tests for fix #9 + #3 — engine.settings module, strategy verbosity injection,
/verbosity OOC command, and --verbosity CLI flag.

All tests are hermetic: the conftest _hermetic_rpg_env fixture clears
RPG_NARRATION_VERBOSITY and RPG_MAX_TOOL_ROUNDS from the env AND calls
reset_from_env() on teardown, so settings state never leaks across tests.
"""
from __future__ import annotations

import pytest


# ===========================================================================
# 1. engine.settings — unit tests
# ===========================================================================

class TestSettings:
    """Unit-test the engine.settings module in isolation."""

    def setup_method(self):
        """Reset to known defaults before each test method."""
        from engine import settings
        settings.reset_from_env()  # env is already cleared by conftest

    # -----------------------------------------------------------------------
    # Default values
    # -----------------------------------------------------------------------

    def test_default_verbosity_is_medium(self):
        from engine import settings
        assert settings.get_verbosity() == "medium"

    def test_default_max_tool_rounds_is_12(self):
        from engine import settings
        assert settings.get_max_tool_rounds() == 12

    # -----------------------------------------------------------------------
    # set_verbosity
    # -----------------------------------------------------------------------

    def test_set_verbosity_concise(self):
        from engine import settings
        ok = settings.set_verbosity("concise")
        assert ok is True
        assert settings.get_verbosity() == "concise"

    def test_set_verbosity_rich(self):
        from engine import settings
        ok = settings.set_verbosity("rich")
        assert ok is True
        assert settings.get_verbosity() == "rich"

    def test_set_verbosity_medium(self):
        from engine import settings
        settings.set_verbosity("concise")  # change first
        ok = settings.set_verbosity("medium")
        assert ok is True
        assert settings.get_verbosity() == "medium"

    def test_set_verbosity_invalid_returns_false_and_unchanged(self):
        from engine import settings
        settings.set_verbosity("medium")
        ok = settings.set_verbosity("ULTRA_VERBOSE")
        assert ok is False
        assert settings.get_verbosity() == "medium"  # unchanged

    def test_set_verbosity_empty_returns_false(self):
        from engine import settings
        ok = settings.set_verbosity("")
        assert ok is False

    def test_set_verbosity_case_insensitive(self):
        from engine import settings
        ok = settings.set_verbosity("CONCISE")
        assert ok is True
        assert settings.get_verbosity() == "concise"

    # -----------------------------------------------------------------------
    # reset_from_env
    # -----------------------------------------------------------------------

    def test_reset_from_env_picks_up_verbosity(self, monkeypatch):
        from engine import settings
        monkeypatch.setenv("RPG_NARRATION_VERBOSITY", "rich")
        settings.reset_from_env()
        assert settings.get_verbosity() == "rich"

    def test_reset_from_env_picks_up_max_tool_rounds(self, monkeypatch):
        from engine import settings
        monkeypatch.setenv("RPG_MAX_TOOL_ROUNDS", "7")
        settings.reset_from_env()
        assert settings.get_max_tool_rounds() == 7

    def test_reset_from_env_unknown_verbosity_falls_back_to_medium(self, monkeypatch):
        from engine import settings
        monkeypatch.setenv("RPG_NARRATION_VERBOSITY", "GARBAGE")
        settings.reset_from_env()
        assert settings.get_verbosity() == "medium"

    def test_reset_from_env_bad_tool_rounds_falls_back_to_12(self, monkeypatch):
        from engine import settings
        monkeypatch.setenv("RPG_MAX_TOOL_ROUNDS", "not_a_number")
        settings.reset_from_env()
        assert settings.get_max_tool_rounds() == 12

    def test_reset_from_env_restores_defaults_when_env_cleared(self, monkeypatch):
        from engine import settings
        # Set a custom value then reset without env vars
        settings.set_verbosity("rich")
        monkeypatch.delenv("RPG_NARRATION_VERBOSITY", raising=False)
        monkeypatch.delenv("RPG_MAX_TOOL_ROUNDS", raising=False)
        settings.reset_from_env()
        assert settings.get_verbosity() == "medium"
        assert settings.get_max_tool_rounds() == 12


# ===========================================================================
# 2. loop.strategy — verbosity fragment injection
# ===========================================================================

class TestStrategyVerbosity:
    """The system prompt must contain the distinguishing verbosity fragment."""

    def setup_method(self):
        from engine import settings
        settings.reset_from_env()

    def test_system_prompt_contains_concise_fragment_when_concise(self):
        from engine import settings
        from loop.strategy import _system_prompt
        settings.set_verbosity("concise")
        prompt = _system_prompt()
        assert "直奔关键" in prompt, (
            f"concise fragment not found in system prompt: {prompt[:200]!r}"
        )

    def test_system_prompt_contains_rich_fragment_when_rich(self):
        from engine import settings
        from loop.strategy import _system_prompt
        settings.set_verbosity("rich")
        prompt = _system_prompt()
        assert "浓墨铺陈" in prompt, (
            f"rich fragment not found in system prompt: {prompt[:200]!r}"
        )

    def test_system_prompt_medium_has_balanced_fragment(self):
        from engine import settings
        from loop.strategy import _system_prompt
        settings.set_verbosity("medium")
        prompt = _system_prompt()
        assert "推进为主" in prompt, (
            f"medium fragment not found in system prompt: {prompt[:200]!r}"
        )

    def test_narrate_prompt_contains_concise_fragment_when_concise(self):
        from engine import settings
        from loop.strategy import _narrate_prompt
        settings.set_verbosity("concise")
        prompt = _narrate_prompt()
        assert "篇幅克制" in prompt, (
            f"concise narrate fragment not found: {prompt[:200]!r}"
        )

    def test_narrate_prompt_contains_rich_fragment_when_rich(self):
        from engine import settings
        from loop.strategy import _narrate_prompt
        settings.set_verbosity("rich")
        prompt = _narrate_prompt()
        assert "浓墨铺陈" in prompt, (
            f"rich narrate fragment not found: {prompt[:200]!r}"
        )

    def test_system_prompt_still_contains_required_structural_keys(self):
        """All structural prompt keys must survive the refactor."""
        from loop.strategy import _system_prompt
        prompt = _system_prompt("medium")
        for keyword in ("narration", "moves", "places", "cast", "facts",
                        "relations", "clock", "quests", "knowledge", "world"):
            assert keyword in prompt, f"Missing {keyword!r} in system prompt"

    def test_author_strategy_uses_settings_max_tool_rounds(self, monkeypatch):
        """AuthorStrategy.produce must read max_tool_rounds from engine.settings,
        not from os.environ directly."""
        from engine import settings
        from loop.strategy import AuthorStrategy
        from kernel.registry import Registry
        from kernel.projection import empty_world
        from systems.ontology import OntologySystem
        from systems.place import PlaceSystem
        from llm.provider import ScriptedToolProvider

        settings.set_verbosity("medium")

        # Set a custom rounds value via settings (not env)
        import engine.settings as _s
        _s._max_tool_rounds = 5  # bypass set_verbosity — direct state poke for test

        registry = Registry()
        registry.register(OntologySystem())
        registry.register(PlaceSystem())
        world = empty_world(registry)
        scene = {"protagonist": "hero", "present": ["hero"], "day": 1, "location": "town"}

        captured_rounds = []

        class PatchedStrategy(AuthorStrategy):
            pass

        from llm.provider import ScriptedToolProvider
        script = [
            {"content": '{"narration": "ok", "moves": []}'},
        ]
        provider = ScriptedToolProvider(script=script)

        # Patch complete_with_tools to capture rounds argument
        original_cwt = provider.complete_with_tools
        def _capture_cwt(messages, schemas, executor, *, max_tool_rounds):
            captured_rounds.append(max_tool_rounds)
            return '{"narration": "ok", "moves": []}'
        provider.complete_with_tools = _capture_cwt

        strat = PatchedStrategy()
        strat.produce(registry, world, scene, "walk", provider=provider)

        assert captured_rounds == [5], (
            f"Expected max_tool_rounds=5 from settings, got {captured_rounds}"
        )

        # Cleanup: restore default
        _s.reset_from_env()


# ===========================================================================
# 3. app.play — /verbosity OOC command
# ===========================================================================

class TestVerbosityOOC:
    """dispatch_ooc /verbosity command."""

    def setup_method(self):
        from engine import settings
        settings.reset_from_env()

    def _make_engine(self, tmp_path):
        from app.engine import build_engine, new_game
        engine = build_engine(tmp_path, provider=None)
        new_game(engine)
        return engine

    def test_verbosity_no_arg_prints_current(self, tmp_path):
        from app.play import dispatch_ooc
        from engine import settings

        engine = self._make_engine(tmp_path)
        collected = []
        stop = dispatch_ooc("/verbosity", engine, out=collected.append,
                            compare_mode=[False])
        assert stop is False
        combined = "\n".join(collected)
        assert settings.get_verbosity() in combined, (
            f"Current verbosity level not in output: {combined!r}"
        )

    def test_verbosity_concise_sets_level(self, tmp_path):
        from app.play import dispatch_ooc
        from engine import settings

        engine = self._make_engine(tmp_path)
        collected = []
        stop = dispatch_ooc("/verbosity concise", engine, out=collected.append,
                            compare_mode=[False])
        assert stop is False
        assert settings.get_verbosity() == "concise"
        combined = "\n".join(collected)
        assert "concise" in combined, (
            f"Expected confirmation mentioning 'concise'; got {combined!r}"
        )

    def test_verbosity_rich_sets_level(self, tmp_path):
        from app.play import dispatch_ooc
        from engine import settings

        engine = self._make_engine(tmp_path)
        collected = []
        dispatch_ooc("/verbosity rich", engine, out=collected.append,
                     compare_mode=[False])
        assert settings.get_verbosity() == "rich"

    def test_verbosity_invalid_level_prints_hint(self, tmp_path):
        from app.play import dispatch_ooc
        from engine import settings

        engine = self._make_engine(tmp_path)
        original = settings.get_verbosity()
        collected = []
        stop = dispatch_ooc("/verbosity BOGUS", engine, out=collected.append,
                            compare_mode=[False])
        assert stop is False
        # Setting must be unchanged
        assert settings.get_verbosity() == original
        combined = "\n".join(collected)
        # Should print a usage hint mentioning the valid levels
        assert any(w in combined for w in ("concise", "medium", "rich", "无效", "invalid")), (
            f"Expected usage hint with valid levels; got {combined!r}"
        )

    def test_verbosity_in_help_text(self):
        from app.play import _HELP_TEXT
        assert "verbosity" in _HELP_TEXT.lower(), (
            "/verbosity must appear in _HELP_TEXT"
        )


# ===========================================================================
# 4. CLI — --verbosity flag
# ===========================================================================

class TestCLIVerbosity:
    """--verbosity flag wires into engine.settings."""

    def setup_method(self):
        from engine import settings
        settings.reset_from_env()

    def test_cli_verbosity_concise_applied_before_play(self, tmp_path):
        """--verbosity concise results in get_verbosity()=='concise' during play."""
        from engine import settings

        verbosity_during_play = []

        # Patch play_loop to capture the verbosity at call time, then stop
        import app.play as _play_mod
        original_play_loop = _play_mod.play_loop

        def _fake_play_loop(engine, inputs, **kwargs):
            verbosity_during_play.append(settings.get_verbosity())

        _play_mod.play_loop = _fake_play_loop
        try:
            from app.__main__ import main
            main(
                ["--campaign", str(tmp_path), "--provider", "fake",
                 "--verbosity", "concise"],
                inputs=[],
                out=lambda _: None,
            )
        finally:
            _play_mod.play_loop = original_play_loop

        assert verbosity_during_play == ["concise"], (
            f"Expected verbosity='concise' at play time; got {verbosity_during_play}"
        )

    def test_cli_no_verbosity_flag_uses_default(self, tmp_path):
        """Without --verbosity, settings stay at their env/default value."""
        from engine import settings

        verbosity_during_play = []

        import app.play as _play_mod
        original_play_loop = _play_mod.play_loop

        def _fake_play_loop(engine, inputs, **kwargs):
            verbosity_during_play.append(settings.get_verbosity())

        _play_mod.play_loop = _fake_play_loop
        try:
            from app.__main__ import main
            main(
                ["--campaign", str(tmp_path), "--provider", "fake"],
                inputs=[],
                out=lambda _: None,
            )
        finally:
            _play_mod.play_loop = original_play_loop

        # Default is "medium" (env is cleared by conftest)
        assert verbosity_during_play == ["medium"], (
            f"Expected verbosity='medium' (default); got {verbosity_during_play}"
        )
