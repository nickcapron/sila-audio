/**
 * SILA Sample Import tool.
 * Token auth identical to app.js — reads from URL hash or localStorage.
 */

const TOKEN_KEY = "sila_token";
let TOKEN = "";

(function initToken() {
  const hash = location.hash.slice(1);
  const params = new URLSearchParams(hash);
  if (params.has("token")) {
    TOKEN = params.get("token");
    localStorage.setItem(TOKEN_KEY, TOKEN);
    history.replaceState(null, "", location.pathname);
  } else {
    TOKEN = localStorage.getItem(TOKEN_KEY) || "";
  }
})();

async function api(method, path, body) {
  const opts = {
    method,
    headers: { "X-SILA-Token": TOKEN, "Content-Type": "application/json" },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch("/api" + path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text);
  }
  return res.json();
}

const POST = (p, b) => api("POST", p, b);

// All 59 SILA canonical categories plus a "— skip —" sentinel.
const CATEGORIES = [
  "— skip —",
  "01. Kick", "02. Snare", "03. Clap",
  "04. Hi-Hat Closed", "05. Hi-Hat Open", "06. Cymbal",
  "07. Ride", "08. Crash", "09. Tom", "10. Rimshot",
  "11. Sidestick", "12. Cowbell", "13. Conga", "14. Bongo",
  "15. Tambourine", "16. Shaker", "17. Cabasa", "18. Maracas",
  "19. Triangle", "20. Electronic Perc",
  "21. Bass - Sub", "22. Bass - Synth", "23. Bass - 808", "24. Bass - Acoustic",
  "25. Lead - Saw", "26. Lead - Square", "27. Lead - Pluck", "28. Lead - Acid",
  "29. Pad - Warm", "30. Pad - Strings", "31. Pad - Atmosphere", "32. Pad - Choir",
  "33. Keys - Piano", "34. Keys - Electric Piano", "35. Keys - Organ", "36. Keys - Rhodes",
  "37. Stab", "38. Brass",
  "39. Strings - Solo", "40. Strings - Ensemble",
  "41. Pluck - Guitar", "42. Pluck - Synth", "43. Pluck - Harp",
  "44. Arp", "45. Drone", "46. Texture", "47. Basic Waveforms",
  "48. Vocal - Chops", "49. Vocal - One Shots", "50. Vocal - Phrases",
  "51. Vocal - Harmony", "52. Vocal - Ad Libs",
  "53. FX - Rise", "54. FX - Fall", "55. FX - Impact",
  "56. FX - Noise", "57. FX - Glitch", "58. Foley", "59. Field Recording",
];

let _scanData = null;

// ---------------------------------------------------------------------------
// Step 0 — Browse (native OS folder picker)
// ---------------------------------------------------------------------------

async function doBrowse() {
  const msg = document.getElementById("scan-msg");
  msg.textContent = "Opening folder picker…";
  try {
    const result = await POST("/import/browse");
    if (result.path) {
      document.getElementById("scan-path").value = result.path;
      msg.textContent = "";
    } else {
      msg.textContent = "";  // user cancelled, silent
    }
  } catch (e) {
    msg.textContent = "Browse unavailable: " + e.message;
  }
}

// ---------------------------------------------------------------------------
// Step 1 — Scan
// ---------------------------------------------------------------------------

async function doScan() {
  const path = document.getElementById("scan-path").value.trim();
  if (!path) return;

  const msg = document.getElementById("scan-msg");
  msg.textContent = "Scanning…";
  document.getElementById("step2").classList.add("hidden");
  document.getElementById("step3").classList.add("hidden");

  try {
    _scanData = await POST("/import/scan", { path });
  } catch (e) {
    msg.textContent = "Error: " + e.message;
    return;
  }

  msg.textContent =
    `Found ${_scanData.total_files} file(s) in ${_scanData.groups.length} group(s).`;

  buildMappingTable(_scanData);
  document.getElementById("step2").classList.remove("hidden");
  document.getElementById("step3").classList.remove("hidden");

  // Pre-fill pack name from the last path component.
  const parts = _scanData.source_path.replace(/\\/g, "/").split("/").filter(Boolean);
  document.getElementById("pack-name").value = parts[parts.length - 1] || "";
  document.getElementById("import-result").innerHTML = "";
}

// ---------------------------------------------------------------------------
// Step 2 — Mapping table
// ---------------------------------------------------------------------------

function buildMappingTable(data) {
  document.getElementById("scan-summary").textContent =
    `${data.total_files} file(s) in ${data.groups.length} group(s)  ·  source: ${data.source_path}`;

  const tbody = document.getElementById("map-body");
  tbody.innerHTML = "";

  for (const g of data.groups) {
    const tr = document.createElement("tr");
    tr.dataset.group = g.name;

    // Group name + optional "suggested" badge
    const tdName = document.createElement("td");
    tdName.className = "col-group";
    tdName.textContent = g.name;
    if (g.suggestion) {
      const badge = document.createElement("span");
      badge.className = "suggested-badge";
      badge.textContent = "suggested";
      tdName.appendChild(badge);
    }
    tr.appendChild(tdName);

    // File count
    const tdCount = document.createElement("td");
    tdCount.className = "col-count";
    tdCount.textContent = g.file_count;
    tr.appendChild(tdCount);

    // Category dropdown
    const tdCat = document.createElement("td");
    tdCat.className = "col-cat";
    const sel = document.createElement("select");
    sel.dataset.group = g.name;
    for (const cat of CATEGORIES) {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      if (g.suggestion && cat === g.suggestion) opt.selected = true;
      if (!g.suggestion && cat === "— skip —") opt.selected = true;
      sel.appendChild(opt);
    }
    tdCat.appendChild(sel);
    tr.appendChild(tdCat);

    tbody.appendChild(tr);
  }
}

// ---------------------------------------------------------------------------
// Step 3 — Import
// ---------------------------------------------------------------------------

async function doImport() {
  const packName = document.getElementById("pack-name").value.trim();
  const resultEl = document.getElementById("import-result");

  if (!packName) {
    resultEl.textContent = "Enter a pack name first.";
    return;
  }
  if (!_scanData) return;

  // Collect only non-skip mappings.
  const mappings = {};
  document.querySelectorAll("#map-body select").forEach(sel => {
    if (sel.value && sel.value !== "— skip —") {
      mappings[sel.dataset.group] = sel.value;
    }
  });

  resultEl.textContent = "Importing…";

  try {
    const r = await POST("/import/execute", {
      source_path: _scanData.source_path,
      pack_name:   packName,
      mappings,
    });
    const cats = r.categories_created;
    resultEl.innerHTML =
      `<span class="num">${r.imported}</span> <span class="label">file(s) imported</span>&emsp;` +
      `<span class="num">${r.skipped}</span> <span class="label">skipped</span>&emsp;` +
      `<span class="num">${cats}</span> <span class="label">` +
      `categor${cats === 1 ? "y" : "ies"} created</span>`;
  } catch (e) {
    resultEl.textContent = "Error: " + e.message;
  }
}
