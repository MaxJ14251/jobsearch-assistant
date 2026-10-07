# Contributing

Thanks for looking. This is a personal job-search tool that happens to be
useful to other people, so the bar is: it must keep working for someone who
isn't me.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Scripts/ on Windows, bin/ elsewhere
.venv/Scripts/python -m jsa init
.venv/Scripts/python -m unittest discover -s tests -t .
```

The suite runs with **no API key and no job data**. Anything that needs either
must `skip`, never fail — a fresh clone has neither, and a test that fails for
every new contributor is a broken test.

```bash
git config core.hooksPath .githooks
```
Installs a pre-commit hook that blocks API keys and personal data.

## The four rules this project is built around

Changes that weaken any of these will not be merged.

1. **No fabrication.** Generated text traces to a `master_profile.yaml` bullet
   id or it is rejected. See `jsa/tailor.py::verify_draft`.
2. **No identity to the API.** Name, address, phone and email are merged into
   documents locally. See `jsa/tailor.py::scrub_prompt`.
3. **No autonomous sending.** Every outbound action needs an `approvals` row
   decided by a human. Enforced by a database trigger, not by convention.
4. **Measure, don't assume.** Claims about model behaviour need a number. The
   benchmarks in the README came from `tools/fabrication_demo.py` and the
   enrichment runs, not from intuition.

## Adding a job board

`jsa/sources.py` holds one adapter per platform. An adapter takes a
`companies.yaml` entry and returns normalized dicts.

Board tokens are **not** derivable from a company name. Before opening a PR,
run `python -m jsa verify` and include the real output — a wrong token 404s, or
worse, silently returns a different company's board.

Platforms already handled: Greenhouse, Lever, Ashby, Workday, Workable,
Recruitee, RSS, plus two company-specific APIs. Eightfold returns 403 to
automated clients and is deliberately unsupported; SmartRecruiters and Breezy
HR were declined on their own terms (ADR 0020).

A new platform needs, in this order: its provider's own docs, terms and
robots.txt read and quoted with the date in the fetcher's header comment (a
provider that rules out automated reading is not added); its kind in the
`sources.kind` CHECK in `jsa/resources/schema.sql` (existing trackers are
rebuilt for it, with a backup first); and its `FETCHERS`, `URL_BUILDERS` and
`REQUIRED_FIELDS` entries.

## Before you open a PR

```bash
.venv/Scripts/python -m unittest discover -s tests -t .
.venv/Scripts/python tools/scan_secrets.py
.venv/Scripts/python tools/scan_history.py
.venv/Scripts/python tools/fresh_clone_check.py
```

The last one simulates a clone and follows the README literally. If you had to
deviate from the README to get something working, fix the README — that's the
bug.

### The changelog

Every user-visible change adds a line under `## [Unreleased]` in
`CHANGELOG.md`: what a person will notice, in their words, with an ADR link
when there is one. The version number lives only in `jsa/__init__.py`.

### Drafting evals

`tests/evals/` holds a fictional profile and 16 fictional postings, each with
written expectations: which bullets lead or never appear, which summary, the
role kind, the skill order, the keyword gaps. `tests/test_evals.py` runs them
in CI (no model, no network, under a second); for a readable scorecard:

```bash
.venv/Scripts/python tools/eval_report.py
```

A change to bullet selection, summaries or skill order that makes a case miss
is a regression until shown otherwise. Change an expectation only when the old
answer was wrong: say why in the case's `why:` line **and** in the commit
message. Editing the answer key to make a test pass is the one thing these
files exist to prevent. New cases are welcome; never copy a real posting or a
real profile into them.

## Before you make a fork public

Publishing a repository publishes **every commit**, not the current files. A
file deleted in commit 12 is still readable in commit 11 by anyone with the
URL, so `git rm` is not a fix — it is a second commit that mentions the file.
Check before the first push, not after.

### What must never reach a commit

| | Why |
|---|---|
| `.env` | your API key |
| `profile/master_profile.yaml` | your name, address, phone, email, and your whole work history |
| `*.db` | the tracker: every posting, every application, and any contact you added — including people who never agreed to be in it |
| `output/`, `documents/`, `*.docx` | generated resumes and letters, which carry your identity |
| `.coverage` | not obvious: it embeds absolute paths, so it leaks the OS account name of the machine that ran the tests |
| `.claude/` | agent session state |

All of these are in `.gitignore`. Trusting that is how the `.coverage` file got
committed anyway — a pattern that matches nothing looks exactly like one that
works. Verify instead:

```bash
git check-ignore -v .env profile/master_profile.yaml jobsearch.db
```

Silence means a file is **not** ignored.

### How to verify

```bash
.venv/Scripts/python tools/scan_secrets.py     # the working tree
.venv/Scripts/python tools/scan_history.py     # every commit, message and ref
.venv/Scripts/python tools/fresh_clone_check.py
```

`scan_history.py` is the one that matters here. It reads every blob reachable
from every ref, every commit message, and every branch name, looking for API
keys, contactable email addresses, absolute home paths, US postal addresses,
and the values in your own profile. Both scanners run in CI; the history job
checks out with `fetch-depth: 0`, because a shallow clone would let it pass
while looking at one commit.

Findings that have been seen and decided about live in
`tools/history_allowlist.txt`, with a note saying what and why. **Do not add a
line there to make a build green.** If something is in history that should not
be, the options are all expensive, and they belong to whoever owns the
repository:

- **rewrite the history** (`git filter-repo`) — removes it, and breaks every
  existing clone, every commit URL, and every CI run link;
- **start a fresh repository** without the old history — keeps the files,
  loses the record of how they got there;
- **leave it private.**

If a real API key ever reaches a commit, none of the above comes first:
**rotate the key.** It is readable from the moment it is pushed, and a rewrite
does not un-read it.

### Prose leaks too

The scanners read the values in your profile and a few fixed shapes. They
cannot read intent. This repository's own README described its feed coverage in
the first person and named a home town and postal code — nothing a profile
field could have flagged, and it sat there for sixteen commits. Read your own
prose as a stranger would before making it public.

Your commit author line is the same kind of decision: every commit carries the
name and email from `git config`. `scan_history.py` prints it as a NOTE on
every run. If you would rather a public history not carry your mailbox, set a
GitHub `users.noreply.github.com` address **before** the first commit —
changing it later only affects new commits.

## Style

Standard library first. `httpx` covers both job feeds and the LLM, so no
provider SDK is needed. New dependencies need a reason in the PR description.
