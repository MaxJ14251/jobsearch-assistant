# ADR 0031 — Autofill in your browser; you press Submit

Status: accepted
Date: 2026-10-07
Relates to: the project's founding rule that nothing sends automatically,
ADR 0012 (what "applied" means), ADR 0026 (Turbo decides interest, never
submits), ADR 0027 (application answers), ADR 0001 (the pay floor is never
an asking figure). Implements plan 30 of the planning session.

## Context

The owner asked to open a job link (SpaceX's, say) and have the application
filled in, with a check before it goes. **Submitting automatically was
declined** on 2026-10-07:

- Greenhouse (SpaceX's board), Lever and Ashby accept applications through
  their APIs only with the **employer's** secret key. There is no candidate
  route.
- A hidden browser pressing Submit would have to get past the forms' bot
  checks, which is exactly what they exist to stop.
- "Human approves every application submission; nothing sends
  automatically" is the project's founding rule.

The owner chose this instead: **the tool fills, you submit.** Your click on
the page's own Submit button is what sends, so the rule holds.

## Decision

A Chrome extension in `extension/`, loaded unpacked (Manifest V3, plain
JavaScript, no build step, no npm dependencies).

1. **It fills only when you ask.** On a Greenhouse, Lever or Ashby
   application (Greenhouse forms embedded in an employer's careers page
   included), a small panel offers "Fill this application". It fills name,
   email, phone, city, links, the **approved** resume and cover letter files,
   yes/no answers your profile states as yes or no, and your stored written
   answers. It never overwrites what you typed.
2. **It never submits.** No `submit()`, `requestSubmit()`, synthetic click or
   key press anywhere in its code, and it never reaches into another frame
   (a CAPTCHA's). `tests/test_extension.py` reads the files and fails on any
   of these.
3. **It says what it did.** Filled fields are outlined faintly green;
   required fields it left empty are outlined amber; the panel lists every
   filled field with its source ("profile", "approved resume v3", "written
   answer, from your sentences"), the required ones left for you, and the
   warnings (an unapproved or superseded resume, a thin posting, a composed
   answer).
4. **It guesses nothing.** A yes/no question is answered only from a yes or
   no in the profile. Work authorization is free text, so its yes/no is
   left for you unless the profile sets the optional
   `job_search_preferences.authorized_to_work_us: true|false`. A label that
   asks two yes/no questions at once is left alone. **Salary is never
   filled** (ADR 0001); it is outlined for you.
5. **"I submitted this" records the application** (`via = employer`, the
   documents it attached) only after a confirm, and a second confirm when
   the resume isn't approved: the same record the job page's "I applied"
   makes (ADR 0012). It is your statement, made after your click.

### The `/ext/` boundary

The dashboard's other endpoints are protected by the Host check (GET) and a
per-process page token (POST). The extension can't read the page token, and
the new endpoints hand out personal data, which any other installed
extension with localhost access could otherwise read. So:

- **A pairing key.** The dashboard's Extension page makes a random 32-byte
  key on request, shows it **once**, and keeps only its SHA-256 (`settings`
  table). A new key retires the old one; Disconnect deletes it. With none
  paired, every `/ext/` request gets 401.
- **The guard** requires the key in an `X-JSA-Key` header for every path
  under `/ext/`, and skips the page token there only. The Host check and the
  size limits still apply. A web page can't send a custom header to
  127.0.0.1 without a CORS preflight, and the dashboard grants none.
- **`GET /ext/fill?url=`** answers with only what a form asks: the job, the
  name, email, phone, city and state, links, decided facts, stored written
  answers and the document choice. Never the street, postal code, pay floor,
  feedback or notes; a test checks the response for each.
- **`GET /ext/document/{id}`** serves a document only if its job's
  application is still being applied to by hand (saved, drafting or ready).
- **`POST /ext/applied`** records only with `confirm=submitted`, as the
  person, through `approvals.set_stage`. It is added to the allowlist in
  `tests/test_apply_by_hand.py` by name.

### What the extension may reach

Permissions: `storage` only. Hosts: `127.0.0.1` and the three boards' own
hosts; content scripts on those boards only. No `<all_urls>`, no `tabs`.
The background worker is the only code that makes requests, and only to
`/ext/` on 127.0.0.1. It asks the dashboard about the frame Chrome says sent
the message, not an address the message claims.

## Rejected

- **Submitting for you**, in any form. Above.
- **Filling on page load.** It would run on every visit and refill fields
  you had cleared; a button keeps it your action.
- **Clicking Ashby's Yes/No buttons.** A click is the one thing the
  extension never does, so those questions are left for you, and the panel
  says so.
- **Workday, LinkedIn, Indeed and employers' own forms.** Out of scope:
  Workday needs an account per employer, and LinkedIn and Indeed are not
  read (ADR 0013, 0020).

## Consequences

- Fixture forms in the shape of each board's page (`extension/test/forms/`,
  hand-written and fictional) were filled in a browser with a stand-in for
  the dashboard, 2026-10-07: Greenhouse 10 fields filled, with sponsorship
  answered from the profile, and work authorization, salary and an unknown
  question left outlined; Lever 8, with relocation (undecided) left alone;
  Ashby 5, with its Yes/No buttons left. Undo cleared every field; no form
  was submitted.
- **Not yet measured on a real application page.** That needs the owner's
  own Chrome with the extension loaded; the counts of what filled, by field
  type, go here when it has been done.
- The extension stays out of the wheel and the Docker image: it is loaded
  from a clone.

## The apply session, 2026-10-07 (plan 31)

The owner asked for a "fully agentic" apply on company-site jobs. Unattended
submission was declined again, for the reasons above, and because forms
carry attestations ("I certify that…") that an agent should not sign
unseen. The owner chose an **apply session**: the tool does everything up to
Submit, job after job, and the person submits each one.

- **The queue** (`jsa/applyqueue.py`, `GET /ext/queue`, read-only): saved
  applications still being applied to by hand, on Greenhouse, Lever or
  Ashby, whose posting is open and which have a resume a person approved.
  Highest match first; ties go to the resume approved earliest. The rest are
  counted with a reason: needs an approved resume, a board the extension
  doesn't fill (apply by hand), or closed.
- **The form it opens** is always the board's own application page, built
  from the board token in the source's API address and the posting id;
  never the employer's careers page. One GET each on 2026-10-07: Rocket Lab
  and SpaceX on job-boards.greenhouse.io, Shield AI on Lever and Replit on
  Ashby all answered 200 with no redirect. SpaceX embeds its form on its own
  site, but the board-hosted page serves it too.
- **Pipeline** offers "Start applying (N ready)", a link to the first form
  with `#jsa-apply-session` (a fragment never reaches the employer's server),
  or "Connect the extension first" when none is paired.
- **The session** lives in the extension (`session.js`, kept in
  `chrome.storage.session`, gone when the browser closes). The panel shows
  "job 3 of 10" and three buttons: **I submitted this, next** (the same
  confirm, then `/ext/applied`, then the next form), **Skip, next** (records
  nothing) and **End session**. On another job's page it offers only "Back
  to job 3" and End session. At the end, a summary and a link to Pipeline.
- **It never moves on by itself.** Only those buttons advance it: no timer,
  no page event and no clock. The worker opens only a form that is in this
  session's queue and on one of the three boards, in the tab that asked, and
  it records before it moves on. `tests/test_extension.py` holds the files
  to this, and a planted auto-advance timer and tab listener both failed it.
  `extension/test/background.test.mjs` runs the worker in Node and checks
  the order: record, then open; a refused record opens nothing; a skip
  records nothing.
- **No new permission.** `chrome.tabs.update` on the asking tab needs none.

Checked on the fixture forms in a browser, 2026-10-07: a three-job session
started from the fragment, stayed put for three seconds untouched, skipped
job 1, recorded job 2 and opened job 3, recorded job 3 and ended with "2
submitted, 1 skipped"; End session ended early; a page that wasn't the
session's job offered only "Back to job 2". No fixture form was submitted.
**Not yet tried in the owner's Chrome on real forms.**
