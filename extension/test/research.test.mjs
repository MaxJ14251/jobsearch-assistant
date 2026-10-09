// "About this company and role" (plan 34): the dashboard's answer as text.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const R = require("../research.js");

const CAVEATS = {
  posting: "What postings ask for, not who gets hired: dropping a degree requirement often changes actual hiring little.",
  role: "People in this role nationwide, not this company's hires.",
};
const READY = {
  state: "ready",
  caveats: CAVEATS,
  posting: { label: "bachelor's or equivalent experience", level: "bachelors_or_equiv",
             evidence: "Bachelor's degree or equivalent experience.", certs: ["CISSP"],
             from: "tracker" },
  company: { line: "Riverton Grid, 12 open postings in your tracker: 50% bachelor's required.",
             from: "tracker" },
  role: { line: "Software developers (15-1252): 3% high school or less · 11% some college or associate's · 52% bachelor's · 34% graduate degree.",
          caveat: CAVEATS.role },
};

test("the collapsed summary", () => {
  assert.equal(R.summary(READY),
               "Asks for: bachelor's or equivalent experience · Role nationwide: 86% hold a bachelor's or more");
  assert.equal(R.summary({ state: "unsupported" }), "");
  assert.equal(R.summary({ state: "missing", caveats: CAVEATS }),
               "Nothing to show for this page yet");
});

test("sections carry their sentence and every caveat", () => {
  const s = R.sections(READY);
  assert.deepEqual(s.map((x) => x.heading),
                   ["This posting", "This company", "This role, nationwide"]);
  assert.equal(s[0].text, "bachelor's or equivalent experience; names CISSP");
  assert.match(s[0].note, /Bachelor's degree or equivalent experience/);
  assert.equal(s[1].note, CAVEATS.posting);
  assert.match(s[2].note, /not this company's hires/);
  assert.match(s[2].note, /CC BY 4\.0/);
});

test("a company not read recently says so", () => {
  const s = R.sections({ state: "missing", caveats: CAVEATS, posting: null, company: null,
                         role: null });
  assert.deepEqual(s, [{ heading: "This company", text: "Not read recently.", note: "" }]);
});

test("the board is read only when the dashboard says to", () => {
  assert.equal(R.shouldRefresh({ state: "missing", can_refresh: true }), true);
  assert.equal(R.shouldRefresh({ state: "stale", can_refresh: true }), true);
  assert.equal(R.shouldRefresh({ state: "stale", can_refresh: false }), false);
  assert.equal(R.shouldRefresh(READY), false);
  assert.equal(R.shouldRefresh(null), false);
});

test("text stays text: markup in the data is passed through as characters", () => {
  const sneaky = { ...READY, company: { line: "<img src=x onerror=alert(1)>" } };
  assert.equal(R.sections(sneaky)[1].text, "<img src=x onerror=alert(1)>");
});
