"""A job: its page, save, tailor, fill, answers, prep, stage and 'I applied'.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import urlencode

from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import approvals
from ..prep import INTERVIEW_ROUNDS
from . import APPLY_BY_HAND_STAGES, _json_list


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    back_to_job = ctx.back_to_job
    back_to_pipeline = ctx.back_to_pipeline
    connect = ctx.connect
    document_status = ctx.document_status
    inspect_document = ctx.inspect_document
    not_found = ctx.not_found
    profile = ctx.profile
    render = ctx.render

    @app.get("/job/{job_id}", response_class=HTMLResponse)
    def job_detail(job_id: int, msg: str = "", bad: int = 0):
        prof = profile()
        sources: dict[str, Any] = {}
        if prof is not None:
            from ..tailor import collect_bullets
            sources = collect_bullets(prof)
        con = connect()
        try:
            row = con.execute(
                "SELECT j.*, c.name AS company, "
                "(SELECT kind FROM sources WHERE id = j.source_id) AS source_kind "
                "FROM jobs j JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
                (job_id,)).fetchone()
            if row is None:
                return not_found("job")
            application = con.execute(
                "SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
            documents = []
            # documents.job_id is authoritative (ADR 0003 decision 2).
            for d in con.execute(
                    "SELECT * FROM documents WHERE job_id = ? "
                    "ORDER BY version DESC, id DESC", (job_id,)).fetchall():
                status, feedback = document_status(con, d["id"])
                info = inspect_document(d, prof)
                documents.append({
                    **dict(d), "status": status, "feedback": feedback,
                    "gaps": _json_list(d["keywords_missing"]),
                    "bullets": [{"id": i, "text": sources[i].text if i in sources else None}
                                for i in _json_list(d["bullet_ids"])],
                    "servable": info["servable"],
                    "flagged": (info["compare"] or {}).get("flagged", 0),
                })
            preps = []
            if application is not None:
                for p in con.execute(
                        "SELECT * FROM interview_prep WHERE application_id = ? "
                        "AND archived_at IS NULL ORDER BY id DESC",
                        (application["id"],)).fetchall():
                    preps.append({**dict(p), "count": len(_json_list(p["questions"]))})
            from .. import answers
            try:
                written = answers.stored(con, job_id) if application is not None else []
            except sqlite3.OperationalError:      # an older tracker: no table yet
                written = []
            checklist = (apply_checklist(con, dict(row), documents)
                         if application is not None
                         and application["status"] in APPLY_BY_HAND_STAGES else None)
        finally:
            con.close()
        facts = (answers.fact_answers(prof, dict(row))
                 if application is not None and prof else [])
        education = _education(row)
        from .. import intake, posting
        return render("job", "matches", title=row["title"], job=dict(row),
                      thin=posting.thin(row["description"], job_id),
                      min_chars=intake.MIN_PASTED_CHARS,
                      max_chars=intake.MAX_PASTED_CHARS,
                      stack=_json_list(row["tech_stack"]),
                      application=dict(application) if application else None,
                      documents=documents, preps=preps, msg=msg, bad=bad,
                      interview_rounds=INTERVIEW_ROUNDS,
                      answer_facts=facts, answer_written=written,
                      checklist=checklist, education=education)

    def _education(row) -> dict | None:
        """What this posting asks for, and its company's postings (plan 32)."""
        from .. import companies, degree
        # An older tracker has no reading until it is upgraded.
        if "degree_level" not in row.keys() or row["degree_level"] is None:
            return None
        con = connect()
        try:
            profile = companies.company_profile(con, row["company_id"])
        finally:
            con.close()
        return {"label": degree.LABELS[row["degree_level"]],
                "evidence": row["degree_evidence"],
                "certs": _json_list(row["certs_named"]),
                "company": profile.line() if profile and profile.n else "",
                "caveat": degree.CAVEAT}

    def apply_checklist(con, job: dict[str, Any], documents: list[dict]) -> dict:
        """Plan 22: what applying by hand needs, in order. Reads only."""
        company = con.execute("SELECT careers_url FROM companies WHERE id = ?",
                              (job["company_id"],)).fetchone()
        via = approvals.channel(job["url"], job["company"],
                                company["careers_url"] if company else None)
        employer = None
        if via in ("linkedin", "indeed"):
            # The employer's own posting of this job, if the tracker has it
            # (plan 21's rule, tracker only: no request on page load).
            from .. import find
            try:
                report = find.find(con, job["company"], job["title"],
                                   job["location"] or "", live=False, entries=[])
            except find.FindError:
                report = None
            for hit in (report.same if report else []):
                if hit.job_id != job["id"] and approvals.channel(
                        hit.url, job["company"]) == "employer":
                    employer = hit
                    break

        def kind(name: str) -> dict:
            docs = [d for d in documents if d["kind"] == name]
            approved = [d for d in docs if d["status"] == "approved"]
            newest = approved[0]["version"] if approved else 0
            waiting = [d for d in docs if d["status"] == "pending"
                       and d["version"] > newest]
            return {"approved": approved, "waiting": waiting,
                    "others": [d for d in docs if d["status"] != "approved"]}

        return {"via": via, "via_label": approvals.VIA_LABELS[via],
                "choices": [(v, approvals.VIA_LABELS[v]) for v in approvals.VIA],
                "employer": employer, "resume": kind("resume"),
                "cover": kind("cover_letter")}

    @app.post("/job/{job_id}/save")
    def do_save(job_id: int):
        con = connect()
        try:
            _, created = approvals.save_application(con, job_id)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_job(job_id, str(exc), True)
        finally:
            con.close()
        return back_to_job(job_id, "Saved as an application." if created
                           else "Already saved.", False)

    @app.post("/job/{job_id}/tailor")
    def do_tailor(job_id: int, kind: str = Form("resume")):
        from ..drafting import DraftError, draft_document
        from ..llm import LLMError
        if kind not in ("resume", "cover_letter"):
            return back_to_job(job_id, f"Unknown document kind {kind!r}.", True)
        prof = profile()
        if prof is None:
            return back_to_job(job_id, "Your profile could not be loaded.", True)
        con = connect()
        try:
            # Pressing the button is the explicit request for a new version,
            # which is what --force means on the command line.
            result = draft_document(con, job_id, kind, force=True, profile=prof)
        except DraftError as exc:
            prefix = "Refused by a safety check: " if exc.refused else ""
            return back_to_job(job_id, prefix + str(exc), True)
        except LLMError as exc:
            # A failed model call was a 500 here (plan 16).
            return back_to_job(job_id, f"The model call failed: {exc}", True)
        finally:
            con.close()
        parts = [f"Drafted {kind.replace('_', ' ')} v{result.version}. "
                 "It is waiting in the review queue."]
        if result.revert_reasons:
            parts.append(f"{len(result.revert_reasons)} rewrite(s) went back to "
                         "your own words.")
        if result.gaps:
            parts.append("Gaps: " + ", ".join(result.gaps) + ".")
        # Both were printed by `jsa tailor` and dropped here.
        if result.description_note:
            parts.append("Note: the " + result.description_note + ".")
        if result.thin_note:
            parts.append("Warning: " + result.thin_note)
        return back_to_job(job_id, " ".join(parts), False)

    @app.post("/job/{job_id}/fill")
    def fill_posting(job_id: int, text: str = Form(...), location: str = Form("")):
        """Paste the real posting into a stub, in place (ADR 0020)."""
        from .. import intake
        from ..config import Preferences
        prof = profile()
        if prof is None:
            return back_to_job(job_id, "Your profile could not be loaded, so the "
                                       "job cannot be scored.", True)
        con = connect()
        try:
            before = con.execute("SELECT match_score FROM jobs WHERE id = ?",
                                 (job_id,)).fetchone()
            if before is None:
                return not_found("job")
            filled = intake.fill(con, job_id, text, Preferences.from_profile(prof),
                                 location=location)
            con.commit()
            filled.enriched = intake.enrich(con, job_id)
            con.commit()
        except intake.IntakeError as exc:
            return back_to_job(job_id, str(exc), True)
        finally:
            con.close()
        parts = ["Posting saved to this job. Score "
                 f"{before['match_score'] or 0:.2f} -> {filled.score:.2f}."]
        if filled.enriched:
            parts.append(filled.enriched[0].upper() + filled.enriched[1:] + ".")
        parts.extend(filled.warnings)
        if filled.status:
            parts.append("Draft a new version to use it.")
        return back_to_job(job_id, " ".join(parts), False)

    @app.post("/job/{job_id}/answers")
    def draft_answers(job_id: int):
        """The button is the request, as with Tailor: one model call, the
        answers checked, a failed one built from your own sentences."""
        from .. import answers
        from ..tailor import IdentityLeakError
        prof = profile()
        if prof is None:
            return back_to_job(job_id, "Your profile could not be loaded.", True)
        con = connect()
        try:
            written = answers.write(con, job_id, prof)
            con.commit()
        except (ValueError, IdentityLeakError) as exc:
            return back_to_job(job_id, str(exc), True)
        finally:
            con.close()
        checked = sum(a.source == "model" for a in written)
        return RedirectResponse(
            f"/job/{job_id}?" + urlencode({
                "msg": f"Drafted {len(written)} answers: {checked} checked, "
                       f"{len(written) - checked} from your own sentences.",
                "bad": 0}) + "#answers", status_code=303)

    @app.post("/job/{job_id}/prep")
    def draft_prep(job_id: int, round: str = Form(...)):
        """Pressing the button is the request (ADR 0003 decision 6), as with
        Tailor: the same steps as `jsa prep`, one model call, nothing else."""
        from .. import prep as prep_mod
        from ..llm import LLMError
        from ..tailor import UndecidedPreferenceError, require_decided_preferences

        if round not in INTERVIEW_ROUNDS:
            return back_to_job(job_id, f"{round} is not an interview round.", True)
        prof = profile()
        if prof is None:
            return back_to_job(job_id, "Your profile could not be loaded.", True)
        con = connect()
        try:
            app_row = con.execute("SELECT id FROM applications WHERE job_id = ?",
                                  (job_id,)).fetchone()
            if app_row is None:
                return back_to_job(job_id, "Save this job first.", True)
            require_decided_preferences(prof)
            prep_mod.generate(con, app_row["id"], round=round, profile=prof)
            prep_id = con.execute(
                "SELECT MAX(id) FROM interview_prep WHERE application_id = ?",
                (app_row["id"],)).fetchone()[0]
            con.commit()
        except (prep_mod.DegreeClaimError, UndecidedPreferenceError, LLMError,
                ValueError) as exc:
            return back_to_job(job_id, str(exc), True)
        finally:
            con.close()
        return RedirectResponse(f"/prep/{prep_id}", status_code=303)

    @app.get("/prep/{prep_id}", response_class=HTMLResponse)
    def prep_detail(prep_id: int):
        con = connect()
        try:
            row = con.execute(
                "SELECT p.*, a.job_id, j.title, c.name AS company "
                "FROM interview_prep p JOIN applications a ON a.id = p.application_id "
                "JOIN jobs j ON j.id = a.job_id JOIN companies c ON c.id = j.company_id "
                "WHERE p.id = ?", (prep_id,)).fetchone()
        finally:
            con.close()
        if row is None:
            return not_found("interview prep")
        questions = [q for q in _json_list(row["questions"]) if isinstance(q, dict)]
        return render("prep", "matches", title="Interview prep",
                      prep=dict(row), questions=questions)

    @app.post("/job/{job_id}/stage")
    def do_stage(job_id: int, stage: str = Form(...)):
        """The same approvals call `jsa status` makes. ADR 0003 decision 5."""
        from .. import prep
        con = connect()
        try:
            application_id, previous = approvals.set_stage(con, job_id, stage)
            con.commit()
            hint = prep.suggestion(con, application_id, stage)
        except approvals.ApprovalError as exc:
            return back_to_pipeline(str(exc), True)
        finally:
            con.close()
        return back_to_pipeline(
            f"Moved from {previous.replace('_', ' ')} to "
            f"{stage.replace('_', ' ')}." + (f" {hint}." if hint else ""), False)

    @app.post("/job/{job_id}/applied")
    def do_applied(job_id: int, via: str = Form(...), resume: str = Form(""),
                   cover: str = Form(""), confirm: str = Form("")):
        """Plan 22: the checklist's "I applied". The person's own statement,
        through set_stage -> mark_applied, as `jsa applied` records it."""
        resume_id, cover_id = (int(v) if v.strip().isdigit() else None
                               for v in (resume, cover))
        con = connect()
        try:
            approved_resume = (resume_id not in (None, approvals.NOT_SENT)
                               and approvals.is_approved(con, "document", resume_id))
            if not approved_resume and confirm != "1":
                return back_to_job(job_id, "Nothing recorded. Record that you applied "
                                   "without an approved resume? Tick the box under "
                                   "\"I applied\" to confirm.", True)
            application_id, previous = approvals.set_stage(
                con, job_id, "applied", via=via, resume=resume_id, cover=cover_id)
            sent = approvals.submitted(con, application_id)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_job(job_id, f"Nothing recorded: {exc}", True)
        finally:
            con.close()
        what = "; ".join(s.describe() for s in sent) or "no document recorded"
        return back_to_job(job_id, f"Recorded: you applied via {approvals.VIA_LABELS[via]}. "
                           f"Sent: {what}. This tool submitted nothing.", False)
