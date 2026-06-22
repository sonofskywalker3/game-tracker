# Game Tracker — Android Companion UI Polish (Design)

**Date:** 2026-06-22
**Status:** Approved direction (owner decisions captured), pre-implementation
**Parent:** Phases 2–3 shipped; this is a cross-cutting visual/UX polish pass driven by on-device testing feedback.
**Topic:** Make the app look and feel good — fix blown-up cover art, give every screen a real top bar/title and consistent spacing, and fix the Add→Detail experience (sensible cover, an "Added ✓" confirmation, and a platform editor on Detail).

---

## 1. Context & feedback

On-device testing surfaced: covers are "blown up" (a small image `Crop`-scaled to full width), there's "no indication a game was added" and "no option to change platform," and the overall UI "sucks." The backend + data flows work (Library, Add, Picks now parse correctly). This pass is purely client-side presentation + two small interactions.

### Owner decisions (2026-06-22)
- **Add flow:** keep navigating to the game's **Detail** after adding, but fix the cover there, show a clear **"Added ✓"** banner, and provide a **platform editor** on Detail.
- **Scope:** a **broad polish pass** across all screens (not just the specific defects).

### Non-goals
- No new screens or features; no backend changes except a small `platforms` update path (the existing `PUT /api/games/<id>` already accepts `platforms`).
- Not a rebrand — a clean, consistent Material 3 treatment, not a new visual identity.

---

## 2. Cover rendering (the #1 visible defect)

A single shared rule, applied everywhere covers appear (Library grid, Picks carousel + rows, Detail, Add results, candidate lists):
- Game covers are portrait (~3:4). Render inside a fixed **3:4 aspect-ratio** box with **`ContentScale.Fit`** (never upscale-crop a low-res image to fill), a subtle surface/`surfaceVariant` background, and **rounded corners** (e.g. 8.dp) with a clip.
- Null/blank/way-too-small covers → a placeholder tile (game-controller icon or the title text) on the surface background, same rounded box.
- Sizes are bounded per context (grid cell width, carousel hero height, detail hero max height) so nothing is stretched beyond its pixels.
- `CoverImage` is the one component that owns all of this; callers pass `url`, `title`, and a size modifier only.

---

## 3. Per-screen polish

A shared `AppScaffold(title, actions, content)` wrapper gives every screen a Material 3 `TopAppBar` (title), consistent content padding, and the bottom nav. Applied to all five destinations.

- **Theme:** a proper Material 3 **dark color scheme** (branded primary/secondary, correct surface/`onSurface` contrast) replacing the bare default `darkColorScheme()`, set once in `MainActivity`.
- **Picks:** titled "Picks"; carousel hero uses the fixed cover box (sized, not full-bleed-stretched) with label + goal in a readable overlay/caption; slot rows as tidy cards with clear action grouping (the outcome buttons wrap gracefully).
- **Library:** titled "Library"; grid cards = cover box + title (1–2 lines, ellipsized) + a small status chip; search/filter row spaced and aligned.
- **Add:** titled "Add"; result rows = small cover thumb + title + platform line, comfortable touch targets; existing debounced search + Scan button kept.
- **Settings:** titled "Settings"; grouped sections with spacing.

Spacing/typography: consistent `MaterialTheme.typography` roles and a standard content padding; no cramped or edge-to-edge text.

---

## 4. Detail screen (Add target) — cover, added banner, platform editor

- **Cover:** the shared cover box, **centered, height-bounded** (e.g. ≤ 240–280.dp, `Fit`) — not a stretched full-width crop.
- **"Added ✓" banner:** when Detail is opened right after an add, show a dismissible banner/snackbar "Added ✓ — <title>". Implemented by a nav arg (`detail/{id}?added=true`); the Add flow navigates with it, normal navigation omits it.
- **Platform editor:** a row of **FilterChips** for the platforms the game has (selected) drawn from a known platform set, letting the owner toggle platforms; on change, `PUT /api/games/<id>` with the new `platforms` list, then reload. Keeps the existing status control.
- Status control, hours, rating, owned-DLC list stay; just re-laid-out cleanly within the scaffold.

---

## 5. Data layer (small)

The Retrofit `updateGame` currently takes a status-only body. Add a general game-update path used by the platform editor:
- `GamePatchBody(status: String? = null, platforms: List<String>? = null)` (snake-case `platforms`), or a dedicated `PlatformsBody(platforms: List<String>)`.
- `Repository.setPlatforms(id, platforms): Result<Unit>` → `PUT /api/games/<id>`.
- `FakeRepo` records platform updates; one repository/VM test covers it.
The existing `setStatus` path is unchanged.

---

## 6. Testing

- **Logic (unit):** the new platforms-update repository/VM path (MockWebServer / fake-repo) — request carries the platforms list; Detail VM `setPlatforms` reloads on success. Existing VM/repo tests stay green.
- **Visual/UX:** owner on-device smoke is the real gate for the look — covers no longer blown up, top bars present, Add shows "Added ✓" on Detail, platform chips toggle and persist. Verified by reinstall + the owner's eyes.

---

## 7. Build order

1. **Foundation** — Material 3 theme + the fixed `CoverImage` + `AppScaffold` wrapper.
2. **Data** — platforms-update API/Repository/FakeRepo + test.
3. **Detail** — cover, "Added ✓" banner (nav arg), platform editor; Add flow passes `added=true`.
4. **Screens** — apply `AppScaffold` + cover box + spacing to Picks, Library, Add, Settings.

Each step reinstalls for owner review; UI taste is iterated from on-device feedback.

---

## 8. Risks

- **Taste is subjective** — mitigated by tight reinstall/feedback loops rather than a big upfront pixel spec; implementers use Material 3 defaults + good judgment.
- **Known platform set** for the editor: derive from the games already loaded (as Library does) or a fixed short_name list; keep it simple, don't block on a perfect list.
