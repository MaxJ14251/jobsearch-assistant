"""What happened to each application, and plain counts by group.

A read-only report (ADR 0003, ADR 0021): it decides nothing and recommends
nothing, and no model is involved (a test checks this module imports nothing
from jsa.llm).

Points, in order, each counted only if it still stands:
  applied      the application went out
  heard_back   a reply from the employer: a confirmed or pending inbox reply
               that isn't an automatic receipt, a rejection, or any later stage
  interview    phone_screen, technical or onsite was reached
  offer
`closed` (rejected, withdrawn, or marked ghosted by the person) is recorded
beside the point, and `quiet`
means no event for approvals.QUIET_DAYS after applying: a report, never
"ghosted" (that stays the person's call).

A stage that was undone doesn't count: when the latest move took the
application back below a stage, the furthest point is recomputed from there.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .approvals import QUIET_DAYS

POINTS = ("applied", "heard_back", "interview", "offer")
_LEVEL = {"saved": 0, "drafting": 0, "ready": 0, "applied": 1,
          "phone_screen": 3, "technical": 3, "onsite": 3, "offer": 4}
GROUPINGS = ("source_kind", "role_kind", "cover_letter", "redrafted", "speed", "via")
# Below this many applications a group shows counts only, no rate. On
# 2026-10-02 the owner had 4 applications in all: any percentage would be
# one person's luck, and ADR 0011 already refused to analyse twelve notes.
MIN_FOR_RATE = 10
# "speed": applied within this many days of the posting being found.
FAST_DAYS = 3


@dataclass
class Outcome:
    application_id: int
    job_id: int
    company: str
    title: str
    point: str
    closed: bool
    quiet: bool
    days_since_applied: int | None


def _when(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _replay(events: list[sqlite3.Row]) -> tuple[int, bool, bool]:
    """(level reached and still standing, closed, rejected after applying)."""
    level, closed, rejected = 0, False, False
    for e in events:
        stage = e["to_status"]
        if stage in ("rejected", "withdrawn", "ghosted"):
            closed = True
            rejected = rejected or (stage == "rejected" and level >= 1)
            continue
        closed = False                  # reopened
        new = _LEVEL.get(stage, 0)
        level = new if new < level else max(level, new)   # undone: back down
        if level == 0:
            rejected = False
    return level, closed, rejected


def furthest(con: sqlite3.Connection, application_id: int,
             now: datetime | None = None) -> Outcome | None:
    """The furthest point one application reached, or None if it never went out."""
    now = now or datetime.now(timezone.utc)
    app = con.execute(
        "SELECT a.id, a.job_id, a.applied_at, j.title, c.name AS company "
        "FROM applications a JOIN jobs j ON j.id = a.job_id "
        "LEFT JOIN companies c ON c.id = j.company_id WHERE a.id = ?",
        (application_id,)).fetchone()
    if app is None:
        return None
    events = con.execute(
        "SELECT to_status, occurred_at FROM application_events "
        "WHERE application_id = ? ORDER BY occurred_at, id", (application_id,)).fetchall()
    level, closed, rejected = _replay(events)
    if level < 1:
        return None
    # An automatic receipt isn't hearing back; a dismissed reply was wrong.
    replied = con.execute(
        "SELECT COUNT(*) FROM inbox_replies WHERE application_id = ? "
        "AND kind != 'received' AND state != 'dismissed'",
        (application_id,)).fetchone()[0]
    if level == 1 and (replied or rejected):
        level = 2
    point = {1: "applied", 2: "heard_back", 3: "interview", 4: "offer"}[level]
    applied = _when(app["applied_at"]) or next(
        (_when(e["occurred_at"]) for e in events if e["to_status"] == "applied"), None)
    last = _when(events[-1]["occurred_at"]) if events else applied
    quiet = (point == "applied" and not closed and last is not None
             and (now - last).days >= QUIET_DAYS)
    return Outcome(app["id"], app["job_id"], app["company"] or "", app["title"] or "",
                   point, closed, quiet,
                   (now - applied).days if applied else None)


def all_outcomes(con: sqlite3.Connection, now: datetime | None = None) -> list[Outcome]:
    found = []
    for (app_id,) in con.execute("SELECT id FROM applications ORDER BY id"):
        o = furthest(con, app_id, now)
        if o is not None:
            found.append(o)
    return found


def _group(con: sqlite3.Connection, o: Outcome, by: str) -> str:
    if by == "source_kind":
        row = con.execute("SELECT s.kind FROM jobs j LEFT JOIN sources s "
                          "ON s.id = j.source_id WHERE j.id = ?", (o.job_id,)).fetchone()
        return row["kind"] or "added by hand"
    if by == "role_kind":
        from .tailor import role_kind
        row = con.execute("SELECT title, track FROM jobs WHERE id = ?",
                          (o.job_id,)).fetchone()
        return role_kind(row["title"], row["track"])
    if by == "via":
        from .approvals import VIA_LABELS
        row = con.execute("SELECT applied_via FROM applications WHERE id = ?",
                          (o.application_id,)).fetchone()
        return VIA_LABELS.get(row["applied_via"] or "", "not recorded")
    sent = {r["kind"]: r["version"] for r in con.execute(
        "SELECT kind, version FROM submitted_documents WHERE application_id = ?",
        (o.application_id,))}
    if by == "cover_letter":
        if not sent:
            return "not recorded"
        return "with a cover letter" if "cover_letter" in sent else "resume only"
    if by == "redrafted":
        if "resume" not in sent:
            return "not recorded"
        return "redrafted first" if sent["resume"] > 1 else "first draft"
    if by == "speed":
        row = con.execute("SELECT j.discovered_at, a.applied_at FROM applications a "
                          "JOIN jobs j ON j.id = a.job_id WHERE a.id = ?",
                          (o.application_id,)).fetchone()
        found, applied = _when(row["discovered_at"]), _when(row["applied_at"])
        if not (found and applied):
            return "not recorded"
        return (f"within {FAST_DAYS} days of finding it"
                if (applied - found).days <= FAST_DAYS else "later")
    raise ValueError(f"group by one of {', '.join(GROUPINGS)}")


@dataclass
class Row:
    group: str
    n: int
    counts: dict[str, int]       # furthest point -> applications
    closed: int
    quiet: int

    @property
    def heard_back_or_better(self) -> int:
        return sum(self.counts[p] for p in POINTS[1:])

    @property
    def rate(self) -> float | None:
        """Share that heard back or better; None for a group too small."""
        return self.heard_back_or_better / self.n if self.n >= MIN_FOR_RATE else None


def table(con: sqlite3.Connection, by: str, now: datetime | None = None) -> list[Row]:
    """Counts per furthest point for each group. `source_kind` means the
    source that found the job first: later finds aren't recorded."""
    if by not in GROUPINGS:
        raise ValueError(f"group by one of {', '.join(GROUPINGS)}")
    rows: dict[str, Row] = {}
    for o in all_outcomes(con, now):
        g = _group(con, o, by)
        row = rows.setdefault(g, Row(g, 0, {p: 0 for p in POINTS}, 0, 0))
        row.n += 1
        row.counts[o.point] += 1
        row.closed += int(o.closed)
        row.quiet += int(o.quiet)
    return sorted(rows.values(), key=lambda r: (-r.n, r.group))


def summary(outcomes: list[Outcome]) -> str:
    n = len(outcomes)
    if not n:
        return "No applications have gone out yet."
    count = {p: sum(o.point == p for o in outcomes) for p in POINTS}
    return (f"{n} application(s): {count['heard_back']} heard back, "
            f"{count['interview']} reached an interview, {count['offer']} offer(s); "
            f"{sum(o.closed for o in outcomes)} closed, "
            f"{sum(o.quiet for o in outcomes)} quiet for {QUIET_DAYS}+ days.")

