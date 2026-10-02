"""Workday: no detail request for a posting whose title alone scores 0 (Plan 3).

A first discovery spent 784 of 868 seconds on Workday, one detail request per
listing, and 283 of 682 of those requests on the four slowest tenants were for
titles score_job() rejects whatever the description says. These tests run
the fetcher against a fake board and count the requests.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import db, discover, sources
from jsa.config import Preferences
from jsa.scoring import score_job, title_rejection


def prefs() -> Preferences:
    return Preferences.from_profile({"job_search_preferences": {
        "target_titles": ["Software Engineer"],
        "locations": ["Remote (US)"],
        "max_years_experience": 3,
        "compensation_floor_usd": "no_floor",
        "exclude_keywords": ["Intern"],
    }, "ats_keywords": {"have": ["Python"]}})


TITLES = [
    "Software Engineer",                 # matches
    "Data Analyst",                      # matches nothing, NOT a lost cause
    "Senior Software Engineer",          # senior title: rejected
    "Manager, Software Engineering",     # people manager: rejected
    "Software Engineering Intern",       # exclude keyword: rejected
]


class FakeBoard:
    """Lists TITLES; records every detail request."""

    def __init__(self, titles=TITLES):
        self.titles = titles
        self.details: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        batch = [{"title": t, "externalPath": f"/job/x/R{i}",
                  "bulletFields": [f"R{i}"], "locationsText": "Remote (US)"}
                 for i, t in enumerate(self.titles)]
        return mock.Mock(status_code=200, raise_for_status=lambda: None,
                         json=lambda: {"total": len(batch), "jobPostings": batch})

    def get(self, url):
        self.details.append(url.rsplit("/", 1)[-1])
        i = int(url.rsplit("R", 1)[-1])
        info = {"jobReqId": f"R{i}", "title": self.titles[i], "location": "Remote (US)",
                "jobDescription": "<p>Python services for customers. Remote.</p>"}
        return mock.Mock(status_code=200, json=lambda: {"jobPostingInfo": info})


ENTRY = {"kind": "workday", "tenant": "acme", "wd": "wd1", "site": "Careers"}


def run(entry, board):
    with mock.patch.object(sources, "_client", return_value=board), \
         mock.patch.object(sources.time, "sleep"):
        return sources.fetch_workday(entry)


class TestTheTitleTestIsShared(unittest.TestCase):
    def test_title_rejection_is_what_score_job_returns(self):
        for title in TITLES:
            with self.subTest(title=title):
                score, reasons = score_job({"title": title, "description": ""}, prefs())
                rejected = title_rejection(title, prefs())
                if rejected is not None:
                    self.assertEqual((score, reasons), (0.0, [rejected]))
                else:
                    self.assertFalse(reasons[0].startswith("rejected: title")
                                     or "senior/lead" in reasons[0]
                                     or "people-management" in reasons[0])

    def test_an_unmatched_title_is_not_a_rejection(self):
        self.assertIsNone(title_rejection("Data Analyst", prefs()))


class TestTheFetcherSkipsLostCauses(unittest.TestCase):
    def test_rejected_titles_get_no_detail_request(self):
        board = FakeBoard()
        result = run({**ENTRY, "title_filter": prefs()}, board)
        self.assertEqual(sorted(board.details), ["R0", "R1"])
        self.assertEqual([j["title"] for j in result.jobs],
                         ["Software Engineer", "Data Analyst"])
        self.assertEqual(result.skipped, 3)
        self.assertEqual(sorted(result.skipped_ids), ["R2", "R3", "R4"])

    def test_without_a_filter_nothing_is_skipped(self):
        """`jsa verify` and `jsa add` pass no prefs: they see the whole board."""
        board = FakeBoard()
        result = run(dict(ENTRY), board)
        self.assertEqual(len(board.details), len(TITLES))
        self.assertEqual(result.skipped, 0)

    def test_the_cap_counts_only_postings_that_were_not_skipped(self):
        titles = ["Senior Software Engineer"] * 5 + ["Software Engineer"] * 4
        board = FakeBoard(titles)
        with mock.patch.object(sources, "MAX_DETAIL_FETCHES", 3):
            result = run({**ENTRY, "title_filter": prefs()}, board)
        self.assertEqual(sorted(board.details), ["R5", "R6", "R7"])
        self.assertEqual(result.skipped, 5)


class TestDiscoveryCountsThemHonestly(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)

    def discover(self, board):
        real = db.connect
        entry = {**ENTRY, "company": "Acme", "slug": "acme", "verified": True}
        with mock.patch("jsa.discover.load_profile", return_value={}), \
             mock.patch("jsa.discover.Preferences.from_profile", return_value=prefs()), \
             mock.patch("jsa.discover.load_sources", return_value=[entry]), \
             mock.patch("jsa.db.upgrade", return_value=[]), \
             mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path)), \
             mock.patch.object(sources, "_client", return_value=board), \
             mock.patch.object(sources.time, "sleep"):
            return discover.discover(min_score=0.0)

    def test_skipped_postings_count_as_fetched_and_filtered(self):
        (report,) = self.discover(FakeBoard())
        self.assertEqual(report.fetched, 5)
        self.assertGreaterEqual(report.rejected, 3)

    def test_a_stored_posting_that_is_now_skipped_is_not_closed(self):
        con = db.connect(self.path)
        self.addCleanup(con.close)
        self.discover(FakeBoard(["Software Engineer", "Senior Software Engineer"]))
        sid = con.execute("SELECT id FROM sources").fetchone()[0]
        # As if stored before the operator's filters changed.
        con.execute("INSERT INTO jobs (company_id, source_id, external_id, title, url) "
                    "VALUES (1, ?, 'R1', 'Senior Software Engineer', 'https://x')", (sid,))
        con.commit()
        self.discover(FakeBoard(["Software Engineer", "Senior Software Engineer"]))
        closed = con.execute("SELECT closed_at FROM jobs WHERE external_id = 'R1'").fetchone()[0]
        self.assertIsNone(closed)


if __name__ == "__main__":
    unittest.main()
