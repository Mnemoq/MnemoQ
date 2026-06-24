"use strict";

// ---------------------------------------------------------------------------
// Learnings Browser
// ---------------------------------------------------------------------------
let allLearnings = [];
let selectedEntry = null;
let sortColumn = null;
let sortDir = 1;

async function loadLearnings() {
  try {
    const data = await API.get("/learnings");
    allLearnings = data.entries || [];
    populateFilters();
    renderLearningsTable();
  } catch (e) {
    toast(`Failed to load learnings: ${e.message}`, "error");
  }
}

function populateFilters() {
  const domains = [...new Set(allLearnings.map((e) => e.domain).filter(Boolean))].sort();
  const types = [...new Set(allLearnings.map((e) => e.type).filter(Boolean))].sort();
  const agents = [...new Set(allLearnings.map((e) => e.source_agent).filter(Boolean))].sort();
  const fill = (id, vals) => {
    const sel = document.getElementById(id);
    sel.innerHTML = '<option value="">All</option>' + vals.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  };
  fill("filter-domain", domains);
  fill("filter-type", types);
  fill("filter-severity", ["critical", "major", "minor"]);
  fill("filter-agent", agents);
}

function getFilteredLearnings() {
  const search = document.getElementById("learnings-search").value.toLowerCase();
  const domain = document.getElementById("filter-domain").value;
  const type = document.getElementById("filter-type").value;
  const severity = document.getElementById("filter-severity").value;
  const resolved = document.getElementById("filter-resolved").value;
  const agent = document.getElementById("filter-agent").value;
  const stepMin = document.getElementById("filter-step-min").value;
  const stepMax = document.getElementById("filter-step-max").value;

  let filtered = allLearnings.filter((e) => {
    if (domain && e.domain !== domain) return false;
    if (type && e.type !== type) return false;
    if (severity && e.severity !== severity) return false;
    if (resolved && String(!!e.resolved) !== resolved) return false;
    if (agent && e.source_agent !== agent) return false;
    if (stepMin && e.step < parseInt(stepMin)) return false;
    if (stepMax && e.step > parseInt(stepMax)) return false;
    if (search) {
      const hay = `${e.trigger} ${e.action} ${e.reason} ${e.source_agent} ${e.domain}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  if (sortColumn) {
    filtered.sort((a, b) => {
      let av = a[sortColumn], bv = b[sortColumn];
      if (typeof av === "string") av = av.toLowerCase();
      if (typeof bv === "string") bv = bv.toLowerCase();
      if (av < bv) return -1 * sortDir;
      if (av > bv) return 1 * sortDir;
      return 0;
    });
  }
  return filtered;
}

function renderLearningsTable() {
  const filtered = getFilteredLearnings();
  const tbody = document.getElementById("learnings-tbody");
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="placeholder">No entries match filters.</td></tr>';
    return;
  }
  tbody.innerHTML = filtered.map((e) => {
    const sevClass = e.severity === "critical" ? "badge-critical" : e.severity === "major" ? "badge-major" : "badge-minor";
    const statusClass = e.resolved ? "badge-resolved" : "badge-unresolved";
    const statusText = e.resolved ? "Resolved" : "Open";
    return `<tr data-ts="${escapeHtml(e.ts)}" class="learning-row">
      <td>${e.step}</td>
      <td>${escapeHtml(e.type)}</td>
      <td>${escapeHtml(e.domain)}</td>
      <td><span class="badge ${sevClass}">${escapeHtml(e.severity)}</span></td>
      <td class="truncate">${escapeHtml(e.trigger)}</td>
      <td>${escapeHtml(e.source_agent)}</td>
      <td><span class="badge ${statusClass}">${statusText}</span></td>
    </tr>`;
  }).join("");
}

function selectEntry(ts) {
  const entry = allLearnings.find((e) => e.ts === ts);
  if (!entry) return;
  selectedEntry = entry;

  document.querySelectorAll("#learnings-tbody tr").forEach((tr) => tr.classList.remove("selected"));
  const row = document.querySelector(`#learnings-tbody tr[data-ts="${ts}"]`);
  if (row) row.classList.add("selected");

  const panel = document.getElementById("learnings-detail");
  const fields = [
    ["Timestamp", entry.ts],
    ["Step", entry.step],
    ["Type", entry.type],
    ["Domain", entry.domain],
    ["Severity", entry.severity],
    ["Scope", entry.scope],
    ["Source Agent", entry.source_agent],
    ["Components", (entry.components || []).join(", ")],
    ["Files Touched", (entry.files_touched || []).join(", ")],
    ["Trigger", entry.trigger],
    ["Action", entry.action],
    ["Reason", entry.reason],
    ["Importance", entry.importance],
    ["Debt Level", entry.debt_level],
    ["Verified", entry.verified ? "Yes" : "No"],
    ["Access Count", entry.access_count || 0],
    ["Reinforcement", entry.reinforcement_count || 0],
    ["Commit", entry.commit || "—"],
    ["Resolved", entry.resolved ? "Yes" : "No"],
  ];
  panel.innerHTML = fields.map(([k, v]) => `<div class="detail-field"><div class="key">${k}</div><div class="val">${escapeHtml(String(v))}</div></div>`).join("")
    + `<div class="detail-actions">${entry.resolved ? "" : `<button class="btn btn-primary resolve-btn" data-ts="${escapeHtml(ts)}">Resolve</button>`}</div>`;
}

async function resolveEntry(ts) {
  try {
    await API.post("/resolve", { ts });
    toast("Entry resolved", "success");
    const entry = allLearnings.find((e) => e.ts === ts);
    if (entry) entry.resolved = true;
    renderLearningsTable();
    selectEntry(ts);
  } catch (e) {
    toast(`Resolve failed: ${e.message}`, "error");
  }
}

// Row click via event delegation (avoids inline onclick with string interpolation)
document.getElementById("learnings-tbody").addEventListener("click", (ev) => {
  const tr = ev.target.closest("tr.learning-row");
  if (!tr) return;
  selectEntry(tr.dataset.ts);
});

// Resolve button in detail panel via event delegation
document.getElementById("learnings-detail").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button.resolve-btn");
  if (!btn) return;
  resolveEntry(btn.dataset.ts);
});

// Sortable columns
document.querySelectorAll("#learnings-table th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    const col = th.dataset.sort;
    if (sortColumn === col) sortDir *= -1;
    else { sortColumn = col; sortDir = 1; }
    renderLearningsTable();
  });
});

// Filter event listeners
["learnings-search", "filter-domain", "filter-type", "filter-severity", "filter-resolved", "filter-agent", "filter-step-min", "filter-step-max"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener("input", renderLearningsTable);
    el.addEventListener("change", renderLearningsTable);
  }
});

// Export buttons
document.getElementById("learnings-export-jsonl").addEventListener("click", () => {
  const filtered = getFilteredLearnings();
  const blob = new Blob([filtered.map((e) => JSON.stringify(e)).join("\n")], { type: "application/jsonl" });
  downloadBlob(blob, "learnings.jsonl");
  toast(`Exported ${filtered.length} entries as JSONL`, "success");
});

document.getElementById("learnings-export-csv").addEventListener("click", () => {
  const filtered = getFilteredLearnings();
  if (!filtered.length) { toast("No entries to export", "error"); return; }
  const keys = ["ts", "step", "type", "domain", "severity", "source_agent", "trigger", "action", "reason", "importance", "resolved", "access_count", "reinforcement_count"];
  const csv = [keys.join(",")].concat(filtered.map((e) => keys.map((k) => `"${String(e[k] ?? "").replace(/"/g, '""')}"`).join(","))).join("\n");
  downloadBlob(new Blob([csv], { type: "text/csv" }), "learnings.csv");
  toast(`Exported ${filtered.length} entries as CSV`, "success");
});
