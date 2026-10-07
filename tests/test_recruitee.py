"""Recruitee boards (plan 28).

Fixtures copy the shape `{board}.recruitee.com/api/offers/` returned on
2026-10-07, with fictional values. SmartRecruiters and Breezy HR were checked
the same day and are not read (ADR 0020); a test below pins that.
"""

import sqlite3
import unittest
from unittest import mock

import httpx

from jsa import db, find, intake, salary, sources
from jsa.config import SCHEMA_PATH


def offer(**over):
    item = {
        "id": 4100001,
        "slug": "support-engineer-2",
        "title": "Support Engineer",
        "department": "Customer Success",
        "description": "<p>Help customers get the most out of Riverton Grid.</p>",
        "requirements": "<ul><li>Two years in a customer-facing role</li></ul>",
        "city": "Albany", "state_code": "NY", "country_code": "US",
        "country": "United States",
        "locations": [{"city": "Albany", "state_code": "NY", "country_code": "US",
                       "country": "United States"}],
        "remote": False, "hybrid": False, "on_site": True,
        "employment_type_code": "fulltime_permanent",
        "careers_url": "https://riverton.recruitee.com/o/support-engineer-2",
        "published_at": "2026-10-05 12:12:50 UTC",
        "salary": {"min": "58000.00", "max": "78000", "period": "year", "currency": "USD"},
    }
    item.update(over)
    return item


def fetched(*offers):
    with mock.patch.object(sources, "_get_json", return_value={"offers": list(offers)}):
        return sources.fetch({"kind": "recruitee", "board": "riverton"})


class TestFetch(unittest.TestCase):
    def test_a_posting_in_the_trackers_shape(self):
        result = fetched(offer())
        self.assertTrue(result.ok, result.status)
        job = result.jobs[0]
        self.assertEqual(job["external_id"], "4100001")
        self.assertEqual(job["title"], "Support Engineer")
        self.assertEqual(job["location"], "Albany, NY")
        self.assertEqual(job["remote"], "onsite")
        self.assertEqual(job["employment_type"], "full-time")
        self.assertEqual(job["url"], "https://riverton.recruitee.com/o/support-engineer-2")
        # Description and requirements both, as the hosted page shows them.
        self.assertIn("Riverton Grid", job["description"])
        self.assertIn("customer-facing role", job["description"])
        self.assertEqual(job["description_hash"], sources.content_hash(job["description"]))
        self.assertEqual((job["pay"].minimum, job["pay"].maximum, job["pay"].period),
                         (58000, 78000, "year"))
        self.assertIn("Recruitee pay field", job["pay"].text)

    def test_the_url_is_the_boards_own_api(self):
        self.assertEqual(sources.feed_url({"kind": "recruitee", "board": "riverton"}),
                         "https://riverton.recruitee.com/api/offers/")

    def test_remote_and_hybrid_come_from_the_boards_flags(self):
        self.assertEqual(fetched(offer(remote=True, on_site=False)).jobs[0]["remote"], "remote")
        self.assertEqual(fetched(offer(hybrid=True, on_site=False)).jobs[0]["remote"], "hybrid")

    def test_places_outside_the_us_name_the_country(self):
        job = fetched(offer(locations=[{"city": "Utrecht", "country_code": "NL",
                                        "country": "Netherlands", "state_code": "UT"},
                                       {"city": "Albany", "country_code": "US",
                                        "state_code": "NY"}])).jobs[0]
        self.assertEqual(job["location"], "Utrecht, Netherlands; Albany, NY")

    def test_a_board_is_required(self):
        result = sources.fetch({"kind": "recruitee"})
        self.assertFalse(result.ok)
        self.assertIn("board", result.status)

    def test_a_missing_board_and_a_token_wall_say_so(self):
        def answer(code):
            request = httpx.Request("GET", "https://riverton.recruitee.com/api/offers/")
            error = httpx.HTTPStatusError("x", request=request,
                                          response=httpx.Response(code, request=request))
            with mock.patch.object(sources, "_get_json", side_effect=error):
                return sources.fetch({"kind": "recruitee", "board": "riverton"})
        self.assertIn("no Recruitee board named 'riverton'", answer(404).status)
        walled = answer(401)
        self.assertFalse(walled.ok)
        self.assertIn("employer's careers-site token", walled.status)


class TestPay(unittest.TestCase):
    def test_a_period_with_no_figure_is_no_pay(self):
        # One board measured 2026-10-07 did this on 27 of 37 postings.
        self.assertIsNone(salary.from_recruitee(
            {"min": None, "max": None, "period": "month", "currency": "USD"}))
        self.assertIsNone(salary.from_recruitee(
            {"min": None, "max": None, "period": None, "currency": None}))
        self.assertIsNone(salary.from_recruitee(None))

    def test_monthly_pay_is_left_unknown(self):
        self.assertIsNone(salary.from_recruitee(
            {"min": "2850", "max": "2950", "period": "month", "currency": "EUR"}))

    def test_hourly_and_one_sided(self):
        pay = salary.from_recruitee({"min": "24", "max": "", "period": "hour",
                                     "currency": "USD"})
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (24, 24, "hour"))

    def test_junk_figures_are_refused(self):
        self.assertIsNone(salary.from_recruitee(
            {"min": "competitive", "max": None, "period": "year", "currency": "USD"}))
        self.assertIsNone(salary.from_recruitee(           # implausible for a year
            {"min": "12", "max": "15", "period": "year", "currency": "USD"}))


class TestLinks(unittest.TestCase):
    def test_a_recruitee_posting_link(self):
        link = intake.parse_link("https://riverton.recruitee.com/o/support-engineer-2")
        self.assertEqual((link.kind, link.board, link.job_id),
                         ("recruitee", "riverton", "support-engineer-2"))

    def test_lookalikes_are_not_recruitee(self):
        for url in ("https://riverton.recruitee.com.example.test/o/x",
                    "https://riverton.recruitee.com/",
                    "https://recruitee.com/o/x",
                    "https://jobs.riverton.example/o/support-engineer-2"):
            self.assertIsNone(intake.parse_link(url), url)

    def test_add_finds_the_posting_by_its_slug(self):
        with mock.patch.object(sources, "_get_json",
                               return_value={"offers": [offer(id=1, slug="other",
                                             careers_url="https://riverton.recruitee.com/o/other"),
                                             offer()]}):
            job = intake.fetch_posting(intake.parse_link(
                "https://riverton.recruitee.com/o/support-engineer-2"))
        self.assertEqual(job["external_id"], "4100001")


class TestFind(unittest.TestCase):
    def test_a_configured_board_is_checked_live(self):
        self.assertIn("recruitee", find.LIVE_KINDS)
        self.assertTrue(find.allowed("https://riverton.recruitee.com/api/offers/"))
        self.assertEqual(find.add_link("recruitee", "riverton", fetched(offer()).jobs[0]),
                         "https://riverton.recruitee.com/o/support-engineer-2")

    def test_but_never_guessed(self):
        # No company-name check exists for it, so a guess could only ever be
        # "possible"; the guess budget stays on the three boards that had it.
        self.assertNotIn("recruitee", find.GUESS_KINDS)

    def test_the_allowlist_still_refuses_the_aggregators(self):
        for url in ("https://www.linkedin.com/jobs/view/1",
                    "https://www.indeed.com/viewjob?jk=1",
                    "https://recruitee.com.linkedin.com/x",
                    "https://evil.example/riverton.recruitee.com"):
            self.assertFalse(find.allowed(url), url)


class TestNotRead(unittest.TestCase):
    def test_the_boards_checked_and_declined_have_no_adapter(self):
        """SmartRecruiters and Breezy HR were checked on 2026-10-07 and declined
        (ADR 0020). Adding one needs a new decision, not just a fetcher."""
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        kinds = db.source_kinds(db._SOURCES_TABLE_RE.search(schema).group(0))
        self.assertIn("recruitee", kinds)
        for kind in ("smartrecruiters", "breezy"):
            self.assertNotIn(kind, sources.FETCHERS)
            self.assertNotIn(kind, kinds)

    def test_a_tracker_from_before_recruitee_takes_it_after_upgrade(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8")
                          .replace("'recruitee',", ""))
        with self.assertRaises(sqlite3.IntegrityError):
            db.upsert_source(con, name="x", kind="recruitee", url="https://x",
                             company_id=None)
        self.assertIn("sources", db.rebuilds_pending(con))
        db.migrate(con)
        db.upsert_source(con, name="x", kind="recruitee", url="https://x", company_id=None)
        con.close()


if __name__ == "__main__":
    unittest.main()
