# SP-A Phase 0 — IGDB capability spike findings (2026-07-06)

Goal of SP-A: auto-resolve series membership and bundle constituents at import
time (local catalog → IGDB fallback on miss → cache), so the three catalogs work
for ANY user's library. This spike answers the "first task" from the
generic-catalog-buildout plan: what does IGDB actually expose? All probes run
live against IGDB v4 with the project's Twitch creds.

## Findings

### 1. Bundle detection: `game_type = 3`
Confirmed. The Great Ace Attorney Chronicles → `game_type: 3`. The full
`game_types` enum (live): 0 Main Game, 1 DLC, 2 Expansion, 3 Bundle,
4 Standalone Expansion, 5 Mod, 6 Episode, 7 Season, 8 Remake, 9 Remaster,
10 Expanded Game, 11 Port, 12 Fork, 13 Pack/Addon, 14 Update.
(`category` is the deprecated predecessor — use `game_type` everywhere,
consistent with popular_seed.py's Phase-0 finding.)

### 2. Bundle constituents: reverse lookup only
A bundle does NOT carry its constituents as a field (`expanded_games`, `ports`,
etc. are all empty on a pure bundle). The reliable path is the reverse query:

    fields id, name, game_type; where bundles = (<bundle_igdb_id>); limit 50;

Live result for bundle 146075 (TGAA Chronicles): exactly its two constituents
(The Great Ace Attorney: Adventures, The Great Ace Attorney 2: Resolve).
Cost: ONE extra IGDB call per bundle — fine for at-import fallback, cacheable
forever in bundle_catalog.

Note the inverse also works: a constituent's `bundles` field lists the bundle
ids that contain it (Adventures → [146326, …]) — useful for "this game you're
adding is also part of a bundle you own" checks later.

### 3. Series membership: the `collections` m2m field
Games carry `collections` (expandable: `collections.name, collections.slug`).
Live example — Final Fantasy VII (port, game_type 11) belongs to THREE
collections at different granularities:

- 39 "Final Fantasy" (franchise-level)
- 5134 "Compilation of Final Fantasy VII"
- 9007 "Final Fantasy VII" (game-family)

**Open design decision for the owner:** which collection is "the series"?
The owner's series philosophy (series-grouping-franchise-level memory) wants
mainline franchise-level grouping ("Final Fantasy"), with distinct spinoff
sub-series kept separate. Candidate policies:
  a. Prefer the collection that already matches an existing local series name.
  b. Else prefer the collection with the MOST matches against the user's own
     library (franchise-level naturally wins for a big FF library).
  c. Tie-break by largest IGDB collection.
Needs a small owner conversation before building the resolver.

### 4. Remaster/port original-release dates
Bonus from the same probe family: `version_parent.first_release_date` and
`parent_game.first_release_date` are expandable in one call — ALREADY SHIPPED
in `igdb_release_dates_by_id` (series sort-by-release now uses the earliest).

## Proposed SP-A shape (next session)

1. `igdb_resolve.py` (new): `resolve_bundle(igdb_id) -> [constituent ids/names]`
   and `resolve_collections(igdb_id) -> [(id, name, slug)]`, requests with
   timeout, token via fetch_covers.get_access_token, degrade-to-None like
   hltb.py.
2. Import pipeline hook: on add/import, if the game's igdb_id has
   `game_type == 3` and no local bundle_catalog entry → resolve constituents →
   write into bundle_catalog (runtime cache = the same file the seed uses;
   ONE code path).
3. Series: after the owner picks the collection policy (see above), same
   local-catalog-first / IGDB-fallback / cache flow into series_catalog.
4. Remember the 2026-06-03 lesson: splitting a bundle parent must migrate its
   series/trait catalog entries to the constituents.
