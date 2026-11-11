"""The browser extension's endpoints (plan 30, ADR 0031).

The extension fills a Greenhouse, Lever or Ashby application form in the
person's own browser; the person reviews it and presses the page's Submit.
Nothing here submits anything. Everything under `/ext/` needs the pairing
key (the guard in `__init__.py`); `/extension` is the dashboard page that
makes and revokes it, behind the usual page token.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .. import approvals, countries, review
from . import APPLY_BY_HAND_STAGES, DOCX_TYPE

# The written answers a form may ask for, in the profile's own words or a
# checked draft. Never the salary note: the floor is not an asking figure.
FACT_KEYS = ("work_authorization", "sponsorship", "relocation", "arrangement",
             "linkedin", "github", "portfolio", "website", "education", "years")


def identity(profile: dict[str, Any]) -> dict[str, Any]:
    """Who is applying: what an application form asks, nothing else.

    No street or postal code: the boards this fills ask for a city at most."""
    ident = profile.get("identity") or {}
    loc = ident.get("location") or {}
    full = " ".join(str(ident.get("full_name") or "").split())
    words = full.split(" ") if full else []
    return {
        "full_name": full,
        "first_name": " ".join(words[:-1]) if len(words) > 1 else full,
        "last_name": words[-1] if len(words) > 1 else "",
        "preferred_name": str(ident.get("preferred_name") or "").strip(),
        "email": str(ident.get("email") or "").strip(),
        "phone": str(ident.get("phone") or "").strip(),
        "city": str(loc.get("city") or "").strip(),
        "state": str(loc.get("state") or "").strip(),
        "country": str(loc.get("country") or "").strip(),
        # "Ontario" for "ON": the name a province or state dropdown lists.
        "region_name": countries.active().SUBDIVISIONS.get(
            str(loc.get("state") or "").strip().upper(), ""),
        "links": {k: str(v) for k, v in (profile.get("links") or {}).items()
                  if k in ("linkedin", "github", "portfolio", "website") and v},
    }


def drafts_left(con: sqlite3.Connection, prof: dict[str, Any] | None) -> int | None:
    """Resumes the panel may still draft today, or None on an older tracker."""
    from .. import prepare
    try:
        return prepare.left_today(con, prof)
    except sqlite3.OperationalError:
        return None


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    document_status = ctx.document_status
    out_dir = ctx.out_dir
    profile = ctx.profile
    render = ctx.render

    # --- pairing, on the dashboard -------------------------------------------

    @app.get("/extension", response_class=HTMLResponse)
    def extension_page():
        from .. import pairing
        con = connect()
        try:
            paired = pairing.paired_at(con)
        finally:
            con.close()
        return render("extension", "pipeline", title="Browser extension",
                      paired=paired, key=None, daily=None)

    @app.post("/extension/connect", response_class=HTMLResponse)
    def extension_connect():
        """A new key, shown on this response only: it is never stored, and
        never put in a URL."""
        from .. import pairing
        con = connect()
        try:
            key = pairing.connect(con)
            con.commit()
            paired = pairing.paired_at(con)
        finally:
            con.close()
        return render("extension", "pipeline", title="Browser extension",
                      paired=paired, key=key, daily=None)

    @app.post("/extension/disconnect")
    def extension_disconnect():
        from .. import pairing
        con = connect()
        try:
            pairing.disconnect(con)
            con.commit()
        finally:
            con.close()
        return RedirectResponse("/extension", status_code=303)

    # --- what the extension calls ----------------------------------------------

    @app.get("/ext/ping")
    def ext_ping():
        from .. import __version__
        return {"ok": True, "version": __version__}

    def documents_for(con: sqlite3.Connection, job_id: int) -> dict[str, Any]:
        """The newest approved resume and cover letter, or the newest draft
        when none is approved, said so."""
        out: dict[str, Any] = {}
        warnings: list[str] = []
        for kind in ("resume", "cover_letter"):
            rows = con.execute("SELECT id, version, path FROM documents WHERE job_id = ? "
                               "AND kind = ? ORDER BY version DESC, id DESC",
                               (job_id, kind)).fetchall()
            statuses = [(r, document_status(con, r["id"])[0]) for r in rows]
            approved = [r for r, s in statuses if s == "approved"]
            # Never a draft you denied (2026-11-04: the panel can deny one).
            usable = [r for r, s in statuses if s != "rejected"]
            pick = approved[0] if approved else (usable[0] if usable else None)
            if pick is None:
                out[kind] = None
                if rows:
                    warnings.append(f"You denied the last {kind.replace('_', ' ')} "
                                    "drafted for this job.")
                continue
            out[kind] = {"id": pick["id"], "version": pick["version"],
                         "filename": str(pick["path"] or "").replace("\\", "/").rsplit("/", 1)[-1],
                         "approved": bool(approved)}
            label = "resume" if kind == "resume" else "cover letter"
            if not approved:
                warnings.append(f"No approved {label}: v{pick['version']} is a draft "
                                "you have not approved.")
            elif any(s == "pending" and r["version"] > pick["version"] for r, s in statuses):
                warnings.append(f"A newer {label} draft is waiting in Review; this "
                                f"fills the approved v{pick['version']}.")
        if out.get("resume") is None and not any("denied the last resume" in w
                                                 for w in warnings):
            warnings.append("No resume drafted for this job yet.")
        return {"documents": out, "warnings": warnings}

    @app.get("/ext/fill")
    def ext_fill(url: str = ""):
        from .. import answers, db, intake, posting
        link = intake.parse_link(url)
        if link is None or link.kind not in ("greenhouse", "lever", "ashby"):
            return {"state": "unsupported",
                    "message": "This page is not a Greenhouse, Lever or Ashby "
                               "application the extension can fill."}
        prof = profile()
        con = connect()
        try:
            job_id = db.job_for_link(con, link)
            if job_id is None:
                return {"state": "unknown", "add_url": "/add",
                        "drafts_left": drafts_left(con, prof),
                        "message": "This job is not in your tracker. Add it from the "
                                   "dashboard's Add page, then save it."}
            job = answers.job_row(con, job_id)
            application = con.execute("SELECT status, applied_at FROM applications "
                                      "WHERE job_id = ?", (job_id,)).fetchone()
            summary = {"id": job_id, "title": job["title"], "company": job["company"]}
            if application is None:
                return {"state": "not_saved", "job": summary, "job_url": f"/job/{job_id}",
                        "drafts_left": drafts_left(con, prof),
                        "message": "Save this job in the dashboard first."}
            if application["status"] not in APPLY_BY_HAND_STAGES:
                return {"state": "past", "job": summary, "job_url": f"/job/{job_id}",
                        "status": application["status"],
                        "applied_at": application["applied_at"],
                        "message": f"This application is already "
                                   f"{application['status'].replace('_', ' ')}."}
            if prof is None:
                return {"state": "no_profile", "job": summary,
                        "message": "No profile: run `jsa init` and fill it in."}
            docs = documents_for(con, job_id)
            left = drafts_left(con, prof)
            try:
                written = answers.stored(con, job_id)
            except sqlite3.OperationalError:          # an older tracker: no table yet
                written = []
        finally:
            con.close()

        facts, left_out = {}, []
        for a in answers.fact_answers(prof, job):
            if a.key not in FACT_KEYS:
                continue                     # salary: never filled (ADR 0001)
            if a.source == "profile":
                facts[a.key] = a.body
            else:
                left_out.append({"key": a.key, "note": a.body})
        prefs = prof.get("job_search_preferences") or {}
        # The profile key is the country's; "authorized_to_work_us" is also the
        # name the extension (fill.js) reads the answer by.
        key = countries.active().AUTHORIZED_KEY
        if isinstance(prefs.get(key), bool):
            facts["authorized_to_work_us"] = "Yes" if prefs[key] else "No"
        warnings = list(docs["warnings"])
        thin = posting.thin(job.get("description"), job_id)
        if thin:
            warnings.append(thin[:1].upper() + thin[1:])
        for a in written:
            if a.source == "composed":
                warnings.append(f"\"{a.question}\" is composed from your own sentences; "
                                "read it before you submit.")
        return {
            "state": "ready",
            "job": summary,
            "board": link.kind,
            "identity": identity(prof),
            # The home country: a yes/no naming another one is left alone,
            # and "City & Country" is answered with this name.
            "country": {"code": countries.active().CODE, "name": countries.active().NAME},
            "facts": facts,
            "left_out": left_out,
            "written": [{"key": a.key, "question": a.question, "body": a.body,
                         "source": a.source} for a in written],
            "documents": docs["documents"],
            "warnings": warnings,
            "drafts_left": left,
        }

    @app.get("/ext/queue")
    def ext_queue():
        """The apply session's jobs, in order (plan 31). Reads only."""
        from .. import applyqueue
        con = connect()
        try:
            return applyqueue.ready_to_apply(con)
        finally:
            con.close()

    @app.get("/ext/research")
    def ext_research(url: str = ""):
        """About this company and role (plan 34). Reads only."""
        from .. import research
        con = connect()
        try:
            return research.research(con, url)
        finally:
            con.close()

    @app.post("/ext/research/refresh")
    def ext_research_refresh(url: str = Form("")):
        """Read this page's board once, when the panel found nothing recent.
        The board's own host only; at most weekly per board, a few a day."""
        from .. import research
        con = connect()
        try:
            return research.refresh(con, url)
        except research.ResearchError as exc:
            return JSONResponse({"state": "failed", "message": str(exc)}, status_code=200)
        finally:
            con.close()

    # --- suggested answers (plan 35) ---------------------------------------------

    def applying_to(con: sqlite3.Connection, url: str) -> tuple[dict | None, dict | None]:
        """The job this page is the application for, if you are applying to
        it now; else (None, the reason as the panel shows it)."""
        from .. import answers, db, intake
        link = intake.parse_link(url)
        if link is None or link.kind not in ("greenhouse", "lever", "ashby"):
            return None, {"state": "unsupported", "message": "Not an application page "
                          "the extension knows."}
        job_id = db.job_for_link(con, link)
        application = job_id and con.execute(
            "SELECT status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        if not application or application["status"] not in APPLY_BY_HAND_STAGES:
            return None, {"state": "not_ready", "message": "Suggestions are for a job you "
                          "saved and are applying to now."}
        return answers.job_row(con, job_id), None

    def maxlength_of(value: str) -> int | None:
        return int(value) if value.strip().isdigit() and int(value) > 0 else None

    @app.post("/ext/suggest")
    def ext_suggest(url: str = Form(""), question: str = Form(""),
                    maxlength: str = Form(""), reason: str = Form("")):
        """One Suggest press: one model call, three checked options."""
        from .. import llm, suggest
        from ..tailor import IdentityLeakError
        prof = profile()
        if prof is None:
            return {"state": "no_profile", "message": "No profile: run `jsa init`."}
        con = connect()
        try:
            job, refusal = applying_to(con, url)
            if refusal:
                return refusal
            try:
                result = suggest.suggest(con, prof, job, question, maxlength_of(maxlength),
                                         reason=reason)
            except (llm.LLMError, IdentityLeakError) as exc:
                return {"state": "failed", "message": f"No suggestions: {exc}"}
            con.commit()
            return result
        finally:
            con.close()

    @app.post("/ext/suggest/earlier")
    def ext_suggest_earlier(url: str = Form(""), question: list[str] = Form([]),
                            maxlength: list[str] = Form([])):
        """Answers you gave before to these questions, the options the last
        press for each showed, and how many suggestions are left today. Reads
        only; no model."""
        from .. import suggest
        prof = profile() or {}
        con = connect()
        try:
            job, refusal = applying_to(con, url)
            left = suggest.left_today(con, prof)
            found, last = {}, {}
            if not refusal:
                for i, raw in enumerate(question[:40]):
                    try:
                        q = suggest.clean_question(raw)
                    except suggest.SuggestError:
                        continue
                    if suggest.refused_topic(q):
                        continue
                    cap = maxlength_of(maxlength[i]) if i < len(maxlength) else None
                    hits = suggest.earlier(con, q, prof, job, cap)
                    if hits:
                        found[raw] = hits
                    shown = suggest.last_shown(con, q, job)
                    if shown:
                        last[raw] = shown
            return {"state": refusal["state"] if refusal else "ok",
                    "left_today": left, "limit": suggest.daily_limit(prof),
                    "earlier": found, "last": last, "note": suggest.panel_note()}
        finally:
            con.close()

    @app.post("/ext/remember")
    def ext_remember(url: str = Form(""), question: str = Form(""), body: str = Form(""),
                     source: str = Form("yours")):
        """At "I submitted this": the answer you submitted, kept for the same
        question next time. Your text: stored, never sent to a model."""
        from .. import suggest
        con = connect()
        try:
            job, refusal = applying_to(con, url)
            if refusal:
                # Recorded as applied already: the page still names the job.
                from .. import answers, db, intake
                link = intake.parse_link(url)
                job_id = link and db.job_for_link(con, link)
                if not job_id:
                    return refusal
                job = answers.job_row(con, job_id)
            try:
                entry = suggest.remember(con, question, body, source, job)
            except suggest.SuggestError as exc:
                return {"ok": False, "message": str(exc)}
            con.commit()
            return {"ok": True, "id": entry}
        finally:
            con.close()

    @app.post("/ext/prepare")
    def ext_prepare(url: str = Form(""), confirm: str = Form("")):
        """The panel's "Add, save and draft a resume" button: at most one
        model call. Never approves; the resume waits in Review."""
        from .. import prepare
        if confirm != "draft":
            return {"state": "failed", "message": "Nothing done: the request was not "
                                                  "confirmed from the panel's button."}
        prof = profile()
        if prof is None:
            return {"state": "no_profile", "message": "No profile: run `jsa init`."}
        con = connect()
        try:
            result = prepare.prepare(con, url, prof)
        finally:
            con.close()
        result["review_url"] = "/review"
        return result

    # --- reading and deciding a draft in the panel (2026-11-04) ---------------------
    # The owner asked to approve or deny from the panel instead of switching to
    # Review. The panel shows the draft's text and what the check flagged
    # first; a decision is your button press and calls exactly what Review's
    # buttons and `jsa approve` / `jsa reject` call (ADR 0003 decision 5).

    def waiting_for(con: sqlite3.Connection, job_id: int, prof: dict | None) -> list[dict]:
        rows = con.execute(
            "SELECT a.id AS approval_id, d.* FROM approvals a JOIN documents d "
            "ON d.id = a.subject_id WHERE a.subject_type = 'document' "
            "AND a.decision = 'pending' AND d.job_id = ? "
            "ORDER BY d.kind DESC, d.version DESC", (job_id,)).fetchall()
        out = []
        for r in rows:
            info = ctx.inspect_document(r, prof)
            flags = [info["problem"]] if info.get("problem") else []
            for b in (info.get("compare") or {}).get("bullets") or []:
                if b["status"] in ("flag", "unsourced"):
                    flags.append(f"{b['note']}: {b['text'][:120]}")
            summary = (info.get("compare") or {}).get("summary")
            if summary and summary["status"] == "flag":
                flags.append("the summary adds words your profile does not support")
            flags += [str(p) for p in ((info.get("letter") or {}).get("problems") or [])]
            out.append({"approval_id": r["approval_id"], "document_id": r["id"],
                        "kind": r["kind"], "version": r["version"],
                        "paragraphs": [p["text"] for p in info.get("paragraphs") or []][:200],
                        "flags": flags})
        return out

    @app.get("/ext/review")
    def ext_review(url: str = ""):
        """Drafts for this page's job that wait for your decision. Reads only."""
        con = connect()
        try:
            job, refusal = applying_to(con, url)
            if refusal:
                return refusal
            return {"state": "ok", "waiting": waiting_for(con, job["id"], profile())}
        finally:
            con.close()

    @app.post("/ext/decide")
    def ext_decide(url: str = Form(""), approval_id: int = Form(...),
                   decision: str = Form(""), confirm: str = Form(""),
                   feedback: str = Form(""), reason: str = Form("")):
        """Your Approve or Deny button in the panel. Only a draft of this
        page's job that is still waiting; nothing is decided without the
        panel's confirm."""
        if confirm != "decide" or decision not in ("approve", "reject"):
            return JSONResponse({"ok": False, "error": "Nothing decided: the request was "
                                 "not confirmed from the panel's button."}, status_code=400)
        con = connect()
        try:
            job, refusal = applying_to(con, url)
            if refusal:
                return JSONResponse({"ok": False, "error": refusal["message"]},
                                    status_code=400)
            mine = {w["approval_id"] for w in waiting_for(con, job["id"], None)}
            if approval_id not in mine:
                return JSONResponse({"ok": False, "error": "Nothing decided: that draft "
                                     "is not one of this job's waiting drafts."},
                                    status_code=400)
            if decision == "approve":
                approvals.approve(con, approval_id)
            else:
                approvals.reject(con, approval_id, feedback.strip()[:2000]
                                 or "Denied from the extension's panel.", reason or None)
            con.commit()
        except approvals.ApprovalError as exc:
            return JSONResponse({"ok": False, "error": f"Nothing decided: {exc}"},
                                status_code=400)
        finally:
            con.close()
        return {"ok": True, "decision": decision}

    @app.get("/ext/document/{document_id}")
    def ext_document(document_id: int):
        """Only a document of a job you are applying to by hand now."""
        con = connect()
        try:
            row = con.execute(
                "SELECT d.path FROM documents d JOIN applications a ON a.job_id = d.job_id "
                "WHERE d.id = ? AND a.status IN (%s)" % ",".join("?" * len(APPLY_BY_HAND_STAGES)),
                (document_id, *APPLY_BY_HAND_STAGES)).fetchone()
        finally:
            con.close()
        path = review.safe_document_path(row["path"], out_dir()) if row else None
        if path is None:
            return JSONResponse({"error": "No such document."}, status_code=404)
        return FileResponse(path, filename=path.name, media_type=DOCX_TYPE,
                            headers={"Cache-Control": "no-store"})

    @app.post("/ext/applied")
    def ext_applied(job_id: int = Form(...), resume: str = Form(""), cover: str = Form(""),
                    confirm: str = Form(""), confirm_unapproved: str = Form(""),
                    how: str = Form("")):
        """The panel's "I submitted this": the person's own statement, recorded
        as the job page's "I applied" records it. The extension never submits.

        `how=confirmation_prompt` (plan 36) says the click was on the prompt
        the board's own "application received" page brought up; it is noted,
        and is the same click by the same person."""
        note = ("recorded from the confirmation-page prompt"
                if how == "confirmation_prompt" else None)
        if confirm != "submitted":
            return JSONResponse({"ok": False, "error": "Nothing recorded: confirm that "
                                 "you submitted it."}, status_code=400)
        resume_id, cover_id = (int(v) if v.strip().isdigit() else None
                               for v in (resume, cover))
        con = connect()
        try:
            approved = (resume_id is not None
                        and approvals.is_approved(con, "document", resume_id))
            if not approved and confirm_unapproved != "1":
                return JSONResponse({"ok": False, "needs": "confirm_unapproved",
                                     "error": "Nothing recorded: the resume is not an "
                                     "approved one. Confirm to record it anyway."},
                                    status_code=409)
            application_id, previous = approvals.set_stage(
                con, job_id, "applied", via="employer", resume=resume_id, cover=cover_id,
                note=note)
            sent = approvals.submitted(con, application_id)
            con.commit()
        except approvals.ApprovalError as exc:
            return JSONResponse({"ok": False, "error": f"Nothing recorded: {exc}"},
                                status_code=400)
        finally:
            con.close()
        return {"ok": True, "previous": previous,
                "sent": [s.describe() for s in sent],
                "message": "Recorded as applied on the employer's site. "
                           "This tool submitted nothing."}
