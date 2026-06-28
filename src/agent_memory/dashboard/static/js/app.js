"use strict";

// ---------------------------------------------------------------------------
// Project switcher
// ---------------------------------------------------------------------------
async function initProjectSwitcher() {
  const select = document.getElementById("project-switcher");
  try {
    const [projectsResp, activeResp] = await Promise.all([
      API.get("/projects"),
      API.get("/projects/active"),
    ]);
    const projects = projectsResp.projects || [];
    const activeId = activeResp.project_id || "";

    const options = [...projects];
    if (activeId && !options.some((p) => p.id === activeId)) {
      options.push({ id: activeId, path: activeResp.path || "" });
    }

    select.innerHTML = options
      .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)}</option>`)
      .join("");
    select.value = activeId;
  } catch (e) {
    select.innerHTML = "<option value=\"\">No projects</option>";
  }

  select.addEventListener("change", async () => {
    const projectId = select.value;
    if (!projectId) return;
    try {
      await API.post("/projects/switch", { project_id: projectId });
      toast(`Switched to project: ${projectId}`, "success");
      handleHash();
    } catch (e) {
      toast(`Switch failed: ${e.message}`, "error");
    }
  });
}

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
  initProjectSwitcher();
  handleHash();
  renderQueryHistory();
  lucide?.createIcons();
});
