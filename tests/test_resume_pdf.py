"""PDF resumes for the import (Plan 10).

The fixtures are fictional and built at test time by tests/pdf_fixtures.py
(nothing binary is committed). The model is mocked.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document
from fastapi.testclient import TestClient

from jsa import db, web
from jsa import resume_import as ri
from jsa.llm import Usage
from tests.pdf_fixtures import (BULLETS, COMPANY, CONTACT, NAME, SCHOOL_LINE,
                                SKILLS, TITLE, build_all)

FIX = build_all(Path(tempfile.mkdtemp()) / "resumes")


def reply():
    """A model that copies all three bullets exactly."""
    return {
        "experience": [{"company": COMPANY, "title": TITLE, "start": "2021",
                        "end": "Present", "current": True, "family": "technical_field",
                        "bullets": [{"text": b, "tags": ["support"]} for b in BULLETS]}],
        "projects": [], "certifications": [],
        "education": [{"institution": "Lakeside Community College",
                       "field": "Business", "start": "2018", "end": "2020",
                       "line": SCHOOL_LINE}],
        "skills": ["Python", "SQL", "Zendesk"],
        "target_titles": ["Support Engineer"],
    }


def same_resume_as_docx(path: Path) -> Path:
    doc = Document()
    for line in (NAME, CONTACT, "EXPERIENCE", COMPANY, f"{TITLE} | 2021 - Present",
                 *BULLETS, "EDUCATION", SCHOOL_LINE, "SKILLS", SKILLS):
        doc.add_paragraph(line)
    doc.save(str(path))
    return path


def kept_bullets(lines):
    ident, redacted = ri.split_identity(lines)
    v = ri.verify(reply(), "\n".join(redacted))
    return [b["text"] for e in v.experience for b in e["bullets"]], v


class TestReadingPdf(unittest.TestCase):
    def test_one_column_imports_the_same_bullets_as_the_docx(self):
        pdf, _ = kept_bullets(ri.read(FIX / "one_column.pdf"))
        docx_path = same_resume_as_docx(Path(tempfile.mkdtemp()) / "same.docx")
        docx, _ = kept_bullets(ri.read(docx_path))
        self.assertEqual(pdf, BULLETS)
        self.assertEqual(pdf, docx)

    def test_a_hyphen_wrapped_bullet_is_kept(self):
        lines = ri.read(FIX / "one_column.pdf")
        self.assertIn("Coordin-", lines)  # the PDF really breaks the word
        kept, v = kept_bullets(lines)
        self.assertIn(BULLETS[1], kept)
        self.assertEqual(v.dropped, [])

    def test_a_real_hyphen_at_a_line_end_is_kept_too(self):
        text = "Resolved tickets for a 40-\nperson team."
        v = ri.verify({"skills": ["Resolved tickets for a 40-person team."]}, text)
        self.assertEqual(v.skills, ["Resolved tickets for a 40-person team."])

    def test_ascii85_over_flate_extracts(self):
        self.assertEqual(ri.read(FIX / "ascii85_flate.pdf"),
                         ri.read(FIX / "one_column.pdf"))

    def test_ligatures_and_pdf_bullets_normalize(self):
        self.assertEqual(ri._pdf_line("\x7f Wrote and ﬁled reports"),
                         "Wrote and filed reports")
        self.assertEqual(ri._pdf_line("  Built a  ﬂow"), "Built a flow")

    def test_contact_details_in_the_header_area_are_removed(self):
        ident, redacted = ri.split_identity(ri.read(FIX / "one_column.pdf"))
        self.assertEqual(ident.full_name, NAME)
        self.assertEqual(ident.email, "jordan.quill@example.com")
        self.assertEqual(ident.phone, "(555) 010-2244")
        self.assertEqual(ident.postal_code, "97024")
        text = "\n".join(redacted)
        for value in (NAME, "jordan.quill@example.com", "010-2244", "97024",
                      "jordan-quill-example"):
            self.assertNotIn(value, text)


class TestRefusals(unittest.TestCase):
    def refused(self, path, words):
        with self.assertRaises(ri.ResumeReadError) as ctx:
            ri.read(path)
        for w in words:
            self.assertIn(w, str(ctx.exception))

    def test_a_scan_is_refused(self):
        self.refused(FIX / "scan.pdf", ["looks like a scan", ".docx"])

    def test_an_encrypted_file_is_refused(self):
        self.refused(FIX / "encrypted.pdf", ["password"])

    def test_eleven_pages_are_refused(self):
        self.refused(FIX / "eleven_pages.pdf", ["11 pages", "right file"])

    def test_an_oversized_file_is_refused_before_parsing(self):
        with mock.patch("jsa.resume_import.MAX_PDF_BYTES", 100), \
             mock.patch("pypdf.PdfReader") as reader:
            self.refused(FIX / "one_column.pdf", ["over"])
        reader.assert_not_called()

    def test_a_pdf_name_on_other_bytes_is_refused(self):
        fake = Path(tempfile.mkdtemp()) / "resume.pdf"
        fake.write_bytes(b"PK\x03\x04 a zip, not a PDF")
        self.refused(fake, ["is not one inside"])


class TestTwoColumns(unittest.TestCase):
    """A layout the reader can't untangle loses bullets; it never garbles one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patch = mock.patch("jsa.resume_import.llm.complete_json",
                           return_value=(reply(), Usage()))
        patch.start()
        self.addCleanup(patch.stop)

    def run_import(self, name):
        return ri.run(FIX / name, out=self.tmp / f"{name}.draft.yaml",
                      live=self.tmp / "master_profile.yaml")

    def test_two_columns_drop_rather_than_garble_and_warn(self):
        report = self.run_import("two_column.pdf")
        kept = [b["text"] for e in report.verified.experience for b in e["bullets"]]
        self.assertTrue(set(kept) <= set(BULLETS))
        self.assertLess(len(kept), len(BULLETS))
        self.assertGreater(report.verified.unmatched_share, ri.WEAK_PDF_SHARE)
        self.assertEqual(report.warning, ri.WEAK_PDF)

    def test_a_clean_pdf_does_not_warn(self):
        self.assertEqual(self.run_import("one_column.pdf").warning, "")


class TestDashboardPdf(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        (self.tmp / "profile").mkdir()
        self.live = self.tmp / "profile" / "master_profile.yaml"
        for patch in (mock.patch("jsa.config.PROFILE_PATH", self.live),
                      mock.patch("jsa.resume_import.llm.complete_json",
                                 return_value=(reply(), Usage()))):
            patch.start()
            self.addCleanup(patch.stop)
        self.app = web.create_app(db_path=self.dbfile, profile_loader=lambda: {})
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")
        self.addCleanup(self.client.close)

    def upload(self, data):
        return self.client.post(
            "/import", data={"csrf": self.app.state.csrf_token},
            files={"resume": ("resume.pdf", data, "application/pdf")})

    def test_a_pdf_upload_is_accepted(self):
        r = self.upload((FIX / "one_column.pdf").read_bytes())
        self.assertIn("Your draft profile", r.text)
        self.assertTrue((self.tmp / "profile" / ri.DRAFT_NAME).exists())

    def test_a_pdf_name_on_other_bytes_is_refused(self):
        r = self.upload(b"not a pdf at all")
        self.assertIn("is not one inside", r.text)


if __name__ == "__main__":
    unittest.main()
