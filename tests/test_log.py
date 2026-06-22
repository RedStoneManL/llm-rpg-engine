import logging
from engine.log import get_logger, configure_logging

def test_get_logger_namespaced():
    assert get_logger("store").name == "rpg.store"

def test_debug_env_enables_debug_level(monkeypatch):
    monkeypatch.setenv("RPG_DEBUG", "1")
    monkeypatch.delenv("RPG_LOG_LEVEL", raising=False)
    root = configure_logging()
    assert root.level == logging.DEBUG

def test_default_is_quiet(monkeypatch):
    monkeypatch.delenv("RPG_DEBUG", raising=False)
    monkeypatch.delenv("RPG_LOG_LEVEL", raising=False)
    root = configure_logging()
    assert root.level == logging.WARNING

def test_log_level_overrides(monkeypatch):
    monkeypatch.setenv("RPG_LOG_LEVEL", "INFO")
    assert configure_logging().level == logging.INFO

def test_logger_emits_through_caplog(caplog):
    caplog.set_level(logging.DEBUG, logger="rpg")
    get_logger("demo").debug("hello %s", "world")
    assert any("hello world" in r.message for r in caplog.records)
