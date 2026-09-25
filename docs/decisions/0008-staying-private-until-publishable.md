# ADR 0008 — Staying private until anyone can use it

Status: accepted
Date: 2026-09-19
Decided by: the repository owner, after the n8 pre-publishing history audit.

## Context

The n8 audit read every blob reachable from every ref, every commit message
and every ref name across 28 commits and 166 blob versions of 77 files
(`tools/scan_history.py`, proved against a planted decoy first — see the
audit section of the README).

It found no leaked credential and no committed private file. `.coveragerc` is
in history; `.coverage`, `.env`, `profile/master_profile.yaml`, the tracker,
and every generated document never were.

It found two things that publishing would expose and that editing a file
cannot undo, because the commits are already written:

1. **Sixteen versions of README.md**, from the initial release through
   `42570f3`, described the feed coverage in the first person and named a
   town and its postal code. HEAD no longer does. The earlier commits still
   do.
2. **Every commit carries a contactable personal address** in its author and
   committer lines, as every git commit everywhere does.

**2026-09-24, asked again: "is it safe to make the project public?"** The
answer is still no, and the scanner was tightened that day to say why in more
detail. It had only ever looked for the owner's details as WHOLE strings --
the full name, the full phone number, the full street address -- so fragments
passed. It now also checks each name part of four or more letters, each
four-digit-or-longer group of the phone, and the house number with the street
name. Three more findings appeared immediately, all in history, none of them
exposed while the repository is private:

3. **tests/test_scoring.py, since the initial release**, listed the surname,
   the house number and street, the last four phone digits and the GitHub
   handle as a "forbidden" list to assert against -- checking for a leak by
   writing the leak down. HEAD now reads those values from the profile at run
   time and skips when there is none.
4. **tests/test_letter.py and the message of commit 1c623b7**, both written
   on 2026-09-24, quote a dashboard warning that names the surname, the given
   name and phone fragments. The file was fixed the same day; the commit
   message cannot be.

All of it is recorded in `tools/history_allowlist.txt` with its reasoning, and
`tests/test_history_scan.py` now reads that file instead of keeping its own
copy of what is known.

Both original findings are removable only by rewriting all 28 commits, which
changes every SHA.
The usual cost of that — breaking existing clones and commit links — is
currently close to zero, because the repository is private and nobody else
has a copy. That cost becomes permanent the moment it is published.

## Decision

**The repository stays private until it is ready for a stranger to use with
their own personal information.** Publication is not a date; it is a bar.

Nothing is rewritten now. Both findings are recorded in
`tools/history_allowlist.txt` and stay visible on every scan run, so the
question comes back rather than fading.

The rewrite decision is deferred to the moment before publishing, when it is
still cheap and when whoever makes it can see the full list of what would be
exposed. Deferring is the reversible option: staying private preserves every
choice, and publishing forecloses the rewrite one forever.

## What "ready" means

The bar is that somebody who is not the author can fill in their own profile
and get useful work out of this, without editing code and without inheriting
the author's life. Progress so far, and what is left:

**Done.** The example profile ships de-personalised (n7). `jsa doctor` names
what a newcomer has not filled in yet. Location scoring works for all fifty
states, not just the author's. The README's first hour was walked by a
simulated newcomer in Columbus, Ohio, which found two real bugs.

**Not done, and known.**

- `config/companies.yaml` is the author's three markets — LA, San Diego, and
  south-central PA / north Maryland. A stranger elsewhere gets the remote
  roles and the AI-lab feeds, which is genuinely useful (the Ohio sandbox
  stored 654 postings and its top 20 were all remote), but no local coverage.
  A published tool should either ship a broader feed list or say plainly in
  the first paragraph that local coverage means adding your own employers.
- Rejection feedback is stored and never read back, so the tool does not
  learn from what the operator turns down.
- Postings are truncated before the model reads them. Corrected 2026-09-20,
  because this line was wrong: `enrich.py`, the pass that extracts the
  disqualifying facts, caps at 6,000 characters, not 4,000 — 39% of postings
  are truncated there and only 2 to 5 lose a fact entirely. The 4,000 cap is
  in `tailor.py` and `prep.py`: 90% truncated, and of those, 14 lose the
  degree requirement, 9 a clearance, 10 the years, 18 the sponsorship line.
  Smaller than stated here, and in a different place.
- The outreach path has never been used against a real contact. Done
  2026-09-20 and it went badly: see ADR 0010. Outreach now refuses about
  eight times in nine, deliberately, and the README says so.
- The LICENSE carries the author's name, which is intended, and which makes
  the two history findings above identifying rather than anonymous.
- The example profile still contains the author's real employment history
  (two roles at one employer, ten bullets). A stranger copying it inherits
  somebody else's life, which is the opposite of what the example is for.
  This one is fixable in HEAD and should be, before publishing.

## Consequences

- No force-push, no history rewrite, no change of visibility, now.
- `tools/scan_history.py` runs in CI on every push and fails on any **new**
  finding. The two known ones are allowlisted with their reasons.
- When publication is next considered, re-read this file first, re-run
  `tools/scan_history.py`, and decide about the rewrite while it is still
  cheap. If a rewrite happens, delete the allowlist entry and the two tests
  in `tests/test_history_scan.py` that pin it.
- Turning on GitHub's "Keep my email address private" and "Block command line
  pushes that expose my email" does not fix the 28 existing commits, but it
  prevents the same finding in the next repository. It is free and worth
  doing regardless of what happens here.
