"""CodexSystem — world setting stored as prose text blocks (世界设定典).

Holds a handful of world-level设定 entries — 编年史 (world history), 实力等级
(power-tier system), 势力志, 地理志, 神话/信仰 … — each a free-text block. Authored
once at genesis by gen_codex (seeded by the world dimension oracles); the narrator
never writes to it (no commit section).

v1 recall (RAG deferred): a compact INDEX is injected in the STABLE context layer so
the narrator always knows which entries exist, and the read-only `codex_query` POV
tool pulls a full block on demand (progressive disclosure).

Events:
  codex_entry_added → append/replace {id, kind, title, body}
State:
  world["systems"]["codex"] = {"entries": [{"id","kind","title","body"}, ...]}
"""
from __future__ import annotations

from kernel.contextsystem import ContextSystem, Fragment
from engine.log import get_logger

log = get_logger("systems.codex")


class CodexSystem(ContextSystem):
    name = "codex"

    def event_types(self) -> set[str]:
        return {"codex_entry_added"}

    def commit_sections(self) -> set[str]:
        return set()  # harness-authored only; the narrator never writes the codex

    def requires(self) -> set[str]:
        return set()

    def empty_state(self) -> dict:
        return {"entries": []}

    # --- projection -------------------------------------------------------
    def apply(self, world: dict, event: dict) -> None:
        if event.get("type") != "codex_entry_added":
            return
        d = event.get("deltas", {}) or {}
        eid = d.get("id")
        if not eid:
            return
        entries = world["systems"]["codex"].setdefault("entries", [])
        entry = {
            "id": eid,
            "kind": d.get("kind", ""),
            "title": d.get("title", ""),
            "body": d.get("body", ""),
        }
        for i, e in enumerate(entries):
            if e.get("id") == eid:   # re-add by same id replaces (idempotent replay-safe)
                entries[i] = entry
                return
        entries.append(entry)

    # --- read path: a compact index in the stable layer -------------------
    def inject(self, scene: dict, world: dict) -> Fragment | None:
        entries = (world.get("systems", {}).get("codex", {}) or {}).get("entries", [])
        if not entries:
            return None
        lines = []
        for e in entries:
            kind = e.get("kind", "")
            title = e.get("title", "") or e.get("id", "")
            tag = f"[{kind}] " if kind else ""
            lines.append(f"  - {tag}{title}")
        text = ("【世界设定典·索引】（用 codex_query 工具按 标题/类别 查看全文）\n"
                + "\n".join(lines))
        return Fragment(system="codex", layer="stable", text=text)


def codex_entries(world: dict) -> list[dict]:
    """Helper: the list of codex entries (or [])."""
    return (world.get("systems", {}).get("codex", {}) or {}).get("entries", []) or []
