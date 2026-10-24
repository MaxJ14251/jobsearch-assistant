# Standing rules for every goal (jobsearch)

## Never
- Never submit an application, send a message or email, or click Submit
  anywhere. Nothing sends automatically: not behind a flag, not a config
  value, not "for testing".
- Never write decided_by='human' from agent code, never run `jsa approve` or
  `jsa reject`, never change an application's stage on the owner's behalf.
- Never put identity (name, email, phone, address, links) or the pay floor
  in a model prompt.
- Never state or imply a degree the profile doesn't hold.
- Never read LinkedIn, Indeed, Glassdoor or ycombinator.com, and never evade
  bot detection.
- Never edit profile/master_profile.yaml, and never print .env values.
- Never write personal values into the repo, commits or ADRs: categories and
  counts only. Test fixtures are fictional.
- Never delete permanently: use the Recycle Bin
  (Microsoft.VisualBasic.FileIO.FileSystem, SendToRecycleBin).
- Never force-push, rewrite history or delete the repo.

## Ask first
- Before any model call that costs money: say how many calls and why, and wait.
- Before any decision the owner owns: present the options and a recommendation.
- Before pushing: commit locally and wait for "push it".

## Every change
- Commits use the repo-local noreply identity and end with:
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
- Run the full suite (python -m unittest discover -s tests -t .),
  node --test extension/test/*.test.mjs when extension/ changes, and
  tools/scan_secrets.py before each commit.
- A user-visible change gets a CHANGELOG line. A decision gets an ADR or a
  dated note on the existing one, with measured numbers.
- Measured claims only. If something wasn't checked, say so.

## Real-world testing
- Prefer real conditions over mocks for verification, at no risk:
  - The real tracker: read-only (open it with ?mode=ro), or a copy via
    JSA_DB=<copy> for anything that writes. `jsa backup` first if a real
    write is needed.
  - Real job boards: read-only public GETs at the polite delay; only boards
    the tool is allowed to read.
  - Real postings already in the tracker make the best test inputs; pick a
    spread (stub, long, pay stated, degree with "or equivalent", remote,
    Workday, Ashby...) and record counts, not contents.
  - The dashboard: start it with the preview tools and check pages in the
    browser pane, at phone and desktop widths, light and dark.
- The extension can't load in the browser pane, and Chrome is the owner's.
  For extension behaviour: use the fixture forms in extension/test/forms/
  plus Node tests, then write an owner checklist. Give exact steps, a real
  page type to use, what to look at, and what to report back (counts and
  field types only). Never ask the owner to submit an application just to
  test; testing stops before Submit unless they really mean to apply.
- When a real-world check finds a bug, write a fictional fixture or test
  that reproduces it first, then fix it.
