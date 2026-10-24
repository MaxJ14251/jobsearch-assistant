"""The degree flag a person sees comes from the posting's text (ADR 0032).

The model's degree_required said "asks for a degree" for 209 open postings
that accept experience instead (measured 2026-10-23). Cards, the job page,
`jsa matches`, the Matches filter and the message after adding a job now read
jobs.degree_level; degree_required stays stored but is shown nowhere.
"""

import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import db, degree
from tests.test_companies import TEXTS, Tracker

ROOT = Path(__file__).resolve().parents[1]


class TestTheFlag(unittest.TestCase):
    def test_every_level_has_a_flag_and_only_required_ones_are_not_ok(self):
        self.assertEqual(set(degree.FLAGS), set(degree.LEVELS))
        for level, (text, kind) in degree.FLAGS.items():
            with self.subTest(level=level):
                self.assertTrue(text)
                self.assertEqual(kind == "", level in degree.REQUIRED_LEVELS)
        self.assertIsNone(degree.flag(None))
        self.assertEqual(set(degree.REQUIRED_LEVELS) | set(degree.OPEN_LEVELS),
                         set(degree.LEVELS))


class TestTheEvidence(unittest.TestCase):
    def test_an_alternative_in_its_own_clause_still_shows_the_degree_line(self):
        facts = degree.classify(
            "Bachelor's degree in computer science, engineering or math;\n"
            "OR 2+ years of professional experience building software in lieu of a degree")
        self.assertEqual(facts.level, "bachelors_or_equiv")
        self.assertIn("Bachelor's degree in computer science", facts.evidence)


class Disagreeing(Tracker):
    """A posting whose two readings disagree: experience accepted, but the
    model said a degree is required."""

    def setUp(self):
        super().setUp()
        self.open_id = self.job(1, TEXTS["bachelors_or_equiv"])
        self.closed_id = self.job(1, TEXTS["bachelors"])
        self.con.execute("UPDATE jobs SET match_score = 0.9, degree_required = 1")
        self.con.commit()

    def client(self):
        from tests.test_web_documents import make_client
        client, _ = make_client(self.db, self.dir / "out")
        return client


class TestTheDashboard(Disagreeing):
    def test_the_card_and_the_job_page_read_the_posting(self):
        page = self.client().get("/?anywhere=1").text
        self.assertIn("degree or equivalent experience", page)
        self.assertIn("asks for a bachelor&#39;s", page)
        self.assertNotIn("asks for a degree", page)
        job = self.client().get(f"/job/{self.open_id}").text
        self.assertIn("degree or equivalent experience", job)
        self.assertNotIn("asks for a degree", job)

    def test_the_filter_reads_the_posting_and_old_links_still_work(self):
        required = self.client().get("/?degree=yes&anywhere=1").text
        self.assertIn(f"/job/{self.closed_id}\"", required)
        self.assertNotIn(f"/job/{self.open_id}\"", required)
        for value in ("open", "no"):        # "no" was "Not required"
            with self.subTest(degree=value):
                page = self.client().get(f"/?degree={value}&anywhere=1").text
                self.assertIn(f"/job/{self.open_id}\"", page)
                self.assertNotIn(f"/job/{self.closed_id}\"", page)


class TestTheCommandLine(Disagreeing):
    def test_jsa_matches_prints_the_postings_reading(self):
        from jsa import cli
        real_connect = db.connect
        out = io.StringIO()
        with mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real_connect(self.db)), \
                redirect_stdout(out):
            cli.main(["matches", "--limit", "5"])
        text = out.getvalue()
        self.assertIn("degree or equivalent experience", text)
        self.assertNotIn("asks for a degree", text)

    def test_the_message_after_adding_a_job_reads_the_posting(self):
        from jsa import intake
        result = mock.Mock(degree_required=True, clearance_required=False, years_required=None)
        with mock.patch("jsa.llm.api_key", return_value="k"), \
                mock.patch("jsa.enrich.enrich_one", return_value=result), \
                mock.patch("jsa.enrich.save"):
            said = intake.enrich(self.con, self.open_id)
        self.assertNotIn("asks for", said)      # the model said yes; the posting says no
        with mock.patch("jsa.llm.api_key", return_value="k"), \
                mock.patch("jsa.enrich.enrich_one", return_value=result), \
                mock.patch("jsa.enrich.save"):
            said = intake.enrich(self.con, self.closed_id)
        self.assertIn("asks for a bachelor's", said)


class TestNothingShowsTheModelsFlag(unittest.TestCase):
    def test_no_template_and_no_printed_flag_reads_degree_required(self):
        for path in sorted((ROOT / "jsa" / "web" / "templates").glob("*.html")):
            with self.subTest(template=path.name):
                self.assertNotIn("degree_required", path.read_text(encoding="utf-8"))
        cli = (ROOT / "jsa" / "cli.py").read_text(encoding="utf-8")
        self.assertNotRegex(cli, r'row\["degree_required"\]')
        intake = (ROOT / "jsa" / "intake.py").read_text(encoding="utf-8")
        self.assertNotIn("result.degree_required", intake)
        web = (ROOT / "jsa" / "web" / "__init__.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"where\.append\([^)]*degree_required", web))


if __name__ == "__main__":
    unittest.main()
