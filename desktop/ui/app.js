// Polls the Python bridge (window.pywebview.api) and drives the three states.
const VENDOR_LABELS = {playstation: "PlayStation", xbox: "Xbox",
                       nintendo: "Nintendo", steam: "Steam"};
// Per-vendor login walkthroughs (code-controlled strings — safe for innerHTML).
const VENDOR_STEPS = {
  playstation: [
    "Login to your PlayStation account on the browser that just opened",
    "Click on your profile icon in the top right corner",
    "Click on Game Library",
  ],
  xbox: [
    "Sign in to your Microsoft account if asked — Xbox usually opens straight to your order history",
  ],
  nintendo: [
    'Click "Log in" in the top right corner of the browser that just opened',
    "Login with your Nintendo Account",
  ],
  steam: [
    "Login to your Steam account in the browser that just opened",
  ],
};
// Closing line under the bullets; some vendors don't need a library page open.
const VENDOR_THEN = {
  xbox: "Then click Continue here.",
  nintendo: "Then click Continue here.",
};
const DEFAULT_THEN = "When your library is showing, click Continue.";
const genericSteps = (label) => [
  `Login to your ${label} account on the browser that just opened`,
  "Open your game library / full purchase history",
];
let doneCounts = {}, skippedNotes = {}, hasToken = false, pollTimer = null, collectStart = 0;
// pollTimer is shared by two consumers (scrape+sync flow, update flow); these
// flags let each consumer's terminal event stop polling only when the OTHER
// consumer isn't still mid-flight.
let scrapeActive = false, updateActive = false;

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
    (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const show = (id) => ["state-setup", "state-scraping", "state-results"]
    .forEach((s) => $(s).classList.toggle("hidden", s !== id));

async function init() {
  const st = await window.pywebview.api.get_state();
  hasToken = st.has_token;
  $("version").textContent = "v" + st.version;
  if (st.update) { $("update-version").textContent = "v" + st.update;
                   $("update-banner").classList.remove("hidden"); }
  $("server-url").value = st.server_url;
  $("vendors").innerHTML = st.vendors.map((v) =>
    `<label><input type="checkbox" value="${v}" checked> ${VENDOR_LABELS[v]}</label>`).join("");
  renderChip();
}

function renderChip() {
  const chip = $("sync-chip");
  chip.textContent = hasToken ? "✓ Sync configured"
                              : "CSV only — add a token to sync";
  chip.classList.toggle("ok", hasToken);
  // With a baked-in/saved token the server fields are just noise — hide them.
  // They come back via showSettings() if the server ever rejects the token.
  $("settings").classList.toggle("hidden", hasToken);
}

function showSettings() {
  $("settings").classList.remove("hidden");
  $("settings").open = true;
}

async function saveSettings() {
  const st = await window.pywebview.api.save_settings($("server-url").value, $("token").value);
  hasToken = st.has_token; renderChip();
}

function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    const {events, captured} = await window.pywebview.api.poll();
    if (!$("scrape-progress").classList.contains("hidden")) {
      // Liveness beats numbers: big vendors (Xbox order history) work for
      // minutes between captured responses, so show a spinner + ticking clock.
      const secs = Math.floor((Date.now() - collectStart) / 1000);
      const mmss = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
      $("scrape-progress").innerHTML =
        `<span class="spinner"></span>Collecting your library — this can take a few minutes` +
        `<span class="dim"> · ${mmss} elapsed · ${captured} network responses</span>`;
    }
    for (const e of events) handleEvent(e);
  }, 500);
}

function handleEvent(e) {
  if (e.type === "login") {
    show("state-scraping");
    $("scrape-vendor").textContent = VENDOR_LABELS[e.vendor];
    const steps = VENDOR_STEPS[e.vendor] || genericSteps(VENDOR_LABELS[e.vendor]);
    $("scrape-instruction").innerHTML =
      `<ul>${steps.map((s) => `<li>${s}</li>`).join("")}</ul>` +
      `<p class="then">${VENDOR_THEN[e.vendor] || DEFAULT_THEN}</p>`;
    $("scrape-instruction").classList.remove("hidden");
    $("scrape-progress").classList.add("hidden");
    $("continue").disabled = false;
    $("skip").disabled = false;
  } else if (e.type === "collecting") {
    collectStart = Date.now();
    $("scrape-instruction").classList.add("hidden");
    $("scrape-progress").classList.remove("hidden");
    $("continue").disabled = true;
    $("skip").disabled = true;
  } else if (e.type === "done") {
    doneCounts[e.vendor] = e.count;
  } else if (e.type === "skipped") {
    skippedNotes[e.vendor] = e.note;
  } else if (e.type === "finished") {
    $("start").disabled = false;
    renderResults();
    // With a token, sync starts automatically — keep polling for its events
    // (and leave scrapeActive true until synced fires).
    if (!hasToken) {
      scrapeActive = false;
      $("update-now").disabled = false;
      if (!updateActive) stopPolling();
    }
  } else if (e.type === "syncing") {
    document.querySelectorAll(".sync-cell").forEach((c) => {
      if (c.dataset.syncable) c.innerHTML = `<span class="spinner"></span>`;
    });
  } else if (e.type === "synced") {
    scrapeActive = false;
    $("update-now").disabled = false;
    if (!updateActive) stopPolling();
    applySyncResults(e.results);
  } else if (e.type === "update_progress") {
    const mb = (n) => (n / 1048576).toFixed(1);
    $("update-status").textContent = e.total
      ? `Downloading — ${mb(e.done)} / ${mb(e.total)} MB`
      : `Downloading — ${mb(e.done)} MB`;
  } else if (e.type === "update_installing") {
    $("update-status").textContent = "Installing — the app will restart itself…";
  } else if (e.type === "update_failed") {
    updateActive = false;
    if (!scrapeActive) stopPolling();
    $("update-now").disabled = false;
    $("update-status").textContent =
      "Update failed: " + e.error + " — you can keep using this version.";
  } else if (e.type === "update_refused") {
    // Not a failure — the bridge declined to start; show the reason verbatim.
    updateActive = false;
    if (!scrapeActive) stopPolling();
    $("update-now").disabled = false;
    $("update-status").textContent = e.reason;
  } else if (e.type === "scrape_refused") {
    scrapeActive = false;
    if (!updateActive) stopPolling();
    $("start").disabled = false;
    $("start-status").textContent = e.reason;
  }
}

function applySyncResults(results) {
  for (const r of results) {
    const cell = $(`sync-${r.source}`);
    if (!cell) continue;
    cell.innerHTML = r.ok ? `<span class="sync-ok">✓ synced</span>`
                          : `<span class="sync-fail" title="${escapeHtml(r.summary)}">sync failed</span>`;
  }
  const failed = results.filter((r) => !r.ok);
  $("retry-sync").classList.toggle("hidden", !failed.length);
  if (failed.some((r) => !r.retryable)) showSettings();
}

function renderResults() {
  show("state-results");
  const rows = Object.entries(doneCounts).map(([v, n]) =>
      `<tr><td>${VENDOR_LABELS[v]}</td><td>${n === 0
        ? "0 found — vendor may have changed their site; check for an update"
        : n + " titles"}</td><td id="sync-${v}" class="sync-cell" data-syncable="1"></td></tr>`)
    .concat(Object.entries(skippedNotes).map(([v, note]) =>
      `<tr><td>${VENDOR_LABELS[v]}</td><td>skipped—${note === "skipped" ? "" : " " + escapeHtml(note)}
       ${note !== "skipped" ? "(site changed? check for an update)" : ""}</td><td class="sync-cell">—</td></tr>`));
  $("results-table").innerHTML = rows.join("");
}

$("start").onclick = () => {
  $("start").disabled = true;
  $("start-status").textContent = "";
  doneCounts = {}; skippedNotes = {};
  const vendors = [...document.querySelectorAll("#vendors input:checked")].map((i) => i.value);
  if (!vendors.length) { $("start").disabled = false; return; }
  scrapeActive = true;
  $("update-now").disabled = true;
  window.pywebview.api.start_scrape(vendors);
  startPolling();
};
$("continue").onclick = () => window.pywebview.api.continue_login();
$("skip").onclick = () => window.pywebview.api.skip_vendor();
$("save-settings").onclick = saveSettings;
$("save-csv").onclick = async () => {
  const folder = await window.pywebview.api.export_csv();
  if (folder) $("action-status").textContent = "Saved one CSV per platform in " + folder;
};
$("retry-sync").onclick = async () => {
  $("retry-sync").disabled = true;
  document.querySelectorAll(".sync-cell[data-syncable]").forEach((c) => {
    c.innerHTML = `<span class="spinner"></span>`;
  });
  applySyncResults(await window.pywebview.api.sync());
  $("retry-sync").disabled = false;
};
$("again").onclick = () => { $("start").disabled = false; show("state-setup"); };
$("update-now").onclick = () => {
  $("update-now").disabled = true;
  updateActive = true;
  $("update-status").textContent = "Starting download…";
  window.pywebview.api.start_update();
  startPolling();
};

// Boot resiliently: the pywebviewready event can fire BEFORE this script
// attaches its listener (warm WebView2), and the bridge can glitch during
// startup — so poll for the api and retry init instead of trusting one event.
let booted = false, bootAttempts = 0;
async function boot() {
  if (booted) return;
  if (!(window.pywebview && window.pywebview.api)) {
    if (++bootAttempts > 100) {   // ~10s: bridge never came up
      $("sync-chip").textContent = "Couldn't start — please close and reopen the app.";
      return;
    }
    setTimeout(boot, 100);
    return;
  }
  booted = true;
  try {
    await init();
  } catch (err) {
    booted = false;               // bridge answered but flaked — retry
    setTimeout(boot, 500);
  }
}
window.addEventListener("pywebviewready", boot);
boot();
