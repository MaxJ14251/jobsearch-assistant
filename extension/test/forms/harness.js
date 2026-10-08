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
  const log = (window.__jsaCalls = []);
  window.chrome = {
    runtime: {
      sendMessage: async (message) => {
        log.push(message);
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
