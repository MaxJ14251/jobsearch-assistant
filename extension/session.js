// The apply session (plan 31): which job you are on, and what you did with
// the ones before. Pure functions, no DOM, no network, no timers.
//
// Only three things move it on: you saying you submitted this one, you
// skipping it, or you ending the session. Nothing on a page, and no clock,
// can advance it. Loaded by background.js (importScripts), by content.js and
// by Node's test runner.

(function (root) {
  "use strict";

  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  // "<board kind>:<posting id>" for an application page (or its confirmation
  // page, which keeps the posting id in its address), or null.
  function pageKey(address) {
    let url;
    try { url = new URL(address); } catch (err) { return null; }
    if (url.protocol !== "https:") return null;
    const parts = url.pathname.split("/").filter(Boolean);
    const host = url.hostname;
    if (host === "boards.greenhouse.io" || host === "job-boards.greenhouse.io") {
      if (parts[0] === "embed" && parts[1] === "job_app") {
        const token = url.searchParams.get("token") || "";
        return /^\d+$/.test(token) ? "greenhouse:" + token : null;
      }
      return parts[1] === "jobs" && /^\d+$/.test(parts[2] || "") ? "greenhouse:" + parts[2] : null;
    }
    if (host === "jobs.lever.co" && UUID.test(parts[1] || "")) return "lever:" + parts[1].toLowerCase();
    if (host === "jobs.ashbyhq.com" && UUID.test(parts[1] || "")) return "ashby:" + parts[1].toLowerCase();
    return null;
  }

  function itemKey(item) {
    return item ? item.kind + ":" + String(item.external_id).toLowerCase() : null;
  }

  function start(items) {
    const kept = (items || []).map((i) => ({
      job_id: i.job_id, title: i.title, company: i.company, kind: i.kind,
      external_id: String(i.external_id), apply_url: i.apply_url,
      resume_version: i.resume_version, warnings: i.warnings || [],
    }));
    return { items: kept, index: 0, submitted: [], skipped: [], done: kept.length === 0,
             ended: false };
  }

  function current(state) {
    return state && !state.done ? state.items[state.index] || null : null;
  }

  // Moves on from the current job only, and only if it is the one named.
  function advance(state, jobId, list) {
    const now = current(state);
    if (!now || now.job_id !== jobId) return state;
    const next = { ...state, [list]: [...state[list], jobId], index: state.index + 1 };
    next.done = next.index >= next.items.length;
    return next;
  }

  function submitted(state, jobId) { return advance(state, jobId, "submitted"); }
  function skip(state, jobId) { return advance(state, jobId, "skipped"); }
  function end(state) { return state ? { ...state, done: true, ended: true } : state; }

  function isQueued(state, address) {
    return Boolean(state && state.items.some((i) => i.apply_url === address));
  }

  function summary(state) {
    if (!state) return { submitted: 0, skipped: 0, left: 0, total: 0 };
    return { submitted: state.submitted.length, skipped: state.skipped.length,
             left: Math.max(state.items.length - state.index, 0), total: state.items.length };
  }

  const api = { pageKey, itemKey, start, current, submitted, skip, end, isQueued, summary };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.JSASession = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
