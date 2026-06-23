"""Translate SillyTavern world-books / character cards into our GenesisSpec.

The LLM does the translation (free-text ST entries -> our structured parts),
validated/repaired against the spec shape. With provider=None we still extract
what is structurally unambiguous (a card's name/description) so offline/tests
work without a model. Never raises — returns the best spec it can.
"""
from __future__ import annotations

from loop.genesis_spec import merge, normalize
from llm.structured import complete_structured
from engine.log import get_logger

log = get_logger("loop.import_st")

_SYSTEM = ("你是设定转换器：把酒馆(SillyTavern)世界书/角色卡翻译成游戏引擎的"
           "结构化 genesis spec，只返回严格符合字段规范的 JSON，故事文本用中文。")


def _card_data(card) -> dict:
    """SillyTavern V2 cards nest fields under `data`; V1 puts them at top level."""
    if not isinstance(card, dict):
        return {}
    return card["data"] if isinstance(card.get("data"), dict) else card


def _card_to_protagonist(card) -> dict:
    d = _card_data(card)
    out = {}
    name = (d.get("name") or "").strip()
    origin = (d.get("description") or d.get("personality") or "").strip()
    if name:
        out["name"] = name
    if origin:
        out["origin"] = origin
    return out


def _validate_spec_shape(obj) -> list:
    # normalize() is tolerant; we only require a JSON object here.
    if not isinstance(obj, dict):
        return ["response must be a JSON object"]
    return []


def convert_sillytavern(provider, *, world_book=None, character_card=None,
                        card_as: str = "protagonist") -> dict:
    spec: dict = {}

    # 1. Character card -> protagonist (default) or npc. Structural, no LLM.
    if character_card is not None:
        prot = _card_to_protagonist(character_card)
        if prot:
            if card_as == "npc":
                d = _card_data(character_card)
                # Fold the name into the sketch so the NPC's identity survives.
                sketch = "，".join(p for p in (prot.get("name"), prot.get("origin")) if p)
                spec = merge(spec, {"npcs": [{
                    "sketch": sketch,
                    "goal": (d.get("scenario") or "").strip() or "（未定）",
                    "secret": "（来自导入角色卡，待补充）",
                }]})
            else:
                spec = merge(spec, {"protagonist": prot})

    # 2. World-book -> world/factions/npcs/threads via LLM translation.
    if world_book is not None:
        entries = []
        wb_entries = world_book.get("entries") if isinstance(world_book, dict) else None
        if isinstance(wb_entries, dict):
            for v in wb_entries.values():
                if isinstance(v, dict) and isinstance(v.get("content"), str) and v["content"].strip():
                    entries.append(v["content"].strip())
        if entries and provider is not None:
            user = (
                "下面是酒馆世界书的条目内容，请翻译为我们的 genesis spec JSON。\n"
                "可包含字段：world_premise{genre,tone,world_name,central_conflict}、"
                "factions[{name,motivation}]、npcs[{sketch,goal,secret}]、"
                "threads[{about,description,trigger,secret,bound}]。\n"
                "只返回 JSON 对象，省略无法从条目推断的字段。\n\n"
                "世界书条目：\n- " + "\n- ".join(entries)
            )
            obj, errors = complete_structured(
                provider, system=_SYSTEM, user=user,
                validate=_validate_spec_shape, max_repairs=2,
                log_label="import_sillytavern")
            if not errors and isinstance(obj, dict):
                spec = merge(spec, normalize(obj))
            else:
                log.warning("convert_sillytavern: world-book translation failed (%s)",
                            "; ".join(errors) if errors else "no object")

    return normalize(spec)
