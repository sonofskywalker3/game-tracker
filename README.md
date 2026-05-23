# Game Tracker

A self-hosted personal game library tracker. Flask + SQLite backend, server-rendered
templates, plus a companion Chrome extension for one-click adding games from store pages.

## What this is / isn't

Game Tracker tracks the games you own and your progress through them so you can
decide **what to play or buy next**. It models **one row per playable game** —
the thing you launch and play.

It is **not** a wishlist, a purchase log, or a catalog of every edition/version
you own. Editions, regional variants, and the same game across platforms collapse
into a single entry (use the **Dedup** tool). You can bend it toward
version-tracking, but you'll be fighting the grain — it's **MIT-licensed**, so
fork it freely.

## Features

- **Library management** — track games across PlayStation, Xbox, Nintendo Switch, and PC, with
  status (backlog / playing / completed / 100% / parked), 4-tier ratings, priority, and notes.
- **IGDB integration** — search-as-you-type when adding a game; auto-fills title and cover art.
- **Series view** — Kanban board with drag-and-drop, auto-grouped franchises, "show missing games."
- **Recommendations** — a weighted scoring engine that suggests what to play next.
- **Filters & navigation** — status/platform filter dropdowns and an alphabet quick-nav bar.
- **Chrome extension** — add games directly from PlayStation, Xbox, Steam, Nintendo, GOG, and
  Humble Bundle store pages.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure IGDB / Twitch credentials (for cover art + search)
cp config.example.json config.json
# edit config.json and add your twitch_client_id / twitch_client_secret
# (create an app at https://dev.twitch.tv/console)

# 3. Initialize the database and import any starter data
python import_data.py

# 4. Run
python app.py
# open http://127.0.0.1:5000
```

## Configuration

Credentials live in `config.json` (gitignored). Copy `config.example.json` to get started.
Get a Twitch/IGDB Client ID + Secret from the [Twitch developer console](https://dev.twitch.tv/console).

## Project layout

| Path | Purpose |
|------|---------|
| `app.py` | Flask routes and API endpoints |
| `models.py` | SQLite schema, queries, title normalization |
| `config.py` | Credential loading |
| `fetch_covers.py` | IGDB cover-art fetching |
| `recommendation.py` | "What to play" scoring engine |
| `import_data.py` | CSV + PSPrices HTML importers |
| `templates/`, `static/` | Server-rendered UI |
| `chrome-extension/` | Companion browser extension |

## Notes

`config.json`, `*.db`, and the personal library CSVs are intentionally gitignored — your
credentials and library data stay local.
