// Copyright (C) 2026 Mnemoq
// SPDX-License-Identifier: AGPL-3.0-or-later
"use strict";

// ---------------------------------------------------------------------------
// Dashboard view
// ---------------------------------------------------------------------------
let activityChart = null;
let severityChart = null;

async function loadDashboard() {
  try {
    const [dashData, health] = await Promise.all([
      API.get("/metrics/dashboard"),
      API.get("/health"),
    ]);

    document.getElementById("engine-version").textContent = "v" + (health.version || "—");

    const stats = dashData.stats || {};
    const healthScore = dashData.health || 0;
    const badge = document.getElementById("health-badge");
    badge.textContent = `Health: ${healthScore}`;
    badge.className = "health-badge " + (healthScore >= 70 ? "good" : healthScore >= 40 ? "warn" : "bad");

    const cards = document.getElementById("dashboard-cards");
    cards.innerHTML = "";
    const cardData = [
      { label: "Total Entries", value: stats.total || 0, sub: `${stats.resolved || 0} resolved` },
      { label: "Unresolved", value: stats.unresolved || 0, sub: stats.sleep_cycle_due ? "Sleep cycle due!" : "Within threshold" },
      { label: "Avg Access", value: (stats.avg_access_count || 0).toFixed(1), sub: "access_count" },
      { label: "Avg Reinforcement", value: (stats.avg_reinforcement_count || 0).toFixed(1), sub: "reinforcement_count" },
      { label: "Verified", value: stats.verified || 0, sub: `${stats.unverified || 0} unverified` },
      { label: "Proven", value: stats.proven || 0, sub: "reinforcement >= 5" },
    ];
    cardData.forEach((c) => {
      const el = document.createElement("div");
      el.className = "metric-card";
      el.innerHTML = `<div class="label">${c.label}</div><div class="value">${c.value}</div><div class="sub">${c.sub}</div>`;
      cards.appendChild(el);
    });

    // Recommendations
    const recsEl = document.getElementById("dashboard-recommendations");
    const recs = dashData.recommendations || [];
    if (recs.length) {
      recsEl.innerHTML = "<h3>Recommendations</h3>" + recs.map((r) =>
        `<div class="rec-item ${r.priority}"><span class="rec-priority">${r.priority}</span> <strong>${r.category}</strong>: ${escapeHtml(r.message)} <span class="rec-action">${escapeHtml(r.action)}</span></div>`
      ).join("");
    } else {
      recsEl.innerHTML = "<h3>Recommendations</h3><p class='placeholder'>No recommendations. All good!</p>";
    }

    // Alerts
    const alertsEl = document.getElementById("dashboard-alerts");
    const alerts = dashData.alerts || [];
    alertsEl.innerHTML = "<h3>Alerts</h3>" + (alerts.length ? alerts.map((a) => `<div class="alert-item ${a.type}">${escapeHtml(a.message)}</div>`).join("") : "<p class='placeholder'>No active alerts.</p>");

    // Charts
    renderActivityChart(dashData.trends || {});
    renderSeverityChart(stats.severity_breakdown || {});

  } catch (e) {
    toast(`Failed to load dashboard: ${e.message}`, "error");
  }
}

function renderActivityChart(trends) {
  const ctx = document.getElementById("chart-activity");
  if (activityChart) activityChart.destroy();
  const days = trends.days || [];
  const buckets = trends.buckets || [];
  if (days.length) {
    activityChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: days,
        datasets: [
          { label: "Retrievals", data: buckets.map((b) => b.retrievals), borderColor: "#6366f1", fill: false, tension: 0.3 },
          { label: "Logs", data: buckets.map((b) => b.logs), borderColor: "#22c55e", fill: false, tension: 0.3 },
          { label: "Consolidations", data: buckets.map((b) => b.consolidations), borderColor: "#eab308", fill: false, tension: 0.3 },
        ],
      },
      options: { responsive: true, plugins: { legend: { display: true, position: "bottom" } }, scales: { x: { ticks: { maxTicksLimit: 10 } } } },
    });
  } else {
    activityChart = new Chart(ctx, {
      type: "bar",
      data: { labels: ["No data"], datasets: [{ data: [0], backgroundColor: "#2a2e3a" }] },
      options: { responsive: true, plugins: { legend: { display: false } } },
    });
  }
}

function renderSeverityChart(breakdown) {
  const ctx = document.getElementById("chart-severity");
  if (severityChart) severityChart.destroy();
  const labels = Object.keys(breakdown);
  const data = Object.values(breakdown);
  const colors = labels.map((l) => l === "critical" ? "#ef4444" : l === "major" ? "#f97316" : "#8b8fa3");
  severityChart = new Chart(ctx, {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: colors }] },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });
}

// Refresh dashboard button
document.getElementById("refresh-dashboard").addEventListener("click", loadDashboard);
