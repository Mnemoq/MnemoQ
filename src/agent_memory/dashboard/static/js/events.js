"use strict";

// ---------------------------------------------------------------------------
// Event Log — subscribes to WS events from api.js
// ---------------------------------------------------------------------------
const EventLog = {
  events: [],
  maxEvents: 100,
  body: null,
  countEl: null,

  init() {
    this.body = document.getElementById("event-log-body");
    this.countEl = document.getElementById("event-log-count");
    WS.onEvent((event) => this.addEvent(event));
  },

  addEvent(event) {
    this.events.unshift({ ...event, received: new Date().toISOString() });
    if (this.events.length > this.maxEvents) this.events.pop();
    this.render();
  },

  render() {
    if (!this.body) return;
    this.countEl.textContent = this.events.length;
    this.body.innerHTML = this.events.map((e) => {
      const time = e.received.slice(11, 19);
      const type = e.event || "unknown";
      const status = e.status || "";
      const cls = status === "ok" || status === "reported" || status === "added" ? "event-ok"
        : status === "quarantined" || status === "error" ? "event-error"
        : "event-info";
      return `<div class="event-row ${cls}"><span class="event-time">${time}</span> <span class="event-type">${type}</span>${status ? ` <span class="event-status">${status}</span>` : ""}</div>`;
    }).join("");
  },
};

document.addEventListener("DOMContentLoaded", () => {
  EventLog.init();
  WS.connect();
});
