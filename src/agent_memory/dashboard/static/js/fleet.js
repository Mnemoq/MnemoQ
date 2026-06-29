// Copyright (C) 2026 Mnemoq
// SPDX-License-Identifier: AGPL-3.0-or-later
// ---------------------------------------------------------------------------
// Cross-Project Fleet
// ---------------------------------------------------------------------------
let fleetData = null;

async function loadFleet() {
  const el = document.getElementById("fleet-content");
  el.innerHTML = '<p class="placeholder">Loading fleet data...</p>';
  try {
    const [fleet, projects] = await Promise.all([
      API.get("/fleet"),
      API.get("/projects"),
    ]);
    if (!fleet.count) {
      el.innerHTML = '<p class="placeholder">No projects registered. Add project paths to ~/.agent-memory/engine/projects.txt</p>';
      return;
    }
    fleetData = fleet;
    el.innerHTML = renderFleet(fleet, projects);
    renderFleetCharts(fleet);
  } catch (e) {
    el.innerHTML = `<p class="placeholder">Could not load data. Check that the engine is running.</p>`;
  }
}

function renderFleet(fleet, projects) {
  const projectOptions = projects.projects.map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)}</option>`).join("");
  const rows = fleet.projects.map((p) => `<tr data-project="${escapeHtml(p.project)}">
    <td>${escapeHtml(p.project)}</td>
    <td><span class="health-pill ${healthClass(p.health)}">${p.health}</span></td>
    <td>${p.total_entries}</td>
    <td>${p.unresolved}</td>
    <td>${(p.hit_rate * 100).toFixed(0)}%</td>
    <td>${p.logs}</td>
    <td>${(p.dup_rate * 100).toFixed(0)}%</td>
    <td>${(p.quar_rate * 100).toFixed(0)}%</td>
    <td>${p.last_consolidation ? p.last_consolidation.slice(0, 19) : "—"}</td>
  </tr>`).join("");

  const heatmap = fleet.domain_heatmap;
  const heatmapHeader = heatmap.domains.map((d) => `<th>${escapeHtml(d)}</th>`).join("");
  const heatmapRows = heatmap.matrix.map((row, i) => `<tr>
    <td>${escapeHtml(heatmap.projects[i])}</td>
    ${row.map((v) => `<td class="heatmap-cell ${v ? "active" : ""}">${v ? "●" : "·"}</td>`).join("")}
  </tr>`).join("");

  return `
    <div class="cards-grid">
      <div class="metric-card"><div class="label">Projects</div><div class="value">${fleet.count}</div></div>
      <div class="metric-card"><div class="label">Avg Health</div><div class="value">${avgHealth(fleet.projects)}</div></div>
    </div>

    <div class="form-card" style="margin-bottom:16px">
      <label>Project <select id="fleet-project" class="select"><option value="">All projects</option>${projectOptions}</select></label>
      <div id="fleet-project-detail" style="flex:1"></div>
    </div>

    <div class="chart-card" style="margin-bottom:16px">
      <h3>Side-by-Side Comparison</h3>
      <div style="overflow:auto">
        <table class="metrics-table" style="width:100%;min-width:700px"><thead><tr>
          <th>Project</th><th>Health</th><th>Entries</th><th>Unresolved</th><th>Hit Rate</th><th>Logs</th><th>Dup%</th><th>Quar%</th><th>Last Consolidation</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>

    <div class="charts-row">
      <div class="chart-card"><h3>Health Scores</h3><canvas id="chart-fleet-health"></canvas></div>
      <div class="chart-card"><h3>Fleet-Wide Trends (30d)</h3><canvas id="chart-fleet-trends"></canvas></div>
    </div>

    <div class="chart-card" style="margin-top:16px">
      <h3>Domain Overlap Heatmap</h3>
      <div style="overflow:auto">
        <table class="metrics-table heatmap"><thead><tr><th>Project</th>${heatmapHeader}</tr></thead><tbody>${heatmapRows}</tbody></table>
      </div>
    </div>
  `;
}

function renderFleetCharts(fleet) {
  const healthCtx = document.getElementById("chart-fleet-health");
  if (healthCtx) {
    new Chart(healthCtx, {
      type: "bar",
      data: {
        labels: fleet.projects.map((p) => p.project),
        datasets: [{
          label: "Health Score",
          data: fleet.projects.map((p) => p.health),
          backgroundColor: fleet.projects.map((p) => healthColor(p.health)),
        }],
      },
      options: { responsive: true, scales: { y: { beginAtZero: true, max: 100 } }, plugins: { legend: { display: false } } },
    });
  }

  const trendsCtx = document.getElementById("chart-fleet-trends");
  if (trendsCtx && fleet.fleet_trends && fleet.fleet_trends.days.length) {
    const days = fleet.fleet_trends.days;
    const buckets = fleet.fleet_trends.buckets;
    new Chart(trendsCtx, {
      type: "line",
      data: {
        labels: days,
        datasets: [
          { label: "Retrievals", data: buckets.map((b) => b.retrievals), borderColor: "#6366f1", fill: false, tension: 0.3 },
          { label: "Logs", data: buckets.map((b) => b.logs), borderColor: "#22c55e", fill: false, tension: 0.3 },
          { label: "Consolidations", data: buckets.map((b) => b.consolidations), borderColor: "#eab308", fill: false, tension: 0.3 },
        ],
      },
      options: { responsive: true, plugins: { legend: { position: "bottom" } }, scales: { x: { ticks: { maxTicksLimit: 10 } } } },
    });
  } else if (trendsCtx) {
    new Chart(trendsCtx, { type: "bar", data: { labels: ["No data"], datasets: [{ data: [0], backgroundColor: "#2a2e3a" }] }, options: { responsive: true, plugins: { legend: { display: false } } } });
  }
}

function healthClass(score) {
  if (score >= 70) return "good";
  if (score >= 40) return "warn";
  return "bad";
}

function healthColor(score) {
  if (score >= 70) return "#22c55e";
  if (score >= 40) return "#eab308";
  return "#ef4444";
}

function avgHealth(projects) {
  if (!projects.length) return "—";
  return Math.round(projects.reduce((a, p) => a + p.health, 0) / projects.length);
}

// Project selector in fleet view updates the detail panel and highlights the row
document.getElementById("fleet-content").addEventListener("change", (ev) => {
  if (ev.target.id !== "fleet-project") return;
  const id = ev.target.value;
  document.querySelectorAll("#fleet-content tbody tr").forEach((tr) => tr.classList.remove("highlight"));
  if (!id) {
    document.getElementById("fleet-project-detail").innerHTML = "";
    return;
  }
  const row = document.querySelector(`#fleet-content tbody tr[data-project="${CSS.escape(id)}"]`);
  if (row) row.classList.add("highlight");
  const project = fleetData?.projects?.find((p) => p.project === id);
  document.getElementById("fleet-project-detail").innerHTML = project
    ? `<span class="health-pill ${healthClass(project.health)}">${project.health}</span> ${project.domains.length} domains · ${project.retrievals} retrievals · ${project.logs} logs`
    : "";
});
