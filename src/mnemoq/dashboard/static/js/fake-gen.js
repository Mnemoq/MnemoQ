// Copyright (C) 2026 Mnemoq
// SPDX-License-Identifier: AGPL-3.0-or-later
"use strict";

// ---------------------------------------------------------------------------
// Fake Generator view
// ---------------------------------------------------------------------------
const DOMAINS = [
  "auto", "ui", "data", "tooling", "performance", "testing", "security",
  "api", "backend", "frontend", "database", "deployment", "documentation",
];

let _fakeGenSSE = null;
let _fakeGenPollTimer = null;

function loadFakeGen() {
  const el = document.getElementById("fake-gen-content");
  el.innerHTML = `
    <div class="form-card fake-gen-form">
      <label>Batch Name
        <input type="text" id="fg-batch-name" placeholder="e.g. UI Stress Test" class="input" style="width:200px">
      </label>
      <label>Script
        <select id="fg-script" class="select">
          <option value="sim_dialogue">sim_dialogue</option>
          <option value="generate_fakes">generate_fakes</option>
        </select>
      </label>
      <label id="fg-count-label">Turns
        <input type="number" id="fg-count" value="20" min="1" class="input" style="width:80px">
      </label>
      <label>Seed
        <input type="number" id="fg-seed" value="42" class="input" style="width:80px">
      </label>
      <label>Mode
        <div class="radio-group">
          <label class="radio-label"><input type="radio" name="fg-mode" value="direct" checked> Direct</label>
          <label class="radio-label"><input type="radio" name="fg-mode" value="pipeline"> Pipeline</label>
        </div>
      </label>
      <label>Domain
        <select id="fg-domain" class="select">
          ${DOMAINS.map(d => `<option value="${d}">${d}</option>`).join("")}
        </select>
      </label>
      <div class="fake-gen-checkboxes">
        <label class="checkbox-label"><input type="checkbox" id="fg-clean"> Clean (delete before run)</label>
        <label class="checkbox-label"><input type="checkbox" id="fg-confirm"> Confirm (required for clean/pipeline)</label>
        <label class="checkbox-label" id="fg-to-fakes-wrap"><input type="checkbox" id="fg-to-fakes"> To Fakes (sim_dialogue only)</label>
        <label class="checkbox-label"><input type="checkbox" id="fg-dry-run"> Dry Run (preview only)</label>
        <label class="checkbox-label"><input type="checkbox" id="fg-auto-switch"> Auto-switch to Fake Data after run</label>
      </div>
      <label id="fg-transcript-wrap">Transcript
        <input type="text" id="fg-transcript" placeholder="path/to/transcript.jsonl" class="input" style="width:240px">
      </label>
      <div class="fake-gen-actions">
        <button id="fg-start" class="btn btn-primary" disabled><i data-lucide="play"></i> Start</button>
        <button id="fg-stop" class="btn btn-ghost"><i data-lucide="square"></i> Stop</button>
      </div>
      <div class="fake-gen-delete-bar">
        <label class="checkbox-label"><input type="checkbox" id="fg-delete-confirm"> Confirm delete</label>
        <button id="fg-delete-fakes" class="btn btn-danger"><i data-lucide="trash-2"></i> Delete All Batches</button>
      </div>
    </div>
    <div class="fake-gen-output-wrap">
      <h3>Output (live stream)</h3>
      <div id="fg-output" class="fake-gen-output"></div>
    </div>
    <div class="batch-section">
      <h3>Batches</h3>
      <div id="fg-batch-list" class="batch-table-wrap"></div>
    </div>
  `;
  lucide?.createIcons();

  const scriptSel = document.getElementById("fg-script");
  scriptSel.addEventListener("change", updateScriptUI);
  updateScriptUI();

  const batchNameInput = document.getElementById("fg-batch-name");
  const startBtn = document.getElementById("fg-start");
  batchNameInput.addEventListener("input", () => {
    startBtn.disabled = !batchNameInput.value.trim();
  });

  document.getElementById("fg-start").addEventListener("click", startFakeGen);
  document.getElementById("fg-stop").addEventListener("click", stopFakeGen);
  document.getElementById("fg-delete-fakes").addEventListener("click", deleteFakeData);

  // Check if a run is already in progress
  checkExistingRun();
  // Load batch list
  loadBatchList();
}

function updateScriptUI() {
  const script = document.getElementById("fg-script").value;
  const countLabel = document.getElementById("fg-count-label");
  const countInput = document.getElementById("fg-count");
  const toFakesWrap = document.getElementById("fg-to-fakes-wrap");
  const transcriptWrap = document.getElementById("fg-transcript-wrap");

  if (script === "sim_dialogue") {
    countLabel.firstChild.textContent = "Turns";
    countInput.value = countInput.value || 20;
    toFakesWrap.style.display = "";
    transcriptWrap.style.display = "";
  } else {
    countLabel.firstChild.textContent = "Count";
    countInput.value = countInput.value || 50;
    toFakesWrap.style.display = "none";
    transcriptWrap.style.display = "none";
  }
}

async function checkExistingRun() {
  try {
    const status = await API.get("/fake-gen/status");
    if (status.status === "running") {
      appendOutput("[run already in progress — connecting to stream]");
      connectSSE();
    }
  } catch {}
}

async function startFakeGen() {
  const batchName = document.getElementById("fg-batch-name").value.trim();
  if (!batchName) {
    toast("Batch name is required", "error");
    return;
  }
  const script = document.getElementById("fg-script").value;
  const count = parseInt(document.getElementById("fg-count").value, 10) || 20;
  const seed = parseInt(document.getElementById("fg-seed").value, 10);
  const mode = document.querySelector('input[name="fg-mode"]:checked').value;
  const domain = document.getElementById("fg-domain").value;
  const clean = document.getElementById("fg-clean").checked;
  const confirm = document.getElementById("fg-confirm").checked;
  const dryRun = document.getElementById("fg-dry-run").checked;
  const autoSwitch = document.getElementById("fg-auto-switch").checked;
  const toFakes = document.getElementById("fg-to-fakes").checked;
  const transcript = document.getElementById("fg-transcript").value.trim();

  const body = {
    batch_name: batchName,
    script, turns: count, mode, domain, seed,
    clean, confirm, dry_run: dryRun, auto_switch: autoSwitch,
  };
  if (script === "sim_dialogue") {
    body.to_fakes = toFakes;
    if (transcript) body.transcript_path = transcript;
  }

  document.getElementById("fg-output").innerHTML = "";
  try {
    await API.post("/fake-gen/start", body);
    appendOutput("[run started]");
    connectSSE();
  } catch (e) {
    toast(`Start failed: ${e.message}`, "error");
  }
}

async function stopFakeGen() {
  try {
    await API.post("/fake-gen/stop", {});
    appendOutput("[stop requested]");
    toast("Stop requested", "info");
  } catch (e) {
    toast(`Stop failed: ${e.message}`, "error");
  }
}

function connectSSE() {
  if (_fakeGenSSE) _fakeGenSSE.close();
  if (_fakeGenPollTimer) { clearInterval(_fakeGenPollTimer); _fakeGenPollTimer = null; }

  _fakeGenSSE = new EventSource("/api/fake-gen/stream");
  _fakeGenSSE.onmessage = (e) => appendOutput(e.data);
  _fakeGenSSE.addEventListener("stderr", (e) => appendOutput("[stderr] " + e.data));
  _fakeGenSSE.addEventListener("done", (e) => {
    _fakeGenSSE.close();
    _fakeGenSSE = null;
    try {
      const info = JSON.parse(e.data);
      appendOutput(`[done: status=${info.status}, exit_code=${info.exit_code}]`);
      if (info.status === "done") {
        toast("Generation completed", "success");
        loadBatchList();
      } else if (info.status === "cancelled") {
        toast("Generation cancelled", "info");
      } else {
        toast("Generation failed", "error");
      }
    } catch {
      appendOutput("[done]");
    }
  });
  _fakeGenSSE.onerror = () => {
    _fakeGenSSE.close();
    _fakeGenSSE = null;
    // Fallback to polling
    _fakeGenPollTimer = setInterval(pollStatus, 500);
  };
}

async function pollStatus() {
  try {
    const status = await API.get("/fake-gen/status");
    const out = document.getElementById("fg-output");
    const expected = status.stdout_lines.length + status.stderr_lines.length;
    if (out.dataset.polled === undefined) out.dataset.polled = 0;
    const polled = parseInt(out.dataset.polled, 10);
    const allLines = [
      ...status.stdout_lines.map(l => ({ text: l, stderr: false })),
      ...status.stderr_lines.map(l => ({ text: l, stderr: true })),
    ];
    for (let i = polled; i < allLines.length; i++) {
      appendOutput(allLines[i].stderr ? "[stderr] " + allLines[i].text : allLines[i].text);
    }
    out.dataset.polled = String(allLines.length);

    if (status.status !== "running") {
      clearInterval(_fakeGenPollTimer);
      _fakeGenPollTimer = null;
      appendOutput(`[done: status=${status.status}, exit_code=${status.exit_code}]`);
    }
  } catch {
    clearInterval(_fakeGenPollTimer);
    _fakeGenPollTimer = null;
  }
}

function appendOutput(text) {
  const out = document.getElementById("fg-output");
  if (!out) return;
  const line = document.createElement("div");
  line.className = "fake-gen-output-line";
  line.textContent = text;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}

async function deleteFakeData() {
  const confirm = document.getElementById("fg-delete-confirm").checked;
  if (!confirm) {
    toast("Check 'Confirm delete' first", "error");
    return;
  }
  try {
    const result = await API.del("/fake-gen/batches", { confirm: true });
    toast(`Deleted ${result.deleted} batches`, "success");
    appendOutput(`[deleted ${result.deleted} batches]`);
    document.getElementById("fg-delete-confirm").checked = false;
    loadBatchList();
  } catch (e) {
    toast(`Delete failed: ${e.message}`, "error");
  }
}

async function loadBatchList() {
  try {
    const data = await API.get("/fake-gen/batches");
    renderBatchList(data.batches || []);
  } catch {
    renderBatchList([]);
  }
}

function renderBatchList(batches) {
  const el = document.getElementById("fg-batch-list");
  if (!el) return;
  if (batches.length === 0) {
    el.innerHTML = '<div class="placeholder">No batches yet</div>';
    return;
  }
  const rows = batches.map(b => `
    <div class="batch-row${b.active ? "" : " inactive"}">
      <label class="checkbox-label">
        <input type="checkbox" class="fg-batch-toggle" data-slug="${b.slug}" ${b.active ? "checked" : ""}>
      </label>
      <span class="batch-name">${escapeHtml(b.name)}</span>
      <span class="batch-category">${escapeHtml(b.category)}</span>
      <span class="batch-count">${b.entry_count}</span>
      <span class="batch-created">${escapeHtml(b.created_at || "")}</span>
      <button class="btn btn-danger btn-sm fg-batch-delete" data-slug="${b.slug}"><i data-lucide="trash-2"></i></button>
    </div>
  `).join("");
  el.innerHTML = `
    <div class="batch-row batch-row-header">
      <span></span>
      <span>Name</span>
      <span>Category</span>
      <span>Entries</span>
      <span>Created</span>
      <span></span>
    </div>
    ${rows}
  `;
  lucide?.createIcons();

  el.querySelectorAll(".fg-batch-toggle").forEach(cb => {
    cb.addEventListener("change", () => toggleBatch(cb.dataset.slug, cb.checked));
  });
  el.querySelectorAll(".fg-batch-delete").forEach(btn => {
    btn.addEventListener("click", () => deleteBatch(btn.dataset.slug));
  });
}

async function toggleBatch(slug, active) {
  try {
    await API.patch(`/fake-gen/batches/${slug}`, { active });
    toast(`Batch ${active ? "activated" : "deactivated"}`, "info");
  } catch (e) {
    toast(`Toggle failed: ${e.message}`, "error");
    loadBatchList();
  }
}

async function deleteBatch(slug) {
  if (!confirm(`Delete batch '${slug}'?`)) return;
  try {
    await API.del(`/fake-gen/batches/${slug}`, { confirm: true });
    toast("Batch deleted", "success");
    loadBatchList();
  } catch (e) {
    toast(`Delete failed: ${e.message}`, "error");
  }
}
