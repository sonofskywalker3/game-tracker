# SP2 — Per-slot Anthropic Decider Chat (The Slate)

**Date:** 2026-06-03
**Status:** Approved (brainstorming)
**Depends on:** SP1 Slate foundation (slots + `rank_candidates`), Spec C session_length
seed (now applied). This is the AI half of the Slate — see [[slate-picks-tab-feature]],
[[gamer-persona-and-backlog-psychology]], [[slate-system-must-be-generic]].

## Goal

A chat on each slot that helps the user decide **what to pin there**. It reasons over the
user's whole library + the slot's context (platforms, session bounds, streamable, free-text
`context_notes`, focus series) + the user's stated mood/energy/time-tonight, and surfaces
recommendations as **clickable, pinnable game cards**. Blocking request/response, multi-turn,
using the user's own Anthropic API key.

The user can ask cross-cutting, natural questions — e.g. *"I know there are a lot of FF15
side stories, anything I could play in the living room?"* — so the chat must SEE the entire
library, not just the slot's pre-filtered candidate list. The slot is the **target** being
filled; the chat ranges over everything but respects the slot's hard constraints in its
reasoning.

## Scope

- **Per-slot decider** (fill one empty slot). NOT a global/cross-slate concierge (deferred).
- **Library access = cached full-library snapshot** (Approach A): a compact text index of all
  games sent as a prompt-cached system block. At ~764 games (~30K tokens) this is cheaper and
  far simpler than tool-use, and gives the model total visibility. Cost ≈ 15–20¢ per
  conversation on Sonnet (cache write once ~$0.11, ~1.7¢/turn on cache reads); model choice
  dominates (Opus ~5×, Haiku ~⅓).
- **Recommendation → action = clickable suggestion cards** reusing the existing
  `gameCardHtml` tiles + pin flow. The chat advises; the user clicks Pin.
- **Key storage = `config.json` + Settings UI** (consistent with twitch/steam; `config.json`
  is gitignored). Extends the existing `/settings` page + `/api/settings`.
- **Blocking responses** for MVP (no streaming).
- **Default model = `claude-sonnet-4-6`**, configurable in settings.

## Architecture

A new focused module `decider.py` owns snapshot-building, prompt assembly (with caching),
the Anthropic call, and suggestion parsing/validation. Config + key ride the existing
`config.py`/Settings infrastructure. The API adds one slot-scoped chat endpoint. UI lives in
the picks tab, reusing existing tile components.

```
slot + conversation messages
  -> decider.build_system_prompt(conn, slot)        # [instructions][snapshot* cached] + slot ctx
  -> Anthropic Messages API (Sonnet, prompt cache)  # blocking
  -> reply text + recommended game ids
  -> validate ids against the snapshot set          # drop invented ids
  -> JSON {reply, suggestions:[game...]}  -> UI: prose + clickable game cards
  -> user clicks Pin -> existing /api/slots/<id>/pin flow
```

## Components

### 1. `decider.py` (new module)

- `build_library_snapshot(conn) -> str`
  One compact line per game for ALL games, status-tagged. Fields:
  `id · title · platforms(short_names) · session_length · series+role · status · hours_played
  · effective_time_to_beat · priority`. Deterministic (stable ordering) so it caches well.
  Reuses `slots.effective_time_to_beat_minutes` for the TTB field.

- `build_system_prompt(conn, slot) -> list[dict]`
  Returns Anthropic system content blocks, ordered stable→dynamic:
  1. **Generic decider instructions** (a backlog-decider role; how to weigh session fit,
     genre fatigue, what was just finished, time-to-beat, mood/energy/time; instruction to
     ask clarifying mood/energy/time questions when useful; instruction to recommend by
     citing game `id`s from the snapshot and to respect the slot's hard constraints).
  2. **Library snapshot block** — marked with `cache_control: {type: "ephemeral"}` (the cache
     breakpoint).
  The **slot-context** (label, platforms, session bounds, streamable_only, `context_notes`,
  focus series name) and the **conversation messages** are passed as the dynamic suffix
  (slot context as a leading user/system-style message; conversation as `messages`). The
  generic instructions contain NO hardcoded user persona — see Generic section.

- `decide(conn, slot, messages) -> dict`
  Calls the Anthropic Messages API with the cached system prompt + `messages`. Model from
  `config`. Parses the reply into `{reply: str, suggestions: [game_id]}`. The model is
  instructed to end its reply with a single
  `<suggestions>12,88</suggestions>` line (comma-separated ids; empty list allowed). The
  backend strips that line from the displayed prose, parses the ids, **validates each against
  the snapshot's id set**, drops unknowns, and resolves the survivors to full game dicts for
  the UI. Built using the **claude-api skill** at
  implementation time for current SDK usage, model ids, and prompt-caching specifics.

- Error handling (return-typed-error pattern, one pattern for this module): missing key →
  `{"error": "no_api_key"}`; `anthropic.APIError`/timeout → logged via `logging`, returns
  `{"error": "api_error", "detail": ...}`. No bare excepts; specific exceptions only.

### 2. `config.py`

Add `anthropic_api_key` and `decider_model` (default `claude-sonnet-4-6`) to
`DEFAULT_CONFIG`; add `get_anthropic_config() -> (key|None, model)`.

### 3. `app.py`

- `POST /api/slots/<int:slot_id>/chat` — body `{messages: [...]}`; loads the slot, calls
  `decider.decide`, returns `{reply, suggestions}`. Returns 400 `{"error":"no_api_key"}` when
  no key is configured.
- Extend `GET /api/settings` to include `anthropic_api_key` (masked) + `decider_model`, and
  `PUT /api/settings` to persist them.

### 4. `templates/settings.html`

Add a masked Anthropic-API-key input + a model field, matching the existing twitch/steam
fields.

### 5. UI — `templates/recommendations.html` (+ `base.html` helpers)

- Each EMPTY slot card gets a **"Help me decide"** affordance that opens a chat panel scoped
  to that slot (filled slots don't show it for MVP).
- Chat panel: a message list + input; on send, POST the full `messages` history; show a
  spinner; render the reply prose + a row of **suggestion tiles** built from the existing
  `gameCardHtml`, each with a **Pin** button wired to the existing pin flow.
- Conversation is **ephemeral** — held in frontend state, reset when the panel closes or the
  slot is filled. No DB persistence.

### 6. Dependency

`uv add anthropic`.

## Prompt caching (claude-api skill)

System blocks are ordered stable→dynamic with the cache breakpoint after the library
snapshot: `[generic instructions][snapshot ← cache_control]`. Within a conversation (5-min
cache TTL) every turn after the first reads the snapshot from cache (~10% cost). The snapshot
only changes when the library changes, so cross-session reuse re-pays one cache write
(~a dime on Sonnet). The implementation MUST follow the claude-api skill for the exact
caching API and the current model id.

## Generic / configurable (honoring slate-system-must-be-generic)

The decider's behavior is driven by **per-slot configuration**, not the owner's persona baked
into code. The generic instructions describe the decider ROLE; the user-specific signal comes
entirely from the slot's `context_notes` + config (platforms, session bounds, streamable,
focus series) and the live conversation. The owner's persona only shaped the *seeded default
slots' `context_notes`* (already in `seed_default_slots`), which any user can edit. A test
asserts no persona literal is hardcoded in the prompt builder.

## Error / edge handling

- No key configured → API returns 400; UI prompts the user to add a key in Settings.
- Anthropic API error/timeout → logged, friendly error surfaced in the chat panel; the
  conversation stays intact so the user can retry.
- Model invents a game id not in the snapshot → dropped during validation (never pins a
  phantom game).
- Empty/whitespace message → client-side guard; no API call.
- `config.json` MUST remain gitignored (verified); the key is masked in `GET /api/settings`.

## Testing (TDD; the Anthropic client is MOCKED — no live API calls, no network in tests)

1. `build_library_snapshot` — includes all games + the expected fields, compact one-line
   format, deterministic ordering (temp DB).
2. `build_system_prompt` — snapshot block carries `cache_control`; block ordering is
   instructions-then-snapshot; slot context (notes/platforms/session) is present; assert NO
   hardcoded persona string.
3. id validation — a `decide` reply citing ids absent from the snapshot drops them; valid ids
   resolve to game dicts.
4. `decide` — with a mocked Anthropic client: asserts request uses the configured model, the
   snapshot block has the cache breakpoint, and `messages` are threaded; asserts reply
   parsing (prose + suggestions).
5. `config.get_anthropic_config` — reads key/model, defaults model to `claude-sonnet-4-6`.
6. `POST /api/slots/<id>/chat` — with a mocked `decider.decide`: returns `{reply,
   suggestions}`; returns 400 when no key.
7. Settings — `GET /api/settings` masks the key; `PUT` saves key + model.

## Phasing

Single spec, phased plan:
- **Phase 1 (this spec):** dependency + config + Settings fields + `decider.py` + chat API
  (blocking) + minimal chat UI with suggestion cards. Shippable MVP.
- **Deferred (future specs):** streaming responses; cross-slate "what should I play anywhere
  tonight" mode; model-pins-via-tool-use; persisted chat history; real-time on-add AI.

## Out of scope (YAGNI)

Streaming, global/cross-slate mode, tool-use pinning, chat-history persistence, on-add
real-time classification.
