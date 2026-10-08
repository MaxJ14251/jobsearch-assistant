// Fills the application form on this page when you ask, from your own
// dashboard, and shows what it did (plan 30, ADR 0031).
//
// It never submits: no submit() or requestSubmit(), no synthetic clicks or
// key presses, nothing in a CAPTCHA frame. You review the real form and press
// the page's own Submit. tests/test_extension.py holds this file to that.
"use strict";

(function () {
  if (window.__jsaFillLoaded) return;
  window.__jsaFillLoaded = true;
  const F = globalThis.JSAFill;
  if (!F) return;

  const host = location.hostname;
  const BOARD = host.endsWith("greenhouse.io") ? "greenhouse"
    : host === "jobs.lever.co" ? "lever"
    : host === "jobs.ashbyhq.com" ? "ashby"
    // The fixture forms in test/forms/ load this file as a page script.
    // Installed, it runs in Chrome's isolated world, where no page can set this.
    : ["", "localhost", "127.0.0.1"].includes(host) ? window.__jsaTestBoard || null : null;
  if (!BOARD) return;

  const AMBER = "2px solid #d18b00";
  const GREEN = "2px solid rgba(47, 111, 94, 0.55)";

  let data = null;           // the dashboard's /ext/fill answer
  let undo = [];             // what to put back
  let attached = { resume: null, cover_letter: null };

  function ask(message) {
    return chrome.runtime.sendMessage(message).then((answer) => {
      if (!answer || !answer.ok) throw new Error((answer && answer.error) || "No answer.");
      return answer.result;
    });
  }

  // --- the form ---------------------------------------------------------------

  function visible(el) {
    if (el.disabled) return false;
    if (el.type === "hidden") return false;
    if (el.closest("[aria-hidden='true']")) return false;
    // File inputs are often hidden behind a styled button; they still count.
    if (el.type === "file") return true;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }

  function text(node) {
    return node ? (node.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  function labelOf(el) {
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const joined = by.split(/\s+/).map((id) => text(document.getElementById(id))).join(" ").trim();
      if (joined) return joined;
    }
    if (el.id) {
      const tag = document.querySelector("label[for='" + CSS.escape(el.id) + "']");
      if (text(tag)) return text(tag);
    }
    const wrapping = el.closest("label");
    if (text(wrapping)) return text(wrapping);
    const group = el.closest("fieldset, .application-question, .field, [class*='question'], [class*='Field']");
    if (group) {
      const head = group.querySelector("legend, label, .application-label, [class*='label']");
      if (text(head)) return text(head);
    }
    return el.getAttribute("aria-label") || el.getAttribute("placeholder") || "";
  }

  function fieldType(el) {
    if (el.tagName === "TEXTAREA") return "textarea";
    if (el.tagName === "SELECT") return "select";
    return (el.type || "text").toLowerCase();
  }

  // One entry per question: radios of one name are one question.
  function questions() {
    const out = [];
    const radios = new Map();
    for (const el of document.querySelectorAll("input, textarea, select")) {
      if (!visible(el)) continue;
      const type = fieldType(el);
      if (["submit", "button", "reset", "image", "checkbox", "search"].includes(type)) continue;
      if (type === "radio") {
        const name = el.name || el.id;
        if (!radios.has(name)) radios.set(name, []);
        radios.get(name).push(el);
        continue;
      }
      out.push({ els: [el], type, id: el.id, name: el.name, label: labelOf(el),
                 required: el.required, ariaRequired: el.getAttribute("aria-required") });
    }
    for (const [name, els] of radios) {
      const group = els[0].closest("fieldset, [role='radiogroup'], .application-question, .field");
      const head = group && group.querySelector("legend, .application-label, label");
      out.push({ els, type: "radio", id: "", name, label: text(head) || labelOf(els[0]),
                 required: els.some((e) => e.required),
                 ariaRequired: group && group.getAttribute("aria-required") });
    }
    return out;
  }

  // --- setting values the way the page's own code expects ---------------------

  function native(el, prop, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, prop).set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function mark(el, outline) {
    undo.push({ restore: () => { el.style.outline = el.dataset.jsaOutline || ""; } });
    if (!("jsaOutline" in el.dataset)) el.dataset.jsaOutline = el.style.outline || "";
    el.style.outline = outline;
  }

  async function attach(el, value, key) {
    if (el.files && el.files.length) return false;          // you chose one already
    const got = await ask({ type: "document", id: value.document.id });
    const raw = atob(got.data);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const file = new File([bytes], value.document.filename || key + ".docx",
                          { type: got.type || "application/octet-stream" });
    const list = new DataTransfer();
    list.items.add(file);
    el.files = list.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    attached[key] = value.document;
    undo.push({ restore: () => { el.value = ""; attached[key] = null; } });
    return true;
  }

  function apply(q, value) {
    const el = q.els[0];
    if (value.kind === "text") {
      if (el.value && el.value.trim()) return false;        // never over your typing
      const before = el.value;
      native(el, "value", value.value);
      undo.push({ restore: () => native(el, "value", before) });
      return true;
    }
    if (value.kind === "yesno" && q.type === "select") {
      if (el.selectedIndex > 0) return false;
      const pick = F.chooseYesNo(Array.from(el.options, (o) => o.text), value.value);
      if (pick < 0) return false;
      const before = el.value;
      native(el, "value", el.options[pick].value);
      undo.push({ restore: () => native(el, "value", before) });
      return true;
    }
    if (value.kind === "yesno" && q.type === "radio") {
      if (q.els.some((r) => r.checked)) return false;
      const pick = F.chooseYesNo(q.els.map((r) => labelOf(r) || r.value), value.value);
      if (pick < 0) return false;
      native(q.els[pick], "checked", true);
      undo.push({ restore: () => native(q.els[pick], "checked", false) });
      return true;
    }
    return false;
  }

  async function fillForm() {
    data = await ask({ type: "fill" });
    if (data.state !== "ready") return { data, filled: [], missing: [], skipped: [] };
    const filled = [], missing = [], skipped = [];
    for (const q of questions()) {
      const key = F.keyFor({ board: BOARD, id: q.id, name: q.name, label: q.label });
      const required = F.isRequired(q);
      const name = q.label || q.name || q.id || "a field";
      if (key === "salary") {
        skipped.push(name + ": salary is never filled for you");
        q.els.forEach((el) => mark(el, AMBER));
        continue;
      }
      const value = F.valueFor(key, data, q.type);
      let done = false;
      if (value && value.kind === "file" && q.type === "file") {
        try {
          done = await attach(q.els[0], value, key);
        } catch (err) {
          skipped.push(name + ": " + err.message);
        }
      } else if (value) {
        done = apply(q, value);
      }
      if (done) {
        filled.push({ name, source: value.source });
        q.els.forEach((el) => mark(el, GREEN));
      } else if (required) {
        missing.push(name);
        q.els.forEach((el) => mark(el, AMBER));
      }
    }
    return { data, filled, missing, skipped };
  }

  function undoFill() {
    for (const step of undo.reverse()) {
      try { step.restore(); } catch (err) { /* the page removed the field */ }
    }
    undo = [];
  }

  // --- the panel --------------------------------------------------------------

  const panelHost = document.createElement("div");
  const shadow = panelHost.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    .box { position: fixed; right: 16px; bottom: 16px; z-index: 2147483646; width: 340px;
           max-width: calc(100vw - 32px); max-height: 70vh; overflow: auto;
           font: 13px/1.45 system-ui, sans-serif; color: #1f2420; background: #fbfcfa;
           border: 1px solid #c9d0c8; border-radius: 6px; box-shadow: 0 6px 24px rgba(0,0,0,.18);
           padding: 12px 14px; }
    @media (prefers-color-scheme: dark) {
      .box { color: #e7ebe6; background: #1c211d; border-color: #3a413c; }
      .meta { color: #a3aba4 !important; }
    }
    h2 { font-size: 14px; margin: 0 0 6px; }
    .meta { color: #5d655f; }
    ul { margin: 6px 0 8px; padding-left: 18px; }
    .warn { color: #a05a00; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    button { font: inherit; padding: 6px 10px; border-radius: 4px; cursor: pointer;
             border: 1px solid #2f6f5e; background: #2f6f5e; color: #fff; }
    button.quiet { background: transparent; color: inherit; border-color: currentColor; }
    button:focus-visible { outline: 2px solid #d18b00; outline-offset: 2px; }
  `;
  const box = document.createElement("div");
  box.className = "box";
  box.setAttribute("role", "region");
  box.setAttribute("aria-label", "Job Search Assistant");
  shadow.append(style, box);

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.assign(node, props || {});
    for (const child of children || []) node.append(child);
    return node;
  }

  function list(items, cls) {
    return el("ul", { className: cls || "" }, items.map((t) => el("li", { textContent: t })));
  }

  function button(label, action, quiet) {
    const b = el("button", { type: "button", textContent: label, className: quiet ? "quiet" : "" });
    b.addEventListener("click", action);
    return b;
  }

  function show(children) {
    box.replaceChildren(...children);
    if (!panelHost.isConnected) document.documentElement.append(panelHost);
  }

  function intro(message) {
    show([
      el("h2", { textContent: "Job Search Assistant" }),
      el("p", { className: "meta", textContent: message ||
        "Fill this application from your tracker. You check it and press Submit yourself." }),
      el("div", { className: "row" }, [
        button("Fill this application", run),
        button("Hide", () => panelHost.remove(), true),
      ]),
    ]);
  }

  function report(result) {
    const { data: d, filled, missing, skipped } = result;
    if (d.state !== "ready") {
      intro(d.message || "Nothing to fill here.");
      return;
    }
    const parts = [
      el("h2", { textContent: d.job.title + " at " + d.job.company }),
      el("p", { className: "meta", textContent: "Filled " + filled.length + " field(s). Check " +
        "every one against the form, then press the page's Submit yourself." }),
    ];
    if (filled.length) parts.push(list(filled.map((f) => f.name + " (" + f.source + ")")));
    if (missing.length) {
      parts.push(el("p", { textContent: "Required, left for you (outlined):" }), list(missing));
    }
    const notes = [...(d.warnings || []), ...skipped,
                   ...(d.left_out || []).map((x) => x.note)];
    if (BOARD === "ashby") {
      notes.push("Questions answered with Yes/No buttons are left for you: " +
                 "this never clicks anything on the page.");
    }
    if (notes.length) parts.push(list(notes, "warn"));
    parts.push(el("div", { className: "row" }, [
      button("Undo fill", () => { undoFill(); intro("Fill undone."); }, true),
      button("I submitted this", submitted),
    ]));
    show(parts);
  }

  async function run() {
    show([el("p", { textContent: "Filling from your tracker…" })]);
    try {
      report(await fillForm());
    } catch (err) {
      intro(err.message);
    }
  }

  // Recorded only after you say so, twice if the resume isn't approved.
  async function submitted() {
    if (!data || data.state !== "ready") return;
    const resume = attached.resume || (data.documents || {}).resume;
    const what = resume ? " with resume v" + resume.version : " with no resume recorded";
    if (!window.confirm("Record that you submitted " + data.job.title + " at " +
                        data.job.company + what + "?\n\nOnly do this after you pressed " +
                        "the page's own Submit.")) return;
    const message = { type: "applied", job_id: data.job.id, confirm: "submitted",
                      resume: resume ? resume.id : "",
                      cover: attached.cover_letter ? attached.cover_letter.id : "" };
    try {
      let answer = await ask(message);
      if (answer.needs === "confirm_unapproved") {
        if (!window.confirm("That resume is not one you approved. Record it anyway?")) return;
        answer = await ask({ ...message, confirm_unapproved: true });
      }
      show([el("p", { textContent: answer.ok ? answer.message : answer.error })]);
    } catch (err) {
      show([el("p", { textContent: err.message })]);
    }
  }

  // Application forms render late; offer the panel once one appears.
  function hasForm() {
    return Boolean(document.querySelector("input[type='file'], input[type='email'], " +
                                          "input[name*='email' i], input[id*='email' i]"));
  }
  if (hasForm()) {
    intro();
  } else {
    const watch = new MutationObserver(() => {
      if (hasForm()) { watch.disconnect(); intro(); }
    });
    watch.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => watch.disconnect(), 20000);
  }
})();
