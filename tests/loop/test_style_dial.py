"""#R8 — a narration STYLE/voice dial (e.g. 日式轻小说), orthogonal to verbosity
(length). Default empty = neutral = byte-identical to the pre-#R8 prompt."""
from engine import settings
from loop.strategy import _system_prompt, _narrate_prompt, _style_fragment


def test_style_default_empty():
    assert settings.get_style() == ""


def test_set_and_clear_style():
    assert settings.set_style("日式轻小说") is True
    assert settings.get_style() == "日式轻小说"
    assert settings.set_style("") is True
    assert settings.get_style() == ""


def test_style_fragment_empty_is_noop():
    assert _style_fragment("") == ""
    assert _style_fragment(None) == ""
    assert _style_fragment("  ") == ""


def test_style_fragment_wraps_user_string_generically():
    frag = _style_fragment("日式轻小说")
    assert "日式轻小说" in frag
    assert "文风基调" in frag


def test_style_injected_into_system_and_narrate_prompts():
    sp = _system_prompt(style="日式轻小说")
    assert "日式轻小说" in sp and "文风基调" in sp
    np = _narrate_prompt(style="日式轻小说")
    assert "日式轻小说" in np


def test_neutral_prompt_has_no_leftover_placeholder():
    sp = _system_prompt(style="")
    assert "__STYLE__" not in sp and "文风基调" not in sp
    np = _narrate_prompt(style="")
    assert "__STYLE__" not in np


def test_env_reads_style(monkeypatch):
    monkeypatch.setenv("RPG_NARRATION_STYLE", "冷硬派侦探")
    settings.reset_from_env()
    assert settings.get_style() == "冷硬派侦探"


def test_style_ooc_command_sets_and_clears():
    from app.play import dispatch_ooc
    out = []
    dispatch_ooc("/style 日式轻小说", engine=None, out=out.append, compare_mode=[False])
    assert settings.get_style() == "日式轻小说"
    dispatch_ooc("/style clear", engine=None, out=out.append, compare_mode=[False])
    assert settings.get_style() == ""
