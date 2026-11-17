"""Workday's rate limit: wait it out, then pause every Workday board.

Measured 2026-11-16: after a burst, Workday's shared front answered 429 to
9 of 13 tenants, with no Retry-After, and let up within minutes. A detail
request that got a 429 used to return {} and the posting was stored with no
description. These tests run the fetcher against a fake board.
"""

import unittest
from unittest import mock

from jsa import sources


def response(status, payload=None, headers=None):
    return mock.Mock(status_code=status, headers=headers or {},
                     raise_for_status=lambda: None, json=lambda: payload or {})


class FakeBoard:
    """Lists `n` postings. `listing` and `detail` are lists of status codes
    to answer with, in order; once used up, every answer is 200."""

    def __init__(self, n=3, listing=(), detail=()):
        self.n = n
        self.listing = list(listing)
        self.detail = list(detail)
        self.posts = 0
        self.gets: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        self.posts += 1
        if self.listing:
            return response(self.listing.pop(0))
        batch = [{"title": "Software Engineer", "externalPath": f"/job/x/R{i}",
                  "bulletFields": [f"R{i}"], "locationsText": "Remote (US)"}
                 for i in range(self.n)]
        return response(200, {"total": self.n, "jobPostings": batch})

    def get(self, url):
        self.gets.append(url)
        if self.detail:
            return response(self.detail.pop(0))
        i = int(url.rsplit("R", 1)[-1])
        return response(200, {"jobPostingInfo": {
            "jobReqId": f"R{i}", "title": "Software Engineer", "location": "Remote (US)",
            "jobDescription": "<p>Python services. Remote.</p>"}})


ENTRY = {"kind": "workday", "tenant": "acme", "wd": "wd1", "site": "Careers"}


class TestRateLimit(unittest.TestCase):
    def setUp(self):
        sources._workday_paused_until = 0.0
        self.sleeps: list[float] = []
        patcher = mock.patch.object(sources.time, "sleep", side_effect=self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, sources, "_workday_paused_until", 0.0)

    def run_board(self, board):
        with mock.patch.object(sources, "_client", return_value=board):
            return sources.fetch_workday(ENTRY)

    def test_a_429_on_the_listing_is_waited_out(self):
        board = FakeBoard(listing=[429, 429])
        result = self.run_board(board)
        self.assertTrue(result.ok, result.status)
        self.assertEqual(len(result.jobs), 3)
        self.assertEqual(board.posts, 3)
        self.assertEqual(self.sleeps[:2], list(sources.WORKDAY_BACKOFF_S))

    def test_retry_after_is_honoured_and_capped(self):
        board = FakeBoard()
        board.listing = []
        answers = [response(429, headers={"Retry-After": "5"}),
                   response(429, headers={"Retry-After": "900"})]
        real_post = board.post
        board.post = lambda *a, **k: answers.pop(0) if answers else real_post(*a, **k)
        self.assertTrue(self.run_board(board).ok)
        self.assertEqual(self.sleeps[:2], [5.0, 120.0])

    def test_a_429_that_persists_fails_the_board_and_pauses_the_rest(self):
        result = self.run_board(FakeBoard(listing=[429, 429, 429]))
        self.assertFalse(result.ok)
        self.assertIn("rate limited by Workday", result.status)
        # The next board is not asked at all.
        other = FakeBoard()
        result = self.run_board(other)
        self.assertFalse(result.ok)
        self.assertIn("paused", result.status)
        self.assertEqual(other.posts, 0)

    def test_a_detail_429_is_never_stored_without_its_description(self):
        # R0 is read; R1 gets 429 three times; R1 and R2 are left for next run.
        board = FakeBoard(n=3)
        board.detail = []
        statuses = iter([200, 429, 429, 429])
        real_get = board.get

        def get(url):
            code = next(statuses, 200)
            if code == 429:
                board.gets.append(url)
                return response(429)
            return real_get(url)
        board.get = get
        result = self.run_board(board)
        self.assertTrue(result.ok, result.status)
        self.assertEqual([j["external_id"] for j in result.jobs], ["R0"])
        self.assertTrue(all(j.get("description") for j in result.jobs))
        self.assertEqual(result.deferred_ids, ["R1", "R2"])

    def test_other_errors_on_a_detail_still_degrade(self):
        board = FakeBoard(n=2, detail=[500])
        result = self.run_board(board)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.jobs), 2)
        self.assertEqual(result.deferred_ids, [])


class TestDiscoveryKeepsDeferredPostings(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        from jsa import db
        self.db = db
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)
        sources._workday_paused_until = 0.0
        self.addCleanup(setattr, sources, "_workday_paused_until", 0.0)

    def discover(self, board):
        from jsa import discover
        from jsa.config import Preferences
        db, real = self.db, self.db.connect
        prefs = Preferences.from_profile({"job_search_preferences": {
            "target_titles": ["Software Engineer"], "locations": ["Remote (US)"],
            "compensation_floor_usd": "no_floor"}})
        entry = {**ENTRY, "company": "Acme", "slug": "acme", "verified": True}
        with mock.patch("jsa.discover.load_profile", return_value={}), \
             mock.patch("jsa.discover.Preferences.from_profile", return_value=prefs), \
             mock.patch("jsa.discover.load_sources", return_value=[entry]), \
             mock.patch("jsa.db.upgrade", return_value=[]), \
             mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path)), \
             mock.patch.object(sources, "_client", return_value=board), \
             mock.patch.object(sources.time, "sleep"):
            return discover.discover(min_score=0.0)

    def test_a_stored_posting_left_for_next_run_is_not_closed(self):
        con = self.db.connect(self.path)
        self.addCleanup(con.close)
        self.discover(FakeBoard(n=2))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)
        sources._workday_paused_until = 0.0
        # Next run: R0 read, then the limit hits on R1.
        (report,) = self.discover(FakeBoard(n=2, detail=[200, 429, 429, 429]))
        self.assertIn("left for the next run", report.status)
        closed = con.execute("SELECT closed_at FROM jobs WHERE external_id = 'R1'"
                             ).fetchone()[0]
        self.assertIsNone(closed)


if __name__ == "__main__":
    unittest.main()
