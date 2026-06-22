import logging
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _hermetic_rpg_env(monkeypatch):
    """Tests must not depend on ambient RPG_* env vars (RPG_EMBEDDER would
    silently turn on the semantic recall path; RPG_DEBUG would change log
    levels). Clear them; tests that need them set do so explicitly."""
    for var in ("RPG_EMBEDDER", "RPG_DEBUG", "RPG_LOG_LEVEL", "RPG_HOME"):
        monkeypatch.delenv(var, raising=False)
    # The shared "rpg" logger is process-global; engine.log.configure_logging()
    # sets propagate=False (+ adds a StreamHandler), which leaks across tests and
    # breaks caplog (records stop at "rpg" and never reach pytest's root handler).
    # Reset to a clean, capturable state before each test; restore after.
    rpg = logging.getLogger("rpg")
    saved = (rpg.level, rpg.propagate, rpg.handlers[:])
    rpg.handlers.clear()
    rpg.propagate = True
    rpg.setLevel(logging.WARNING)
    yield
    rpg.setLevel(saved[0])
    rpg.propagate = saved[1]
    rpg.handlers[:] = saved[2]


@pytest.fixture
def campaign(tmp_path) -> Path:
    d = tmp_path / "camp"
    (d / "projections").mkdir(parents=True)
    return d
