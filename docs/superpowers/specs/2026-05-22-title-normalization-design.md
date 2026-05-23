# Title normalization — design

**Date:** 2026-05-22
**Status:** approach approved (pending spec review); ready for implementation
**Workstream:** 2 of 4 (normalization) — see [[post-import-cleanup-workstreams]]

## Goal

Make display titles read and sort correctly, **display-only** (recomputing match
keys + merging duplicates is workstream 3). Two complementary parts: deterministic
junk removal, and authoritative name lookup for casing — we do **not** guess
capitalization (per user: "don't just change the names, look them up somehow").
Durable pipeline logic + extensible tables, not one-time patches
([[cleanup-fixes-must-be-general]]).

Concrete issues observed: `(English) Pokémon FireRed Version` sorts to the top;
`"Edna & Harvey" Bundle` leading quotes; lowercase articles after a colon
("Ai: the Somnium Files", "…: the Breakout"); platform-edition suffixes
("…Nintendo Switch 2 Edition").

## Part A — deterministic `clean_title` rules (non-name junk)

Extend `models.clean_title` (used at import for new games; applied to existing
rows via a reclean migration). Removes things that are clearly *not* part of the
name — no casing/word guessing:

- Strip a **leading** region/language parenthetical: `(English) `, `(USA) `,
  `(Europe) `, etc. (extensible `LEADING_TAG` set). (Today only the trailing
  platform paren is stripped.)
- Strip surrounding/stray straight quotes: `"Edna & Harvey" Bundle` → `Edna &
  Harvey Bundle`.
- Strip known platform-edition suffixes via an extensible
  `KNOWN_EDITION_SUFFIXES` table: `Nintendo Switch 2 Edition`, `Nintendo Switch
  Edition` (and the leading `:`/`-`/space joining them).
- Keep existing behavior: trademark removal, trailing platform paren,
  `smart_title_case` (which only fires on ALL-CAPS titles).

No algorithmic re-casing of normal-case titles — casing comes from Part B.

## Part B — canonical title via IGDB (the casing fix, authoritative)

Reuse the IGDB pipeline (`fetch_covers.get_access_token` + `search_game`). On a
**confident (strict) match**, adopt IGDB's official `name` as the display title
(e.g. "Ai: the Somnium Files" → IGDB "AI: The Somnium Files"; "…: the Breakout"
→ IGDB's official casing). `search_game` already requests `name`; extend it to
return the canonical name alongside the cover, or add a parallel lookup.

- **Strict match only** (normalized equality/containment, no loose fallback) so a
  title is never renamed to a wrong game.
- **Miss → keep** the Part-A-cleaned existing title (do not guess).
- Opt-in flag (e.g. `fetch_covers.py --canonical-titles`); needs Twitch/IGDB
  creds (same as covers); re-runnable and idempotent.

Most canonical-name changes are casing/punctuation, which `normalize_title`
already strips — so adopting them rarely changes the match key. The big match-key
changes (suffix stripping) and all merging are workstream 3.

## Application

1. Reclean migration: recompute `clean_title` for every game and update the
   display `title`. Does **not** touch `normalized_title` (no UNIQUE collisions,
   no merges here). `--dry-run`-able; idempotent.
2. Run the IGDB canonical-title pass to adopt official names on confident matches.

## Deferred to workstream 3 (dedup)

Recompute `normalized_title` with the improved rules; the collisions it surfaces
(e.g. Fantasy Life i 633/794 once the suffix is stripped) are the duplicates to
merge. Build merge machinery + an alias table for non-colliding dupes (Disco
Elysium "the Final Cut", Don't Starve editions).

## Testing

- Pure `clean_title` rules unit-tested: leading region tag stripped; surrounding
  quotes stripped; known edition suffix stripped; stylized/normal casing left
  unchanged; trailing platform paren still stripped.
- IGDB canonical pass is live (not unit-tested), reuses strict matching; the
  `search_game` canonical-name return is covered by a parse-level test if pure.
- Existing 48 tests stay green.

## Constraints

- Public repo: never commit `games.db`, `config.json`, `.igdb_token.json`, etc.
- Conventional commit style, no co-author trailer.
- Display-only: never recompute `normalized_title` or merge here.
