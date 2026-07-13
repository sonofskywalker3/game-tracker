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
};
const genericSteps = (label) => [
  `Login to your ${label} account on the browser that just opened`,
  "Open your game library / full purchase history",
];
let doneCounts = {}, skippedNotes = {}, hasToken = false, pollTimer = null, collectStart = 0;

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

function startPolling() {
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
      `<p class="then">When your library is showing, click Continue.</p>`;
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
    clearInterval(pollTimer);
    $("start").disabled = false;
    renderResults();
  }
}

function renderResults() {
  show("state-results");
  const rows = Object.entries(doneCounts).map(([v, n]) =>
      `<tr><td>${VENDOR_LABELS[v]}</td><td>${n === 0
        ? "0 found — vendor may have changed their site; check for an update"
        : n + " titles"}</td></tr>`)
    .concat(Object.entries(skippedNotes).map(([v, note]) =>
      `<tr><td>${VENDOR_LABELS[v]}</td><td>skipped—${note === "skipped" ? "" : " " + escapeHtml(note)}
       ${note !== "skipped" ? "(site changed? check for an update)" : ""}</td></tr>`));
  $("results-table").innerHTML = rows.join("");
  $("sync").classList.toggle("hidden", !hasToken);
}

$("start").onclick = () => {
  $("start").disabled = true;
  doneCounts = {}; skippedNotes = {};
  const vendors = [...document.querySelectorAll("#vendors input:checked")].map((i) => i.value);
  if (!vendors.length) { $("start").disabled = false; return; }
  window.pywebview.api.start_scrape(vendors);
  startPolling();
};
$("continue").onclick = () => window.pywebview.api.continue_login();
$("skip").onclick = () => window.pywebview.api.skip_vendor();
$("save-settings").onclick = saveSettings;
$("save-csv").onclick = async () => {
  const path = await window.pywebview.api.export_csv();
  if (path) $("action-status").textContent = "Saved: " + path;
};
$("sync").onclick = async () => {
  $("action-status").textContent = "Syncing…";
  const results = await window.pywebview.api.sync();
  $("action-status").textContent = results.map((r) =>
    `${VENDOR_LABELS[r.source] || r.source}: ${r.summary}`).join("\n");
  // A rejected token needs a way back to the (normally hidden) settings.
  if (results.some((r) => !r.ok && !r.retryable)) showSettings();
};
$("again").onclick = () => { $("start").disabled = false; show("state-setup"); };
window.addEventListener("pywebviewready", init);
