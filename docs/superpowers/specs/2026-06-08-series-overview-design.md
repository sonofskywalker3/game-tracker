# Series overview (fanned-stack tiles) — design

## Problem
The Series tab (`/series`) is only a single-series *manager* (dropdown → drag-reorder /
add / remove / find-missing). There's no visual way to browse all series. The owner wants
a library-style grid where each series is a fanned stack of its cover art, labelled with
the series name, that expands inline to show the series' games.

## Decisions (owner-approved)
- Tile style: **fanned stack** (first ~3 covers layered like a held hand of cards).
- Contents: **only series** (loose/standalone games stay on the Library tab).
- Click behaviour: **expand inline** — the clicked tile grows to full grid width and fans
  open into a horizontal row of the series' game cards; click again to collapse. Multiple
  can be open independently.

## Routing
- `/series` → NEW `series_overview.html` (the overview).
- `/series/<int:series_id>` → existing `series.html` editor, preselecting that series.
- `/series/manage` → existing `series.html` editor with no preselection (dropdown +
  "New Series" live here).
The route change in `app.py` is Python, so the running server must be **restarted** for
`/series` to serve the new overview (templates auto-reload, routes do not — see
[[python-changes-need-server-restart]]). Restart is the assistant's job.

## Data
No new endpoint. `GET /api/series` already returns, per series: `id`, `name`,
`game_count`, and `games: [{id, title, cover_url, series_order}]` ordered by
`series_order, title`. The overview consumes this directly.

## UI — `series_overview.html` (extends base.html)
- Header: page title + a "Manage / + New Series" link → `/series/manage`.
- Grid: library-style responsive grid
  (`grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-4`).
- **Collapsed tile (one per series):**
  - Fanned stack in a fixed 3:4-ish aspect box: up to the first 3 games' covers,
    absolutely positioned; back covers rotated ~-7° / +7° and offset, front cover straight
    on top (highest z-index). Missing `cover_url` → 🎮 placeholder tile (reuse
    `.cover-placeholder`). A series with 1 game shows a single cover (no fan).
  - Below: series **name** (truncate) + **"N games"**.
  - The whole tile is clickable (toggles expand). Cursor pointer.
- **Expanded tile:** the tile gets `grid-column: 1 / -1` (full row; siblings reflow). It
  shows a header row — series name, "N games", a **Manage** link → `/series/<id>`, and a
  collapse ✕ — above a horizontal, wrapping/scrollable row of the series' game cards
  (cover + truncated title, in `series_order`). Each game card calls `openModal(gameId)`
  (defined in base.html). Clicking the header/✕ collapses back to the stack.
- Empty state when there are no series: 📚 + "No series yet" + link to `/series/manage`.
- `refreshGameList()` defined to re-fetch + re-render (base.html's `closeModal` calls it
  after a modal edit), preserving which tiles are expanded.

## State & rendering
- Fetch `/api/series` once into a module variable; `renderOverview()` builds the grid.
- Track expanded series ids in a `Set`; on (re)render, expanded tiles render in expanded
  form. Toggle updates the Set and re-renders (or toggles a class) so expansion survives a
  modal-driven refresh — mirrors the slate's expand-state handling.

## Out of scope
- No reordering/editing from the overview (that's the manage page).
- No standalone-games section. No new series-creation UI on the overview (link to manage).

## Testing / verification
- No pytest (pure view over an existing API). Verify in-browser with Playwright against a
  COPY of `games.db` on an alt port (per [[verify-ui-changes-yourself]]): the overview
  renders one tile per series with the correct names/counts; clicking a tile reveals its
  game cards; collapse works; a game card opens the modal. Report evidence before
  declaring done.

## Constraints
- Follow existing template patterns (Tailwind classes, `api` helper, `escapeHtml`,
  `openModal`, nav `{% block nav_series %}`). `ruff check` is irrelevant (no Python logic
  beyond the route split); if `app.py` changes, run `uv run ruff check app.py`.
