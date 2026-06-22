# engine/log.py
import logging
import os
import sys

_ROOT = "rpg"

def configure_logging():
    """Configure the 'rpg' logger from env. RPG_LOG_LEVEL wins; else RPG_DEBUG → DEBUG; else WARNING."""
    level_name = os.environ.get("RPG_LOG_LEVEL")
    if not level_name:
        level_name = "DEBUG" if os.environ.get("RPG_DEBUG") else "WARNING"
    level = getattr(logging, level_name.upper(), logging.WARNING)
    root = logging.getLogger(_ROOT)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
    root.setLevel(level)
    root.propagate = False
    return root

def get_logger(name):
    return logging.getLogger(f"{_ROOT}.{name}")
