"""Read-only summaries for explicitly supplied genesis sources."""
from __future__ import annotations

from app.engine import resolve_genesis_spec
from loop.genesis_spec import missing_required


def preview_genesis_sources(*, pitch="", blueprint_path=None, world_book_path=None,
                            card_path=None, card_as="protagonist") -> dict:
    """Resolve the offline-safe subset of genesis inputs without opening a campaign.

    Passing ``None`` as the provider deliberately preserves only structural
    imports (for example a character-card name/description).  A world book is
    reported as requiring a provider rather than guessed from its source text.
    """
    spec = resolve_genesis_spec(
        None,
        pitch=pitch,
        blueprint_path=blueprint_path,
        world_book_path=world_book_path,
        card_path=card_path,
        card_as=card_as,
    )
    return {
        "parts": sorted(spec),
        "missing_required": missing_required(spec),
        "counts": {key: len(spec.get(key, []))
                   for key in ("regions", "factions", "npcs", "threads")},
        "world_book_note": (
            "世界书条目在离线预览中不会翻译；需要 provider。"
            if world_book_path else None
        ),
    }
