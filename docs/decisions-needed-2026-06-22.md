# Decisions — RESOLVED 2026-06-22

- D1 Streaming → **NO** (verbosity dial + modular repair are enough for now).
- D2 Re-push public repo → **DONE** (github.com:RedStoneManL/llm-rpg-engine @ 0695943, v0.1.1).
- D3 Richer world history → **NO** (central_conflict backdrop is enough).
- D4 Protagonist/customization → **EXPANDED into the next design effort** (see below).

## NEXT DESIGN EFFORT (D4, agreed) — player-definable genesis + SillyTavern import
User's vision (verbatim intent): at 开局, EVERY part should be user-definable — world frame,
map/regions, factions, NPCs, **protagonist**, threads, etc. — and if the user does NOT define a
part, the model fills it in (the current bootstrap). FUTURE: compatible with importing
**SillyTavern (酒馆) world-books (世界书)** and **character cards (角色卡)**.
This is a new subsystem (a per-part override/input layer over bootstrap + import adapters that
map SillyTavern lorebooks/character-cards into the engine's entities/lore/facts). Needs
brainstorming → spec → plan → build. NOT started.
