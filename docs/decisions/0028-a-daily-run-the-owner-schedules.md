# ADR 0028 — A daily run the owner schedules

Status: accepted
Date: 2026-10-03
Relates to: ADR 0021 (the mailbox), ADR 0025 (backups), ADR 0026 (Turbo).
Implements plan 18 of the planning session.

## Context

Discovery, the inbox check and backups each ran only when asked, so new
matches and replies waited until the owner remembered. ADR 0021 and 0025
both say the tool doesn't run on a schedule.

## Decision

1. **`jsa daily`** runs, in order: a backup (label `daily`, verified, 7
   kept apart from manual ones), discovery, and the inbox check (skipped when
   the mailbox isn't set up). A failed step doesn't stop the next; each
   result is recorded in `daily_runs`, and the command exits 1 when any step
   failed, so a scheduler shows it.
2. **The owner schedules it; the tool never does.** `jsa daily
   --schedule-help` prints the exact `schtasks` (or crontab) line for the
   owner to run. Nothing in the tool creates, changes or runs a schedule,
   which keeps ADR 0021 and 0025 true as written.
3. **What's new since the last run** (from when that run finished; the last
   24 hours the first time): new matches still in the list, with the top
   five; replies waiting to be confirmed; follow-ups due; drafts waiting in
   Review. Shown at the end of the run and as a banner on Matches and
   Pipeline until "Got it" (a token-checked POST).
4. **A UTF-8 log** in `logs/daily-YYYY-MM-DD.log` beside the tracker, kept 30
   days. Under Task Scheduler stdout can't hold "—" or "·"; the log can.
   `logs/` is kept out of git, Docker images and the secret scanner.
5. **`jsa doctor`** mentions a failed last run (naming the step), or a last
   run more than 3 days old, once one has ever run.

## Fixed on the way

- **Discovery held a write lock across the network.** It upserted the
  company and source, then fetched the feed (up to minutes) before
  committing, so a dashboard save, a pass or Turbo's drafter failed with
  "database is locked". It now commits before fetching, and every
  connection waits up to 10 seconds for another writer.
- **`jsa inbox` crashed with a traceback on a network error**; it now says
  it couldn't reach the mail server.

## Not done

Creating the scheduled task, notifications or emails to yourself (nothing
sends), and drafting new matches automatically.
