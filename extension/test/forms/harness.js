// Stands in for the extension's background worker on the fixture forms, so
// fill.js and content.js can be watched working in an ordinary browser tab.
// Fictional data only. Nothing here talks to a network.
"use strict";

(function () {
  const FILL = {
    state: "ready",
    job: { id: 1, title: "Support Engineer", company: "Riverton Grid" },
    board: window.__jsaTestBoard,
    identity: {
      full_name: "Robin Q Example", first_name: "Robin Q", last_name: "Example",
      preferred_name: "Robin", email: "you@example.com", phone: "+1 (555) 555-0100",
      city: "Riverton", state: "ID", country: "USA",
      links: { linkedin: "https://linkedin.com/in/example", github: "https://github.com/example" },
    },
    facts: { work_authorization: "Authorized to work in the US (fictional)",
             sponsorship: "No", arrangement: "remote, hybrid" },
    left_out: [{ key: "relocation",
                 note: "Not decided in your profile: set job_search_preferences.willing_to_relocate" }],
    written: [
      { key: "why_role", question: "Why this role?", source: "model",
        body: "I have spent three years answering customers' hardest questions." },
      { key: "why_company", question: "Why Riverton Grid?", source: "composed",
        body: "Riverton Grid builds the tools I already recommend to customers." },
    ],
    documents: {
      resume: { id: 7, version: 3, filename: "resume-v3.docx", approved: true },
      cover_letter: { id: 8, version: 1, filename: "cover-letter-v1.docx", approved: true },
    },
    warnings: ["\"Why Riverton Grid?\" is composed from your own sentences; read it before you submit."],
  };
  // The apply session, as background.js keeps it, in sessionStorage so it
  // lasts across the fixture pages. "Navigating" here goes to the fixture
  // for that board and is logged, as the real worker opens the queued form.
  const QUEUE = [
    { job_id: 1, title: "Support Engineer", company: "Riverton Grid", kind: "greenhouse",
      external_id: "4012345", apply_url: "greenhouse.html", resume_version: 3, warnings: [] },
    { job_id: 2, title: "Solutions Engineer", company: "Riverton Grid", kind: "lever",
      external_id: "0a1b2c3d-0000-4000-8000-000000000002", apply_url: "lever.html",
      resume_version: 1, warnings: [] },
    { job_id: 3, title: "Support Analyst", company: "Riverton Grid", kind: "ashby",
      external_id: "0a1b2c3d-0000-4000-8000-000000000003", apply_url: "ashby.html",
      resume_version: 2, warnings: ["A newer resume draft is waiting in Review."] },
  ];
  const PIPELINE = "pipeline-stand-in";
  const load = () => JSON.parse(sessionStorage.getItem("jsa-session") || "null");
  const save = (state) => sessionStorage.setItem("jsa-session", JSON.stringify(state));
  const moved = JSON.parse(sessionStorage.getItem("jsa-moved") || "[]");
  window.__jsaMoved = moved;
  function go(state) {
    const next = window.JSASession.current(state);
    if (!next) return { state, done: true, pipeline_url: PIPELINE };
    moved.push(next.apply_url);
    sessionStorage.setItem("jsa-moved", JSON.stringify(moved));
    location.href = next.apply_url;
    return { state };
  }
  const SESSION = {
    session_get: () => ({ state: load(), pipeline_url: PIPELINE }),
    session_start: () => {
      const running = load();
      if (running && !running.done) return { state: running, pipeline_url: PIPELINE };
      const state = window.JSASession.start(QUEUE);
      save(state);
      return { state, pipeline_url: PIPELINE };
    },
    session_submitted: (m) => {
      const state = window.JSASession.submitted(load(), m.job_id);
      save(state);
      return { recorded: { ok: true, message: "Recorded (fixture)." }, ...go(state) };
    },
    session_skip: (m) => { const state = window.JSASession.skip(load(), m.job_id); save(state); return go(state); },
    session_back: () => go(load()),
    session_end: () => { const state = window.JSASession.end(load()); save(state);
                         return { state, done: true, pipeline_url: PIPELINE }; },
  };

  // "About this company and role": the first answer says the board hasn't
  // been read, the refresh comes back read, as the dashboard would.
  const CAVEATS = {
    posting: "What postings ask for, not who gets hired: dropping a degree requirement " +
             "often changes actual hiring little.",
    role: "People in this role nationwide, not this company's hires.",
  };
  const ROLE = { line: "Software developers (15-1252): 3% high school or less · 11% some " +
                       "college or associate's · 52% bachelor's · 34% graduate degree.",
                 caveat: CAVEATS.role };
  const POSTING = { label: "bachelor's or equivalent experience", level: "bachelors_or_equiv",
                    evidence: "Bachelor's degree in computer science or equivalent experience.",
                    certs: [], from: "board" };
  SESSION.research = () => ({ state: "missing", can_refresh: true, caveats: CAVEATS,
                              board: { kind: "greenhouse", board: "riverton" },
                              posting: POSTING, company: null, role: ROLE });
  SESSION.research_refresh = () => ({
    state: "ready", can_refresh: false, caveats: CAVEATS, posting: POSTING, role: ROLE,
    board: { kind: "greenhouse", board: "riverton" },
    company: { line: "Riverton Grid, 64 open postings on its job board: 9% bachelor's " +
                     "required, 61% bachelor's or equivalent experience, 30% no degree " +
                     "mentioned.", from: "board" } });

  const log = (window.__jsaCalls = []);
  window.chrome = {
    runtime: {
      sendMessage: async (message) => {
        log.push(message);
        if (SESSION[message.type]) return { ok: true, result: SESSION[message.type](message) };
        if (message.type === "fill") return { ok: true, result: FILL };
        if (message.type === "document") {
          return { ok: true, result: { data: btoa("PK fixture " + message.id),
                                       type: "application/octet-stream" } };
        }
        if (message.type === "applied") {
          return { ok: true, result: { ok: true, message: "Recorded (fixture)." } };
        }
        return { ok: false, error: "unknown message" };
      },
    },
  };
})();
