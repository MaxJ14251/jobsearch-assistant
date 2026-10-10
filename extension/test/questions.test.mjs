// Open-ended questions and their matching key (plan 35).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const fill = require("../fill.js");

function field(over) {
  return { type: "textarea", label: "What excites you about Riverton Grid?", maxlength: null,
           empty: true, key: null, ...over };
}

test("which fields are open-ended questions", () => {
  const yes = [
    field({}),
    field({ label: "Additional information" }),
    field({ type: "text", label: "Why do you want this role?" }),
    field({ type: "text", label: "Describe a project you are proud of *", maxlength: 500 }),
    field({ key: "why_company" }),                // a written key with no stored answer
  ];
  const no = [
    field({ empty: false }),                      // filled already
    field({ type: "text", label: "Email" }),
    field({ type: "text", label: "Website", key: "website" }),
    field({ type: "text", label: "Will you need sponsorship?", key: "sponsorship" }),
    field({ label: "What are your salary expectations?", key: "salary" }),
    field({ label: "Desired pay" }),
    field({ label: "What is your gender identity?" }),
    field({ label: "Are you a protected veteran?" }),
    field({ label: "How did you hear about us?" }),
    field({ label: "Cover letter" }),
    field({ type: "text", label: "Why this role?", maxlength: 100 }),   // too short for prose
    field({ type: "text", label: "Current company" }),                  // not a question
    field({ type: "select" }), field({ type: "radio" }), field({ type: "file" }),
  ];
  for (const f of yes) assert.equal(fill.isOpenEnded(f), true, JSON.stringify(f));
  for (const f of no) assert.equal(fill.isOpenEnded(f), false, JSON.stringify(f));
});

test("the key is the same one the dashboard computes", () => {
  const cases = JSON.parse(readFileSync(new URL("./question_cases.json", import.meta.url), "utf8"));
  assert.ok(cases.length >= 5);
  for (const c of cases) assert.equal(fill.questionKey(c.text, c.company), c.key, c.text);
});

test("the question as shown and sent: no asterisk, capped", () => {
  assert.equal(fill.questionText("  Why us? *  "), "Why us?");
  assert.equal(fill.questionText("Why ".repeat(200)).length <= 300, true);
});
