"""What the dashboard needs to show a generated document honestly.

Three jobs, all read-only:

- `safe_document_path` decides whether a path read from the database may be
  served at all. The database is local, but a path in it is still input.
- `readable` turns a .docx back into headings, paragraphs and bullets.
- `compare` puts every bullet beside the profile bullet it came from and names
  any word nothing in the profile supports -- the check ADR 0005 added to
  verify_draft, re-run on what was actually written. Documents drafted before
  that check existed carry claims it would have reverted; this is where a
  reviewer sees them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document

from .config import HOME

SERVABLE_SUFFIXES = frozenset({".docx"})
HEADINGS = frozenset({"SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS",
                      "CERTIFICATIONS", "EDUCATION"})


def safe_document_path(stored: str | None, output_dir: Path) -> Path | None:
    """The resolved file if it may be served, else None.

    Resolving first means `output/../.env` and symlinks are judged by where
    they actually lead. Containment is checked on path components, not string
    prefixes, so `output-private/` is not mistaken for `output/`.
    """
    if not stored:
        return None
    path = Path(stored)
    if not path.is_absolute():
        # documents.path is relative to HOME: the clone, for every tracker
        # made before plan 27, so stored rows keep working.
        path = HOME / path
    try:
        real = path.resolve(strict=True)
        base = Path(output_dir).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not real.is_relative_to(base) or real == base:
        return None
    if real.suffix.lower() not in SERVABLE_SUFFIXES or not real.is_file():
        return None
    return real


def readable(path: Path) -> list[dict[str, str]]:
    """Paragraphs as {'kind': heading|bullet|text, 'text': ...}."""
    out = []
    for para in Document(str(path)).paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if para.style is not None and para.style.name == "List Bullet":
            kind = "bullet"
        elif text in HEADINGS:
            kind = "heading"
        else:
            kind = "text"
        out.append({"kind": kind, "text": text})
    return out


_ALIGN = {0: "left", 1: "center", 2: "right", 3: "justify"}


def _pt(length: Any) -> float | None:
    return round(length.pt, 1) if length is not None else None


def layout(path: Path) -> dict[str, Any]:
    """The document as it would look on a page, read from the .docx itself.

    For the dashboard preview. Only what the file says: alignment, bold,
    italic, size, bullets, spacing, the page's margins. Anything it does not
    say falls back to the Normal style, then to Word's own defaults. It returns
    data, not HTML; the template escapes every word of it.
    """
    doc = Document(str(path))
    normal = doc.styles["Normal"]
    base_size = _pt(normal.font.size) or 11.0
    font = normal.font.name or "Calibri"

    section = doc.sections[0] if doc.sections else None
    width = section.page_width.inches if section is not None and section.page_width else 8.5
    left = section.left_margin.inches if section is not None and section.left_margin is not None else 1.0
    right = section.right_margin.inches if section is not None and section.right_margin is not None else 1.0

    paragraphs = []
    for para in doc.paragraphs:
        style = para.style
        fmt, style_fmt = para.paragraph_format, style.paragraph_format if style else None
        align = para.alignment if para.alignment is not None else (
            style_fmt.alignment if style_fmt is not None else None)
        runs = []
        for run in para.runs:
            if not run.text:
                continue
            runs.append({
                "text": run.text,
                "bold": bool(run.bold if run.bold is not None
                             else (style.font.bold if style else False)),
                "italic": bool(run.italic if run.italic is not None
                               else (style.font.italic if style else False)),
                "size": _pt(run.font.size) or (_pt(style.font.size) if style else None)
                        or base_size,
            })
        before = _pt(fmt.space_before)
        after = _pt(fmt.space_after)
        if before is None and style_fmt is not None:
            before = _pt(style_fmt.space_before)
        if after is None and style_fmt is not None:
            after = _pt(style_fmt.space_after)
        paragraphs.append({
            "runs": runs,
            "align": _ALIGN.get(int(align) if align is not None else 0, "left"),
            "bullet": bool(style is not None and style.name.startswith("List Bullet")),
            "before": before or 0.0,
            "after": after if after is not None else 0.0,
            # An empty paragraph is a blank line on the page, and a letter is
            # laid out with them. It keeps its height.
            "empty": not runs,
        })
    return {"paragraphs": paragraphs, "font": font, "size": base_size,
            "margin_left_pct": round(100 * left / width, 2),
            "margin_right_pct": round(100 * right / width, 2)}


def _as_written(text: str, stems: list[str]) -> list[str]:
    """The draft's own words for the stems the verifier flagged.

    The verifier compares crude stems ("pressur", "enabl"); a reader should
    see "pressure" and "enabled".
    """
    from .tailor import _stem, _words
    wanted = set(stems)
    return sorted({w for w in _words(text) if _stem(w) in wanted})


def compare(paragraphs: list[dict[str, str]], bullet_ids: list[str],
            profile: dict[str, Any]) -> dict[str, Any]:
    """Pair each written bullet with its source and report what it added.

    The document does not record which paragraph came from which id, so each
    written bullet is paired with the listed source it overlaps most -- the
    same overlap measure the verifier uses.
    """
    from .tailor import (
        INVENTION_CEILING, _supported_stems, _whole_profile_stems,
        added_share, claims_completion, collect_bullets, source_overlap,
    )

    sources = collect_bullets(profile)
    supported = _supported_stems(profile)
    written = [p["text"] for p in paragraphs if p["kind"] == "bullet"]
    listed = [i for i in dict.fromkeys(bullet_ids) if i in sources]

    # Best pairs first, each side used once. Pairing in page order mismatched
    # a broken document where one bullet had been printed twice.
    pairs = sorted(((source_overlap(t, sources[i].text), n, i)
                    for n, t in enumerate(written) for i in listed),
                   key=lambda x: (-x[0], x[1], x[2]))
    paired: dict[int, str] = {}
    for overlap, n, source_id in pairs:
        if overlap <= 0 or n in paired or source_id in paired.values():
            continue
        paired[n] = source_id

    rows = []
    for n, text in enumerate(written):
        source_id = paired.get(n)
        if source_id is None:
            rows.append({"text": text, "source_id": None, "source": None,
                         "status": "unsourced", "added": [],
                         "note": "no listed profile bullet matches this line"})
            continue
        source = sources[source_id]
        share, new = added_share(
            text, supported.get((source.origin, source.parent), set()))
        if " ".join(text.split()) == source.text:
            status, note = "verbatim", "your words, unchanged"
        elif claims_completion(source.text, text):
            status, note = "flag", "describes ongoing work as finished"
        elif share > INVENTION_CEILING:
            status, note = "flag", "adds words your profile does not support"
        else:
            status, note = "reworded", "reworded from your words"
        rows.append({"text": text, "source_id": source_id, "source": source.text,
                     "status": status, "added": _as_written(text, new),
                     "note": note})

    remaining = [i for i in bullet_ids if i not in paired.values()]
    missing = [i for i in remaining if i not in sources]
    summary = None
    heads = [i for i, p in enumerate(paragraphs) if p["text"] == "SUMMARY"]
    if heads and heads[0] + 1 < len(paragraphs):
        text = paragraphs[heads[0] + 1]["text"]
        share, new = added_share(text, _whole_profile_stems(profile))
        summary = {"text": text, "added": _as_written(text, new),
                   "status": "flag" if share > INVENTION_CEILING else "ok"}
    return {"bullets": rows, "summary": summary, "missing_sources": missing,
            "flagged": sum(r["status"] in ("flag", "unsourced") for r in rows)
                       + (1 if summary and summary["status"] == "flag" else 0)}
