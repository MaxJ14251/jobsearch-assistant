"""Render a verified draft to .docx and record its provenance.

ATS-safe formatting is a functional requirement, not a style preference. An
applicant tracking system parses the file; anything it cannot read costs the
application. So: one column, no text boxes, no tables for layout, no content in
headers or footers, no images, real list paragraphs, parseable dates.

The source resume for this project was a ReportLab PDF whose text extraction
was genuinely difficult — ASCII85 over Flate, no text layer tooling available.
.docx exists so that never happens again, and `tests/test_render.py` proves the
text comes back out.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from . import db
from .config import OUTPUT_DIR
from .tailor import TailoredDraft, collect_bullets

BODY_PT = 10.5
NAME_PT = 18
HEADING_PT = 11


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "item"


def contact_line(profile: dict[str, Any]) -> str:
    """Identity, merged locally. None of this was ever sent to a model."""
    ident = profile.get("identity") or {}
    loc = ident.get("location") or {}
    links = profile.get("links") or {}
    city = ", ".join(p for p in (loc.get("city"), loc.get("state")) if p)
    parts = [city, ident.get("phone"), ident.get("email")]
    for url in (links.get("linkedin"), links.get("github")):
        if url:
            parts.append(re.sub(r"^https?://", "", url))
    return "  |  ".join(p for p in parts if p)


def _heading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(HEADING_PT)
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(2)


def _body(doc: Document, text: str, *, bullet: bool = False) -> None:
    # A real list paragraph, not a literal "•" character an ATS must guess at.
    p = doc.add_paragraph(style="List Bullet" if bullet else None)
    run = p.add_run(text)
    run.font.size = Pt(BODY_PT)
    p.paragraph_format.space_after = Pt(2)


def _dates(start: Any, end: Any, current: bool = False) -> str:
    def fmt(v):
        if v is None:
            return ""
        s = str(v)
        return s if re.match(r"^\d{4}(-\d{2})?$", s) else s
    return f"{fmt(start)} – {'Present' if current else fmt(end)}".strip(" –")


@dataclass
class RenderedDoc:
    kind: str
    path: Path
    document_id: int | None = None


def render_resume(
    draft: TailoredDraft, profile: dict[str, Any], job: dict[str, Any], out: Path
) -> Path:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(BODY_PT)

    ident = profile.get("identity") or {}
    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name.add_run(ident.get("full_name", ""))
    run.bold = True
    run.font.size = Pt(NAME_PT)
    name.paragraph_format.space_after = Pt(1)

    contact = doc.add_paragraph()
    contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
    crun = contact.add_run(contact_line(profile))
    crun.font.size = Pt(9)

    _heading(doc, "Summary")
    _body(doc, draft.summary)

    sources = collect_bullets(profile)
    by_parent: dict[str, list[str]] = {}
    for b in draft.bullets:
        src = sources.get(b.source_id)
        if src:
            by_parent.setdefault(f"{src.origin}::{src.parent}", []).append(b.text)

    def write_experience() -> None:
        written = False
        for exp in profile.get("experience") or []:
            texts = by_parent.get(
                f"experience::{exp.get('id') or exp.get('company')}")
            if not texts:
                continue
            if not written:
                _heading(doc, "Experience")
                written = True
            line = doc.add_paragraph()
            r = line.add_run(f"{exp.get('title')} — {exp.get('company')}")
            r.bold = True
            r.font.size = Pt(BODY_PT)
            meta = doc.add_paragraph()
            m = meta.add_run(
                "  |  ".join(
                    p for p in (exp.get("location"),
                                _dates(exp.get("start"), exp.get("end"),
                                       exp.get("current", False))) if p
                )
            )
            m.italic = True
            m.font.size = Pt(9)
            meta.paragraph_format.space_after = Pt(1)
            for t in texts:
                _body(doc, t, bullet=True)

    def write_projects() -> None:
        written = False
        for proj in profile.get("projects") or []:
            texts = by_parent.get(
                f"project::{proj.get('id') or proj.get('name')}")
            if not texts:
                continue
            if not written:
                _heading(doc, "Projects")
                written = True
            line = doc.add_paragraph()
            label = proj.get("name", "")
            # Status is shown, never quietly dropped to make a project look shipped.
            if proj.get("status") == "in_development":
                label += " (in development)"
            r = line.add_run(label)
            r.bold = True
            r.font.size = Pt(BODY_PT)
            # The project's public link, as plain text: a reader can type it,
            # and an ATS reads it, without a hyperlink field either has to parse.
            repo = proj.get("repo")
            if isinstance(repo, str) and repo.strip().startswith(("http://", "https://")):
                link = line.add_run(" — " + repo.strip().split("://", 1)[1].rstrip("/"))
                link.font.size = Pt(BODY_PT)
            line.paragraph_format.space_after = Pt(1)
            for t in texts:
                _body(doc, t, bullet=True)

    # Whichever section holds more of the selected bullets leads. Experience
    # used to lead unconditionally, so a resume for a robotics role opened with
    # fire-alarm installation while the relevant AI projects sat below it.
    # Experience wins ties, because for most roles and most readers that is the
    # conventional and expected order.
    counts = {"experience": 0, "project": 0}
    for key, texts in by_parent.items():
        origin = key.split("::", 1)[0]
        if origin in counts:
            counts[origin] += len(texts)
    if counts["project"] > counts["experience"]:
        write_projects()
        write_experience()
    else:
        write_experience()
        write_projects()

    skills = profile.get("skills") or {}
    if skills:
        _heading(doc, "Skills")
        for key in ("ai_tools", "technical", "applied_focus"):
            vals = skills.get(key)
            if vals:
                _body(doc, f"{key.replace('_', ' ').title()}: {', '.join(vals)}")

    certs = profile.get("certifications") or []
    if certs:
        _heading(doc, "Certifications")
        for c in certs:
            issued = c.get("issued")
            _body(doc, f"{c.get('name')} — {c.get('issuer')}"
                       + (f" ({issued})" if issued else ""))

    edu = profile.get("education") or []
    if edu:
        _heading(doc, "Education")
        for e in edu:
            field = e.get("field")
            years = _dates(e.get("start"), e.get("end"))
            # The credential string is reproduced verbatim. It says the degree
            # was not conferred, and nothing here may soften that.
            line = f"{e.get('institution')}"
            if field:
                line += f" — {field}"
            if years:
                line += f", {years}"
            _body(doc, line)
            _body(doc, e.get("credential", ""))

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


def render_cover_letter(
    draft: TailoredDraft, profile: dict[str, Any], job: dict[str, Any],
    body: str, out: Path
) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(BODY_PT)

    ident = profile.get("identity") or {}
    head = doc.add_paragraph()
    r = head.add_run(ident.get("full_name", ""))
    r.bold = True
    r.font.size = Pt(13)
    c = doc.add_paragraph().add_run(contact_line(profile))
    c.font.size = Pt(9)

    doc.add_paragraph()
    _body(doc, f"Re: {job.get('title')} at {job.get('company', '')}".strip())
    doc.add_paragraph()
    for para in [p for p in body.split("\n\n") if p.strip()]:
        _body(doc, " ".join(para.split()))
        doc.add_paragraph()
    _body(doc, ident.get("full_name", ""))

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


def extract_text(path: Path) -> str:
    """Pull the text back out — the same thing an ATS has to do.

    If this returns nothing useful, the document is unreadable to a parser no
    matter how good it looks on screen.
    """
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def record(
    con: sqlite3.Connection, *, job_id: int | None, kind: str, path: Path,
    draft: TailoredDraft, prompt_hash: str, note: str | None = None,
) -> int:
    """Insert a documents row with full provenance."""
    version = con.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM documents "
        "WHERE job_id IS ? AND kind = ?", (job_id, kind),
    ).fetchone()[0]
    cur = con.execute(
        """INSERT INTO documents
             (job_id, kind, path, format, version, bullet_ids,
              keywords_matched, keywords_missing, model, prompt_hash, note)
           VALUES (:job_id,:kind,:path,'docx',:version,:bullet_ids,
                   :matched,:missing,:model,:prompt_hash,:note)""",
        {
            "job_id": job_id, "kind": kind,
            "path": str(path), "version": version,
            "bullet_ids": json.dumps([b.source_id for b in draft.bullets]),
            "matched": json.dumps(draft.keywords_matched),
            "missing": json.dumps(draft.keywords_missing),
            "model": draft.model, "prompt_hash": prompt_hash,
            "note": note or None,
        },
    )
    return int(cur.lastrowid)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def next_version(con: sqlite3.Connection, job_id: int | None, kind: str) -> int:
    """The version a new document of this kind for this job would take."""
    return int(con.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM documents "
        "WHERE job_id IS ? AND kind = ?", (job_id, kind),
    ).fetchone()[0])


def output_path(company: str, title: str, kind: str, version: int = 1) -> Path:
    """Where a rendered document lives.

    The version is in the FILENAME, not just the documents row. Without it a
    redraft overwrites the file its predecessor's row still points at -- and
    since approval is per version (ADR 0003 decision 3), an approved document
    would silently come to mean different content than the human approved.
    """
    name = f"{kind}.docx" if version <= 1 else f"{kind}-v{version}.docx"
    return OUTPUT_DIR / slug(company) / slug(title) / name
