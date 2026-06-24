"use strict";

// ---------------------------------------------------------------------------
// Tab routing (hash-based)
// ---------------------------------------------------------------------------
const tabs = document.querySelectorAll(".nav-tabs li");
const views = document.querySelectorAll(".view");

function navigateTo(name) {
  location.hash = name;
}

function handleHash() {
  const name = location.hash.slice(1) || "dashboard";
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  views.forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "dashboard") loadDashboard();
  if (name === "learnings") loadLearnings();
  if (name === "metrics") loadMetricsSubtab();
  if (name === "consolidation") loadConsolidation();
  if (name === "fleet") loadFleet();
  if (name === "settings") loadSettings();
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => navigateTo(tab.dataset.tab));
});

window.addEventListener("hashchange", handleHash);

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  handleHash();
  renderQueryHistory();
  lucide?.createIcons();
});
