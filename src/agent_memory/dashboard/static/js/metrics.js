"use strict";

// ---------------------------------------------------------------------------
// Metrics Deep-Dive with sub-tabs
// ---------------------------------------------------------------------------
let metricsSubtab = "retrieval";

document.querySelectorAll(".sub-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".sub-tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    metricsSubtab = btn.dataset.subtab;
    loadMetricsSubtab();
  });
});

document.getElementById("metrics-range").addEventListener("change", loadMetricsSubtab);

async function loadMetricsSubtab() {
  const range = document.getElementById("metrics-range").value;
  const sinceParam = range && range !== "0" ? `?since=${getSinceDate(parseInt(range))}` : "";
  const el = document.getElementById("metrics-content");
  el.innerHTML = '<p class="placeholder">Loading...</p>';

  try {
    if (metricsSubtab === "retrieval") {
      const data = await API.get(`/metrics/retrieval-quality${sinceParam}`);
      el.innerHTML = renderRetrievalMetrics(data);
    } else if (metricsSubtab === "logging") {
      const data = await API.get(`/metrics${sinceParam}`);
      const l = data.logging || {};
      el.innerHTML = renderLoggingMetrics(l);
    } else if (metricsSubtab === "consolidation") {
      const data = await API.get("/metrics/consolidation-quality");
      el.innerHTML = renderConsolidationMetrics(data);
    } else if (metricsSubtab === "lifecycle") {
      const data = await API.get("/metrics/lifecycle");
      el.innerHTML = renderLifecycleMetrics(data);
    } else if (metricsSubtab === "agents") {
      const data = await API.get("/metrics/agents");
      el.innerHTML = renderAgentMetrics(data.agents || []);
    } else if (metricsSubtab === "dedup") {
      const data = await API.get("/metrics/dedup");
      el.innerHTML = renderDedupMetrics(data);
    }
  } catch (e) {
    el.innerHTML = `<p class="placeholder">Could not load data. Check that the engine is running.</p>`;
  }
}

function getSinceDate(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function renderRetrievalMetrics(r) {
  if (!r || !r.total_retrievals) return '<p class="placeholder">No retrieval events.</p>';
  return `
    <div class="cards-grid">
      <div class="metric-card"><div class="label">Total Retrievals</div><div class="value">${r.total_retrievals}</div></div>
      <div class="metric-card"><div class="label">Hit Rate</div><div class="value">${(r.hit_rate * 100).toFixed(1)}%</div><div class="sub">${r.hit_count} hits, ${r.empty_count} empty</div></div>
      <div class="metric-card"><div class="label">Avg Results</div><div class="value">${r.avg_results.toFixed(1)}</div></div>
      <div class="metric-card"><div class="label">Avg Top Score</div><div class="value">${r.avg_top_score.toFixed(3)}</div><div class="sub">Max: ${r.max_top_score.toFixed(3)}, Min: ${r.min_top_score.toFixed(3)}</div></div>
    </div>
    <div class="chart-card"><h3>Top Query Components</h3>${(r.top_query_components || []).map(([c, n]) => `<div class="bar-row"><span>${escapeHtml(c)}</span><div class="bar" style="width:${Math.min(n * 10, 100)}%">${n}</div></div>`).join("")}</div>
    <div class="chart-card"><h3>Top Query Domains</h3>${(r.top_query_domains || []).map(([d, n]) => `<div class="bar-row"><span>${escapeHtml(d)}</span><div class="bar" style="width:${Math.min(n * 10, 100)}%">${n}</div></div>`).join("")}</div>
  `;
}

function renderLoggingMetrics(l) {
  if (!l || !l.total_logs) return '<p class="placeholder">No logging events.</p>';
  const outcomes = Object.entries(l.outcomes || {}).map(([o, n]) => `<div class="metric-card"><div class="label">${o}</div><div class="value">${n}</div><div class="sub">${(n / l.total_logs * 100).toFixed(1)}%</div></div>`).join("");
  return `
    <div class="cards-grid">
      <div class="metric-card"><div class="label">Total Logs</div><div class="value">${l.total_logs}</div></div>
      <div class="metric-card"><div class="label">Added</div><div class="value">${l.added || 0}</div></div>
      <div class="metric-card"><div class="label">Dup Rate</div><div class="value">${(l.duplicate_rate * 100).toFixed(1)}%</div></div>
      <div class="metric-card"><div class="label">Quarantine Rate</div><div class="value">${(l.quarantine_rate * 100).toFixed(1)}%</div></div>
    </div>
    <div class="cards-grid">${outcomes}</div>
    ${l.quarantine_reasons ? `<div class="chart-card"><h3>Quarantine Reasons</h3>${Object.entries(l.quarantine_reasons).map(([r, n]) => `<div class="bar-row"><span>${escapeHtml(r)}</span><div class="bar" style="width:${Math.min(n * 10, 100)}%">${n}</div></div>`).join("")}</div>` : ""}
    <div class="chart-card"><h3>Agent Contributions</h3>${(l.agent_contributions || []).map(([a, n]) => `<div class="bar-row"><span>${escapeHtml(a)}</span><div class="bar" style="width:${Math.min(n * 5, 100)}%">${n}</div></div>`).join("")}</div>
    <div class="chart-card"><h3>Domain Distribution</h3>${(l.domain_distribution || []).map(([d, n]) => `<div class="bar-row"><span>${escapeHtml(d)}</span><div class="bar" style="width:${Math.min(n * 5, 100)}%">${n}</div></div>`).join("")}</div>
  `;
}

function renderConsolidationMetrics(c) {
  if (!c || !c.total_consolidations) return '<p class="placeholder">No consolidation events.</p>';
  setTimeout(() => {
    if (c.daily && c.daily.days && c.daily.days.length) {
      renderMultiLineChart("chart-consolidation-trend", "Consolidation Trends", c.daily.days, [
        { label: "Promotion Candidates", data: c.daily.promotion_candidates, borderColor: "#10b981" },
        { label: "Contradictions", data: c.daily.contradictions, borderColor: "#f59e0b" },
        { label: "Stale Entries", data: c.daily.stale_entries, borderColor: "#ef4444" },
      ]);
    }
  }, 0);
  return `
    <div class="cards-grid">
      <div class="metric-card"><div class="label">Total Consolidations</div><div class="value">${c.total_consolidations}</div></div>
      <div class="metric-card"><div class="label">Promotion Candidates</div><div class="value">${c.total_promotion_candidates}</div><div class="sub">Avg: ${(c.avg_promotion_candidates || 0).toFixed(1)}/sprint</div></div>
      <div class="metric-card"><div class="label">Contradictions</div><div class="value">${c.total_contradictions}</div></div>
      <div class="metric-card"><div class="label">Stale Entries</div><div class="value">${c.total_stale}</div></div>
    </div>
    <div class="charts-row">
      <div class="chart-card"><h3>Consolidation Trends (30d)</h3><canvas id="chart-consolidation-trend"></canvas></div>
    </div>
    <div class="chart-card"><h3>Sprints</h3><p>${(c.sprints || []).join(", ")}</p></div>
  `;
}

function renderLifecycleMetrics(lc) {
  if (!lc || !lc.total) return '<p class="placeholder">No lifecycle data.</p>';
  setTimeout(() => {
    if (lc.age_distribution) renderBarChart("chart-lifecycle-age", "Age Distribution", lc.age_distribution.labels, lc.age_distribution.values);
    if (lc.access_distribution) renderBarChart("chart-lifecycle-access", "Access Distribution", lc.access_distribution.labels, lc.access_distribution.values);
  }, 0);
  const zombies = lc.zombie_entries || [];
  return `
    <div class="cards-grid">
      <div class="metric-card"><div class="label">Total</div><div class="value">${lc.total}</div></div>
      <div class="metric-card"><div class="label">Resolved</div><div class="value">${lc.resolved}</div><div class="sub">${(lc.resolution_rate * 100).toFixed(1)}%</div></div>
      <div class="metric-card"><div class="label">Avg Age</div><div class="value">${lc.avg_age_days}d</div><div class="sub">Max: ${lc.max_age_days}d</div></div>
      <div class="metric-card"><div class="label">Avg Access</div><div class="value">${lc.avg_access_count}</div></div>
      <div class="metric-card"><div class="label">Avg Reinforcement</div><div class="value">${lc.avg_reinforcement_count}</div></div>
      <div class="metric-card"><div class="label">Zero Access</div><div class="value">${lc.zero_access}</div></div>
      <div class="metric-card"><div class="label">High Access (>=10)</div><div class="value">${lc.high_access}</div></div>
      <div class="metric-card"><div class="label">Zombies</div><div class="value">${lc.zombie_count || 0}</div></div>
    </div>
    <div class="charts-row">
      <div class="chart-card"><h3>Age Distribution</h3><canvas id="chart-lifecycle-age"></canvas></div>
      <div class="chart-card"><h3>Access Distribution</h3><canvas id="chart-lifecycle-access"></canvas></div>
    </div>
    ${zombies.length ? `<div class="chart-card"><h3>Zombie Entries</h3>
      <table class="metrics-table"><thead><tr><th>Agent</th><th>Domain</th><th>Severity</th><th>Age</th><th>Trigger</th></tr></thead><tbody>
        ${zombies.map(z => `<tr><td>${escapeHtml(z.source_agent)}</td><td>${escapeHtml(z.domain)}</td><td>${escapeHtml(z.severity)}</td><td>${z.age_days}d</td><td>${escapeHtml(z.trigger)}</td></tr>`).join("")}
      </tbody></table>
    </div>` : ""}
  `;
}

function renderAgentMetrics(agents) {
  if (!agents.length) return '<p class="placeholder">No agent data.</p>';
  setTimeout(() => {
    const hasTrend = agents.some(a => a.trend && a.trend.length);
    if (hasTrend) {
      const labels = agents[0].trend.map((_, i) => `D-${agents[0].trend.length - i - 1}`);
      renderMultiLineChart("chart-agents-trends", "Agent Log Trends (30d)", labels, agents.map(a => ({
        label: a.agent,
        data: a.trend,
      })));
    }
  }, 0);
  const severities = ["critical", "major", "minor"];
  const heatmapRows = agents.map(a => {
    const counts = a.severity_counts || {};
    return `<tr><td>${escapeHtml(a.agent)}</td>${severities.map(s => `<td>${counts[s] || 0}</td>`).join("")}</tr>`;
  }).join("");
  return `
    <div class="charts-row">
      <div class="chart-card"><h3>Agent Log Trends (30d)</h3><canvas id="chart-agents-trends"></canvas></div>
    </div>
    <div class="chart-card"><h3>Severity Heatmap</h3>
      <table class="metrics-table"><thead><tr><th>Agent</th><th>Critical</th><th>Major</th><th>Minor</th></tr></thead><tbody>
        ${heatmapRows}
      </tbody></table>
    </div>
    <table style="width:100%" class="metrics-table"><thead><tr><th>Agent</th><th>Entries</th><th>Resolved</th><th>Resolution Rate</th><th>Avg Importance</th><th>Domains</th></tr></thead><tbody>
      ${agents.map((a) => `<tr><td>${escapeHtml(a.agent)}</td><td>${a.entries}</td><td>${a.resolved}</td><td>${(a.resolution_rate * 100).toFixed(0)}%</td><td>${a.avg_importance}</td><td>${(a.domains || []).join(", ")}</td></tr>`).join("")}
    </tbody></table>
  `;
}

function renderDedupMetrics(d) {
  if (!d || !d.total_logs) return '<p class="placeholder">No dedup data.</p>';
  setTimeout(() => {
    if (d.daily && d.daily.days && d.daily.days.length) {
      renderMultiLineChart("chart-dedup-trend", "Dedup Trends (30d)", d.daily.days, [
        { label: "Duplicates", data: d.daily.duplicates, borderColor: "#8b5cf6" },
        { label: "Conflicts", data: d.daily.conflicts, borderColor: "#ef4444" },
        { label: "Added", data: d.daily.added, borderColor: "#10b981" },
      ]);
    }
  }, 0);
  const conflicts = d.conflicts_list || [];
  return `
    <div class="cards-grid">
      <div class="metric-card"><div class="label">Total Logs</div><div class="value">${d.total_logs}</div></div>
      <div class="metric-card"><div class="label">Added</div><div class="value">${d.added}</div></div>
      <div class="metric-card"><div class="label">Duplicates</div><div class="value">${d.duplicates}</div></div>
      <div class="metric-card"><div class="label">Semantic Dups</div><div class="value">${d.semantic_duplicates}</div></div>
      <div class="metric-card"><div class="label">Conflicts</div><div class="value">${d.conflicts}</div></div>
      <div class="metric-card"><div class="label">Dedup Rate</div><div class="value">${(d.dedup_rate * 100).toFixed(1)}%</div></div>
      <div class="metric-card"><div class="label">Avg Similarity</div><div class="value">${d.avg_similarity}</div><div class="sub">Max: ${d.max_similarity}</div></div>
    </div>
    <div class="charts-row">
      <div class="chart-card"><h3>Dedup Trends (30d)</h3><canvas id="chart-dedup-trend"></canvas></div>
    </div>
    ${conflicts.length ? `<div class="chart-card"><h3>Recent Conflicts</h3>
      <table class="metrics-table"><thead><tr><th>Time</th><th>New Agent</th><th>Matched Agent</th><th>Similarity</th><th>Trigger</th></tr></thead><tbody>
        ${conflicts.map(c => `<tr><td>${escapeHtml(c.ts)}</td><td>${escapeHtml(c.source_agent)}</td><td>${escapeHtml(c.matched_source_agent)}</td><td>${c.similarity_score.toFixed(3)}</td><td>${escapeHtml(c.trigger)}</td></tr>`).join("")}
      </tbody></table>
    </div>` : ""}
  `;
}

function renderBarChart(canvasId, title, labels, values) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;
  const ctx = canvas.getContext("2d");
  if (canvas._chart) {
    canvas._chart.destroy();
  }
  canvas._chart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: title,
        data: values,
        backgroundColor: "rgba(59, 130, 246, 0.6)",
        borderColor: "rgba(59, 130, 246, 1)",
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, grid: { color: "rgba(255,255,255,0.05)" }, ticks: { color: "#9ca3af" } },
        x: { grid: { display: false }, ticks: { color: "#9ca3af" } },
      },
    },
  });
}

function renderMultiLineChart(canvasId, title, labels, datasets) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;
  const ctx = canvas.getContext("2d");
  if (canvas._chart) {
    canvas._chart.destroy();
  }
  const colors = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];
  datasets.forEach((ds, i) => {
    ds.borderColor = ds.borderColor || colors[i % colors.length];
    ds.backgroundColor = ds.borderColor + "20";
    ds.tension = 0.3;
    ds.fill = false;
  });
  canvas._chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#e5e7eb" } },
      },
      scales: {
        y: { beginAtZero: true, grid: { color: "rgba(255,255,255,0.05)" }, ticks: { color: "#9ca3af" } },
        x: { grid: { display: false }, ticks: { color: "#9ca3af" } },
      },
    },
  });
}
