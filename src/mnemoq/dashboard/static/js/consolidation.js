// Copyright (C) 2026 Mnemoq
// SPDX-License-Identifier: AGPL-3.0-or-later
// ---------------------------------------------------------------------------
// Consolidation Console
// ---------------------------------------------------------------------------
async function loadConsolidation() {
  const el = document.getElementById("consolidation-content");
  el.innerHTML = '<p class="placeholder">Loading...</p>';
  try {
    const state = await API.get("/consolidation");
    el.innerHTML = renderConsolidationState(state);
  } catch (e) {
    el.innerHTML = `<p class="placeholder">Could not load data. Check that the engine is running.</p>`;
  }
}

function renderConsolidationState(state) {
  const due = state.sleep_cycle_due ? '<span class="badge badge-critical">Due</span>' : '<span class="badge badge-resolved">OK</span>';
  let html = `
    <div class="cards-grid">
      <div class="metric-card"><div class="label">Unresolved</div><div class="value">${state.unresolved}</div><div class="sub">${state.total_entries} total entries</div></div>
      <div class="metric-card"><div class="label">Last Sprint</div><div class="value">${state.last_sprint ?? "—"}</div><div class="sub">${state.last_consolidation_ts ? state.last_consolidation_ts.slice(0, 19) : "No session"}</div></div>
      <div class="metric-card"><div class="label">Sleep Cycle</div><div class="value">${due}</div></div>
      <div class="metric-card"><div class="label">Candidates</div><div class="value">${state.promotion_candidates.length}</div><div class="sub">Promotion</div></div>
      <div class="metric-card"><div class="label">Contradictions</div><div class="value">${state.contradictions.length}</div></div>
      <div class="metric-card"><div class="label">Stale</div><div class="value">${state.stale_entries.length}</div></div>
      <div class="metric-card"><div class="label">Quarantine</div><div class="value">${state.quarantine.count}</div></div>
      <div class="metric-card"><div class="label">Archives</div><div class="value">${state.archive_history.length}</div></div>
    </div>
  `;

  html += renderCandidateTable(state.promotion_candidates);
  html += renderContradictions(state.contradictions);
  html += renderStaleEntries(state.stale_entries);
  html += renderQuarantineQueue(state.quarantine);
  html += renderArchiveHistory(state.archive_history);
  return html;
}

// ponytail: promotion approval is handled by the sleep cycle; the dashboard only
// shows candidates and lets the user reject (resolve) false positives.
function renderCandidateTable(candidates) {
  if (!candidates.length) return `<div class="chart-card"><h3>Promotion Candidates</h3><p class="placeholder">No promotion candidates.</p></div>`;
  return `
    <div class="chart-card">
      <h3>Promotion Candidates (${candidates.length})</h3>
      <table class="metrics-table" style="width:100%"><thead><tr>
        <th>Step</th><th>Domain</th><th>Severity</th><th>Access</th><th>Score</th><th>Trigger / Action</th><th>Actions</th>
      </tr></thead><tbody>
        ${candidates.map((c) => {
          const e = c.entry;
          return `<tr>
            <td>${e.step}</td>
            <td>${escapeHtml(e.domain)}</td>
            <td><span class="badge ${e.severity === "critical" ? "badge-critical" : e.severity === "major" ? "badge-major" : "badge-minor"}">${escapeHtml(e.severity)}</span></td>
            <td>${e.access_count || 0}</td>
            <td>${c.score.toFixed(2)}</td>
            <td class="truncate">${escapeHtml(e.trigger)}: ${escapeHtml(e.action)}</td>
            <td>
              <button class="btn btn-ghost btn-sm reject-btn" data-ts="${escapeHtml(e.ts)}">Reject</button>
            </td>
          </tr>`;
        }).join("")}
      </tbody></table>
    </div>
  `;
}

function renderContradictions(contradictions) {
  if (!contradictions.length) return `<div class="chart-card"><h3>Contradictions</h3><p class="placeholder">No contradictions detected.</p></div>`;
  return `<div class="chart-card"><h3>Contradictions (${contradictions.length})</h3>
    ${contradictions.map((e) => `<div class="metric-card" style="margin-bottom:8px;border-left:3px solid var(--red)">
      <div class="label">Step ${e.step} · ${escapeHtml(e.domain)} · ${escapeHtml(e.source_agent)}</div>
      <div class="val" style="margin-top:4px">${escapeHtml(e.trigger)}: ${escapeHtml(e.action)}</div>
      <div class="sub" style="margin-top:4px">${escapeHtml(e.reason || "")}</div>
    </div>`).join("")}
  </div>`;
}

function renderStaleEntries(staleEntries) {
  if (!staleEntries.length) return `<div class="chart-card"><h3>Stale Entries</h3><p class="placeholder">No stale entries.</p></div>`;
  return `
    <div class="chart-card">
      <h3>Stale Entries (${staleEntries.length})</h3>
      <table class="metrics-table" style="width:100%"><thead><tr>
        <th>Step</th><th>Domain</th><th>Files</th><th>Lines Changed</th><th>Status</th><th>Diff</th>
      </tr></thead><tbody>
        ${staleEntries.map((s) => {
          const e = s.entry;
          const tierBadge = { minor: "badge-minor", moderate: "badge-major", severe: "badge-critical" };
          const tier = s.tier && s.tier !== "none" ? s.tier : "minor";
          const status = s.error
            ? `<span class="badge badge-minor">Check error</span>`
            : `<span class="badge ${tierBadge[tier] || "badge-critical"}">${tier.charAt(0).toUpperCase() + tier.slice(1)}</span>`;
          const diff = e.commit && e.files_touched ? `<a href="${s.diff_url}" target="_blank" class="link">View diff</a>` : "—";
          return `<tr>
            <td>${e.step}</td>
            <td>${escapeHtml(e.domain)}</td>
            <td class="truncate">${(e.files_touched || []).map(escapeHtml).join(", ")}</td>
            <td>${s.lines_changed}</td>
            <td>${status}</td>
            <td>${diff}</td>
          </tr>`;
        }).join("")}
      </tbody></table>
    </div>
  `;
}

function renderQuarantineQueue(quarantine) {
  if (!quarantine.count) return `<div class="chart-card"><h3>Quarantine Review</h3><p class="placeholder">Quarantine is empty.</p></div>`;
  const recent = quarantine.recent || [];
  return `
    <div class="chart-card">
      <h3>Quarantine Review (${quarantine.count})</h3>
      <table class="metrics-table" style="width:100%"><thead><tr>
        <th>Timestamp</th><th>Reason</th><th>Raw</th><th>Action</th>
      </tr></thead><tbody>
        ${recent.map((q) => {
          const raw = typeof q.raw === "string" ? q.raw : JSON.stringify(q.raw || q);
          return `<tr>
            <td>${escapeHtml(q.ts || "—")}</td>
            <td>${escapeHtml(q.reason || "unknown")}</td>
            <td class="truncate" style="max-width:300px">${escapeHtml(raw.slice(0, 120))}</td>
            <td><button class="btn btn-ghost btn-sm relog-btn" data-raw="${escapeHtml(raw)}">Re-log</button></td>
          </tr>`;
        }).join("")}
      </tbody></table>
    </div>
  `;
}

function renderArchiveHistory(archives) {
  if (!archives.length) return `<div class="chart-card"><h3>Archive History</h3><p class="placeholder">No archives yet.</p></div>`;
  return `
    <div class="chart-card">
      <h3>Archive History</h3>
      <table class="metrics-table" style="width:100%"><thead><tr><th>File</th><th>Entries</th></tr></thead><tbody>
        ${archives.map((a) => `<tr><td>${escapeHtml(a.file)}</td><td>${a.entries}</td></tr>`).join("")}
      </tbody></table>
    </div>
  `;
}

// Consolidation Console actions: reject, re-log
document.getElementById("consolidation-content").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  const ts = btn.dataset.ts;
  if (btn.classList.contains("reject-btn")) {
    if (ts) resolveEntry(ts).then(() => loadConsolidation());
    return;
  }
  if (btn.classList.contains("relog-btn")) {
    openRelogModal(btn.dataset.raw);
  }
});

function openRelogModal(raw) {
  const modal = document.getElementById("relog-modal");
  const editor = document.getElementById("relog-editor");
  let value = raw;
  try {
    const parsed = JSON.parse(raw);
    value = JSON.stringify(parsed, null, 2);
  } catch {}
  editor.value = value;
  modal.style.display = "block";
}

function closeRelogModal() {
  document.getElementById("relog-modal").style.display = "none";
}

document.getElementById("relog-cancel").addEventListener("click", closeRelogModal);
document.getElementById("relog-submit").addEventListener("click", async () => {
  const text = document.getElementById("relog-editor").value;
  try {
    const parsed = JSON.parse(text);
    await API.post("/log", { entry: parsed });
    toast("Entry re-logged", "success");
    closeRelogModal();
    loadConsolidation();
  } catch (e) {
    if (e instanceof SyntaxError) toast(`Invalid JSON: ${e.message}`, "error");
    else toast(`Re-log failed: ${e.message}`, "error");
  }
});

document.getElementById("consolidate-run").addEventListener("click", async () => {
  try {
    const sprintInput = document.getElementById("consolidate-sprint").value;
    const force = document.getElementById("consolidate-force").checked;
    const body = {};
    if (sprintInput) body.sprint_number = parseInt(sprintInput);
    body.force = force;
    toast("Running sleep cycle...", "info");
    const result = await API.post("/consolidate", body);
    if (result.promotion_candidates?.length) {
      toast(`Sleep cycle complete: ${result.promotion_candidates.length} promotion candidates`, "success");
    } else {
      toast("Sleep cycle complete", "success");
    }
    loadConsolidation();
  } catch (e) {
    toast(`Consolidation failed: ${e.message}`, "error");
  }
});
