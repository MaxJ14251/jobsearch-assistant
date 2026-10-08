// The apply session's state machine (plan 31): it never moves on by itself.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const S = require("../session.js");

const ITEMS = [
  { job_id: 1, title: "A", company: "X", kind: "greenhouse", external_id: "111",
    apply_url: "https://job-boards.greenhouse.io/x/jobs/111" },
  { job_id: 2, title: "B", company: "X", kind: "lever",
    external_id: "0A1B2C3D-0000-4000-8000-000000000002",
    apply_url: "https://jobs.lever.co/x/0a1b2c3d-0000-4000-8000-000000000002/apply" },
  { job_id: 3, title: "C", company: "X", kind: "ashby",
    external_id: "0a1b2c3d-0000-4000-8000-000000000003",
    apply_url: "https://jobs.ashbyhq.com/x/0a1b2c3d-0000-4000-8000-000000000003/application" },
];

test("it starts on the first job and stays there until told", () => {
  const s = S.start(ITEMS);
  assert.equal(S.current(s).job_id, 1);
  assert.equal(S.current(s).job_id, 1);           // reading never moves it
  assert.deepEqual(S.summary(s), { submitted: 0, skipped: 0, left: 3, total: 3 });
});

test("only the current job can be submitted or skipped", () => {
  const s = S.start(ITEMS);
  assert.equal(S.submitted(s, 2), s);              // not the current one: unchanged
  assert.equal(S.skip(s, 3), s);
  const after = S.submitted(s, 1);
  assert.equal(S.current(after).job_id, 2);
  assert.deepEqual(after.submitted, [1]);
  assert.equal(S.current(s).job_id, 1);            // the old state is untouched
});

test("submitted and skipped are kept apart, and it ends", () => {
  let s = S.start(ITEMS);
  s = S.submitted(s, 1);
  s = S.skip(s, 2);
  s = S.submitted(s, 3);
  assert.equal(s.done, true);
  assert.equal(S.current(s), null);
  assert.deepEqual(S.summary(s), { submitted: 2, skipped: 1, left: 0, total: 3 });
  assert.equal(S.submitted(s, 3), s);              // nothing after the end
});

test("ending early leaves the rest unreached", () => {
  const s = S.end(S.submitted(S.start(ITEMS), 1));
  assert.equal(s.done, true);
  assert.equal(s.ended, true);
  assert.equal(S.current(s), null);
  assert.deepEqual(S.summary(s), { submitted: 1, skipped: 0, left: 2, total: 3 });
});

test("an empty queue is done at once", () => {
  assert.equal(S.start([]).done, true);
  assert.equal(S.current(S.start([])), null);
});

test("a page knows which posting it is, confirmation pages included", () => {
  const cases = {
    "https://job-boards.greenhouse.io/x/jobs/111": "greenhouse:111",
    "https://job-boards.greenhouse.io/x/jobs/111/confirmation": "greenhouse:111",
    "https://boards.greenhouse.io/embed/job_app?for=x&token=111": "greenhouse:111",
    "https://jobs.lever.co/x/0A1B2C3D-0000-4000-8000-000000000002/apply":
      "lever:0a1b2c3d-0000-4000-8000-000000000002",
    "https://jobs.lever.co/x/0a1b2c3d-0000-4000-8000-000000000002/thanks":
      "lever:0a1b2c3d-0000-4000-8000-000000000002",
    "https://jobs.ashbyhq.com/x/0a1b2c3d-0000-4000-8000-000000000003/application":
      "ashby:0a1b2c3d-0000-4000-8000-000000000003",
    "https://jobs.lever.co/x": null,
    "https://example.com/x/jobs/111": null,
    "http://job-boards.greenhouse.io/x/jobs/111": null,
    "not a url": null,
  };
  for (const [url, key] of Object.entries(cases)) assert.equal(S.pageKey(url), key, url);
  for (const item of ITEMS) assert.equal(S.pageKey(item.apply_url), S.itemKey(item));
});

test("only queued addresses count", () => {
  const s = S.start(ITEMS);
  assert.equal(S.isQueued(s, ITEMS[1].apply_url), true);
  assert.equal(S.isQueued(s, "https://jobs.lever.co/x/other/apply"), false);
  assert.equal(S.isQueued(null, ITEMS[0].apply_url), false);
});
