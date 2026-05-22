# Library Views + Whole-App Reskin — Design

**Date:** 2026-05-22
**Status:** Approved (pending written-spec review)
**Sub-project:** 1 of 2 (the Playwright library scraping + PSPrices sync is a separate, later spec)

## Summary

Add five switchable **library views** ("modes") to the Game Tracker and reskin the whole
app around them. Modes are: **All**, **Modern consoles**, **Legacy**, **PC**, and **Physical**.
The tracker page gets a stats hero with the mode switcher right-aligned opposite the stats, a
filter row beneath it, and the existing cover grid. A new persistent app shell (top bar) wraps
every page so the look is consistent. The user's last-used view is remembered client-side.

## Goals

- Let the user slice their library by **platform era / format** without legacy or PC titles
  polluting their current collection (the explicit motivation: *"I didn't want them to mix"*).
- Make the five views data-driven so future imports self-sort into the correct mode.
- A cohesive, modern reskin applied across all pages — not a logic rewrite.
- Remember the last-used view + filters across reloads.

## Non-goals (explicitly out of scope for this spec)

- Playwright scraping of Nintendo / Microsoft / Sony libraries and PSPrices syncing — **separate spec.**
- Actually importing legacy / PC games. Those modes ship as working-but-empty scaffolding;
  they populate when the scraping sub-project lands.
- User accounts / multi-user / auth (remains a local single-user app).
- Pre-existing advisory cleanups (`eval(onclick)` in `series.html`, `app.run(debug=True)`) —
  tracked separately; may be folded into reskin touch-ups but are not requirements here.

## The five views (modes)

| Mode | Definition | Today's count |
|------|-----------|---------------|
| **All** | Every game. The only place legacy + current intentionally mix. | 598 |
| **Modern consoles** | Games on a platform categorized `modern_console` (PS4/PS5, Switch 1 & 2, Xbox One/Series). | 598 |
| **Legacy** | Games on a platform categorized `legacy_console` (PS3/360/Wii era & older, older handhelds, retro). | 0 |
| **PC** | Games on a platform categorized `pc` (Steam/GOG/Epic/etc.). | 0 |
| **Physical** | Games carrying the existing `Physical` tag. Cross-cuts all eras. | 18 |

**Era boundary (decided):** last two console generations are "Modern"; everything older is
"Legacy." This keeps all 598 current games in Modern, justified by strong
last-gen→current-gen back-compat. A game owned on both a modern and a legacy platform appears
in *both* Modern and Legacy; a game owned **only** on legacy platforms never appears in Modern
(this is what keeps legacy out of the current collection).

## Data model changes

Add one column to the `platforms` table:

```sql
ALTER TABLE platforms ADD COLUMN category TEXT NOT NULL DEFAULT 'modern_console';
-- categories: 'modern_console' | 'legacy_console' | 'pc'
```

Backfill via `migrate_db()` (idempotent, follows the existing migration pattern in `models.py`):

- Default `modern_console` covers all current console platforms (Switch, PS4, PS5, Xbox).
- `UPDATE platforms SET category='pc' WHERE short_name='PC';`
- A known-legacy short_name set (PS3, PS2, PS1, X360, XBOX, Wii, WiiU, GC, N64, SNES, NES,
  PSP, Vita, 3DS, DS, GBA, etc.) maps to `legacy_console` if/when those rows exist, so the
  later import can rely on it.

`Physical` stays a tag (no schema change). No other tables change.

## Mode → query logic

A game's mode membership derives from its platforms' `category` plus the `Physical` tag:

- **All** → no filter.
- **Modern** → game has ≥1 platform with `category='modern_console'`.
- **Legacy** → game has ≥1 platform with `category='legacy_console'`.
- **PC** → game has ≥1 platform with `category='pc'`.
- **Physical** → game has the `Physical` tag.

**Implementation approach:** keep the current **client-side filtering** pattern (the templates
already load the game list once and filter in JS for status/platform). The `/api/games`
serializer is extended so each game carries its platform `category` values and a `physical`
boolean; the client computes mode membership and combines it with the existing Status / Platform
/ Rating / Sort filters. This gives instant mode switching with no extra requests. (Server-side
`?mode=` filtering is a viable alternative if the payload ever gets large; documented but not chosen.)

## UI / Information architecture

**Whole-app shell (every page):** a persistent top bar — `🎮 Game Tracker` · Library · Series ·
What to Play · Settings · (right) Search · + Add Game. The active page is underlined with the accent color.

**Tracker (Library) page, top to bottom:**

1. **Stats hero** (gradient band): left-anchored stats — games, completed, playing, backlog, % done.
2. **Mode switcher**: right-aligned, opposite the stats — segmented control of the five modes,
   each showing a live count. Empty modes (Legacy/PC) are dimmed but clickable.
3. **Filter row**: Status / Platform / Rating / Sort controls; search on the right. Filters apply
   *within* the active mode.
4. **Grid + alphabet rail**: existing cover grid (physical-disc indicator, click-to-detail) with
   the existing left alphabet quick-nav.

**Empty state:** Legacy/PC modes with no games show a friendly message
("Nothing here yet — these fill in when you import your PC / older-console libraries.").

**Other pages** (Series, What to Play / recommendations, Settings): keep their functionality,
adopt the new shell + visual system. Series Kanban, recommendation engine, and settings logic unchanged.

## View persistence

Persist UI view state in **`localStorage`** (not cookies — avoids sending UI state on every
request, no backend change):

- Stored: `{ mode, statusFilter, platformFilter, ratingFilter, sort }`.
- Restored on page load; **first-ever load defaults to `All`**.
- **Not** stored: the search box (transient).
- Self-healing: if a stored mode/filter is invalid (e.g., schema changed), fall back to defaults.

## Visual design system (reskin)

- **Theme:** dark, evolved from the current palette. Accent **`#6c5ce7`** (violet).
- **Surfaces:** layered greys (`#141414` base, `#1c1c1c` bars, `#202020` cards), `#2e2e2e` borders.
- **Hero:** subtle violet→dark gradient.
- **Components:** segmented control (modes), pill/dropdown filters, rounded cover cards with the
  pink-dot physical indicator, consistent spacing scale and type ramp.
- Implementation continues with Tailwind utility classes in templates (current approach); shared
  shell + tokens centralized in `templates/base.html` so all pages inherit them.

## Affected components

| File | Change |
|------|--------|
| `models.py` | Add `category` to `platforms` schema + `init_db` seed; backfill in `migrate_db()`. |
| `app.py` | `/api/games` serializer includes platform `category` + `physical` boolean; `/api/stats` feeds the hero (already exists). |
| `templates/base.html` | New persistent app-shell top bar; centralized visual tokens. |
| `templates/index.html` | Stats hero, right-aligned mode switcher, filter row, mode + localStorage logic, empty states. |
| `templates/series.html`, `templates/recommendations.html`, `templates/settings.html` | Adopt new shell + visual system. |
| `import_data.py` | Set `category` when creating platform rows (so later imports self-sort). |

## Edge cases & behavior

- Game on both modern + legacy platforms → appears in both Modern and Legacy (intended).
- Switching modes preserves the active Status/Rating/Sort filters; Platform filter options are
  scoped to the platforms present in the active mode.
- Counts on mode buttons reflect post-era filtering but ignore the in-mode filters (they show
  "how many games this mode contains," not the filtered subset).
- A physical legacy game shows in both Physical and Legacy.

## Testing

- **Migration:** on a copy of `games.db`, run `migrate_db()`; assert `category` exists, PC→`pc`,
  consoles→`modern_console`, and that re-running is a no-op (idempotent).
- **Mode logic (unit):** seed games across categories + the Physical tag; assert each mode
  returns the expected set, including the both-modes overlap case.
- **Persistence (manual):** set a mode + filters, reload, confirm restoration; clear
  localStorage, confirm default `All`.
- **Visual (manual):** all four pages render with the new shell; empty Legacy/PC states show;
  counts match the DB.
- **Regression (manual):** add/edit/delete game, series Kanban, recommendations, cover fetch,
  alphabet nav still work.

## Open questions

None blocking. Stat tiles, mode order, and exact palette can be tuned during implementation.
```
