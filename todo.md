# Game Tracker - TODO

## Immediate Priority: Reduce Onboarding Friction

Adding games one at a time is the main pain point before sharing the project publicly.

---

### 1. Batch Add API Endpoint
- **Status:** Not started
- **Endpoint:** `POST /api/games/batch`
- **Request:** `{ "games": [{"title": "...", "cover_url": "...", "platforms": [...]}] }`
- **Response:** `{ "added": N, "skipped": N, "results": [{title, status, game_id}] }`
- **Notes:** This is a shared backend for both multi-select add (feature #2) and Chrome extension bulk add (feature #4). Build this first since both depend on it. Use same logic as single game create, return per-game status (added/exists/error).

### 2. CSV Import/Export
- **Status:** Not started
- **Export endpoint:** `GET /api/data/export` — returns CSV with headers: `title, status, rating, priority, platforms, notes`. Use Content-Disposition header for browser download.
- **Import endpoint:** `POST /api/data/import` — accepts CSV file upload (multipart/form-data). Minimum required column: `title`. Optional: `status`, `platforms` (comma-separated). Returns `{imported: N, skipped: N, errors: [...]}`. Use existing `normalize_title()` for deduplication.
- **UI location:** New "Data Management" section in Settings page, placed after the Cover Art section. The settings page already has a clean modular pattern with real-time status updates and background task polling (used for cover fetch) — follow that same pattern for import progress.
- **UI elements:** "Export Library" button triggering download, "Import from CSV" button with file picker, progress/results display, format help text.
- **Files to modify:** `app.py` (2 new endpoints), `templates/settings.html` (new section)
- **Notes:** `import_data.py` already handles CSV and PSPrices HTML parsing — reference its logic but build proper API endpoints rather than exposing the script directly.

### 3. Multi-Select Add Interface
- **Status:** Not started
- **Depends on:** Batch Add API (#1)
- **Changes to Add Game modal (`templates/base.html`):**
  - Change IGDB search results from click-to-select to checkboxes
  - Add a "selected games queue" area below search showing checked games
  - Add "Add X Games" button (disabled when queue is empty)
  - Keep existing single "Add Game" button for current one-at-a-time flow
- **Use case:** "Let me add all the Final Fantasy games" — search IGDB, check boxes on multiple results, add all at once.
- **Notes:** The existing IGDB search-as-you-type in the Add Game modal is the foundation. This extends it with multi-select capability.

### 4. Chrome Extension Bulk Add (PlayStation Library)
- **Status:** Not started
- **Depends on:** Batch Add API (#1)
- **Target URL:** `https://library.playstation.com/recently-purchased`
- **content.js changes:**
  - `detectPlayStationLibraryGames()` — detect if on `library.playstation.com`, scrape all game titles, return array of `{title, coverUrl}`
  - `showBulkAddUI()` — floating panel with checkboxes per game, "Select All"/"Deselect All", "Add X Games" button, progress indicator
- **background.js changes:** `bulkAddGames(games)` calling `/api/games/batch`
- **popup.html/popup.js changes:** "Scan Page for Games" button that messages content script, shows count of found games, "Add All" option
- **manifest.json:** May need `library.playstation.com` host permission
- **Open question:** Need to inspect the actual page structure of `library.playstation.com/recently-purchased` to identify game card selectors, title text, cover image elements.
- **Notes:** Extension already supports individual add on PlayStation, Xbox, Steam, Nintendo, GOG, Humble Bundle store pages with IGDB search-as-you-type in its dialog.

---

## Polish & Refinements

### 5. Series: Sort by Original Release Date
- **Status:** Partially implemented
- **Issue:** Remasters sort by remaster date, not original game date. Example: "Tales of Symphonia Remastered" should sort by when Tales of Symphonia originally released.
- **Current approach:** Strips remaster/remake suffixes before searching IGDB. May still have edge cases.

### 6. Series: Mini-Boxart Thumbnails
- **Status:** Not started
- **Request:** Replace series numbers on the Kanban view with small cover art thumbnails.

### 7. Alphabet Navigation Bar: Series-Aware Scrolling
- **Status:** Partially implemented
- **Issue:** The letter bar should navigate by series name, not individual game name, when a game belongs to a series. Uses `data-sort-name` attribute on game cards. May need further refinement — clicking "M" was jumping to Final Fantasy row because of "Monster of the Deep: Final Fantasy XV".

---

## Sharing & Distribution

### 8. GitHub / Open Source Release
- **Status:** Not started (polish first)
- **Needs:** Good README, screenshots, maybe a short demo video
- **Priority order:**
  1. Finish bulk add features (#1-4) to reduce onboarding friction
  2. Clean up for release
  3. Post to r/patientgamers, r/gaming, r/gamedev, Hacker News (Show HN), indie game tracking communities
- **Alternative angle:** A blog post or video about building a game tracker with AI in 8 hours — the process might get more traction than the app itself.

### 9. Free Hosting Deployment
- **Status:** Not started
- **Options:** Render free tier, Fly.io free allowance, Railway starter, PythonAnywhere free tier
- **Cost:** $0-5/month for the basic Flask + SQLite app
- **Note:** If hosting publicly, will need user accounts and authentication. Local-first approach sidesteps this but limits audience to technical users.

---

## Long-Term Vision

### 10. AI Chat — "What Should I Play?"
- **Status:** Not started — build only if traction warrants it
- **Vision:** Conversational AI that helps decide what to play next, going beyond the metric-based recommendation engine. Uses the user's full library, ratings, and play history as context.
- **Architecture:** "Bring your own Claude API key" model
  - Free tier: existing weighted scoring recommendation engine
  - Premium tier: user supplies their own Claude API key to enable AI chat
  - Advantages: zero AI hosting cost, no rate limiting, privacy (conversations go directly to Anthropic), scales infinitely
- **Security concern:** If storing API keys, encrypt at rest, never log them
- **Strategic advice from prior session:** "Don't build the AI chat feature yet. Share what you have. If people actually use it and ask for features, then you have signal worth investing in."

---

## Completed

- [x] IGDB search in Add Game modal (search-as-you-type, auto-fills title and cover)
- [x] "Needs Rating" section on What to Play page (shows non-backlog games without ratings)
- [x] Series page with Kanban drag-and-drop, auto-populated 52 series with 204 games
- [x] Series suggestions based on game title patterns
- [x] "Show Missing Games" checkbox using IGDB franchises/collections API
- [x] Alphabet navigation bar (fixed left edge, scroll tracking, series-aware via data-sort-name)
- [x] "Parked" status for sidelined games
- [x] Rating system: Hate it / Meh / Like it / Love it
- [x] Status/Platform checklist filter dropdowns
- [x] Physical game disc icon on cards
- [x] 100% completion status
- [x] Title normalization (ALL CAPS, platform suffixes, trademark symbols)
- [x] PS4 vs PS5 visual distinction (white-on-blue vs black-on-white tags)
- [x] Chrome extension (individual add on PlayStation, Xbox, Steam, Nintendo, GOG, Humble)
