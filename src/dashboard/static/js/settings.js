"use strict";

// ---------------------------------------------------------------------------
// Settings view
// ---------------------------------------------------------------------------
async function loadSettings() {
  try {
    const [health, config] = await Promise.all([
      API.get("/health"),
      API.get("/config"),
    ]);
    const el = document.getElementById("settings-content");
    el.innerHTML = `
      <div class="metric-card" style="margin-bottom:16px">
        <div class="label">Engine Version</div>
        <div class="value" style="font-size:18px">${health.version || "—"}</div>
      </div>
      <div class="chart-card">
        <h3>Config Editor</h3>
        <p style="margin-bottom:8px;color:var(--text-dim);font-size:12px">Edit config.json directly. Save to apply.</p>
        <textarea id="config-editor" class="config-editor" spellcheck="false">${escapeHtml(JSON.stringify(config, null, 2))}</textarea>
        <button id="config-save" class="btn btn-primary" style="margin-top:8px">Save Config</button>
      </div>
    `;

    document.getElementById("config-save").addEventListener("click", async () => {
      const text = document.getElementById("config-editor").value;
      try {
        const parsed = JSON.parse(text);
        await API.put("/config", parsed);
        toast("Config saved", "success");
      } catch (e) {
        if (e instanceof SyntaxError) toast(`Invalid JSON: ${e.message}`, "error");
        else toast(`Save failed: ${e.message}`, "error");
      }
    });
  } catch (e) {
    document.getElementById("settings-content").innerHTML = `<p class="placeholder">Error: ${e.message}</p>`;
  }
}
