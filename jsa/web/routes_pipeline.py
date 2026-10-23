"""The pipeline, and the Replies page: the inbox's suggested replies.

Moved from jsa/web.py without change (plan 24); see register(). Replies got
their own page on 2026-10-23, with "Got it" for receipts that move nothing."""

from __future__ import annotations

from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import approvals, db


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
    def pipeline(msg: str = "", bad: int = 0):
        from datetime import date

        con = connect()
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT a.id AS application_id, a.job_id, a.status, a.next_action, "
                "       a.next_action_due, a.last_activity_at, j.title, "
                "       c.name AS company "
                "  FROM applications a JOIN jobs j ON j.id = a.job_id "
                "  LEFT JOIN companies c ON c.id = j.company_id "
                " WHERE a.archived_at IS NULL").fetchall()]
            from .. import applyqueue, outcomes, pairing
            happened = outcomes.all_outcomes(con)
            # The apply session (plan 31): what is ready, and whether the
            # extension that walks it is connected.
            session = applyqueue.ready_to_apply(con)
            session["paired"] = pairing.paired_at(con) is not None
        finally:
            con.close()

        today = date.today()
        for row in rows:
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
        live = [r for r in rows if r["status"] not in approvals.CLOSED]
        closed = [r for r in rows if r["status"] in approvals.CLOSED]
        groups = [(stage, [r for r in live if r["status"] == stage])
                  for stage in approvals.STAGES if stage not in approvals.CLOSED]
        groups = [(stage, items) for stage, items in groups if items]
        return render("pipeline", "pipeline", groups=groups, closed=closed,
                      total=len(live), stages=list(approvals.STAGES),
                      overdue=sum(1 for r in live
                                  if r["days_out"] is not None and r["days_out"] < 0),
                      happened=happened, happened_line=outcomes.summary(happened),
                      session=session, msg=msg, bad=bad)

    @app.get("/replies", response_class=HTMLResponse)
    def replies_page(msg: str = "", bad: int = 0):
        from .. import inbox
        con = connect()
        try:
            replies = [dict(r) for r in inbox.pending(con)]
        finally:
            con.close()
        receipts = sum(1 for r in replies if r["kind"] == "received" and r["application_id"])
        return render("replies", "replies", replies=replies, receipts=receipts,
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
            f"Checked {report.user}: {report.searched} message(s) looked at, "
            f"{report.stored} new repl(ies) about your applications.", False)

    @app.post("/inbox/{reply_id}/confirm")
    def inbox_confirm(reply_id: int, stage: str = Form(...)):
        """Your confirmation moves the stage: the same call as the stage form."""
        from .. import inbox, prep
        con = connect()
        try:
            done = inbox.confirm(con, reply_id, stage=stage)
            con.commit()
            hint = prep.suggestion(con, done.application_id, done.stage)
        except (inbox.InboxError, approvals.ApprovalError) as exc:
            return back_to_replies(said(exc), True)
        finally:
            con.close()
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
        return back_to_replies(f"Noted {n} receipt(s). No stage moved.", False)

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
