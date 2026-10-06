"""`jsa daily`: backup, discovery and the inbox in one run, and what's new.

ADR 0028. The owner schedules this command (Task Scheduler, cron); the tool
never creates a schedule. Each step runs even when the one before it failed,
the run is recorded in `daily_runs`, and a UTF-8 log is written beside the
tracker whatever the console's encoding.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db

STEPS = ("backup", "discover", "inbox")
LOG_KEEP_DAYS = 30
_LOG_NAME = re.compile(r"^daily-(\d{4}-\d{2}-\d{2})\.log$")
# jsa doctor mentions a daily run older than this, once one has ever run.
STALE_DAYS = 3


@dataclass
class Step:
    name: str
    state: str          # ok | failed | skipped
    detail: str = ""


@dataclass
class Run:
    id: int | None
    started_at: str
    steps: list[Step] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(s.state != "failed" for s in self.steps)


def _db_path(db_path: Path | None) -> Path:
    from .config import DB_PATH
    return Path(db_path or DB_PATH)


def logs_dir(db_path: Path | None = None) -> Path:
    return _db_path(db_path).parent / "logs"


# --- the steps ------------------------------------------------------------------


def _backup(db_path: Path) -> Step:
    from . import backup
    path = backup.make(backup.default_root(db_path), label="daily", db_path=db_path)
    check = backup.verify(path)
    if check.ok:                       # never prune good copies for a bad one
        backup.prune(backup.default_root(db_path))
    if not check.ok:
        return Step("backup", "failed", "copy does not verify: " + "; ".join(check.problems))
    return Step("backup", "ok", f"{check.tables} tables, {check.rows} rows, "
                                f"{check.files} files, verified")


def _discover(db_path: Path | None = None) -> Step:
    from . import discover
    reports = discover.discover(db_path=db_path)
    failed = [r.company for r in reports if str(r.status).startswith("FAIL")]
    new = sum(getattr(r, "new", 0) for r in reports)
    polled = sum(1 for r in reports if r.status == "ok" or str(r.status).startswith("FAIL"))
    detail = f"{polled} feeds, {new} new posting(s)"
    if failed:
        return Step("discover", "failed",
                    f"{detail}; {len(failed)} feed(s) failed: {', '.join(failed)}")
    return Step("discover", "ok", detail)


def _inbox(db_path: Path) -> Step:
    from . import inbox
    if inbox.settings() is None:
        return Step("inbox", "skipped", "not set up")
    con = db.connect(db_path)
    try:
        report = inbox.fetch(con)
        con.commit()
    finally:
        con.close()
    return Step("inbox", "ok", f"{report.stored} new repl(ies)")


def _run_step(name: str, db_path: Path) -> Step:
    try:
        if name == "backup":
            return _backup(db_path)
        if name == "discover":
            return _discover(db_path)
        return _inbox(db_path)
    except Exception as exc:  # noqa: BLE001 - one failed step never stops the next
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        return Step(name, "failed", first[:300])


# --- the summary ------------------------------------------------------------------


def summarize(con: sqlite3.Connection, since: str) -> dict[str, Any]:
    """What's new since `since` (a stored UTC stamp)."""
    from . import approvals

    new = con.execute(
        "SELECT job_id, title, company, match_score FROM v_new_matches "
        "WHERE discovered_at > ? ORDER BY match_score DESC", (since,)).fetchall()
    due = approvals.due_items(con, days=1)
    return {
        "since": since,
        "new_matches": len(new),
        "top": [{"job_id": r["job_id"], "title": r["title"], "company": r["company"],
                 "score": round(r["match_score"] or 0, 2)} for r in new[:5]],
        "replies": con.execute("SELECT COUNT(*) FROM inbox_replies "
                               "WHERE state = 'pending'").fetchone()[0],
        "due": sum(1 for d in due if d.days_out is not None),
        "review": con.execute("SELECT COUNT(*) FROM approvals "
                              "WHERE decision = 'pending'").fetchone()[0],
    }


def summary_line(summary: dict[str, Any]) -> str:
    return (f"{summary.get('new_matches', 0)} new match(es) · "
            f"{summary.get('replies', 0)} repl(ies) to confirm · "
            f"{summary.get('due', 0)} follow-up(s) due · "
            f"{summary.get('review', 0)} draft(s) waiting in Review")


# --- the run --------------------------------------------------------------------------


def latest(con: sqlite3.Connection, *, unseen: bool = False) -> sqlite3.Row | None:
    sql = "SELECT * FROM daily_runs"
    if unseen:
        sql += " WHERE seen_at IS NULL AND finished_at IS NOT NULL"
    return con.execute(sql + " ORDER BY id DESC LIMIT 1").fetchone()


def run(skip: tuple[str, ...] = (), db_path: Path | None = None,
        now: datetime | None = None) -> Run:
    """Every step in order, each one's result kept; then the summary."""
    db_path = _db_path(db_path)
    now = now or datetime.now(timezone.utc)
    started = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    db.upgrade(db_path)       # an older tracker has no daily_runs table yet
    con = db.connect(db_path)
    try:
        before = latest(con)
        # From the end of the run before: what it found, it already reported.
        since = (before["finished_at"] or before["started_at"]) if before else (
            now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = con.execute("INSERT INTO daily_runs (started_at) VALUES (?)", (started,))
        con.commit()
        result = Run(cur.lastrowid, started)
    finally:
        con.close()

    for name in STEPS:
        if name in skip:
            result.steps.append(Step(name, "skipped", "--skip"))
        else:
            result.steps.append(_run_step(name, db_path))

    con = db.connect(db_path)
    try:
        result.summary = summarize(con, since)
        failed_feeds = next((s.detail for s in result.steps
                             if s.name == "discover" and s.state == "failed"), "")
        result.summary["failed"] = [s.name for s in result.steps if s.state == "failed"]
        result.summary["feeds"] = failed_feeds
        con.execute(
            "UPDATE daily_runs SET finished_at = ?, ok = ?, steps_json = ?, "
            "summary_json = ? WHERE id = ?",
            (db.utcnow(), int(result.ok), json.dumps([asdict(s) for s in result.steps]),
             json.dumps(result.summary), result.id))
        con.commit()
    finally:
        con.close()
    write_log(result, db_path)
    return result


def report(result: Run) -> str:
    lines = [f"jsa daily -- {db.local_time(result.started_at)}"]
    for s in result.steps:
        lines.append(f"  {s.name:<9} {s.state:<8} {s.detail}")
    lines.append("")
    lines.append(f"since {db.local_time(result.summary.get('since'))}: "
                 + summary_line(result.summary))
    for t in result.summary.get("top") or []:
        lines.append(f"  {t['score']:.2f}  #{t['job_id']} {t['company']} — {t['title']}")
    lines.append("")
    lines.append("ok" if result.ok else
                 "FAILED: " + ", ".join(result.summary.get("failed") or []))
    return "\n".join(lines)


def write_log(result: Run, db_path: Path | None = None) -> Path:
    """Append to logs/daily-YYYY-MM-DD.log in UTF-8, and drop logs older than
    LOG_KEEP_DAYS. Under Task Scheduler stdout can't hold "—"; the log can."""
    folder = logs_dir(db_path)
    folder.mkdir(parents=True, exist_ok=True)
    today = datetime.now().date()
    path = folder / f"daily-{today.isoformat()}.log"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(report(result) + "\n\n")
    for old in folder.iterdir():
        m = _LOG_NAME.match(old.name)
        if m and (today - datetime.strptime(m[1], "%Y-%m-%d").date()).days > LOG_KEEP_DAYS:
            old.unlink()
    return path


def schedule_help(at: str = "07:00") -> str:
    """The command the OWNER runs to schedule this. Printed, never run."""
    from .config import ROOT
    python = Path(sys.executable)
    windows = (f'schtasks /Create /SC DAILY /ST {at} /TN "jsa daily" /TR '
               f'"cmd /c cd /d \\"{ROOT}\\" && \\"{python}\\" -X utf8 -m jsa daily"')
    hour, minute = (at.split(":") + ["0"])[:2]
    cron = (f"{int(minute)} {int(hour)} * * * cd \"{ROOT}\" && "
            f"\"{python}\" -X utf8 -m jsa daily")
    head = ("To run `jsa daily` every day, run this yourself. This command "
            "changed nothing and scheduled nothing.\n\n")
    if sys.platform == "win32":
        return (head + f"In Command Prompt:\n  {windows}\n\n"
                'To remove it: schtasks /Delete /TN "jsa daily"')
    return head + f"Add this line with `crontab -e`:\n  {cron}"
