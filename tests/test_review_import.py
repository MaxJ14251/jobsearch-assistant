"""Resume import fixes from the October review.

R-10: names the old ASCII-only pattern missed went to the model, and the
check after it was built from the same pattern. R-19: a small .docx that
unzips to hundreds of MB. R-20: a few-KB PDF whose page content expands to
megabytes, which text extraction then reads twice. All names are fictional.
"""

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from jsa import resume_import as ri
from jsa.llm import Usage
from tests import pdf_fixtures
from tests.test_resume_import import model_reply


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)


class TestNames(Tmp):
    def test_names_the_old_pattern_missed_are_redacted(self):
        for line, name in (("José García", "José García"),
                           ("Rowan Ellery, MBA", "Rowan Ellery"),
                           ("Rowan Ellery | Data Analyst", "Rowan Ellery"),
                           ("Ludo van Aster", "Ludo van Aster")):
            with self.subTest(line=line):
                ident, redacted = ri.split_identity(
                    [line, "rowan@example.test", "EXPERIENCE", "Northwind Supply"])
                self.assertEqual(ident.full_name, name)
                self.assertNotIn(name.lower(), "\n".join(redacted).lower())

    def test_a_section_heading_or_contact_line_is_not_a_name(self):
        for line in ("EXPERIENCE", "rowan@example.test", "Fairview, OR", "Rowan"):
            self.assertIsNone(ri.name_on(line), line)

    def test_a_name_on_file_is_redacted_even_when_no_pattern_finds_it(self):
        lines = ["rowan ellery-quint", "rowan@example.test", "EXPERIENCE",
                 "Worked with rowan ellery-quint's team lead."]
        ident, redacted = ri.split_identity(lines, known=["Rowan Ellery-Quint"])
        self.assertNotIn("ellery-quint", "\n".join(redacted).lower())

    def test_run_checks_the_prompt_against_the_profile_on_file(self):
        from docx import Document
        resume = self.dir / "resume.docx"
        doc = Document()
        for text in ("rowan ellery-quint", "rowan@example.test", "EXPERIENCE",
                     "Northwind Supply", "Support Specialist 2021 - Present",
                     "Resolved customer tickets for a 40-person warehouse team "
                     "within one business day."):
            doc.add_paragraph(text)
        doc.save(resume)
        live = self.dir / "master_profile.yaml"
        live.write_text("identity: {full_name: Rowan Ellery-Quint}\n", encoding="utf-8")
        seen = {}

        def capture(prompt, **kw):
            seen["prompt"] = prompt
            return model_reply(), Usage()
        with mock.patch("jsa.resume_import.llm.complete_json", side_effect=capture):
            ri.run(resume, out=self.dir / "master_profile.draft.yaml", live=live)
        self.assertNotIn("ellery-quint", seen["prompt"].lower())


class TestHostileFiles(Tmp):
    def test_a_docx_that_unzips_too_large_is_refused_before_reading(self):
        bomb = self.dir / "resume.docx"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", b"\0" * (ri.MAX_DOCX_UNZIPPED + 1))
        self.assertLess(bomb.stat().st_size, 1_000_000)
        with mock.patch("docx.Document") as document:
            with self.assertRaises(ri.ResumeReadError) as ctx:
                ri.read_docx(bomb)
        self.assertIn("unpacks to", str(ctx.exception))
        document.assert_not_called()

    def test_a_pdf_with_megabytes_of_page_content_is_refused_before_extraction(self):
        heavy = self.dir / "resume.pdf"
        body = b"BT /F1 10 Tf 72 720 Td (x) Tj ET\n" * (ri.MAX_PDF_CONTENT // 30)
        heavy.write_bytes(pdf_fixtures._pdf([body]))
        self.assertLess(heavy.stat().st_size, 200_000)
        with mock.patch("pypdf.PageObject.extract_text") as extract:
            with self.assertRaises(ri.ResumeReadError) as ctx:
                ri.read_pdf(heavy)
        self.assertIn("page content", str(ctx.exception))
        extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
