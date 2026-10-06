"""Add a job: from a link or pasted text, find a listing (plan 21), import a resume.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import File, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from . import MAX_UPLOAD_BYTES


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    back_to_job = ctx.back_to_job
    connect = ctx.connect
    profile = ctx.profile
    render = ctx.render

    @app.get("/add", response_class=HTMLResponse)
    def add_form(msg: str = "", bad: int = 0, company: str = "", title: str = "",
                 location: str = "", link: str = ""):
        from ..intake import MAX_PASTED_CHARS, MIN_PASTED_CHARS
        prefill = {"company": company, "title": title, "location": location,
                   "link": link}
        return render("add", "add", title="Add a job", msg=msg, bad=bad,
                      min_chars=MIN_PASTED_CHARS, max_chars=MAX_PASTED_CHARS,
                      prefill=prefill)

    @app.post("/find", response_class=HTMLResponse)
    def find_listing(company: str = Form(...), title: str = Form(...),
                     city: str = Form(""), link: str = Form("")):
        """Plan 21: the listing's job on the employer's own board. Reads only;
        never requests LinkedIn or Indeed (jsa/find.py's host allowlist)."""
        from .. import find
        con = connect()
        try:
            report = find.find(con, company, title, city, link=link)
        except find.FindError as exc:
            return back_to_add(str(exc))
        finally:
            con.close()
        paste = urlencode({"company": report.company, "title": report.title,
                           "location": report.city, "link": report.link})
        return render("find", "add", title="Find a listing", report=report,
                      live=True, paste_query=paste)

    def back_to_add(msg: str) -> RedirectResponse:
        return RedirectResponse("/add?" + urlencode({"msg": msg, "bad": 1}),
                                status_code=303)

    def run_intake(action) -> RedirectResponse:
        """One path for both forms: add, commit, then the optional model check."""
        from .. import intake
        from ..config import Preferences
        prof = profile()
        if prof is None:
            return back_to_add("Your profile could not be loaded, so the job "
                               "cannot be scored.")
        con = connect()
        try:
            added = action(intake, con, Preferences.from_profile(prof))
            con.commit()
            added.enriched = intake.enrich(con, added.job_id)
            con.commit()
        except intake.IntakeError as exc:
            return back_to_add(str(exc))
        finally:
            con.close()
        parts = ["Added." if added.new else "Already in your tracker.",
                 f"Score {added.score:.2f}."]
        if added.enriched:
            parts.append(added.enriched[0].upper() + added.enriched[1:] + ".")
        parts.extend(added.warnings)
        if added.status:
            parts.append(f"You are tracking it: {added.status.replace('_', ' ')}.")
        return back_to_job(added.job_id, " ".join(parts), False)

    def import_page(msg: str = "") -> HTMLResponse:
        from .. import resume_import as ri
        return render("import", "add", title="Import your resume", msg=msg,
                      draft_name="profile/" + ri.DRAFT_NAME,
                      accept=",".join(sorted(ri.SUPPORTED)),
                      kinds=" or ".join(sorted(ri.SUPPORTED)),
                      max_mb=MAX_UPLOAD_BYTES // 1_000_000)

    @app.get("/import", response_class=HTMLResponse)
    def import_form(msg: str = ""):
        return import_page(msg)

    @app.post("/import", response_class=HTMLResponse)
    def import_resume(resume: UploadFile = File(...), replace: str = Form("")):
        """Resume -> draft profile (ADR 0022). Never writes the live profile;
        the upload lives only in a temporary directory for the import."""
        import tempfile

        from .. import config
        from .. import llm
        from .. import resume_import as ri
        from ..config import ConfigError

        name = Path(resume.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in ri.SUPPORTED:
            return import_page(f"{name or 'That file'} is not a "
                               f"{' or '.join(sorted(ri.SUPPORTED))} file. {ri.DOCX_ONLY}")
        data = resume.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return import_page("That file is too large.")
        if not data.startswith(ri.SUPPORTED[suffix]):
            # Never trust the name or the browser's content type.
            return import_page(f"{name} is named {suffix} but is not one inside. "
                               f"{ri.DOCX_ONLY}")
        live = config.PROFILE_PATH
        con = connect()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("resume" + suffix)
                path.write_bytes(data)
                report = ri.run(path, out=live.with_name(ri.DRAFT_NAME), live=live,
                                force=bool(replace), con=con, source_name=name)
        except (ConfigError, llm.LLMError) as exc:
            return import_page(str(exc))
        except Exception as exc:  # noqa: BLE001 - e.g. the identity guard
            return import_page(f"The import stopped: {exc}")
        finally:
            con.close()
        return render("imported", "add", title="Your draft profile",
                      v=report.verified, blocking=report.blocking,
                      warning=report.warning,
                      matches=report.matches, no_preview=report.no_preview,
                      draft_path=str(report.draft_path), live_path=str(live))

    @app.post("/add/link")
    def add_link(url: str = Form(...), company: str = Form("")):
        return run_intake(lambda intake, con, prefs: intake.add_link(
            con, url, prefs, company=company.strip() or None))

    @app.post("/add/paste")
    def add_paste(company: str = Form(...), title: str = Form(...),
                  text: str = Form(...), location: str = Form(""),
                  link: str = Form("")):
        return run_intake(lambda intake, con, prefs: intake.add_pasted(
            con, company=company, title=title, text=text, prefs=prefs,
            url=link, location=location))
