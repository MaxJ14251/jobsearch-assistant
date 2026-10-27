"""Wording and structure found on the 2026-10-27 dashboard review: counts
read as English ("1 match", not "1 match(es)"), "asks for 1 year", and a
main landmark so a screen reader can skip the nav."""

import re
import unittest

from jsa.scoring import score_job
from jsa.web import plural
from tests.test_scoring import PREFS, job
from tests.test_web_documents import Sandbox, make_client


class TestPlural(unittest.TestCase):
    def test_counts_read_as_english(self):
        self.assertEqual(plural(1, "match", "matches"), "1 match")
        self.assertEqual(plural(0, "match", "matches"), "0 matches")
        self.assertEqual(plural(2, "reply", "replies"), "2 replies")
        self.assertEqual(plural(3, "draft"), "3 drafts")


class TestYearsReason(unittest.TestCase):
    def test_one_year_is_singular(self):
        _, reasons = score_job(job(title="Support Engineer",
                                   description="Requires 1 year of experience."), PREFS)
        self.assertIn("asks for 1 year", reasons)

    def test_more_years_stay_plural(self):
        _, reasons = score_job(job(title="Support Engineer",
                                   description="Requires 2 years of experience."), PREFS)
        self.assertIn("asks for 2 years", " ".join(reasons))


class TestPages(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.client, _ = make_client(self.box.db, self.box.out, profile={"x": 1})

    def test_every_page_has_one_main_landmark(self):
        for url in ("/", "/pipeline", "/replies", "/review", "/job/1", "/add"):
            with self.subTest(url=url):
                page = self.client.get(url).text
                self.assertEqual(len(re.findall(r"<main\b", page)), 1)

    def test_no_page_says_s_in_brackets(self):
        for url in ("/", "/pipeline", "/replies", "/review", "/job/1"):
            with self.subTest(url=url):
                text = re.sub(r"<script.*?</script>", "", self.client.get(url).text, flags=re.S)
                self.assertIsNone(re.search(r"[a-z]\((s|es|ies)\)", text))


if __name__ == "__main__":
    unittest.main()
