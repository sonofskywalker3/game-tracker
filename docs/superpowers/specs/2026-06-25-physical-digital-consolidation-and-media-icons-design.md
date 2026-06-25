# Physical/Digital Consolidation + Cartridge/Disc Media Icons — Design

**Date:** 2026-06-25
**Status:** Approved direction (owner decisions captured), pre-implementation
**Topic:** Collapse the two competing physical/digital representations (the legacy per-game "Physical" tag vs. per-platform `game_platforms.format`) into a single source of truth, heal the resulting mislabels with a conservative one-time migration, and make the web's physical badge media-aware (cartridge icon for cartridge systems, disc icon for disc systems).

---

## 1. Context & motivation

A scan of *Kirby's Return to Dream Land Deluxe* reported "Switch (Digital)" although the owner owns it physically. Root cause: **two independent format representations** exist and nothing syncs them.

- The legacy per-game **"Physical" tag** (a `tags` row). The web "Physical" checkbox (`base.html:1196`, `toggleTag(... 'Physical' ...)`) toggles it, and the API's `game['physical']` field is derived from it (`app.py:196`). It is per-game and format-only-physical (no digital/both, no per-platform).
- The per-platform **`game_platforms.format`** (`physical`/`digital`/`both`), added by the 2026-06-22 scan-for-info work as "the new source of truth for display." The scan's ownership line and the per-platform Physical/Digital toggle (`base.html:884`) read/write this.

The owner ticked the *tag* checkbox; the scan reads the *per-platform format*, which stayed `digital`. The June-2022 design intended to retire the legacy flag "later" — this spec is that retirement.

### Owner decisions (2026-06-25, from brainstorming)
- **Single source of truth = `game_platforms.format`.** Re-derive the API `physical` field from it.
- **Migration is conservative:** only the **48 games carrying the "Physical" tag** are flipped to physical (NOT the ~703 single-platform digital games — most of the library is genuinely digital). Owner refines the rest by hand/scan over time.
- **Multi-platform tagged games:** set **all** owned platforms of a tagged game to physical (the tag means the whole game is owned physically).
- **Media icons:** cartridge icon for cartridge/card systems, disc icon for disc systems; replace the single disc badge on the web.
- **Scope:** web for the icon (where the badge lives); mobile already reads per-platform format so it will display correctly after consolidation — a mobile media-icon is a deferred fast-follow.

### Non-goals
- No mobile UI changes (the scanner already reads `game_platforms.format`; consolidation fixes its data).
- No deletion of existing "Physical" tag rows (left inert; already hidden from tag chips by `base.html:1202`).
- No change to the per-platform Physical/Digital toggle behavior (`base.html:884`) — it stays the editor.

---

## 2. Single source of truth

`game_platforms.format` is authoritative. Everything that today reads the "Physical" tag for format reads format instead.

- **`app.py:196`** — replace `game['physical'] = any(t['name'] == 'Physical' for t in game['tags'])` with a derivation from the game's platform formats: **physical iff any owned platform's `format` is `'physical'` or `'both'`**.
- The web "Physical" filter (`index.html:170` `match: g => !!g.physical`) is unchanged in code — it now reflects format because `g.physical` is re-derived.
- **Create path (`app.py` `POST /api/games`):** the "physical" checkbox already sets `game_platforms.format` (`app.py:254`). Stop *also* adding the "Physical" tag on create (`app.py:266-277` removed) — the tag is no longer a format source. Format alone carries it.

## 3. Retire the "Physical" tag as a control (web)

- **Remove** the standalone "Physical" tag checkbox from the edit UI (`base.html:1196-1197`, the `toggleTag(... 'Physical' ...)` control). The **per-platform Physical/Digital toggle** (`base.html:884`) becomes the only way to set format.
- Existing "Physical" tag rows stay in the DB (inert, already excluded from the tag-chip list). No destructive tag deletion.

## 4. One-time migration (idempotent, conservative)

A guarded migration in `models.py` (runs once, additive, re-runnable):

> For every game that has the "Physical" tag, set `game_platforms.format = 'physical'` on each of its owned platforms **where the current format is `'digital'` or NULL**. Leave platforms already `'physical'` or `'both'` untouched (never downgrade `'both'`).

- Idempotent: re-running changes nothing (only `digital`/NULL rows move, and only for tagged games).
- Heals *Kirby* (it is tagged) and the other 47 without hand-patching.
- Implemented as a `migrate_*` function called from the existing migration runner; covered by pytest against a temp DB (never the live DB).

## 5. Media-type lookup + `physical_media` API field

A new module-level lookup (extensible tuple/dict, per CLAUDE.md "lookup tables at module scope") maps platform `short_name` → media type:

- **`cartridge`** — Switch, Switch2, 3DS, NDS, N64, SNES, NES, GB, GBC, GBA, Genesis, Vita
- **`disc`** — PS1, PS2, PS3, PS4, PS5, OGXbox, X360, Xbox, GC, Wii, WiiU, Dreamcast, Saturn, PSP (UMD), PC

(Unmapped/mobile/subscription platforms → no media type; they never carry a physical disc/cart badge.)

The games-list API (`api_games`) gains, per game, **`physical_media`**: the sorted distinct media types of the platforms owned in `physical`/`both` format (e.g. `["cartridge"]`, `["disc"]`, `["cartridge","disc"]`, or `[]`). One small, declarative field; the lookup lives server-side (single source).

## 6. Web badge (media-aware)

`index.html` (card render, ~line 343): the current single "Physical disc icon" (shown when the Physical tag is present) is replaced by rendering **one badge icon per entry in `physical_media`** — a **cartridge SVG** for `cartridge`, the existing **disc SVG** for `disc`. A multi-format game (e.g. cartridge on Switch + disc on PS5) shows both, deduped by media type. No badge when `physical_media` is empty.

- Add a clean inline cartridge SVG next to the existing disc SVG (small, monochrome, matches the current badge styling: `bg-black/50 rounded-full` chip).

## 7. Components & boundaries

- **`models.py`** — `migrate_*` for the tagged→format reconcile (§4).
- **`app.py`** — `physical` re-derivation (§2); drop tag-add on create (§2); `PLATFORM_MEDIA` lookup + `physical_media` in `api_games` (§5).
- **`templates/base.html`** — remove the "Physical" tag checkbox (§3).
- **`templates/index.html`** — media-aware badge from `physical_media` (§6).

## 8. Testing (pytest, `uv run python -m pytest`)

- `physical` derived from format, not tag: a game with `format='physical'` and no tag is `physical=true`; a game with the tag but `format='digital'` is `physical=false` (post-migration this case won't persist, but the derivation is what's tested).
- Migration: a tagged game with a `digital` platform → `physical` after migration; a tagged game with a `both` platform stays `both`; an untagged `digital` game is untouched; multi-platform tagged game flips all owned platforms; idempotent on re-run.
- `PLATFORM_MEDIA` lookup: representative cartridge (Switch, 3DS) and disc (PS5, GC, PSP) mappings; unmapped platform → no media.
- `api_games` `physical_media`: physical-Switch game → `["cartridge"]`; physical-PS5 → `["disc"]`; mixed → `["cartridge","disc"]`; all-digital → `[]`.
- Create path no longer writes a "Physical" tag (format still set).

Web badge rendering is verified manually (no JS test harness in this project).

## 9. Risks

- **Migration moves ownership data** — mitigated by conservative scope (48 tagged games only), never downgrading `both`, idempotency, and a fresh DB backup before the live run (owner-gated, as with prior migrations).
- **Derivation flips the filter semantics** — the "Physical" filter now means "owns a physical/both copy on some platform" (format-based) instead of "tagged Physical." For the 48 tagged games this is equivalent post-migration; for any game made physical purely via the per-platform toggle (no tag) it is now *correctly* included.
- **Media mapping gaps** — a platform not in `PLATFORM_MEDIA` simply shows no media badge; extend the lookup if a new physical platform appears.
