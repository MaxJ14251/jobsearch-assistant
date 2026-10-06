"""Turbo mode: decide interest one match at a time (plan 16, ADR 0026).

A right swipe SAVES the job and queues a resume and a cover letter for
drafting; a left swipe PASSES it. Nothing here submits anything: the drafts
land in the Review queue, and applying stays something the person does on
the employer's site (ADR 0012). The worker's only model work is
drafting.draft_document, the same call `jsa tailor` makes; this module has
no network code of its own (tests/test_turbo.py checks its imports).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import approvals, db, posting

KINDS = ("resume", "cover_letter")
# Jobs a day whose drafts Turbo queues. The busiest drafting day so far was
# 10 resumes (measured 2026-10-03), so 20 leaves room without letting a fast
# thumb queue hundreds of paid drafts. `turbo.daily_jobs` in the profile
# overrides it.
TURBO_DAILY_JOBS = 20
# Tries to record a finished draft before giving up (review R-26).
FINISH_ATTEMPTS = 3


def _group(con: sqlite3.Connection, job_id: int) -> str | None:
    row = con.execute("SELECT COALESCE(dedup_key, 'job:' || id) AS grp FROM jobs "
                      "WHERE id = ?", (job_id,)).fetchone()
    return row["grp"] if row else None


IN_GROUP = "COALESCE(dedup_key, 'job:' || id) = ?"


# --- pass, and undo it --------------------------------------------------------


def pass_job(con: sqlite3.Connection, job_id: int) -> int:
    """Stamp every copy of this posting as passed. Returns how many. Nothing
    is deleted, and no application exists for it, so this isn't an event."""
    grp = _group(con, job_id)
    if grp is None:
        raise ValueError(f"there is no job {job_id}")
    return con.execute(f"UPDATE jobs SET passed_at = ? WHERE {IN_GROUP} "
                       "AND passed_at IS NULL", (db.utcnow(), grp)).rowcount


def unpass(con: sqlite3.Connection, job_id: int) -> int:
    grp = _group(con, job_id)
    if grp is None:
        raise ValueError(f"there is no job {job_id}")
    return con.execute(f"UPDATE jobs SET passed_at = NULL WHERE {IN_GROUP}",
                       (grp,)).rowcount


def passed(con: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """One row per passed posting, newest pass first."""
    return con.execute(
        "SELECT MIN(j.id) AS job_id, j.title, c.name AS company, MAX(j.passed_at) AS passed_at "
        "FROM jobs j LEFT JOIN companies c ON c.id = j.company_id "
        "WHERE j.passed_at IS NOT NULL "
        "GROUP BY COALESCE(j.dedup_key, 'job:' || j.id) "
        "ORDER BY passed_at DESC LIMIT ?", (limit,)).fetchall()


# --- interested: save, and maybe queue drafts --------------------------------


def daily_limit(profile: dict[str, Any] | None) -> int:
    value = ((profile or {}).get("turbo") or {}).get("daily_jobs")
    return int(value) if isinstance(value, int) and value >= 0 else TURBO_DAILY_JOBS


def queued_today(con: sqlite3.Connection) -> int:
    """Jobs whose drafts were queued today (local date)."""
    today = date.today()
    return len({r["job_id"] for r in con.execute("SELECT job_id, queued_at FROM draft_queue")
                if db.local_date(r["queued_at"]) == today})


def drafting_blocker(profile: dict[str, Any] | None) -> str | None:
    """Why a right swipe can only save right now, or None."""
    from .llm import LLMError, api_key
    from .tailor import UndecidedPreferenceError, require_decided_preferences

    if profile is None:
        return "your profile could not be loaded"
    try:
        require_decided_preferences(profile)
    except UndecidedPreferenceError as exc:
        return str(exc)
    try:
        api_key()
    except LLMError:
        return "no model API key is set (see .env.example)"
    return None


def enqueue(con: sqlite3.Connection, job_id: int) -> bool:
    """Queue a resume and a cover letter, unless this job already has an item
    waiting or running. Returns whether anything was queued."""
    busy = con.execute("SELECT 1 FROM draft_queue WHERE job_id = ? "
                       "AND state IN ('queued','running')", (job_id,)).fetchone()
    if busy:
        return False
    for kind in KINDS:
        con.execute("INSERT INTO draft_queue (job_id, kind) VALUES (?, ?)", (job_id, kind))
    return True


@dataclass
class Interested:
    queued: bool
    message: str


def interested(con: sqlite3.Connection, job_id: int,
               profile: dict[str, Any] | None) -> Interested:
    """Save the job (the person's act), then queue its drafts if allowed."""
    row = con.execute("SELECT id, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"there is no job {job_id}")
    approvals.save_application(con, job_id)
    blocker = drafting_blocker(profile)
    if blocker:
        return Interested(False, f"Saved. Swiping right saves only: {blocker}.")
    if posting.thin(row["description"], job_id):
        return Interested(False, "Saved, not drafted: this posting has almost no "
                                 f"text. Fill it in (`jsa fill {job_id}` or the job "
                                 "page), then draft.")
    limit = daily_limit(profile)
    if not enqueue_allowed(con, job_id, limit):
        return Interested(False, f"Saved. Today's drafting limit ({limit}) is "
                                 "reached; draft it from its job page, or swipe it "
                                 "again tomorrow. Nothing is queued for tomorrow "
                                 "on its own.")
    if not enqueue(con, job_id):
        return Interested(False, "Saved; its drafts are already queued.")
    return Interested(True, "Saved. Drafting a resume and cover letter; they "
                            "will be waiting in Review.")


def enqueue_allowed(con: sqlite3.Connection, job_id: int, limit: int) -> bool:
    already = con.execute("SELECT 1 FROM draft_queue WHERE job_id = ?",
                          (job_id,)).fetchone()
    return bool(already) or queued_today(con) < limit


def cancel_waiting(con: sqlite3.Connection) -> int:
    """Cancel drafts that haven't started. Running ones finish."""
    return con.execute("UPDATE draft_queue SET state = 'cancelled', finished_at = ? "
                       "WHERE state = 'queued'", (db.utcnow(),)).rowcount


def status(con: sqlite3.Connection) -> dict[str, Any]:
    today = date.today()
    rows = con.execute(
        "SELECT q.*, j.title, c.name AS company FROM draft_queue q "
        "JOIN jobs j ON j.id = q.job_id LEFT JOIN companies c ON c.id = j.company_id "
        "ORDER BY q.id").fetchall()
    pending = con.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'pending'"
                          ).fetchone()[0]
    return {
        "queued": sum(r["state"] == "queued" for r in rows),
        "running": sum(r["state"] == "running" for r in rows),
        "done_today": sum(r["state"] == "done" and db.local_date(r["finished_at"]) == today
                          for r in rows),
        "failed": [{"job_id": r["job_id"], "kind": r["kind"], "title": r["title"],
                    "company": r["company"], "error": r["error"]}
                   for r in rows if r["state"] == "failed"
                   and db.local_date(r["finished_at"]) == today],
        "pending_review": pending,
        # Every model call today, any purpose (plan 26's ledger).
        "model_calls_today": _calls_today(con, today),
    }


def _calls_today(con: sqlite3.Connection, today: date) -> int:
    from . import ledger
    return ledger.on_day(today, con).calls


# --- the worker -----------------------------------------------------------------


def requeue_stale(con: sqlite3.Connection) -> int:
    """Items a stopped server left running go back to the queue."""
    return con.execute("UPDATE draft_queue SET state = 'queued', started_at = NULL "
                       "WHERE state = 'running'").rowcount


class Worker:
    """One thread, one item at a time, so two drafts never race for a version
    number. Every failure is stored on the item; none reaches the dashboard."""

    def __init__(self, db_path: Path | None = None,
                 profile_loader: Callable[[], dict[str, Any]] | None = None):
        self.db_path = db_path
        self.profile_loader = profile_loader
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def _connect(self) -> sqlite3.Connection:
        con = db.connect(self.db_path) if self.db_path else db.connect()
        con.execute("PRAGMA busy_timeout = 30000")
        return con

    def _profile(self) -> dict[str, Any]:
        if self.profile_loader is not None:
            return self.profile_loader()
        from .config import load_profile
        return load_profile()

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        con = self._connect()
        try:
            requeue_stale(con)
            con.commit()
        finally:
            con.close()
        self._thread = threading.Thread(target=self._loop, name="turbo-drafts",
                                        daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            # Cleared BEFORE draining: a wake() that lands while drain() runs
            # is then kept, not lost for up to 30 s (review R-26).
            self._wake.clear()
            try:
                self.drain()
            except Exception:  # noqa: BLE001 - the thread must outlive any error
                pass
            self._wake.wait(timeout=30)

    def drain(self) -> int:
        """Draft every queued item, one at a time. Returns how many ran."""
        count = 0
        while self.run_one():
            count += 1
        return count

    def run_one(self) -> bool:
        con = self._connect()
        try:
            item = con.execute("SELECT * FROM draft_queue WHERE state = 'queued' "
                               "ORDER BY id LIMIT 1").fetchone()
            if item is None:
                return False
            claimed = con.execute(
                "UPDATE draft_queue SET state = 'running', started_at = ? "
                "WHERE id = ? AND state = 'queued'", (db.utcnow(), item["id"])).rowcount
            con.commit()
            if not claimed:
                return True
            state, note = self._draft(con, item["job_id"], item["kind"])
            # Retried: a lost update left the item "running" until a restart,
            # and the page polling forever (review R-26).
            for attempt in range(FINISH_ATTEMPTS):
                try:
                    con.execute("UPDATE draft_queue SET state = ?, error = ?, "
                                "finished_at = ? WHERE id = ?",
                                (state, note, db.utcnow(), item["id"]))
                    con.commit()
                    break
                except sqlite3.OperationalError:
                    con.rollback()
                    if attempt == FINISH_ATTEMPTS - 1:
                        raise
                    time.sleep(1)
            return True
        finally:
            con.close()

    def _draft(self, con: sqlite3.Connection, job_id: int, kind: str) -> tuple[str, str | None]:
        from . import render
        from .drafting import draft_document

        try:
            if render.next_version(con, job_id, kind) > 1:
                return "done", f"a {kind.replace('_', ' ')} was already drafted"
            draft_document(con, job_id, kind, force=False, profile=self._profile())
            return "done", None
        except Exception as exc:  # noqa: BLE001 - stored, never raised
            con.rollback()
            first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            return "failed", first[:300]
