// The board's "application received" page, and the fill a tab remembers (plan 36).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const fill = require("../fill.js");

const page = (over) => ({ host: "jobs.ashbyhq.com", path: "/acme/x/application", headings: [],
                          formPresent: false, ...over });

test("confirmation pages are recognised", () => {
  const yes = [
    page({ host: "jobs.lever.co", path: "/acme/0a1b2c3d-0000-4000-8000-000000000002/thanks" }),
    page({ host: "job-boards.greenhouse.io", path: "/riverton/jobs/4012345/confirmation" }),
    page({ headings: ["Thank you for applying!"] }),
    page({ headings: ["Your application was successfully submitted"] }),
    page({ headings: ["Application received"] }),
    page({ headings: ["We’ve received your application to Acme"] }),
    page({ headings: ["Thanks for your application"] }),
  ];
  for (const p of yes) assert.equal(fill.looksSubmitted(p), true, JSON.stringify(p));
});

test("nothing counts while the form is still there, or on an error", () => {
  const no = [
    page({ headings: ["Thank you for applying!"], formPresent: true }),
    page({ host: "jobs.lever.co", path: "/acme/x/thanks", formPresent: true }),
    page({ headings: ["Your application could not be submitted"] }),
    page({ headings: ["Application not submitted: fix the errors below"] }),
    page({ headings: ["Apply for this job", "Submit your application"] }),
    page({ headings: ["Thank you for your interest. This position is closed."] }),
    page({ host: "jobs.lever.co", path: "/acme/x/apply" }),
    null,
  ];
  for (const p of no) assert.equal(fill.looksSubmitted(p), false, JSON.stringify(p));
});

test("a remembered fill counts for its own posting, for two hours", () => {
  const now = 1_800_000_000_000;
  const memory = { job_id: 1, page_key: "greenhouse:4012345", filled_at: now - 60_000 };
  assert.equal(fill.rememberedFor(memory, "greenhouse:4012345", now), memory);
  assert.equal(fill.rememberedFor(memory, "greenhouse:999", now), null);           // another job
  assert.equal(fill.rememberedFor(memory, null, now), null);
  assert.equal(fill.rememberedFor({ ...memory, filled_at: now - fill.FILL_MEMORY_MS - 1 },
                                  "greenhouse:4012345", now), null);               // too old
  assert.equal(fill.rememberedFor({ ...memory, filled_at: now + 5000 },
                                  "greenhouse:4012345", now), null);               // from the future
  assert.equal(fill.rememberedFor(null, "greenhouse:4012345", now), null);
});
