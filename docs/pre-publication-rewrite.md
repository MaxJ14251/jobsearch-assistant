# Pre-publication rewrite: the runbook

**Status: performed, 2026-09-28, and again on 2026-09-29.** The history was
rewritten locally, the scan is clean with an empty allowlist, and the
repository is still private.
[ADR 0008](decisions/0008-staying-private-until-publishable.md) records the
owner's decisions and the results. This file is kept as the plan, for
whoever does this to their own fork.

**What the second rewrite changed about the method.** Instead of listing
files a rule must not touch, it never touches a line that is still in HEAD's
version of the same file. Whatever HEAD says was already reviewed, so HEAD's
tree comes out byte-identical without per-file exceptions, and old text that
HEAD no longer has is fair game. It also squashed the older basemap versions
into the current one. Two traps found in its rehearsal: a guard meant for
"District of Columbia" that skipped any town after "of ", and a five-digit
salary in the profile that looked like a postal code. Keep only postal codes
that resolve to a real place near the profile's towns.

**One change from the plan below, found by rehearsing it.** Step 3's
`--replace-text` rules file cannot tell *which file* a line is in, and three
rules were only right in some files: the owner's name belongs in LICENSE,
README.md and CONTRIBUTING.md; the GitHub handle is part of the project's
own URL; and `data/*.gz` is public Census data that contains every town and
postal code, the owner's included. The real rewrite therefore used
git-filter-repo's Python API with a per-file callback: personal details
replaced everywhere, the name and links everywhere except those three files
and the project URL, `data/` and binaries never. Rehearse it the same way,
and check the rehearsal's HEAD tree against the real one -- if they match,
no current file was touched.

*SHAs below are from the second rewrite; earlier ones no longer exist.*

Nothing here runs automatically. Every step is the repository owner's to take.

## What has to be gone before the repository is public

Run `python tools/scan_history.py` for the current list. As of 2026-09-26, in
39 commits across `main` and `ci/docker`:

| # | What | Where |
|---|---|---|
| 1 | The owner's email | author and committer lines of all 39 commits |
| 2 | A town and its postal code | 16 versions of `README.md`, through `e304969` |
| 3 | Surname, house number and street, last four phone digits, repo handle | `tests/test_scoring.py`, from the initial release until 2026-09-24 |
| 4 | Surname, given name, phone fragments | `tests/test_letter.py` as added in `cd7780a`, and that commit's *message* |

Items 3 and 4 are gone from HEAD. Only a rewrite removes them from history.

The LICENSE carries the owner's real name on purpose. That is a choice, not a
leak — but it is what ties items 1–4 to a person, so it is worth re-deciding
at the same time.

## The rehearsal, step by step

Everything below was run on a mirror clone. Times are from a 39-commit repo;
it takes seconds.

### 1. Get the tool

`git-filter-repo` is the maintained one. `git filter-branch` is deprecated by
git itself and is slower and easier to get wrong.

```bash
pip install git-filter-repo
```

### 2. Build the replacement rules from the profile, not by hand

Generate them; do not type them. A hand-written list is a list of the owner's
personal details sitting in a file, which is the problem this is solving.
`scratchpad/` (gitignored) is the place for it.

The rules must cover, for each value: the whole value, and the fragments the
scanner now looks for — each name part of four or more letters, each
four-or-more digit group of the phone, and the house number with the street
name.

**Three things the rehearsal got wrong the first time:**

- **Case.** `literal:` rules are case-sensitive, and the old test file wrote
  the surname in lower case. The surname survived the first pass. Use
  `regex:(?i)` for every rule.
- **A mailmap line whose name does not match is silently ignored.** The
  profile says one form of the name, the commits say another. Key the mailmap
  on the email alone:
  `Example Author <you@example.com> <the.old@address>`
- **A placeholder that still looks like the thing is not a fix.** Replacing a
  town and postal code with a placeholder town and a placeholder five-digit
  number left a state code followed by five digits behind — exactly the shape
  the scanner looks for, so the scan still failed. Replace with a word, not a
  fake number. (Writing the failing example out in this file tripped the
  scanner too, which is the rule working.)

Also include anything the findings name that the profile does *not*: the town
in item 2 is a family home and appears in nobody's identity block.

### 3. Rewrite a copy first

```bash
git clone --mirror . /tmp/rehearsal.git        # includes ci/docker
cd /tmp/rehearsal.git
git filter-repo --replace-text /path/rules.txt \
                --replace-message /path/rules.txt \
                --mailmap /path/mailmap --force
```

`--replace-text` covers blob contents, `--replace-message` covers commit
messages (item 4 lives in one), `--mailmap` covers item 1.

### 4. Verify with the scanner, not by eye

The scanner reads the owner's values from `profile/master_profile.yaml`, which
is gitignored — **a clone has no profile, so the scanner has nothing to look
for and reports clean**. Copy the real profile into the verification clone, and
empty the allowlist so nothing is excused:

```bash
git clone /tmp/rehearsal.git /tmp/verify && cd /tmp/verify
cp <the real profile> profile/master_profile.yaml
: > tools/history_allowlist.txt
python tools/scan_history.py
```

Expected output, and the bar for going ahead:

```
clean: nothing in any commit beyond what has been reported
```

The rehearsal reached this on 2026-09-26 with 14 rules.

### 5. Expect the rewrite to edit today's files too

It rewrites every blob, including the current ones. In the rehearsal it
changed a test that lists place names for a legitimate reason, and the suite
caught it. After the real rewrite, before pushing anything:

- run the full suite and repair what the rewrite mangled, in a normal commit;
- delete the entries in `tools/history_allowlist.txt` for findings that no
  longer exist — an allowlist that outlives its findings teaches the next
  person to ignore it;
- delete the two tests in `tests/test_history_scan.py` that assert the known
  findings are *still there*. They say so in their own failure messages;
- re-point or remove links to commits that no longer exist: `README.md` (4),
  ADR 0008 (2), `tools/history_allowlist.txt` (5, which go anyway).
- CI run URLs do **not** survive the delete-and-recreate recommended below:
  runs belong to the repository. This file said they would, and was wrong.
  Either re-run the builds on the new repository, or drop the links.

## The remote is not the local copy

**A force-push does not remove anything from GitHub.** The old commits stay
reachable by their SHA on github.com after a force-push — no branch points at
them, but anyone with the SHA can still fetch them, and a fork or a pull
request keeps them indefinitely. Anyone who saw the repository while it was
private, and any automation with a token for it, can have those SHAs.

So a force-push is the wrong instrument here. Two options that actually work:

1. **Delete the GitHub repository and push the rewritten history as a new
   one.** Complete, immediate, and under the owner's own control. The cost is
   the repository's URL history: stars, watchers, issues and existing clone
   URLs go. This repository is private with a single collaborator, so that
   cost is close to zero. **This is the recommended one.**
2. **Force-push, then ask GitHub Support to run garbage collection** on the
   repository. This works, but it depends on a third party's queue, and the
   window between the push and the collection is exactly the window where a
   SHA still resolves.

Whichever is chosen, do it *before* the repository is made public, never
after. Once it is public, assume every commit has been cloned, cached and
indexed within minutes, and a rewrite fixes nothing.

## Order of operations on the day

1. Re-read ADR 0008 and this file.
2. `python tools/scan_history.py` — get today's list, not this file's.
3. Rehearse on a mirror clone; verify with an empty allowlist and the real
   profile; reach "clean".
4. Rewrite the real repository the same way.
5. Run the suite; repair HEAD; delete the stale allowlist entries and the two
   history tests; fix the commit links.
6. Delete the GitHub repository and push the rewritten history to a new one.
7. Turn on GitHub's *Keep my email address private* and *Block command line
   pushes that expose my email* before the first push, so this does not start
   again.
8. Only then consider making it public, which is a separate decision, with
   its own bar in ADR 0008.
