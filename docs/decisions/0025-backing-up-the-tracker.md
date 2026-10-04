# ADR 0025 — Backing up the tracker

Status: accepted
Date: 2026-10-02
Relates to: ADR 0003 (application lifecycle), ADR 0012 (what applied
means). Implements plan 12 of the planning session.

## Context

`jobsearch.db` is the only record of applications, stage history,
approvals, rejection notes and inbox replies. It is gitignored by design
and nothing copied it anywhere. `db.upgrade()` runs on most write paths,
and three of its migrations rebuild whole tables; their docstrings record
past damage to the author's tracker. `output/` (the drafted documents the
tracker points to) and the profile can't be recreated either.

## Decision

1. **`jsa backup`** makes a dated folder holding the tracker, `output/`,
   the profile and a `manifest.json` (row counts per table, every file's
   sha256), then verifies it: `PRAGMA integrity_check`, the counts, every
   hash, and each sent document against the hash recorded when it was sent.
   A mismatch is reported, never fixed.
2. **Online backup, not a file copy.** The tracker runs in WAL mode, so
   recent writes can sit in `-wal`; `sqlite3.Connection.backup` copies
   every committed row and is safe while the dashboard runs.
3. **`.env` is never copied.** Keys can be reissued; a stray copy of a key
   is a leak.
4. **A copy before every rebuild.** `upgrade()` asks the rebuild steps'
   own staleness checks (now pure predicates, `db.rebuilds_pending`)
   whether one would run. If so it takes a `pre-upgrade` copy first, and if
   that copy fails the upgrade does not run. The approvals rebuild also no
   longer drops the old table when the new definition is missing.
5. **Restore copies first.** `jsa restore` verifies the copy, refuses while
   `-wal` holds writes, asks for the word `restore`, and takes a
   `pre-restore` copy of the current state before putting anything back.
   Only the tracker by default; `--all` also restores `output/` and the
   profile.
6. **Where copies live.** `backups/` beside the tracker, so under Docker
   they land on the `/data` volume. Kept out of git, Docker images and the
   secret scanner. Pruning removes only folders matching this tool's own
   name pattern: 10 manual, 3 pre-upgrade and 3 pre-restore copies are kept.
7. **A reminder, not a schedule.** `jsa doctor` mentions a missing copy or
   one older than 7 days. Nothing runs on a timer.

`jsa backup --check` verifies the live tracker's sent documents against
their recorded hashes, read-only. Nothing did that before.

## Out of scope

Scheduled backups, encrypting copies, uploading them anywhere, and backing
up `.env`.

## Note, 2026-10-03

The owner may schedule `jsa daily` (ADR 0028), which takes a `daily` copy (7 kept). The tool itself still never creates or runs a schedule.
