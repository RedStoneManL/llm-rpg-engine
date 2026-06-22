# engine/oracle.py
import hashlib
import json
import random
from pathlib import Path

from engine.log import get_logger

log = get_logger("oracle")

_ORACLE_DIR = Path(__file__).resolve().parent.parent / "data" / "oracles"

class Oracle:
    """Seeded, deterministic RNG so director rolls are reproducible (rewind-safe)."""
    def __init__(self, seed):
        self._rng = random.Random(seed)
        self.seed = seed

    def d100(self):
        return self._rng.randint(1, 100)

    def chance(self, p):
        return self._rng.random() < p

    def pick(self, items):
        return items[self._rng.randrange(len(items))]

    def draw(self, entries):
        """Weighted draw from [{'weight': w, ...}, ...]."""
        weights = [max(0.0, float(e.get("weight", 1))) for e in entries]
        chosen = self._rng.choices(entries, weights=weights, k=1)[0]
        log.debug("draw from %d entries → %s", len(entries), chosen.get("name", chosen))
        return chosen

    def random(self):
        return self._rng.random()

    def randint(self, a, b):
        return self._rng.randint(a, b)

def load_table(name, genre=None):
    """Load data/oracles/<genre>/<name>.json, falling back to default/."""
    for sub in ([genre] if genre else []) + ["default"]:
        p = _ORACLE_DIR / sub / f"{name}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"oracle table not found: {name} (genre={genre})")

def scene_seed(campaign_seed, scene_ordinal, salt=0):
    """Deterministic per-scene seed → reproducible rolls; salt to perturb (--reroll)."""
    h = hashlib.sha256(f"{campaign_seed}:{scene_ordinal}:{salt}".encode()).hexdigest()
    return int(h[:12], 16)
