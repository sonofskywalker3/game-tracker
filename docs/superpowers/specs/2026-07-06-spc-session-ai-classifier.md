# SP-C — Opt-in AI session-length classifier (2026-07-06)

Goal (from the generic-catalog plan, order B → A → C): after an import, games
the shipped `game_traits.default.json` doesn't cover have `session_length =
NULL`. SP-C lets the user classify that long tail with THEIR OWN Anthropic key
(the decider's key + model, `config.get_anthropic_config()`), opt-in, cached
locally so each title is ever classified once.

## Decisions

- **Classification rule** (SP-B refinement, reused verbatim): session_length
  keys on the length of the natural UNIT OF PLAY — not total game length and
  not merely whether stopping points exist. Episodic multi-hour narratives
  (Telltale TWD) = long; per-run / per-level / per-match / VN-scene = short.
- **Model**: the user's configured decider model (default claude-sonnet-4-6) —
  same convention as the decider chat; the user picked it in Settings.
- **Prompt/response contract**: games are sent as a NUMBERED list; the model
  returns a strict JSON object `{"<number>": "short"|"long"|"unknown"}`. We
  key by our own index (SP-B lesson: never match on model-echoed titles/ids).
  `unknown`/invalid values are left NULL (retryable later), never cached.
- **Writes**: `games.session_length` + `session_length_source='ai'` for rows
  still NULL at write time; each classified title is also cached into the
  per-user `game_traits.json` (seeded from the effective catalog on first
  write, minimal-diff format) so re-imports and other libraries hit the
  catalog path with no further AI cost. Manual source always locks (rows with
  a manual value are never NULL, so the NULL filter suffices).
- **Execution**: background daemon via the existing `background_tasks`
  TaskManager (mirrors UPC enrichment), batches of `TRAITS_AI_BATCH` titles,
  live N/M progress; API `GET /api/traits/ai/status` +
  `POST /api/traits/ai/run`; Settings section with count + Run button
  (hidden when nothing is unclassified); scrape summary carries
  `session_unclassified` so the post-scrape modal can nudge.
- **Never blocks anything**: no key configured → status says so, run refuses
  politely; API errors mark the task errored and leave rows NULL.
