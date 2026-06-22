from __future__ import annotations

from kernel.registry import Registry
from kernel.contextsystem import Fragment
from engine.log import get_logger

log = get_logger("kernel.assembler")

LAYER_ORDER = ("stable", "scene", "volatile")


def assemble(registry: Registry, scene: dict, world: dict) -> list[Fragment]:
    """Gather each system's fragment, ordered stable->scene->volatile (cache-friendly)."""
    frags: list[Fragment] = []
    for s in registry.systems:
        f = s.inject(scene, world)
        if f is not None:
            frags.append(f)
    # stable sort -> intra-layer order = system registration order (cache-prefix contract)
    frags.sort(key=lambda f: LAYER_ORDER.index(f.layer) if f.layer in LAYER_ORDER else len(LAYER_ORDER))
    log.debug("assemble produced %d fragments", len(frags))
    return frags


def render(frags: list[Fragment]) -> str:
    """Flatten fragments to context text, grouped by layer, affordances appended."""
    out: list[str] = []
    last_layer = None
    affordances: list[str] = []
    for f in frags:
        if f.layer != last_layer:
            out.append(f"## [{f.layer}]")
            last_layer = f.layer
        out.append(f.text)
        if f.affordance:
            affordances.append(f.affordance)
    if affordances:
        out.append("## [affordance · 本轮可声明]")
        out.extend(affordances)
    return "\n".join(out)
