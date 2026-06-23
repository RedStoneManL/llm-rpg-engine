"""Interactive session-zero: enforce the minimal required floor before genesis.

For each required part the spec is missing (world_premise / protagonist), prompt
the player once: a non-empty answer fills that part's minimal field, while an
empty line or a delegate token (/auto / 你来定) hands the part to the model.
Behind injected inputs/out seams (mirrors app.play.play_loop) for testability.
"""
from __future__ import annotations

from loop.genesis_spec import merge, missing_required, normalize

_DELEGATE = {"/auto", "你来定", "auto", ""}

# Required part -> (minimal field, human prompt).
_PROMPTS = {
    "world_premise": ("genre", "【世界】这是个什么样的世界？（题材/基调/一句话钩子，"
                                "或输入 /auto 让模型决定）："),
    "protagonist":   ("name", "【主角】你是谁？（至少给个名字，"
                                "或输入 /auto 让模型决定）："),
}


def run_session_zero(spec, *, inputs, out, interactive: bool = True) -> dict:
    spec = normalize(spec)
    if not interactive:
        return spec

    it = iter(inputs)
    for part in missing_required(spec):
        field, prompt = _PROMPTS[part]
        out(prompt)
        try:
            raw = next(it)
        except StopIteration:
            return spec   # input exhausted — leave the rest to the model
        line = (raw or "").strip()
        if line.lower() in _DELEGATE:
            out("（已交由模型生成）")
            continue
        spec = merge(spec, {part: {field: line}})
    return spec
