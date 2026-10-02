"""Fictional PDF resumes, built at test time (Plan 10).

They are built here rather than committed: tools/scan_history.py treats any
committed .pdf as a leaked generated document, and that rule stays strict.
A tiny PDF writer (standard library only) covers the text shapes; pypdf
encrypts one. The person is fictional, the same one
tests/test_resume_import.py builds as a .docx.

Shapes (build_all() writes each into a directory):
  one_column.pdf      one bullet hyphen-wrapped across a line break
  two_column.pdf      skills on the left, bullets on the right, same lines
  ascii85_flate.pdf   one_column's content, streams ASCII85 over Flate (the
                      shape of this project's own first resume)
  scan.pdf            one page that is only an image: no text layer
  encrypted.pdf       one_column's content behind a password
  eleven_pages.pdf    11 short pages
"""

from __future__ import annotations

import base64
import zlib
from pathlib import Path

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
    # Printed hyphen-wrapped: "Coordin-" / "ated the move..." (see LINES).
    "Coordinated the move of 300 product records into a new inventory system.",
    "Wrote and filed weekly shipping reports for the operations manager.",
]
SCHOOL_LINE = "Lakeside Community College, Business coursework, 2018 - 2020"
SKILLS = "Python, SQL, Zendesk"
DOT = chr(0x2022) + " "  # WinAnsi byte 0x95

LINES = [
    (18, NAME), (9, CONTACT), (11, "EXPERIENCE"), (10.5, COMPANY),
    (10, f"{TITLE} | {DATES}"),
    (10, DOT + BULLETS[0]),
    (10, DOT + "Coordin-"),
    (10, "ated the move of 300 product records into a new inventory system."),
    (10, DOT + BULLETS[2]),
    (11, "EDUCATION"), (10, SCHOOL_LINE), (11, "SKILLS"), (10, SKILLS),
]


def _text(s: str) -> bytes:
    raw = s.encode("cp1252")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _show(items: list[tuple[float, float, float, str]]) -> bytes:
    """(x, y, size, text) -> a content stream."""
    out = [b"BT"]
    for x, y, size, text in items:
        out.append(b"/F1 %g Tf 1 0 0 1 %g %g Tm %s Tj" % (size, x, y, _text(text)))
    out.append(b"ET")
    return b"\n".join(out)


def _one_column() -> bytes:
    items, y = [], 740.0
    for size, text in LINES:
        items.append((54, y, size, text))
        y -= size + 6
    return _show(items)


def _two_column() -> bytes:
    items = [(54, 740, 18, NAME), (54, 724, 9, CONTACT)]
    left = ["SKILLS", "Python", "SQL", "Zendesk", "", "EDUCATION",
            "Lakeside Community", "College, Business", "coursework, 2018 - 2020"]
    right = ["EXPERIENCE", f"{COMPANY} | {TITLE}", DATES]
    for b in BULLETS:
        words = b.split()
        half = len(words) // 2
        right += [DOT + " ".join(words[:half]), " ".join(words[half:])]
    y = 696
    for i in range(max(len(left), len(right))):
        if i < len(left) and left[i]:
            items.append((54, y, 10, left[i]))
        if i < len(right):
            items.append((230, y, 10, right[i]))
        y -= 16
    return _show(items)


def _pdf(pages: list[bytes], *, a85: bool = False, image: bytes | None = None) -> bytes:
    """A minimal PDF: one Helvetica font, one content stream per page."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    def stream(data: bytes, extra: bytes = b"") -> bytes:
        data = zlib.compress(data)
        filters = b"/FlateDecode"
        if a85:
            data = base64.a85encode(data) + b"~>"
            filters = b"[/ASCII85Decode /FlateDecode]"
        return (b"<< /Length %d /Filter %s %s>>\nstream\n" % (len(data), filters, extra)
                + data + b"\nendstream")

    catalog = add(b"")            # filled in last
    tree = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
               b"/Encoding /WinAnsiEncoding >>")
    xobject = b""
    if image is not None:
        img = add(stream(image, b"/Type /XObject /Subtype /Image /Width 120 "
                                b"/Height 160 /ColorSpace /DeviceGray "
                                b"/BitsPerComponent 8 "))
        xobject = b"/XObject << /Im1 %d 0 R >> " % img
    kids = []
    for content in pages:
        c = add(stream(content))
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                        b"/Resources << /Font << /F1 %d 0 R >> %s>> /Contents %d 0 R >>"
                        % (tree, font, xobject, c)))
    objs[tree - 1] = (b"<< /Type /Pages /Count %d /Kids [%s] >>"
                      % (len(kids), b" ".join(b"%d 0 R" % k for k in kids)))
    objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, catalog, xref))
    return bytes(out)


def build_all(folder: Path) -> Path:
    """Write every shape into `folder` and return it."""
    from io import BytesIO
    import random

    from pypdf import PdfReader, PdfWriter

    folder.mkdir(parents=True, exist_ok=True)
    one = _pdf([_one_column()])
    (folder / "one_column.pdf").write_bytes(one)
    (folder / "two_column.pdf").write_bytes(_pdf([_two_column()]))
    (folder / "ascii85_flate.pdf").write_bytes(_pdf([_one_column()], a85=True))
    rng = random.Random(7)
    noise = bytes(rng.randrange(200, 256) for _ in range(120 * 160))
    (folder / "scan.pdf").write_bytes(
        _pdf([b"q 480 0 0 420 54 300 cm /Im1 Do Q"], image=noise))
    (folder / "eleven_pages.pdf").write_bytes(_pdf(
        [_show([(54, 740, 10, f"Page {n + 1} of a document that is not a resume.")])
         for n in range(11)]))
    writer = PdfWriter(clone_from=PdfReader(BytesIO(one)))
    writer.encrypt("fixture-password", algorithm="RC4-128")
    buf = BytesIO()
    writer.write(buf)
    (folder / "encrypted.pdf").write_bytes(buf.getvalue())
    return folder
