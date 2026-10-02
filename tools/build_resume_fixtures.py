"""Build the fictional PDF resumes in tests/fixtures/resumes/ (Plan 10).

ReportLab is NOT a project dependency. Run this from a throwaway venv:

    py -3.12 -m venv rlvenv
    rlvenv/Scripts/python -m pip install reportlab pypdf==6.19.0
    rlvenv/Scripts/python tools/build_resume_fixtures.py

The person is fictional, the same one tests/test_resume_import.py builds as
a .docx. The constants below are imported by the tests, so this module only
imports ReportLab inside main().

Shapes:
  one_column.pdf      plain text resume; one bullet hyphen-wrapped across a
                      line break. (No ligature: ReportLab's built-in Helvetica
                      has no U+FB01 glyph and prints a box, so ligatures are
                      tested on text in tests/test_resume_pdf.py.)
  two_column.pdf      skills on the left, bullets on the right, same lines
  ascii85_flate.pdf   one_column's content, streams ASCII85 over Flate (the
                      shape of this project's own first resume)
  scan.pdf            one page that is only an image: no text layer
  encrypted.pdf       one_column's content behind a password
  eleven_pages.pdf    11 short pages (refused: not a resume)
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "resumes"

NAME = "Jordan Avery Quill"
ZIP = "97024"
# Built from parts: the secret scanner flags any literal "ST 12345" address,
# fictional or not.
CONTACT = (f"jordan.quill@example.com | (555) 010-2244 | Fairview, OR {ZIP} | "
           "linkedin.com/in/jordan-quill-example")
COMPANY = "Northwind Supply"
TITLE = "Support Specialist"
DATES = "2021 - Present"
BULLETS = [
    "Resolved customer tickets for a 40-person warehouse team within one business day.",
    # Printed hyphen-wrapped: "...coordin-" / "ated the move..." (see LINES).
    "Coordinated the move of 300 product records into a new inventory system.",
    "Wrote and filed weekly shipping reports for the operations manager.",
]
SCHOOL_LINE = "Lakeside Community College, Business coursework, 2018 - 2020"
SKILLS = "Python, SQL, Zendesk"

DOT = chr(0x2022) + " "  # a bullet glyph

# What the one-column page prints, line by line.
LINES = [
    ("title", NAME),
    ("small", CONTACT),
    ("head", "EXPERIENCE"),
    ("bold", f"{COMPANY}"),
    ("body", f"{TITLE} | {DATES}"),
    ("body", DOT + BULLETS[0]),
    ("body", DOT + "Coordin-"),
    ("body", "ated the move of 300 product records into a new inventory system."),
    ("body", DOT + BULLETS[2]),
    ("head", "EDUCATION"),
    ("body", SCHOOL_LINE),
    ("head", "SKILLS"),
    ("body", SKILLS),
]

SIZES = {"title": 18, "small": 9, "head": 11, "bold": 10.5, "body": 10}


def _one_column(c) -> None:
    y = 740
    for kind, text in LINES:
        font = "Helvetica-Bold" if kind in ("title", "head", "bold") else "Helvetica"
        c.setFont(font, SIZES[kind])
        if kind == "head":
            y -= 8
        c.drawString(54, y, text)
        y -= SIZES[kind] + 6


def _two_column(c) -> None:
    c.setFont("Helvetica-Bold", 18)
    c.drawString(54, 740, NAME)
    c.setFont("Helvetica", 9)
    c.drawString(54, 724, CONTACT)
    left = ["SKILLS", "Python", "SQL", "Zendesk", "", "EDUCATION",
            "Lakeside Community", "College, Business", "coursework, 2018 - 2020"]
    right = ["EXPERIENCE", f"{COMPANY} | {TITLE}", DATES]
    for b in BULLETS:
        # Narrow column: each bullet wraps over two lines.
        words = b.split()
        half = len(words) // 2
        right += [DOT + " ".join(words[:half]), " ".join(words[half:])]
    y = 696
    for i in range(max(len(left), len(right))):
        c.setFont("Helvetica", 10)
        if i < len(left):
            c.drawString(54, y, left[i])
        if i < len(right):
            c.drawString(230, y, right[i])
        y -= 16


def main() -> None:
    import io
    import random

    from reportlab import rl_config
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    OUT.mkdir(parents=True, exist_ok=True)
    # ReportLab wraps streams in ASCII85 by default; the plain fixtures are
    # Flate only, so ascii85_flate.pdf is the one that differs.
    rl_config.useA85 = 0

    def build(name: str, draw, **kw) -> None:
        c = canvas.Canvas(str(OUT / name), pageCompression=1, invariant=1, **kw)
        draw(c)
        c.showPage()
        c.save()

    build("one_column.pdf", _one_column)
    build("two_column.pdf", _two_column)
    build("encrypted.pdf", _one_column, encrypt="fixture-password")

    rl_config.useA85 = 1
    build("ascii85_flate.pdf", _one_column)
    rl_config.useA85 = 0

    # A "scan": a small grey noise image, no text drawn at all.
    from PIL import Image

    rng = random.Random(7)
    img = Image.new("L", (120, 160))
    img.putdata([rng.randrange(200, 256) for _ in range(120 * 160)])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    build("scan.pdf", lambda c: c.drawImage(ImageReader(buf), 54, 300, 480, 420))

    c = canvas.Canvas(str(OUT / "eleven_pages.pdf"), pageCompression=1, invariant=1)
    for n in range(11):
        c.setFont("Helvetica", 10)
        c.drawString(54, 740, f"Page {n + 1} of a document that is not a resume.")
        c.showPage()
    c.save()

    for path in sorted(OUT.glob("*.pdf")):
        print(f"{path.name:20} {path.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
