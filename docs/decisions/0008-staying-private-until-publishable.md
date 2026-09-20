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

Both are removable only by rewriting all 28 commits, which changes every SHA.
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
- The model sees only the first 4,000 characters of a posting.
- The outreach path has never been used against a real contact.
- The LICENSE carries the author's name, which is intended, and which makes
  the two history findings above identifying rather than anonymous.

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
