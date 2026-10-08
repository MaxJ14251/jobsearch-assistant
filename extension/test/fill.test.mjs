// node --test extension/test/*.test.mjs   (Node's own runner; no npm, no dependencies)
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const fill = require("../fill.js");

const READY = {
  state: "ready",
  identity: {
    full_name: "Robin Q Example", first_name: "Robin Q", last_name: "Example",
    preferred_name: "Robin", email: "you@example.com", phone: "+1 (555) 555-0100",
    city: "Riverton", state: "ID",
    links: { linkedin: "https://linkedin.com/in/example", github: "https://github.com/example" },
  },
  facts: { sponsorship: "No", work_authorization: "Authorized (fictional)" },
  written: [
    { key: "why_role", body: "Because support work is the job.", source: "model" },
    { key: "why_company", body: "From my own sentences.", source: "composed" },
  ],
  documents: { resume: { id: 7, version: 3, filename: "r.docx", approved: true },
               cover_letter: null },
};

test("labels map to keys", () => {
  const cases = {
    "First Name *": "first_name", "Last name": "last_name", "Full name": "full_name",
    "Name": "full_name", "Email": "email", "E-mail address": "email",
    "Phone": "phone", "Mobile number": "phone", "LinkedIn Profile": "linkedin",
    "GitHub URL": "github", "Portfolio": "portfolio", "Website": "website",
    "Resume/CV": "resume", "Cover Letter": "cover_letter",
    "Location (City)": "location", "Where are you based?": "location",
    "Will you now or in the future require visa sponsorship?": "sponsorship",
    "Are you willing to relocate?": "relocation",
    "Are you legally authorized to work in the United States?": "authorized",
    "Why are you interested in this role?": "why_role",
    "Why do you want to work here?": "why_company",
    "Tell us about a project you are proud of": "relevant_project",
    "Preferred first name": "preferred_name",
  };
  for (const [label, key] of Object.entries(cases)) {
    assert.equal(fill.keyForLabel(label), key, label);
  }
});

test("salary is recognized only to be left alone", () => {
  for (const label of ["Desired salary", "Compensation expectations", "Pay expectations"]) {
    assert.equal(fill.keyForLabel(label), "salary", label);
    assert.equal(fill.valueFor("salary", READY, "text"), null);
  }
});

test("a label asking two yes/no questions at once is not answered", () => {
  assert.equal(fill.keyForLabel(
    "Are you authorized to work in the US, and will you require sponsorship?"), null);
});

test("unknown labels stay unfilled", () => {
  for (const label of ["How did you hear about us?", "Current company", "Pronouns", ""]) {
    assert.equal(fill.keyForLabel(label), null, label);
  }
});

test("board system fields need no label", () => {
  assert.equal(fill.keyFor({ board: "greenhouse", id: "first_name", label: "" }), "first_name");
  assert.equal(fill.keyFor({ board: "lever", name: "urls[LinkedIn]", label: "" }), "linkedin");
  assert.equal(fill.keyFor({ board: "lever", name: "name", label: "" }), "full_name");
  assert.equal(fill.keyFor({ board: "ashby", id: "_systemfield_email", label: "" }), "email");
  // Another board's id does not leak across.
  assert.equal(fill.keyFor({ board: "lever", id: "_systemfield_email", label: "" }), null);
});

test("values come from the profile and say so", () => {
  assert.deepEqual(fill.valueFor("first_name", READY, "text"),
                   { kind: "text", value: "Robin Q", source: "profile" });
  assert.equal(fill.valueFor("location", READY, "text").value, "Riverton, ID");
  assert.equal(fill.valueFor("linkedin", READY, "url").value, "https://linkedin.com/in/example");
  assert.equal(fill.valueFor("portfolio", READY, "url"), null);
});

test("documents go only into file inputs", () => {
  const resume = fill.valueFor("resume", READY, "file");
  assert.equal(resume.kind, "file");
  assert.equal(resume.document.id, 7);
  assert.equal(resume.source, "approved resume v3");
  assert.equal(fill.valueFor("resume", READY, "text"), null);
  assert.equal(fill.valueFor("cover_letter", READY, "file"), null);
  const draft = { ...READY, documents: { resume: { id: 8, version: 4, approved: false } } };
  assert.match(fill.valueFor("resume", draft, "file").source, /unapproved draft/);
});

test("yes/no answers only from a yes/no in the profile", () => {
  assert.deepEqual(fill.valueFor("sponsorship", READY, "select"),
                   { kind: "yesno", value: "No", source: "profile" });
  // Relocation undecided, and work authorization only free text: both stay empty.
  assert.equal(fill.valueFor("relocation", READY, "select"), null);
  assert.equal(fill.valueFor("authorized", READY, "select"), null);
  const explicit = { ...READY, facts: { ...READY.facts, authorized_to_work_us: "Yes" } };
  assert.equal(fill.valueFor("authorized", explicit, "radio").value, "Yes");
});

test("written answers carry where they came from", () => {
  assert.equal(fill.valueFor("why_role", READY, "textarea").source, "written answer, checked");
  assert.equal(fill.valueFor("why_company", READY, "textarea").source,
               "written answer, from your sentences");
  assert.equal(fill.valueFor("relevant_project", READY, "textarea"), null);
});

test("nothing is filled unless the dashboard said ready", () => {
  assert.equal(fill.valueFor("email", { ...READY, state: "not_saved" }, "email"), null);
  assert.equal(fill.valueFor("email", null, "email"), null);
});

test("choosing a yes/no option", () => {
  assert.equal(fill.chooseYesNo(["Select...", "Yes", "No"], "No"), 2);
  assert.equal(fill.chooseYesNo(["Yes, I will", "No, I will not"], "Yes"), 0);
  // No option that plainly says it: leave it for the person.
  assert.equal(fill.chooseYesNo(["I will require sponsorship", "I will not"], "No"), -1);
  assert.equal(fill.chooseYesNo(["Yes", "Yes - later", "No"], "Yes"), -1);
  assert.equal(fill.chooseYesNo(["Yes", "No"], "Maybe"), -1);
});

test("required fields", () => {
  assert.equal(fill.isRequired({ required: true }), true);
  assert.equal(fill.isRequired({ ariaRequired: "true" }), true);
  assert.equal(fill.isRequired({ label: "Email *" }), true);
  assert.equal(fill.isRequired({ label: "Email \u2731" }), true);
  assert.equal(fill.isRequired({ label: "Website" }), false);
});
