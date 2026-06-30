// Copyright (C) 2026 Mnemoq
// SPDX-License-Identifier: AGPL-3.0-or-later
"use strict";

// ---------------------------------------------------------------------------
// Retrieval Explorer
// ---------------------------------------------------------------------------
let retScoreChart = null;

document.getElementById("ret-run").addEventListener("click", async () => {
  const step = parseInt(document.getElementById("ret-step").value) || 1;
  const components = document.getElementById("ret-components").value;
  const files = document.getElementById("ret-files").value;
  const domain = document.getElementById("ret-domain").value;
  const params = new URLSearchParams({ step });
  if (components) params.set("components", components);
  if (files) params.set("files", files);
  if (domain) params.set("domain", domain);

  saveQueryHistory({ step, components, files, domain });

  try {
    const result = await API.get(`/retrieve?${params}`);
    const el = document.getElementById("ret-results");
    const warnings = result.warnings || [];
    const patterns = result.patterns || [];
    if (!warnings.length && !patterns.length) {
      el.innerHTML = '<p class="placeholder">No results found.</p>';
      document.getElementById("ret-charts").style.display = "none";
      document.getElementById("ret-filtered-out").style.display = "none";
      return;
    }
    el.innerHTML = "";
    if (warnings.length) {
      el.innerHTML += `<h3 style="margin-bottom:8px">Warnings (${warnings.length})</h3>`;
      el.innerHTML += warnings.map((w) => `<div class="metric-card" style="margin-bottom:8px">
        <div class="label">Score: ${w.score?.toFixed(3) || "—"} · Step ${w.step} · ${w.severity}</div>
        <div class="val" style="margin-top:4px">${escapeHtml(w.trigger)}: ${escapeHtml(w.action)}</div>
        <div class="sub" style="margin-top:4px">${escapeHtml(w.reason || "")}</div>
      </div>`).join("");
    }
    if (patterns.length) {
      el.innerHTML += `<h3 style="margin:16px 0 8px">Patterns (${patterns.length})</h3>`;
      el.innerHTML += patterns.map((p) => `<div class="metric-card" style="margin-bottom:8px">
        <div class="label">Score: ${p.score?.toFixed(3) || "—"} · Step ${p.step} · ${p.domain}</div>
        <div class="val" style="margin-top:4px">${escapeHtml(p.trigger)}: ${escapeHtml(p.action)}</div>
        <div class="sub" style="margin-top:4px">${escapeHtml(p.reason || "")}</div>
      </div>`).join("");
    }

    // Score distribution histogram
    const allResults = [...warnings, ...patterns];
    const scores = allResults.map((r) => r.score || 0).filter((s) => s > 0);
    if (scores.length) {
      document.getElementById("ret-charts").style.display = "grid";
      renderRetScoreChart(scores);
    } else {
      document.getElementById("ret-charts").style.display = "none";
    }

    // Filtered-out panel
    const filteredOut = result.filtered_out || [];
    if (filteredOut.length) {
      const foEl = document.getElementById("ret-filtered-out");
      foEl.style.display = "block";
      foEl.innerHTML = `<h3 style="margin-bottom:8px">Filtered Out (${filteredOut.length})</h3>`;
      foEl.innerHTML += filteredOut.map((f) => `<div class="metric-card" style="margin-bottom:8px;opacity:0.6">
        <div class="label">Score: ${f.score?.toFixed(3) || "—"} · ${f.filter_reason || "filtered"}</div>
        <div class="val" style="margin-top:4px">${escapeHtml(f.trigger || "")}: ${escapeHtml(f.action || "")}</div>
      </div>`).join("");
    } else {
      document.getElementById("ret-filtered-out").style.display = "none";
    }

    renderQueryHistory();
  } catch (e) {
    toast(`Retrieval failed: ${e.message}`, "error");
  }
});

function renderRetScoreChart(scores) {
  const ctx = document.getElementById("chart-ret-scores");
  if (retScoreChart) retScoreChart.destroy();
  const bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
  const counts = new Array(bins.length - 1).fill(0);
  scores.forEach((s) => {
    for (let i = 0; i < bins.length - 1; i++) {
      if (s >= bins[i] && s < bins[i + 1]) { counts[i]++; break; }
    }
  });
  retScoreChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: bins.slice(0, -1).map((b, i) => `${b.toFixed(1)}-${bins[i+1].toFixed(1)}`),
      datasets: [{ label: "Count", data: counts, backgroundColor: "#6366f1" }],
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
  });
}

function saveQueryHistory(query) {
  let history = JSON.parse(localStorage.getItem("ret-history") || "[]");
  history.unshift({ ...query, ts: new Date().toISOString() });
  history = history.slice(0, 20);
  localStorage.setItem("ret-history", JSON.stringify(history));
}

function renderQueryHistory() {
  const history = JSON.parse(localStorage.getItem("ret-history") || "[]");
  const el = document.getElementById("ret-history");
  if (!history.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<h3 style="margin:16px 0 8px">Query History</h3><div class="query-history">`;
  el.innerHTML += history.map((q) => `<div class="query-history-item" data-query="${encodeURIComponent(JSON.stringify(q))}">
    <span class="badge badge-minor">${q.step}</span> ${escapeHtml(q.components || "—")} / ${escapeHtml(q.domain || "—")} <span class="sub">${q.ts.slice(0, 19)}</span>
  </div>`).join("");
  el.innerHTML += `</div>`;
}

// Query history click via event delegation
document.getElementById("ret-history").addEventListener("click", (ev) => {
  const item = ev.target.closest(".query-history-item");
  if (!item) return;
  try { replayQuery(JSON.parse(decodeURIComponent(item.dataset.query))); } catch {}
});

function replayQuery(q) {
  document.getElementById("ret-step").value = q.step || 1;
  document.getElementById("ret-components").value = q.components || "";
  document.getElementById("ret-files").value = q.files || "";
  document.getElementById("ret-domain").value = q.domain || "";
  document.getElementById("ret-run").click();
}
