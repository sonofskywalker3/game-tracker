# Schedule-Aware Picks — Plan B (Web Editing + Annotation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. NOTE: this is FRONTEND work — there is no pytest gate. Implementers write the HTML/JS and do a static self-review; the **controller** performs the real-browser verification (Task 4), never the implementer (subagents must not run the server/touch the live DB).

**Goal:** Let the owner manage slot schedule windows + a daily-rhythm profile from the web (canonical), and see at-a-glance which slots are active now.

**Architecture:** Pure frontend against the Plan A endpoints (already shipped). Three UI additions: (1) a per-slot schedule-window editor inside the existing slot-settings panel in `templates/recommendations.html`; (2) a "Daily rhythm" profile section in `templates/settings.html`; (3) a schedule badge on each slot card. Window edits save immediately via the granular CRUD endpoints (add→POST, change→PUT, remove→DELETE), then re-render.

**Tech Stack:** Jinja templates + inline vanilla JS + Tailwind (CDN), the `api` helper + `escapeHtml` from `base.html`.

**Spec:** `docs/superpowers/specs/2026-06-23-schedule-aware-picks-and-widget-design.md`

## Global Constraints

- FRONTEND ONLY — no pytest. Implementers must NOT run the app, touch the live `games.db`, hit the network, or start the server. The controller does the browser verification (real Chrome, against a COPY of `games.db` on an alt port).
- Use the existing `api` helper (`base.html`): `api.get(url)`/`api.patch`/`api.delete` return the parsed JSON body; `api.post(url,data)`/`api.put(url,data)` return `{ok, status, data}`. Check `.ok` for post/put.
- Escape ALL dynamic text injected via innerHTML with `escapeHtml(...)`.
- House style (Tailwind, inline): section card = `bg-surface-light rounded-xl p-6`; heading = `text-lg font-medium text-white mb-4 flex items-center` with a leading emoji; button = `px-4 py-2 bg-accent hover:bg-accent-hover rounded-lg text-white font-medium transition-colors`; input = `bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none`.
- Day bitmask: **bit 0 = Monday … bit 6 = Sunday** (must match the backend). Times are stored as **minutes since midnight (0–1439)**; the UI uses `<input type="time">` (HH:MM) and converts.
- Endpoints (Plan A, already live): `POST /api/slots/<id>/windows {days,start_min,end_min}` → `{ok:true,id}` (201); `PUT /api/slots/<id>/windows/<wid> {days,start_min,end_min}`; `DELETE /api/slots/<id>/windows/<wid>`; `GET /api/profile` → `{work_start_min,work_end_min,bed_time_min,meal_windows:[...]}`; `PUT /api/profile`.
- App uses `use_reloader=False`: the controller must restart `:5000` (or use a separate verify instance) for template changes to show. Verification uses a separate instance on an alt port against a DB copy — never the live server.
- Work directly on `main` + push. Commit trailers:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01PQZfqye4BxsLcC3ZiWo6fG`

---

### Task 1: Schedule-window editor in the slot-settings panel

**Files:**
- Modify: `templates/recommendations.html` (the slot-settings panel built in `toggleSlotSettings()` ~lines 344–383; add JS helpers near the other slot JS)

**Interfaces:**
- Consumes: `slot.windows` (array of `{id, days, start_min, end_min}`) already present on each slot object from `/api/slots`; the window CRUD endpoints; `loadSlate()` (re-fetch + re-render); `escapeHtml`, `api`.
- Produces (new JS functions): `minToHHMM(min)`, `hhmmToMin(str)`, `dayChecksHtml(windowId, days)`, `windowRowHtml(slotId, win)`, `scheduleEditorHtml(slot)`, `collectWindowPayload(slotId, windowId)`, `addSlotWindow(slotId)`, `saveSlotWindow(slotId, windowId)`, `deleteSlotWindow(slotId, windowId)`.

- [ ] **Step 1: Add the time/day conversion helpers**

In `templates/recommendations.html`, in the `<script>` block near the other slot helpers, add:

```javascript
// --- schedule-window helpers ---
const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];  // bit 0..6

function minToHHMM(min) {
    const m = Math.max(0, Math.min(1439, parseInt(min, 10) || 0));
    return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
}

function hhmmToMin(str) {
    const parts = String(str || '').split(':');
    const h = parseInt(parts[0], 10) || 0;
    const m = parseInt(parts[1], 10) || 0;
    return Math.max(0, Math.min(1439, h * 60 + m));
}
```

- [ ] **Step 2: Add the editor markup builders**

Add to the same script block:

```javascript
function dayChecksHtml(windowId, days) {
    return DAY_LABELS.map((label, bit) => {
        const checked = (days & (1 << bit)) ? 'checked' : '';
        return `<label class="inline-flex items-center mr-1 text-xs text-gray-300">
            <input type="checkbox" data-win="${windowId}" data-bit="${bit}" ${checked}
                   class="mr-0.5 accent-accent"> ${label}</label>`;
    }).join('');
}

function windowRowHtml(slotId, win) {
    return `<div class="schedule-window mb-2 p-2 bg-surface rounded border border-gray-700" data-window="${win.id}">
        <div class="mb-1">${dayChecksHtml(win.id, win.days)}</div>
        <div class="flex items-center gap-2 text-xs text-gray-300">
            <input type="time" data-win="${win.id}" data-f="start" value="${minToHHMM(win.start_min)}"
                   class="bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none">
            <span>to</span>
            <input type="time" data-win="${win.id}" data-f="end" value="${minToHHMM(win.end_min)}"
                   class="bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none">
            <button onclick="saveSlotWindow(${slotId}, ${win.id})"
                    class="ml-auto px-2 py-1 bg-accent hover:bg-accent-hover rounded text-white text-xs transition-colors">Save</button>
            <button onclick="deleteSlotWindow(${slotId}, ${win.id})"
                    class="px-2 py-1 bg-gray-700 hover:bg-red-700 rounded text-white text-xs transition-colors">Remove</button>
        </div>
    </div>`;
}

function scheduleEditorHtml(slot) {
    const windows = slot.windows || [];
    const rows = windows.length
        ? windows.map(w => windowRowHtml(slot.id, w)).join('')
        : '<p class="text-xs text-gray-500 mb-2">No windows — this slot is <span class="text-gray-300">Anytime</span> (always active).</p>';
    return `<div class="mt-3 border-t border-gray-700 pt-2">
        <div class="text-xs text-gray-400 mb-1">Schedule windows (when this slot is active)</div>
        ${rows}
        <button onclick="addSlotWindow(${slot.id})"
                class="mt-1 px-2 py-1 bg-gray-700 hover:bg-accent rounded text-white text-xs transition-colors">+ Add window</button>
    </div>`;
}
```

- [ ] **Step 3: Render the editor inside the slot-settings panel**

In `toggleSlotSettings()` (~line 344), where the settings panel inner HTML is assembled (after the existing context-notes / save-delete block), append the schedule editor. Find the line that sets the panel's `innerHTML` (the template literal building the settings form) and add `${scheduleEditorHtml(slot)}` just before the closing of that template literal (after the Save/Delete buttons row). The `slot` object is already in scope in `toggleSlotSettings` (it's looked up from `_slateData` by slotId — if the function currently only has `slotId`, add `const slot = _slateData.slots.find(s => s.id === slotId);` at the top of the function).

- [ ] **Step 4: Add the immediate-save CRUD handlers**

Add to the script block:

```javascript
function collectWindowPayload(slotId, windowId) {
    const panel = document.getElementById('slot-settings-' + slotId);
    const row = panel.querySelector(`.schedule-window[data-window="${windowId}"]`);
    let days = 0;
    row.querySelectorAll(`input[type="checkbox"][data-win="${windowId}"]`).forEach(cb => {
        if (cb.checked) days |= (1 << parseInt(cb.dataset.bit, 10));
    });
    const start = hhmmToMin(row.querySelector(`input[data-win="${windowId}"][data-f="start"]`).value);
    const end = hhmmToMin(row.querySelector(`input[data-win="${windowId}"][data-f="end"]`).value);
    return { days, start_min: start, end_min: end };
}

async function addSlotWindow(slotId) {
    // sensible default: weekdays 19:00-21:00
    const res = await api.post(`/api/slots/${slotId}/windows`,
        { days: 0b0011111, start_min: 1140, end_min: 1260 });
    if (!res.ok) { alert('Could not add window: ' + (res.data.error || res.status)); return; }
    await loadSlate();
}

async function saveSlotWindow(slotId, windowId) {
    const payload = collectWindowPayload(slotId, windowId);
    if (payload.start_min === payload.end_min) { alert('Start and end times must differ.'); return; }
    const res = await api.put(`/api/slots/${slotId}/windows/${windowId}`, payload);
    if (!res.ok) { alert('Could not save window: ' + (res.data.error || res.status)); return; }
    await loadSlate();
}

async function deleteSlotWindow(slotId, windowId) {
    await api.delete(`/api/slots/${slotId}/windows/${windowId}`);
    await loadSlate();
}
```

- [ ] **Step 5: Static self-review**

Re-read the diff. Confirm: day bits are 0=Mon..6=Sun; times convert both ways; `addSlotWindow` default has `start_min != end_min`; all dynamic values pass through `escapeHtml` where they are free text (the numeric/time values here are numbers, no escaping needed, but slot labels rendered elsewhere already use escapeHtml); `loadSlate()` is called after each mutation so the panel re-renders. Note: after `loadSlate()` the settings panel collapses (full re-render) — this is acceptable for v1 (the window persisted; the owner reopens the gear to add another).

- [ ] **Step 6: Commit**

```bash
git add templates/recommendations.html
git commit -m "feat(web): per-slot schedule-window editor in the Picks settings panel"
```

---

### Task 2: "Daily rhythm" profile section in Settings

**Files:**
- Modify: `templates/settings.html` (add a section card in the markup; add load/save JS; call the loader from `loadSettings()` ~line 173)

**Interfaces:**
- Consumes: `GET /api/profile`, `PUT /api/profile`; `api`, the `minToHHMM`/`hhmmToMin` conversions (define local copies here — settings.html is a separate template/script scope from recommendations.html).
- Produces (new JS): `loadProfile()`, `saveProfile()`, `addMealRow(start, end)`, `collectMeals()`.

- [ ] **Step 1: Add the conversion helpers (local to settings.html)**

In `templates/settings.html`'s `<script>` block, add the same `minToHHMM`/`hhmmToMin` from Task 1 Step 1 (settings.html cannot see recommendations.html's scope). Copy both functions verbatim.

- [ ] **Step 2: Add the section markup**

In `templates/settings.html`, add a new section card alongside the existing sections (e.g. after the UPC Enrichment section ~line 144):

```html
<div class="bg-surface-light rounded-xl p-6 mt-6">
    <h2 class="text-lg font-medium text-white mb-4 flex items-center">
        <span class="mr-2">🕑</span> Daily rhythm
    </h2>
    <p class="text-sm text-gray-400 mb-4">Optional. Used only to suggest window times when you schedule a slot — it does not change which picks show.</p>
    <div class="grid grid-cols-2 gap-3 max-w-md">
        <label class="text-sm text-gray-300">Work start
            <input type="time" id="prof-work-start" class="mt-1 w-full bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none"></label>
        <label class="text-sm text-gray-300">Work end
            <input type="time" id="prof-work-end" class="mt-1 w-full bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none"></label>
        <label class="text-sm text-gray-300">Bedtime
            <input type="time" id="prof-bed" class="mt-1 w-full bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none"></label>
    </div>
    <div class="mt-4">
        <div class="text-sm text-gray-300 mb-1">Meal / break windows</div>
        <div id="prof-meals"></div>
        <button onclick="addMealRow()" class="mt-1 px-2 py-1 bg-gray-700 hover:bg-accent rounded text-white text-xs transition-colors">+ Add break</button>
    </div>
    <button onclick="saveProfile()" class="mt-4 px-4 py-2 bg-accent hover:bg-accent-hover rounded-lg text-white font-medium transition-colors">Save profile</button>
    <span id="prof-status" class="ml-3 text-sm text-gray-400"></span>
</div>
```

- [ ] **Step 3: Add the load/save JS**

```javascript
function addMealRow(start, end) {
    const wrap = document.getElementById('prof-meals');
    const row = document.createElement('div');
    row.className = 'meal-row flex items-center gap-2 mb-1';
    row.innerHTML = `<input type="time" data-f="start" value="${start != null ? minToHHMM(start) : '12:00'}"
            class="bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none">
        <span class="text-gray-400 text-sm">to</span>
        <input type="time" data-f="end" value="${end != null ? minToHHMM(end) : '13:00'}"
            class="bg-surface rounded border border-gray-600 px-2 py-1 text-white focus:border-accent focus:outline-none">
        <button onclick="this.parentElement.remove()" class="px-2 py-1 bg-gray-700 hover:bg-red-700 rounded text-white text-xs transition-colors">×</button>`;
    wrap.appendChild(row);
}

function collectMeals() {
    return Array.from(document.querySelectorAll('#prof-meals .meal-row')).map(row => ({
        start_min: hhmmToMin(row.querySelector('input[data-f="start"]').value),
        end_min: hhmmToMin(row.querySelector('input[data-f="end"]').value),
    }));
}

async function loadProfile() {
    const p = await api.get('/api/profile');
    if (p.work_start_min != null) document.getElementById('prof-work-start').value = minToHHMM(p.work_start_min);
    if (p.work_end_min != null) document.getElementById('prof-work-end').value = minToHHMM(p.work_end_min);
    if (p.bed_time_min != null) document.getElementById('prof-bed').value = minToHHMM(p.bed_time_min);
    document.getElementById('prof-meals').innerHTML = '';
    (p.meal_windows || []).forEach(m => addMealRow(m.start_min, m.end_min));
}

async function saveProfile() {
    const valOrNull = id => { const v = document.getElementById(id).value; return v ? hhmmToMin(v) : null; };
    const payload = {
        work_start_min: valOrNull('prof-work-start'),
        work_end_min: valOrNull('prof-work-end'),
        bed_time_min: valOrNull('prof-bed'),
        meal_windows: collectMeals(),
    };
    const res = await api.put('/api/profile', payload);
    const status = document.getElementById('prof-status');
    status.textContent = res.ok ? 'Saved.' : ('Error: ' + (res.data.error || res.status));
    status.className = 'ml-3 text-sm ' + (res.ok ? 'text-green-400' : 'text-red-400');
}
```

- [ ] **Step 4: Call `loadProfile()` from `loadSettings()`**

In `loadSettings()` (~line 173), add a `loadProfile();` call (fire-and-forget alongside the other section loaders).

- [ ] **Step 5: Static self-review + commit**

Confirm: helpers defined locally; null handling (empty input → null minute field, accepted by the backend); meal rows round-trip. Commit:

```bash
git add templates/settings.html
git commit -m "feat(web): daily-rhythm profile section in Settings"
```

---

### Task 3: Schedule badge on slot cards

**Files:**
- Modify: `templates/recommendations.html` (`slotCardHtml()` ~lines 95–171; add a `scheduleBadgeHtml(slot)` helper)

**Interfaces:**
- Consumes: `slot.active_now` (bool), `slot.restrictiveness_rank` (int|null), `slot.windows` (array); `minToHHMM`.
- Produces: `scheduleBadgeHtml(slot)` returning a small badge span; inserted into the slot card header near the label.

- [ ] **Step 1: Add the badge helper**

```javascript
function scheduleBadgeHtml(slot) {
    const windows = slot.windows || [];
    if (slot.active_now && windows.length === 0) {
        return '<span class="ml-2 text-xs px-2 py-0.5 rounded-full bg-gray-700 text-gray-300">Anytime</span>';
    }
    if (slot.active_now) {
        return '<span class="ml-2 text-xs px-2 py-0.5 rounded-full bg-green-700 text-green-100">● Active now</span>';
    }
    return '<span class="ml-2 text-xs px-2 py-0.5 rounded-full bg-surface text-gray-500">Off-schedule</span>';
}
```

- [ ] **Step 2: Insert the badge into the slot card header**

In `slotCardHtml()` (~line 159 where the slot label + ⚙ gear are rendered), add `${scheduleBadgeHtml(slot)}` immediately after the slot label text span, so the badge sits next to the label.

- [ ] **Step 3: Static self-review + commit**

Confirm the badge reads `active_now`/`windows` correctly and doesn't disrupt the existing header layout (gear icon, drag affordance). NOTE (documented v1 decision): the slate grid keeps its manual drag-reorder order — slots are NOT auto-resorted by restrictiveness on web; the badge conveys the schedule state. (Auto-sort / most-restrictive-first selection is delivered by the Android widget + app in Plan C.)

```bash
git add templates/recommendations.html
git commit -m "feat(web): schedule badge (Active/Anytime/Off-schedule) on slot cards"
```

---

### Task 4: Controller browser verification (gate)

**Files:** none (verification only — performed by the CONTROLLER, not a subagent).

- [ ] **Step 1: Stand up an isolated verify instance**

Copy the live DB to a scratch path, run a SECOND app instance on an alt port (e.g. 5057) against the copy — never the live `:5000`, never the live DB. Migrate it (boot runs `migrate_db`). (Controller-only; this is a live-ish op the subagents must not do.)

- [ ] **Step 2: Verify the window editor (real Chrome)**

Open the Picks tab, open a slot's ⚙ settings, confirm: the Schedule section renders; "+ Add window" creates a window (weekday default) and it persists after reload; toggling day checkboxes + changing the times + Save persists (re-open the gear to confirm); Remove deletes it; a slot with no windows shows "Anytime". Confirm no console errors.

- [ ] **Step 3: Verify the profile section**

Open Settings, confirm the "Daily rhythm" section renders, set work/bed times + add a meal window, Save → "Saved.", reload the page → values persist (round-trip through `/api/profile`).

- [ ] **Step 4: Verify the badge**

Confirm slot cards show the correct badge: a slot whose window covers "now" shows "● Active now"; a no-window slot shows "Anytime"; a slot scheduled for a different time shows "Off-schedule". (Temporarily add a window covering the current time to confirm the green state, then remove it.)

- [ ] **Step 5: Tear down + report**

Stop the verify instance, remove the scratch DB copy. Record the verification result (what was exercised, screenshots/observations, any console errors) in the progress ledger.

---

## Self-Review (completed by plan author)

- **Spec coverage:** web per-slot window editor (Task 1) ✓; profile section in Settings (Task 2) ✓; schedule-aware Picks view — delivered as a badge/annotation, with the auto-sort intentionally deferred to avoid breaking the existing manual drag-reorder (Task 3, documented decision — flag to owner) ◐; controller browser verification (Task 4) ✓.
- **Placeholder scan:** none — every step has the actual JS/HTML to add and exact insertion guidance.
- **Type/name consistency:** `minToHHMM`/`hhmmToMin` defined in BOTH templates (separate script scopes — intentional duplication, noted); day bit order 0=Mon..6=Sun matches the backend; endpoint shapes match Plan A (`api.post`/`api.put` return `{ok,status,data}`; `api.get` returns the body). `loadSlate()` is the established refresh call; `loadSettings()` is the Settings loader.
- **Known deviation from spec:** spec said the web Picks view "sorts/annotates" most-restrictive-first; this plan annotates and keeps manual order (auto-sort would fight drag-reorder). Surface to the owner before/at verification.
