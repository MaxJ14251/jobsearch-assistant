"""The pipeline, and the Replies page: the inbox's suggested replies.

Moved from jsa/web.py without change (plan 24); see register(). Replies got
their own page on 2026-10-23, with "Got it" for receipts that move nothing."""

from __future__ import annotations

from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import approvals
from ..inbox import BEFORE_APPLIED
from . import plural, db


# "A strong match" for the empty-pipeline note: on 2026-10-27, 140 of 1,559
# unreviewed matches scored this or higher, a list worth a sitting.
STRONG_MATCH = 0.8

# How the saved group can be ordered, besides the usual due-first order.
SAVED_SORTS = {"years_asc": "fewest years first", "years_desc": "most years first",
               "posted_desc": "newest posting first", "posted_asc": "oldest posting first"}

# How an outreach message's status reads on a Pipeline card, in order.
OUTREACH_ORDER = ("drafted", "approved", "sent", "replied", "declined")


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    render = ctx.render

    def back_to_replies(msg: str, bad: bool) -> RedirectResponse:
        from urllib.parse import urlencode
        return RedirectResponse("/replies?" + urlencode({"msg": msg, "bad": int(bad)}),
                                status_code=303)

    def said(exc: Exception) -> str:
        return str(exc)[:1].upper() + str(exc)[1:]

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline(msg: str = "", bad: int = 0, saved_sort: str = ""):
        from datetime import date

        con = connect()
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT a.id AS application_id, a.job_id, a.status, a.next_action, "
                "       a.next_action_due, a.last_activity_at, j.title, j.closed_at, "
                "       j.years_required, j.posted_at, "
                "       c.name AS company "
                "  FROM applications a JOIN jobs j ON j.id = a.job_id "
                "  LEFT JOIN companies c ON c.id = j.company_id "
                " WHERE a.archived_at IS NULL").fetchall()]
            # Outreach per job, by status, for a line on each card. A
            # discarded draft is history, not news.
            sent_out: dict[int, dict[str, int]] = {}
            for o in con.execute(
                    "SELECT job_id, status, COUNT(*) AS n FROM outreach "
                    "WHERE job_id IS NOT NULL AND archived_at IS NULL "
                    "AND status != 'discarded' GROUP BY job_id, status"):
                sent_out.setdefault(o["job_id"], {})[o["status"]] = o["n"]
            from .. import applyqueue, outcomes, pairing
            happened = outcomes.all_outcomes(con)
            strong = con.execute("SELECT COUNT(*) FROM v_new_matches WHERE match_score >= ?",
                                 (STRONG_MATCH,)).fetchone()[0]
            # The apply session (plan 31): what is ready, and whether the
            # extension that walks it is connected.
            session = applyqueue.ready_to_apply(con)
            session["paired"] = pairing.paired_at(con) is not None
        finally:
            con.close()

        today = date.today()
        for row in rows:
            counts = sent_out.get(row["job_id"], {})
            items = [(s, counts[s]) for s in OUTREACH_ORDER if counts.get(s)]
            # "2 messages drafted · 1 sent": the noun once, on the first.
            row["outreach"] = " · ".join(
                (plural(n, "message") if i == 0 else str(n)) + f" {s}"
                for i, (s, n) in enumerate(items))
            row["days_out"] = None
            row["quiet"] = None
            if row["next_action_due"]:
                try:
                    row["days_out"] = (
                        date.fromisoformat(row["next_action_due"]) - today).days
                except ValueError:
                    pass
            if row["last_activity_at"] and not row["next_action_due"]:
                try:
                    quiet = (today - db.local_date(row["last_activity_at"])).days
                    row["quiet"] = quiet if quiet >= approvals.QUIET_DAYS else None
                except (TypeError, ValueError):
                    pass
        # Within a stage: overdue and due first (soonest first), then gone
        # quiet (longest first), then the rest as before.
        rows.sort(key=lambda r: (r["days_out"] is None, r["days_out"] or 0,
                                 -(r["quiet"] or 0)))
        live = [r for r in rows if r["status"] not in approvals.CLOSED]
        waiting = sum(1 for r in live if r["status"] in BEFORE_APPLIED)
        closed = [r for r in rows if r["status"] in approvals.CLOSED]
        groups = [(stage, [r for r in live if r["status"] == stage])
                  for stage in approvals.STAGES if stage not in approvals.CLOSED]
        groups = [(stage, items) for stage, items in groups if items]
        # Saved, by the years the posting asks for or the day it went up,
        # either way; a posting that states neither goes last both ways (it
        # is not "0 years" or "posted long ago").
        for row in rows:
            row["posted_on"] = db.posted_date(row["posted_at"])
        if saved_sort in SAVED_SORTS:
            field = "years_required" if saved_sort.startswith("years") else "posted_on"
            for stage, items in groups:
                if stage == "saved":
                    known = sorted((r for r in items if r[field] is not None),
                                   key=lambda r: r[field],
                                   reverse=saved_sort.endswith("_desc"))
                    items[:] = known + [r for r in items if r[field] is None]
        return render("pipeline", "pipeline", groups=groups, closed=closed,
                      total=len(live), stages=list(approvals.STAGES),
                      overdue=sum(1 for r in live
                                  if r["days_out"] is not None and r["days_out"] < 0),
                      happened=happened, happened_line=outcomes.summary(happened),
                      session=session, msg=msg, bad=bad, saved_sorts=SAVED_SORTS,
                      waiting=waiting, strong=strong, strong_at=STRONG_MATCH,
                      saved_sort=saved_sort if saved_sort in SAVED_SORTS else "")

    @app.post("/job/{job_id}/next")
    def set_follow_up(job_id: int, action: str = Form(""), due: str = Form(""),
                      clear: str = Form("")):
        """Your next action for this application, set or cleared from its
        Pipeline card: the same call `jsa next` makes. Never set by the tool."""
        from urllib.parse import urlencode
        con = connect()
        try:
            if clear == "1":
                approvals.clear_next_action(con, job_id)
                message = "Follow-up cleared."
            else:
                approvals.set_next_action(con, job_id, action, due=due.strip() or None)
                message = "Follow-up set."
            con.commit()
            bad = False
        except approvals.ApprovalError as exc:
            message, bad = said(exc), True
        finally:
            con.close()
        return RedirectResponse("/pipeline?" + urlencode({"msg": message, "bad": int(bad)})
                                + f"#app-{job_id}", status_code=303)

    @app.get("/replies", response_class=HTMLResponse)
    def replies_page(msg: str = "", bad: int = 0, ask: int = 0, to: str = ""):
        from .. import inbox
        con = connect()
        try:
            replies = [dict(r) for r in inbox.pending(con)]
        finally:
            con.close()
        receipts = sum(1 for r in replies if r["kind"] == "received" and r["application_id"])
        if to not in inbox.BEFORE_APPLIED:
            ask = 0                 # only a move back is ever asked about
        return render("replies", "replies", replies=replies, receipts=receipts,
                      ask=ask, ask_to=to,
                      stages=list(approvals.STAGES),
                      mail_ready=inbox.settings() is not None, msg=msg, bad=bad)

    @app.post("/inbox/fetch")
    def inbox_fetch():
        """Read the mailbox, read-only, and store suggestions (ADR 0021)."""
        from .. import inbox
        con = connect()
        try:
            report = inbox.fetch(con)
        except inbox.InboxError as exc:
            return back_to_replies(said(exc), True)
        except OSError as exc:
            return back_to_replies(f"Could not reach the mailbox: {exc}", True)
        finally:
            con.close()
        return back_to_replies(
            f"Checked {report.user}: {plural(report.searched, 'message')} looked at, "
            f"{plural(report.stored, 'new reply', 'new replies')} about your "
            "applications.", False)

    @app.post("/inbox/{reply_id}/confirm")
    def inbox_confirm(reply_id: int, stage: str = Form(...), back: str = Form("")):
        """Your confirmation moves the stage: the same call as the stage form.
        A move back before applied is asked about first (inbox.MovesBack)."""
        from urllib.parse import urlencode

        from .. import inbox, prep
        con = connect()
        try:
            done = inbox.confirm(con, reply_id, stage=stage, back=back == "1")
            con.commit()
            hint = prep.suggestion(con, done.application_id, done.stage)
        except inbox.MovesBack as exc:
            return RedirectResponse("/replies?" + urlencode(
                {"ask": reply_id, "to": exc.stage}), status_code=303)
        except (inbox.InboxError, approvals.ApprovalError) as exc:
            return back_to_replies(said(exc), True)
        finally:
            con.close()
        if done.previous == done.stage:
            return back_to_replies(f"Noted. It was already {done.stage.replace('_', ' ')}; "
                                   "nothing moved.", False)
        return back_to_replies(
            f"Moved from {done.previous.replace('_', ' ')} to "
            f"{done.stage.replace('_', ' ')}." + (f" {hint}." if hint else ""), False)

    @app.post("/inbox/receipts/acknowledge")
    def inbox_acknowledge_receipts():
        """Every pending "thank you for applying": seen, no stage moves."""
        from .. import inbox
        con = connect()
        try:
            n = inbox.acknowledge_receipts(con)
            con.commit()
        finally:
            con.close()
        return back_to_replies(f"Noted {plural(n, 'receipt')}. No stage moved.", False)

    @app.post("/inbox/{reply_id}/acknowledge")
    def inbox_acknowledge(reply_id: int):
        """Noted, and nothing moves: a reply with no stage to suggest."""
        from .. import inbox
        con = connect()
        try:
            inbox.acknowledge(con, reply_id)
            con.commit()
        except inbox.InboxError as exc:
            return back_to_replies(said(exc), True)
        finally:
            con.close()
        return back_to_replies("Noted. No stage moved.", False)

    @app.post("/inbox/{reply_id}/dismiss")
    def inbox_dismiss(reply_id: int):
        from .. import inbox
        con = connect()
        try:
            inbox.dismiss(con, reply_id)
            con.commit()
        except inbox.InboxError as exc:
            return back_to_replies(said(exc), True)
        finally:
            con.close()
        return back_to_replies("Dismissed. Nothing else changed.", False)
