"""Player-facing recall of visible facts and verifiable original records."""
from __future__ import annotations

import re

from context.access import pov_world
from kernel.recall import recall as kernel_recall


def player_recall(registry, world: dict, scene: dict, query: str, *, limit: int = 5) -> list[str]:
    """Search only the current protagonist's redacted POV and return display text.

    ``kernel_recall`` fans out through registered systems.  Give it the same
    redacted projection that normal player context uses, then keep only bounded
    strings for presentation.  The function is read-only: it creates no event,
    receipt, transcript, or discovery grant.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    if type(limit) is not int or limit < 1:
        raise ValueError("recall limit must be a positive integer")
    visible_world = pov_world(
        world, scene, pov=scene.get("protagonist"), redact_facts=True,
    )
    return [
        hit.text for hit in kernel_recall(registry, query.strip(), visible_world, k=limit)
        if isinstance(getattr(hit, "text", None), str)
    ][:limit]


_DAY = r"(?:第([0-9一二两三四五六七八九十]+)天|今天|昨天)"
_RECEIPT_QUESTION = re.compile(
    rf"(?P<when>{_DAY})?我(?:曾经)?(?:对|跟|向|给)(?P<who>[^，。！？？：:\s]{{1,64}}?)"
    r"(?:都)?(?:说|发)(?:了|过)?(?:什么|哪些话|什么话)[？?。]?"
)
_QUOTE_QUESTION = re.compile(
    r"(?:我(?:是)?(?:在)?哪(?:一)?天|哪(?:一)?天我)(?:对|向|跟|给)(?P<who>[^，。！？：:\s]{1,64}?)"
    r"(?:说|发)(?:了|过)?[“「](?P<words>.+?)[”」][？?。]?"
)
_KINDS = {"玩家输入":"input", "通信收据":"receipt", "公开旁白":"narration", "现场观察":"observation"}


def _game_day(value, today):
    if value in {"今天", "昨天"}:
        return today if value == "今天" else today - 1
    value = value.removeprefix("第").removesuffix("天")
    if value.isascii() and value.isdecimal():
        return int(value)
    digits = "零一二三四五六七八九"
    value = value.replace("两", "二")
    if len(value) == 1 and value in digits:
        return digits.index(value)
    if re.fullmatch("[一二三四五六七八九]?十[一二三四五六七八九]?", value):
        tens, ones = value.split("十")
        return (digits.index(tens) if tens else 1) * 10 + (digits.index(ones) if ones else 0)
    raise ValueError("请使用正整数游戏日，例如 日期=2")


def source_question(text):
    """Only unambiguous out-of-character requests take the read-only path."""
    text = text.strip()
    if text.startswith(("回忆：", "回忆:")):
        return text if text[3:].strip() else None
    return text if _RECEIPT_QUESTION.fullmatch(text) or _QUOTE_QUESTION.fullmatch(text) else None


def _source_question(query, today):
    q = query.strip()
    cursor = {}
    if q.split() and q.split()[-1].startswith("续页="):
        token = q.split()[-1]
        cursor["before"] = token.removeprefix("续页=")
        if not cursor["before"]:
            raise ValueError("请填写续页引用")
        q = q[:-len(token)].strip()
    question = _RECEIPT_QUESTION.fullmatch(q)
    if question:
        when = question["when"]
        return "", {**cursor, "recipient":question["who"], "kind":"receipt",
                    **({"day":_game_day(when, today)} if when else {})}
    quote = _QUOTE_QUESTION.fullmatch(q)
    if quote:
        return quote["words"], {**cursor, "recipient":quote["who"], "kind":"receipt"}
    words, filters = [], cursor
    for token in q.split():
        name, sep, value = token.partition("=")
        if sep and name in {"日期", "收件人", "类型", "续页"}:
            if not value:
                raise ValueError(f"请填写{name}")
            if name == "日期":
                filters["day"] = _game_day(value, today)
            elif name == "类型":
                if value not in _KINDS:
                    raise ValueError("类型可选：" + "、".join(_KINDS))
                filters["kind"] = _KINDS[value]
            else:
                filters["recipient" if name == "收件人" else "before"] = value
        else:
            words.append(token)
    return (" ".join(words), filters) if filters else (q, {})


def _recipient_id(world, scene, name):
    graph = pov_world(world, scene, pov=scene.get("protagonist"))["systems"].get("ontology")
    if graph is None or graph.get_entity(name):
        return name
    day = scene.get("day") or 1
    matches = [eid for eid, person in graph.entities.items()
               if person.etype == "Person" and graph.value_at(eid, "name", day) == name]
    if len(matches) > 1:
        raise ValueError(f"可见人物有同名者，请用收件人 ID：{'、'.join(sorted(matches))}")
    return matches[0] if matches else name


def recall_with_sources(engine, scene, query, *, before=None):
    from engine.history import PlayerHistory

    reader = PlayerHistory(engine.store, scene.get("protagonist"))
    exact = query.startswith(("回忆：", "回忆:"))
    words, filters = _source_question(query[3:] if exact else query, scene.get("day") or 1)
    if filters or exact:
        if filters.get("day") == 0:
            if "昨天" not in query:
                raise ValueError("游戏日从第 1 天开始")
            return {"hits":["游戏开始前没有可核验的原文。"], "opened":[], "revision":engine.store.revision}
        if before:
            filters["before"] = before
        page = reader.locate(words, **filters, verify=True)
        # Legacy receipts predate name snapshots. Only retry against an exact
        # name visible to the current POV, retaining every other search filter.
        if not page["matches"] and "recipient" in filters and "before" not in filters:
            candidate = _recipient_id(engine.world, scene, filters["recipient"])
            if candidate != filters["recipient"]:
                page = reader.locate(words, **{**filters, "recipient":candidate}, verify=True)
        hits = []
        for item in page["opened"]:
            row = item["source"]
            recipient = f" → {row['recipient']}" if row["recipient"] else ""
            hits.append(f"第{row['day']}天 · 回合{row['turn']} · {row['kind']}{recipient}：{row['text']}")
        return {"hits": hits or ["在这些条件下找不到可核验的原文。"], "sources":page["matches"],
                "opened":page["opened"], "next_before":page["next_before"], "revision":page["revision"]}

    if before:
        raise ValueError("续页需要原来的查证条件")
    records = reader.search(words)
    hits = [f"[第{r['day']}天 · 回合{r['turn']} · {r['kind']}] {r['text'][:180]}" for r in records]
    hits.extend(player_recall(engine.registry, engine.world, scene, words))
    result = {"hits":hits[:10]}
    if records:
        result["sources"] = records
    return result
