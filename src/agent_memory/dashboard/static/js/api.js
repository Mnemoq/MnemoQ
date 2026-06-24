"use strict";

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------
const API = {
  base: "/api",
  async get(path) {
    const r = await fetch(this.base + path);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(this.base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail?.message || err.message || `${r.status}`);
    }
    return r.json();
  },
  async put(path, body) {
    const r = await fetch(this.base + path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail?.message || err.message || `${r.status}`);
    }
    return r.json();
  },
};

// ---------------------------------------------------------------------------
// Shared utils
// ---------------------------------------------------------------------------
function toast(msg, type = "info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// WebSocket client
// ---------------------------------------------------------------------------
const WS = {
  _socket: null,
  _handlers: [],
  _reconnectDelay: 1000,
  _maxReconnectDelay: 30000,

  connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/events`;
    try {
      this._socket = new WebSocket(url);
    } catch {
      return;
    }
    this._socket.onopen = () => {
      this._reconnectDelay = 1000;
    };
    this._socket.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        this._handlers.forEach((h) => h(event));
      } catch {}
    };
    this._socket.onclose = () => {
      setTimeout(() => this.connect(), this._reconnectDelay);
      this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxReconnectDelay);
    };
    this._socket.onerror = () => {
      try { this._socket.close(); } catch {}
    };
  },

  onEvent(handler) {
    this._handlers.push(handler);
  },
};
