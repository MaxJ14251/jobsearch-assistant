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
  // The fixture forms in test/forms/ load this file as a page script on a
  // local server, and say which board and posting they stand for. Installed,
  // it runs in Chrome's isolated world, where no page can set these, and its
  // manifest never runs it on a local address anyway.
  const LOCAL = ["", "localhost", "127.0.0.1"].includes(host);
  const BOARD = host.endsWith("greenhouse.io") ? "greenhouse"
    : host === "jobs.lever.co" ? "lever"
    : host === "jobs.ashbyhq.com" ? "ashby"
    : LOCAL ? window.__jsaTestBoard || null : null;
  if (!BOARD) return;

  const AMBER = "2px solid #d18b00";
  const GREEN = "2px solid rgba(47, 111, 94, 0.55)";
  const BLUE = "2px dashed #3b6fb6";        // an answer you chose: review it

  let data = null;           // the dashboard's /ext/fill answer
  let undo = [];             // what to put back
  let attached = { resume: null, cover_letter: null };
  let openQuestions = [];    // {el, label, maxlength, required, box, status}
  const inserted = new Map(); // label -> {text, source} you chose with Use this

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
    openQuestions = [];
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
      const first = q.els[0];
      const cap = first.maxLength > 0 ? first.maxLength : null;
      if (done) {
        filled.push({ name, source: value.source });
        q.els.forEach((el) => mark(el, GREEN));
      } else if (F.isOpenEnded({ type: q.type, label: q.label, maxlength: cap, key,
                                 empty: !String(first.value || "").trim() })) {
        // Listed under "Questions to answer", with suggestions on request.
        openQuestions.push({ el: first, label: F.questionText(q.label), maxlength: cap,
                             required });
        if (required) mark(first, AMBER);
      } else if (required) {
        missing.push(name);
        q.els.forEach((el) => mark(el, AMBER));
      }
    }
    rememberFill();
    return { data, filled, missing, skipped };
  }

  // What this tab filled, for the confirmation page's question (plan 36).
  function rememberFill() {
    if (!PAGE || !data || data.state !== "ready") return;
    const docs = data.documents || {};
    const resume = attached.resume || docs.resume || null;
    const cover = attached.cover_letter || null;
    ask({ type: "fill_memory_set", entry: {
      job_id: data.job.id, page_key: PAGE, title: data.job.title, company: data.job.company,
      resume_id: resume ? resume.id : null, resume_version: resume ? resume.version : null,
      cover_id: cover ? cover.id : null, cover_version: cover ? cover.version : null,
      filled_at: Date.now() } }).catch(() => {});
  }

  function undoFill() {
    for (const step of undo.reverse()) {
      try { step.restore(); } catch (err) { /* the page removed the field */ }
    }
    undo = [];
    inserted.clear();
  }

  // --- the panel --------------------------------------------------------------

  const panelHost = document.createElement("div");
  const shadow = panelHost.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    .box { position: fixed; right: 16px; bottom: 16px; z-index: 2147483646;
           width: min(380px, calc(100vw - 16px)); max-height: 80vh; overflow: auto;
           box-sizing: border-box; overflow-wrap: anywhere;
           font: 13px/1.45 system-ui, sans-serif; color: #1f2420; background: #fbfcfa;
           border: 1px solid #c9d0c8; border-radius: 6px; box-shadow: 0 6px 24px rgba(0,0,0,.18);
           padding: 12px 14px; }
    a { color: #2f6f5e; }
    @media (prefers-color-scheme: dark) {
      .box { color: #e7ebe6; background: #1c211d; border-color: #3a413c; }
      .meta { color: #a3aba4 !important; }
      a { color: #7cc2ad; }
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
    details.about { margin-top: 10px; border-top: 1px solid #c9d0c8; padding-top: 8px; }
    details.about summary { cursor: pointer; font-weight: 600; }
    details.about p { margin: 6px 0; }
    details.questions { margin-top: 10px; border-top: 1px solid #c9d0c8; padding-top: 8px; }
    details.questions > summary { cursor: pointer; font-weight: 600; }
    .question { margin: 10px 0 4px; }
    .question > p { margin: 0 0 4px; }
    .card { border: 1px solid #c9d0c8; border-radius: 4px; padding: 8px; margin: 6px 0; }
    .card p { margin: 4px 0; }
    .answer { white-space: pre-wrap; }
    .clamp { display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical;
             overflow: hidden; }
    .chip { display: inline-block; padding: 0 6px; border-radius: 9px; font-size: 12px;
            border: 1px solid currentColor; }
    .chip.model { color: #2f6f5e; }
    .chip.composed, .chip.yours { color: #5d655f; }
    @media (prefers-color-scheme: dark) {
      .card, details.questions { border-color: #3a413c; }
      .chip.model { color: #7cc2ad; }
      .chip.composed, .chip.yours { color: #a3aba4; }
    }
    @media (max-width: 600px) {
      .box { left: 8px; right: 8px; bottom: 8px; width: auto; max-height: 60vh; }
      button { min-height: 44px; }
      .card .row button { flex: 1 1 100%; }
    }
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
    if (aboutShown) box.append(aboutBox);
    if (!panelHost.isConnected) document.documentElement.append(panelHost);
    // Only once the panel is on screen: a page you never see the panel on
    // never asks, and so never has its board read.
    if (PAGE || LOCAL) loadAbout();
  }

  // --- about this company and role (plan 34) -------------------------------------
  // Read-only: what this posting asks for, the company's postings, and who
  // holds this kind of job nationwide. Collapsed until you open it. The
  // dashboard reads the company's board only when it says it should, once.

  const R = globalThis.JSAResearch;
  const aboutBox = document.createElement("details");
  aboutBox.className = "about";
  let aboutShown = false;
  let aboutAsked = false;

  function renderAbout(data, status) {
    const line = R.summary(data);
    const parts = [el("summary", { textContent: "About this company and role" +
                                                (line ? ": " + line : "") })];
    if (status) parts.push(el("p", { className: "meta", textContent: status }));
    for (const part of R.sections(data)) {
      parts.push(el("p", {}, [el("strong", { textContent: part.heading + ": " }),
                              document.createTextNode(part.text)]));
      if (part.note) parts.push(el("p", { className: "meta", textContent: part.note }));
    }
    aboutBox.replaceChildren(...parts);
  }

  async function loadAbout() {
    if (!R || aboutAsked) return;
    aboutAsked = true;
    let data;
    try {
      data = await ask({ type: "research" });
    } catch (err) {
      return;                                  // not paired, or no dashboard: say nothing
    }
    if (!data || data.state === "unsupported") return;
    aboutShown = true;
    renderAbout(data);
    box.append(aboutBox);
    if (!R.shouldRefresh(data)) return;
    const name = data.board ? data.board.board : "the company";
    renderAbout(data, "Checking " + name + "'s job board…");
    try {
      const fresh = await ask({ type: "research_refresh" });
      if (fresh.state === "failed") {
        renderAbout(data, fresh.message || "Couldn't reach the board just now.");
      } else {
        renderAbout(fresh);
      }
    } catch (err) {
      renderAbout(data, "Couldn't reach the board just now.");   // no retry
    }
  }

  // --- the apply session (plan 31) ----------------------------------------------
  // Every step to another job is one of the buttons below; nothing on the
  // page and no timer moves the session on.

  const S = globalThis.JSASession;
  const PAGE = S ? S.pageKey(location.href) || (LOCAL ? window.__jsaTestPage || null : null)
    : null;
  let session = null;          // {state, pipeline_url} while a session runs

  function sessionJob() {
    const now = session && S.current(session.state);
    return now && S.itemKey(now) === PAGE ? now : null;
  }

  function sessionBar() {
    const now = S.current(session.state);
    const n = session.state.index + 1;
    const total = session.state.items.length;
    if (sessionJob()) {
      return [
        el("p", { className: "meta", textContent: "Apply session · job " + n + " of " + total }),
        el("div", { className: "row" }, [
          button("I submitted this, next", nextAfterSubmit),
          button("Skip, next", skipNext, true),
          button("End session", endSession, true),
        ]),
      ];
    }
    return [
      el("p", { textContent: "Apply session: you are on job " + n + " of " + total + ", " +
                             now.title + " at " + now.company + "." }),
      el("div", { className: "row" }, [
        button("Back to job " + n, backToCurrent),
        button("End session", endSession, true),
      ]),
    ];
  }

  function doneScreen() {
    const s = S.summary(session.state);
    const link = el("a", { href: session.pipeline_url, textContent: "Open Pipeline",
                           target: "_blank", rel: "noopener" });
    show([
      el("h2", { textContent: session.state.ended ? "Session ended" : "Session done" }),
      el("p", { textContent: s.submitted + " submitted, " + s.skipped + " skipped" +
                             (s.left ? ", " + s.left + " not reached" : "") + "." }),
      el("p", {}, [link]),
    ]);
  }

  function after(answer) {
    session = { state: answer.state,
                pipeline_url: answer.pipeline_url || (session && session.pipeline_url) };
    if (answer.done) doneScreen();
    else show([el("p", { textContent: "Opening the job…" })]);
  }

  // Recorded first (with the same confirm as outside a session), then on.
  async function nextAfterSubmit() {
    const now = sessionJob();
    if (!now) return;
    if (!data) {
      try { data = await ask({ type: "fill" }); } catch (err) { data = null; }
    }
    const docs = (data && data.state === "ready" && data.documents) || {};
    const resume = attached.resume || docs.resume || null;
    const what = resume ? " with resume v" + resume.version : " with no resume recorded";
    if (!window.confirm("Record that you submitted " + now.title + " at " + now.company +
                        what + "?\n\nOnly do this after you pressed the page's own " +
                        "Submit. The next job opens after.")) return;
    await rememberAnswers();          // before the page moves on
    const message = { type: "session_submitted", job_id: now.job_id,
                      resume: resume ? resume.id : "",
                      cover: attached.cover_letter ? attached.cover_letter.id : "" };
    try {
      let answer = await ask(message);
      if (answer.recorded && answer.recorded.needs === "confirm_unapproved") {
        if (!window.confirm("That resume is not one you approved. Record it anyway?")) return;
        answer = await ask({ ...message, confirm_unapproved: true });
      }
      if (answer.recorded && !answer.recorded.ok) {
        show([el("p", { textContent: answer.recorded.error || "Nothing recorded." }),
              ...sessionBar()]);
        return;
      }
      after(answer);
    } catch (err) {
      show([el("p", { textContent: err.message })]);
    }
  }

  async function skipNext() {
    const now = sessionJob();
    if (!now) return;
    try { after(await ask({ type: "session_skip", job_id: now.job_id })); }
    catch (err) { show([el("p", { textContent: err.message })]); }
  }

  async function backToCurrent() {
    try { after(await ask({ type: "session_back" })); }
    catch (err) { show([el("p", { textContent: err.message })]); }
  }

  async function endSession() {
    try { after(await ask({ type: "session_end" })); }
    catch (err) { show([el("p", { textContent: err.message })]); }
  }

  // --- questions to answer (plan 35) --------------------------------------------
  // The open-ended questions the fill left empty. Nothing is asked for when
  // the section appears except your earlier answers (no model). Suggest is
  // one paid model call, from its button only; an option goes into the field
  // only from its Use this button; nothing here submits.

  const SOURCE_LABEL = { model: "drafted and checked", composed: "from your own sentences",
                         yours: "your own words" };
  let questionsHead = null;  // the section's summary line
  let left = null;           // {left_today, limit}

  function headText() {
    return "Questions to answer (" + openQuestions.length + ")" +
      (left ? " · " + left.left_today + " of " + left.limit + " suggestions left today" : "");
  }

  function card(q, option) {
    const kind = SOURCE_LABEL[option.source] ? option.source : "composed";
    const body = el("p", { className: "answer clamp", textContent: option.text });
    const more = button("Show all", () => {
      body.classList.toggle("clamp");
      more.textContent = body.classList.contains("clamp") ? "Show all" : "Show less";
    }, true);
    const parts = [
      el("p", {}, [el("strong", { textContent: option.angle }), document.createTextNode(" "),
                   el("span", { className: "chip " + kind, textContent: SOURCE_LABEL[kind] })]),
      body,
    ];
    if (option.note && option.note !== SOURCE_LABEL[kind]) {
      parts.push(el("p", { className: "meta", textContent: option.note }));
    }
    parts.push(el("div", { className: "row" }, [button("Use this", () => useOption(q, option)),
                                                 more]));
    return el("div", { className: "card" }, parts);
  }

  // The one way a suggestion reaches the page: your click on Use this.
  function useOption(q, option) {
    const field = q.el;
    if (!field.isConnected) return;
    const now = String(field.value || "");
    if (now.trim() && now !== option.text &&
        !window.confirm("Replace what's in this field with this answer?")) return;
    native(field, "value", option.text);
    undo.push({ restore: () => native(field, "value", now) });
    inserted.set(q.label, { text: option.text, source: option.source });
    mark(field, BLUE);
    q.status.textContent = "Inserted. Review it and make it sound like you before you submit.";
  }

  function showOptions(q, answer) {
    if (answer.left_today !== undefined) {
      left = { left_today: answer.left_today, limit: answer.limit };
      if (questionsHead) questionsHead.textContent = headText();
    }
    if (answer.state !== "ok") {
      q.status.textContent = answer.message || "No suggestions.";
      return;
    }
    q.status.textContent = "Pick one to put in the field; you can edit it there.";
    q.box.replaceChildren(...answer.options.map((o) => card(q, o)));
    if ((answer.earlier || []).length) {
      q.box.append(el("p", { className: "meta", textContent: "You answered this before:" }),
                   ...answer.earlier.map((o) => card(q, o)));
    }
  }

  async function suggestFor(q) {
    q.status.textContent = "Drafting three options…";
    try {
      showOptions(q, await ask({ type: "suggest", question: q.label, maxlength: q.maxlength }));
    } catch (err) {
      q.status.textContent = err.message;
    }
  }

  async function suggestAll() {
    const n = openQuestions.length;
    const budget = left ? " of your " + left.left_today + " left today" : "";
    if (!window.confirm("Suggest answers for all " + n + " questions? This uses " + n +
                        " suggestion(s)" + budget + ", one model call each.")) return;
    for (const q of openQuestions) await suggestFor(q);
  }

  async function loadEarlier() {
    let answer;
    try {
      answer = await ask({ type: "suggest_earlier",
                           questions: openQuestions.map((q) => ({ question: q.label,
                                                                  maxlength: q.maxlength })) });
    } catch (err) {
      return;
    }
    left = { left_today: answer.left_today, limit: answer.limit };
    if (questionsHead) questionsHead.textContent = headText();
    for (const q of openQuestions) {
      const hits = (answer.earlier || {})[q.label] || [];
      if (!hits.length || q.box.childElementCount) continue;
      q.box.replaceChildren(el("p", { className: "meta", textContent: "You answered this before:" }),
                            ...hits.map((o) => card(q, o)));
    }
  }

  function questionsSection() {
    if (!openQuestions.length) return [];
    questionsHead = el("summary", { textContent: headText() });
    const parts = [questionsHead,
      el("p", { className: "meta", textContent: "Suggest gives three options from your " +
        "profile. Most are made from your own sentences: drafted ones rarely pass the " +
        "check against your profile (1 of 20 when measured), so edit the one you pick " +
        "into an answer. Salary and demographic questions are yours to answer." })];
    if (openQuestions.length > 1) {
      parts.push(el("div", { className: "row" },
                    [button("Suggest for all (" + openQuestions.length + ")", suggestAll, true)]));
    }
    for (const q of openQuestions) {
      q.status = el("p", { className: "meta" });
      q.box = el("div");
      parts.push(el("div", { className: "question" }, [
        el("p", {}, [el("strong", { textContent: q.label }),
                     document.createTextNode(q.required ? " (required)" : "")]),
        el("div", { className: "row" }, [button("Suggest", () => suggestFor(q))]),
        q.status, q.box]));
    }
    loadEarlier();
    return [el("details", { className: "questions", open: true }, parts)];
  }

  // At "I submitted this": what you put in each question, for next time.
  // Your text is stored on your dashboard and never sent to a model.
  async function rememberAnswers() {
    for (const q of openQuestions) {
      const value = String(q.el.value || "").trim();
      if (!value) continue;
      const put = inserted.get(q.label);
      const source = put && put.text.trim() === value ? put.source : "yours";
      try {
        await ask({ type: "remember", question: q.label, body: value, source });
      } catch (err) { /* recording the application matters more */ }
    }
  }

  // --- the panel's screens ----------------------------------------------------

  function intro(message) {
    const now = session && S.current(session.state);
    const mine = sessionJob();
    const head = el("h2", { textContent: mine ? mine.title + " at " + mine.company
                                              : "Job Search Assistant" });
    if (now && !mine) {
      show([head, ...sessionBar()]);
      return;
    }
    const parts = [head, el("p", { className: "meta", textContent: message || (mine
      ? "Fill the form, check it, press its Submit, then tell the session below."
      : "Fill this application from your tracker. You check it and press Submit yourself.") })];
    if (mine && mine.warnings.length) parts.push(list(mine.warnings, "warn"));
    const row = [];
    if (hasForm()) row.push(button("Fill this application", run));
    if (!mine) row.push(button("Hide", () => panelHost.remove(), true));
    if (row.length) parts.push(el("div", { className: "row" }, row));
    if (mine) parts.push(...sessionBar());
    show(parts);
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
    parts.push(...questionsSection());
    const undoButton = button("Undo fill", () => { undoFill(); intro("Fill undone."); }, true);
    if (sessionJob()) {
      parts.push(el("div", { className: "row" }, [undoButton]), ...sessionBar());
    } else {
      parts.push(el("div", { className: "row" }, [undoButton,
                                                  button("I submitted this", submitted)]));
    }
    show(parts);
  }

  let lastReport = null;

  async function run() {
    show([el("p", { textContent: "Filling from your tracker…" })]);
    try {
      lastReport = await fillForm();
      report(lastReport);
      watchForConfirmation();
    } catch (err) {
      intro(err.message);
    }
  }

  // --- "Record it as applied?" (plan 36) -----------------------------------------
  // When the board shows its own "application received" page, the panel asks.
  // Watching the page only ever shows the question; recording is your click
  // on Record as applied, the same record "I submitted this" makes.

  let promptShown = false;
  let watching = false;
  let pending = null;          // the remembered fill the question is about

  function pageFacts() {
    const heads = Array.from(document.querySelectorAll("h1, h2, h3, [role='alert'], [role='status']"),
                             (n) => text(n).slice(0, 200)).slice(0, 30);
    return { host: location.hostname, path: location.pathname, headings: heads,
             formPresent: hasForm() };
  }

  // Ashby swaps the form for its thank-you text in place.
  function watchForConfirmation() {
    if (watching) return;
    watching = true;
    const watch = new MutationObserver(() => {
      if (F.looksSubmitted(pageFacts())) {
        watch.disconnect();
        offerRecord();
      }
    });
    watch.observe(document.documentElement, { childList: true, subtree: true,
                                              characterData: true });
  }

  async function offerRecord() {
    if (promptShown || !PAGE) return;
    promptShown = true;
    let memory = null;
    try {
      memory = F.rememberedFor(await ask({ type: "fill_memory_get" }), PAGE, Date.now());
    } catch (err) {
      memory = null;
    }
    if (!memory) {                           // nothing filled here lately: nothing to ask
      if (session && S.current(session.state)) intro();
      return;
    }
    let state = null;
    try { state = await ask({ type: "fill" }); } catch (err) { state = null; }
    const head = el("h2", { textContent: memory.title + " at " + memory.company });
    if (state && state.state === "past") {
      show([head, el("p", { textContent: state.status === "applied" && state.applied_at
        ? "Already recorded as applied on " + state.applied_at.slice(0, 10) + "."
        : state.message || "Already recorded." }),
            ...(sessionJob() ? sessionBar() : [])]);
      return;
    }
    pending = memory;
    const docs = memory.resume_version
      ? "resume v" + memory.resume_version +
        (memory.cover_version ? " and cover letter v" + memory.cover_version : "")
      : "no resume recorded";
    const row = sessionJob()
      ? [button("Record as applied, next", nextAfterSubmit), button("Not yet", notYet, true)]
      : [button("Record as applied", recordFromPrompt), button("Not yet", notYet, true)];
    show([head,
          el("p", { textContent: "Looks like your application went through. Record it as " +
                                 "applied, with " + docs + "?" }),
          el("div", { className: "row" }, row)]);
  }

  async function recordFromPrompt() {
    const m = pending;
    if (!m) return;
    await rememberAnswers();
    const message = { type: "applied", job_id: m.job_id, confirm: "submitted",
                      how: "confirmation_prompt", resume: m.resume_id || "",
                      cover: m.cover_id || "" };
    try {
      let answer = await ask(message);
      if (answer.needs === "confirm_unapproved") {
        if (!window.confirm("That resume is not one you approved. Record it anyway?")) return;
        answer = await ask({ ...message, confirm_unapproved: true });
      }
      pending = null;
      show([el("p", { textContent: answer.ok ? "Recorded. It's in your Pipeline as applied."
                                             : answer.error || "Nothing recorded." })]);
    } catch (err) {
      show([el("p", { textContent: err.message })]);
    }
  }

  function notYet() {
    pending = null;
    if (lastReport) report(lastReport);
    else panelHost.remove();
  }

  // Recorded only after you say so, twice if the resume isn't approved.
  async function submitted() {
    if (!data || data.state !== "ready") return;
    const resume = attached.resume || (data.documents || {}).resume;
    const what = resume ? " with resume v" + resume.version : " with no resume recorded";
    if (!window.confirm("Record that you submitted " + data.job.title + " at " +
                        data.job.company + what + "?\n\nOnly do this after you pressed " +
                        "the page's own Submit.")) return;
    await rememberAnswers();
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

  // --- starting up ------------------------------------------------------------

  // Application forms render late; show the panel once one appears (or, on a
  // session's job, refresh it so the Fill button appears).
  function hasForm() {
    return Boolean(document.querySelector("input[type='file'], input[type='email'], " +
                                          "input[name*='email' i], input[id*='email' i]"));
  }

  function whenFormAppears(then) {
    if (hasForm()) { then(); return; }
    const watch = new MutationObserver(() => {
      if (hasForm()) { watch.disconnect(); then(); }
    });
    watch.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => watch.disconnect(), 20000);
  }

  async function begin() {
    const asked = location.hash === "#jsa-apply-session";
    // A board's own "application received" page (Lever's /thanks,
    // Greenhouse's /confirmation): ask about the fill this tab remembers.
    const received = !hasForm() && F.looksSubmitted(pageFacts());
    if (S) {
      try {
        session = await ask({ type: "session_get" });
        if ((!session.state || session.state.done) && asked) {
          session = await ask({ type: "session_start" });
          if (session.state.done) { doneScreen(); return; }     // nothing ready
        }
        if (!session.state || session.state.done) session = null;
      } catch (err) {
        session = null;
        if (asked) { intro(err.message); return; }
      }
    }
    if (received) { offerRecord(); return; }
    if (session && sessionJob()) {
      intro();
      if (!hasForm()) whenFormAppears(intro);
      return;
    }
    if (session && PAGE) { intro(); return; }   // a job page, but not the session's
    whenFormAppears(intro);
  }

  begin();
})();
