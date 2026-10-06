"""Drafted documents: download and preview.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

from fastapi.responses import FileResponse, HTMLResponse

from .. import review
from . import DOCX_TYPE


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    document_status = ctx.document_status
    not_found = ctx.not_found
    out_dir = ctx.out_dir
    render = ctx.render

    @app.get("/document/{document_id}")
    def download(document_id: int):
        con = connect()
        try:
            row = con.execute("SELECT path FROM documents WHERE id = ?",
                              (document_id,)).fetchone()
        finally:
            con.close()
        path = review.safe_document_path(row["path"], out_dir()) if row else None
        if path is None:
            # One answer for "no such row" and "the row points somewhere it
            # may not": the difference is not the requester's business.
            return not_found("document")
        return FileResponse(path, filename=path.name, media_type=DOCX_TYPE,
                            headers={"Cache-Control": "no-store"})

    @app.get("/document/{document_id}/preview", response_class=HTMLResponse)
    def preview(document_id: int):
        con = connect()
        try:
            row = con.execute(
                "SELECT d.*, j.title AS job_title, c.name AS company FROM documents d "
                "LEFT JOIN jobs j ON j.id = d.job_id "
                "LEFT JOIN companies c ON c.id = j.company_id WHERE d.id = ?",
                (document_id,)).fetchone()
            status = document_status(con, document_id)[0] if row else ""
        finally:
            con.close()
        path = review.safe_document_path(row["path"], out_dir()) if row else None
        if path is None:
            # The same single answer the download gives.
            return not_found("document")
        sheet, problem = None, None
        try:
            sheet = review.layout(path)
        except Exception:  # noqa: BLE001 - a corrupt file is reported, not raised
            problem = "This file could not be read as a Word document."
        return render("preview", "matches",
                      title=f"{row['kind'].replace('_', ' ').capitalize()} v{row['version']}",
                      doc=dict(row), sheet=sheet, problem=problem, status=status,
                      job_title=row["job_title"] or "job", company=row["company"] or "")
