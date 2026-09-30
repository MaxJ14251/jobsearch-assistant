# ADR 0008 — Staying private until anyone can use it

Status: accepted. The history rewrite it deferred was performed on
2026-09-28; see "The rewrite, performed" below. Still private, and not yet
publishable: the same day's readiness check found more in the history (see
"Not done").
Date: 2026-09-19
Decided by: the repository owner, after the n8 pre-publishing history audit.

*SHAs below are the rewritten ones; the originals no longer exist.*

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
   `e41ce16`, described the feed coverage in the first person and named a
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
4. **tests/test_letter.py and the message of commit 761932f**, both written
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

**Done.** The example profile ships de-personalised -- properly, as of
2026-09-26 (n15). n7 claimed this and was wrong: every experience bullet was
still the author's own, verbatim, with the real employer, titles and dates.
It is now an invented person, with the shapes the tool needs kept on purpose
(two roles at one employer, a project in development, no conferred degree),
and `tests/test_example_profile.py` fails if any bullet, summary, employer,
school or project name is shared with the author's real profile. `jsa doctor` names
what a newcomer has not filled in yet. Location scoring works for all fifty
states, not just the author's. The README's first hour was walked by a
simulated newcomer in Columbus, Ohio, which found two real bugs.

**Where the old "not done" list stands (readiness check, 2026-09-28).**
Several items on the list this section used to carry had been settled
elsewhere, and one was wrong the day it was written:

- *Local coverage for a stranger:* the shipped feeds were the author's
  markets. The nationwide source (ADR 0013) now asks about the reader's own
  cities, and the README's opening says what it finds, with numbers for
  four cities.
- *Rejection feedback is never read back:* decided, not missing. It is a
  record for a person and a trigger for nothing (ADR 0011).
- *Postings truncated before the model reads them:* replaced by one budget
  of 12,000 characters in one function (ADR 0016).
- *Outreach never used against a real contact:* done, and it refuses about
  eight times in nine, deliberately (ADR 0010).
- *The example profile holds the author's employment history:* fixed in n15
  (see "Done" above). This bullet had outlived its fix.
- *The LICENSE carries the author's name:* intended, and decided again in
  the rewrite (decision b below).

**Not done: found by the readiness check, 2026-09-28.**

- **The history carries the author's employers and school.** The scanner
  only ever looked for contact details (name, phone, address, email), so it
  passed:
  - two tests naming the author's university in a list of false claims about
    the degree (in the files since the initial release);
  - a short employer name inside bullet ids and code comments, in old
    versions of six files and three commit messages;
  - the old example profile, which held the employer until n15.

  The scanner now checks employers and schools from the live profile.
  Three-letter names are matched as whole words, in any case. Values the
  example profile itself ships are exempt, and the example profile is
  checked for these even though it is exempt from the contact-detail scan.
  HEAD is clean. The history scan reports 27 findings, and
  `tests/test_history_scan.py` fails on the owner's machine (CI has no
  real profile, so it passes there) until either the history is rewritten
  again or the owner records the findings in `tools/history_allowlist.txt`.
  Either is the owner's decision. Publishing before it would publish the
  findings.
- **The repository says where the author looks for work.** The shipped feed
  list is built around the author's markets, the README quotes a result
  "measured on this tracker" for a town near the author, and this ADR named
  the author's regions outright until this edit. None of it identifies the
  author alone, but with the name in the LICENSE it narrows things down. It
  is the owner's call whether to generalise it before publishing.
  *Done in HEAD, 2026-09-29 (n27), with the owner's choice:* every employer
  kept, and the personal framing removed. That means a postal code in the
  employer list, commute times from one town, "local roles" wording, the
  README's market groups and region paragraph, and the author's towns and a
  nearby ZIP in tests (the tests that listed them now read them from the
  live profile at run time). The history still carries all of it, for n28.

## How the rewrite would be done

Written up and rehearsed on a throwaway mirror clone on 2026-09-26:
[docs/pre-publication-rewrite.md](../pre-publication-rewrite.md). The
rehearsal reached a clean scan -- with the allowlist emptied and the owner's
profile in place, so the scanner actually had values to look for -- and it
found three ways to get the rewrite wrong that are now written down.

The one that changes the plan: **a force-push does not remove anything from
GitHub.** Old commits stay reachable by SHA until GitHub garbage collects,
and a fork or pull request keeps them indefinitely. So the rewrite ends by
deleting the GitHub repository and pushing the rewritten history to a new
one, not by force-pushing over the old.

## The rewrite, performed (2026-09-28, goal n15b)

The owner decided, in that session:

| | Question | Decision |
|---|---|---|
| a | Name and address on commits | Keep the name; the GitHub noreply address |
| b | Name in the LICENSE | Keep it |
| c | How the old commits leave GitHub | Delete the repository and create a new one, not force-push |
| d | The three CI runs the README linked as evidence | Keep the claim, drop the links (they die with the repository) |
| e | Repository name | Keep `jobsearch-assistant` |

**What ran.** All 54 commits on `main` were rewritten with git-filter-repo;
`ci/docker`, merged and stale, was deleted rather than rewritten. The
replacement was **path-aware**, not the runbook's `--replace-text` rules
file, because the rehearsal of that file showed three rules that were only
right in some files: it rewrote the owner's name in the LICENSE (deciding
(b) for them), turned the handle in the project's own clone URL into a
placeholder, and would have touched the Census data. So personal details
were replaced everywhere; the name and profile links everywhere except
LICENSE, README.md and CONTRIBUTING.md (the scanner's AUTHORSHIP_FILES) and
the project URL; `data/` and binary files not at all. Every number rule is
bounded so it cannot match inside a longer number. 14 rules, 20 file
versions and 1 commit message changed. The values were read from the
profile in memory and never written to a file.

**Result, verified:**

- `tools/scan_history.py` with the owner's real profile and an **empty**
  allowlist: clean, and the commit-identity note is gone.
- Every author and committer line carries the noreply address.
- The current files came out byte-identical to before (same HEAD tree), so
  nothing in HEAD needed repairing except what referred to the old history.
- `data/*.gz` byte-identical, by SHA-256.
- The allowlist's four entries and the two tests that pinned the findings
  were deleted with the findings.

The owner deleted the old GitHub repository and created a new, private one
under the same name; the rewritten `main` was pushed to it the same day and
CI passed on its first run, the full-history scan included. A clone of the
new repository passes `tools/fresh_clone_check.py`. The repository is set to
commit with the noreply address, so the next commit cannot bring the
personal one back.

A mirror backup of the old history exists outside the project, is never
pushed, and is the owner's to delete now that the new repository is
confirmed.

## Consequences

- No force-push and no change of visibility. The repository is still
  private; publishing is a separate decision against the bar above.
- `tools/scan_history.py` runs in CI on every push and fails on any finding.
  The allowlist is empty; adding to it is a decision about published data.
- Before the first push to the new repository, turn on GitHub's "Keep my
  email address private" and "Block command line pushes that expose my
  email", so the clean history cannot be re-contaminated.
