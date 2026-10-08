"""The pipeline and the inbox's suggested replies.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

from fastapi import Form
from fastapi.responses import HTMLResponse

from .. import approvals, db


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    back_to_pipeline = ctx.back_to_pipeline
    connect = ctx.connect
    render = ctx.render

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
            from .. import applyqueue, inbox, outcomes, pairing
            replies = [dict(r) for r in inbox.pending(con)]
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
                      replies=replies, mail_ready=inbox.settings() is not None,
                      happened=happened, happened_line=outcomes.summary(happened),
                      session=session, msg=msg, bad=bad)

    @app.post("/inbox/fetch")
    def inbox_fetch():
        """Read the mailbox, read-only, and store suggestions (ADR 0021)."""
        from .. import inbox
        con = connect()
        try:
            report = inbox.fetch(con)
        except inbox.InboxError as exc:
            return back_to_pipeline(str(exc)[:1].upper() + str(exc)[1:], True)
        except OSError as exc:
            return back_to_pipeline(f"Could not reach the mailbox: {exc}", True)
        finally:
            con.close()
        return back_to_pipeline(
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
            return back_to_pipeline(str(exc)[:1].upper() + str(exc)[1:], True)
        finally:
            con.close()
        return back_to_pipeline(
            f"Moved from {done.previous.replace('_', ' ')} to "
            f"{done.stage.replace('_', ' ')}." + (f" {hint}." if hint else ""), False)

    @app.post("/inbox/{reply_id}/dismiss")
    def inbox_dismiss(reply_id: int):
        from .. import inbox
        con = connect()
        try:
            inbox.dismiss(con, reply_id)
            con.commit()
        except inbox.InboxError as exc:
            return back_to_pipeline(str(exc)[:1].upper() + str(exc)[1:], True)
        finally:
            con.close()
        return back_to_pipeline("Dismissed. Nothing else changed.", False)
