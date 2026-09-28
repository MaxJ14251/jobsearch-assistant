"""Pay the job boards publish as data (n22).

Fixtures copy the shapes the three public APIs returned on 2026-09-28:
Ashby's `compensation` (includeCompensation=true), Greenhouse's
`pay_input_ranges` (pay_transparency=true) and Lever's `salaryRange`. The
rules are ADR 0006's: base pay only, a year or an hour, plausible for the
period, several ranges combined, and dollars compared only with dollars.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import db, discover, salary, sources, web
from jsa.config import Preferences
from tests.test_map import PROFILE


def ashby(*components, tiers=None):
    comp = {"summaryComponents": list(components)}
    if tiers is not None:
        comp["compensationTiers"] = [{"components": t} for t in tiers]
    return comp


def part(kind="Salary", low=150000, high=200000, interval="1 YEAR", currency="USD"):
    return {"compensationType": kind, "interval": interval, "currencyCode": currency,
            "minValue": low, "maxValue": high}


class TestAshby(unittest.TestCase):
    def test_salary_is_read_and_equity_is_not(self):
        pay = salary.from_ashby(ashby(part(), part("EquityCashValue", None, None)))
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (150000, 200000, "year"))
        self.assertEqual(pay.source, "field")
        self.assertIn("Ashby pay field", pay.text)

    def test_equity_alone_is_no_pay(self):
        self.assertIsNone(salary.from_ashby(ashby(part("EquityCashValue"))))
        self.assertIsNone(salary.from_ashby(ashby(part("Bonus"), part("Commission"))))

    def test_tiers_combine_to_the_widest_range(self):
        pay = salary.from_ashby(ashby(tiers=[[part(low=120000, high=150000)],
                                             [part(low=160000, high=210000)]]))
        self.assertEqual((pay.minimum, pay.maximum), (120000, 210000))
        self.assertIn("2 ranges combined", pay.text)

    def test_hourly(self):
        pay = salary.from_ashby(ashby(part(low=28.5, high=34, interval="1 HOUR")))
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (28, 34, "hour"))

    def test_a_month_is_unknown_not_guessed(self):
        self.assertIsNone(salary.from_ashby(ashby(part(low=9000, high=11000,
                                                       interval="1 MONTH"))))

    def test_one_sided_is_a_point(self):
        pay = salary.from_ashby(ashby(part(low=None, high=180000)))
        self.assertEqual((pay.minimum, pay.maximum), (180000, 180000))

    def test_other_currencies_are_kept_as_they_are(self):
        pay = salary.from_ashby(ashby(part(low=70000, high=90000, currency="EUR")))
        self.assertEqual(pay.currency, "EUR")
        self.assertIn("EUR", pay.text)
        self.assertNotIn("$", pay.text)

    def test_dollars_win_when_both_are_given(self):
        pay = salary.from_ashby(ashby(part(low=70000, high=90000, currency="GBP"),
                                      part(low=150000, high=190000)))
        self.assertEqual((pay.currency, pay.minimum), ("USD", 150000))

    def test_implausible_figures_are_refused(self):
        self.assertIsNone(salary.from_ashby(ashby(part(low=5, high=9))))


def gh(low_cents, high_cents, title="Pay Range", currency="USD"):
    return {"min_cents": low_cents, "max_cents": high_cents,
            "currency_type": currency, "title": title, "blurb": "<p>x</p>"}


class TestGreenhouse(unittest.TestCase):
    def test_cents_become_dollars_and_size_says_year(self):
        pay = salary.from_greenhouse([gh(12000000, 15000000, "US Base Salary Range")])
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (120000, 150000, "year"))

    def test_the_title_says_hour(self):
        """Rocket Lab's shape: one hourly figure, named so."""
        pay = salary.from_greenhouse([gh(2800, 2800, "Hourly Pay Range (CA Only)")])
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (28, 28, "hour"))

    def test_an_hourly_sized_figure_that_does_not_say_hour_is_refused(self):
        """ADR 0006: an hourly figure must say "hour"."""
        self.assertIsNone(salary.from_greenhouse([gh(2800, 3500, "Pay Range")]))

    def test_non_base_ranges_are_skipped(self):
        self.assertIsNone(salary.from_greenhouse([gh(20000000, 30000000, "OTE Range")]))
        pay = salary.from_greenhouse([gh(20000000, 30000000, "OTE"),
                                      gh(10000000, 13000000, "Base Salary")])
        self.assertEqual(pay.maximum, 130000)

    def test_ranges_by_location_combine(self):
        pay = salary.from_greenhouse([gh(10000000, 12000000, "Zone 2"),
                                      gh(11500000, 14000000, "Zone 1")])
        self.assertEqual((pay.minimum, pay.maximum), (100000, 140000))

    def test_nothing_is_nothing(self):
        self.assertIsNone(salary.from_greenhouse(None))
        self.assertIsNone(salary.from_greenhouse([]))


class TestLever(unittest.TestCase):
    def test_hourly_in_another_currency(self):
        """matchgroup's shape, measured: CAD, per hour."""
        pay = salary.from_lever({"min": 35, "max": 38, "currency": "CAD",
                                 "interval": "per-hour-wage"})
        self.assertEqual((pay.minimum, pay.maximum, pay.period, pay.currency),
                         (35, 38, "hour", "CAD"))

    def test_yearly(self):
        pay = salary.from_lever({"min": 130000, "max": 160000, "currency": "USD",
                                 "interval": "per-year-salary"})
        self.assertEqual((pay.period, pay.maximum), ("year", 160000))

    def test_a_month_is_unknown(self):
        self.assertIsNone(salary.from_lever({"min": 8000, "max": 9000, "currency": "USD",
                                             "interval": "per-month-salary"}))


class TestTheAdaptersAskAndRead(unittest.TestCase):
    """One more parameter on the same host; the field lands on the job."""

    def test_the_urls_ask_for_pay_on_the_same_hosts(self):
        self.assertTrue(sources.ashby_url({"board": "x"}).startswith(
            "https://api.ashbyhq.com/posting-api/job-board/x?"))
        self.assertIn("includeCompensation=true", sources.ashby_url({"board": "x"}))
        self.assertTrue(sources.greenhouse_url({"board": "x"}).startswith(
            "https://boards-api.greenhouse.io/v1/boards/x/jobs?"))
        self.assertIn("pay_transparency=true", sources.greenhouse_url({"board": "x"}))

    def fetch(self, fn, payload):
        with mock.patch.object(sources, "_get_json", lambda url, *a, **k: payload):
            result = fn({"board": "x"})
        self.assertTrue(result.ok)
        return result.jobs[0]

    def test_ashby(self):
        job = self.fetch(sources.fetch_ashby, {"jobs": [{
            "id": 1, "title": "Engineer", "location": "Remote", "descriptionPlain": "x",
            "compensation": ashby(part())}]})
        self.assertEqual(job["pay"].maximum, 200000)

    def test_greenhouse(self):
        job = self.fetch(sources.fetch_greenhouse, {"jobs": [{
            "id": 1, "title": "Engineer", "content": "x", "location": {"name": "LA"},
            "pay_input_ranges": [gh(10000000, 12000000)]}]})
        self.assertEqual(job["pay"].minimum, 100000)

    def test_lever(self):
        job = self.fetch(sources.fetch_lever, [{
            "id": "a", "text": "Engineer", "descriptionPlain": "x", "categories": {},
            "salaryRange": {"min": 100000, "max": 120000, "currency": "USD",
                            "interval": "per-year-salary"}}])
        self.assertEqual(job["pay"].maximum, 120000)

    def test_a_posting_without_pay_data_has_none(self):
        job = self.fetch(sources.fetch_lever, [{
            "id": "a", "text": "Engineer", "descriptionPlain": "x", "categories": {}}])
        self.assertIsNone(job["pay"])


class TestPrecedenceAndRescore(unittest.TestCase):
    """The field wins, and nothing that re-reads the text may erase it."""

    TEXT = "Base salary range: $90,000 - $110,000 per year."

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'A','a')")
        field = salary.from_ashby(ashby(part(low=150000, high=190000)))
        for job_id, pay in ((1, field), (2, salary.extract(self.TEXT))):
            self.con.execute(
                "INSERT INTO jobs (id,company_id,title,url,description,location,remote,"
                "salary_min,salary_max,salary_period,salary_text,salary_currency,"
                "salary_source) VALUES (?,1,'Engineer',?,?,'Boise, ID','onsite',?,?,?,?,?,?)",
                (job_id, f"https://a.test/{job_id}", self.TEXT, pay.minimum, pay.maximum,
                 pay.period, pay.text, pay.currency, pay.source))
        self.con.commit()

    def test_rescore_keeps_a_field_figure(self):
        discover.rescore(self.con, Preferences.from_profile(PROFILE))
        row = self.con.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
        self.assertEqual((row["salary_min"], row["salary_source"]), (150000, "field"))

    def test_rescore_still_rereads_a_text_figure(self):
        discover.rescore(self.con, Preferences.from_profile(PROFILE))
        row = self.con.execute("SELECT * FROM jobs WHERE id = 2").fetchone()
        self.assertEqual((row["salary_min"], row["salary_source"]), (90000, "text"))


class TestOnlyDollarsCompare(unittest.TestCase):
    def test_from_row_treats_another_currency_as_unknown(self):
        self.assertIsNone(salary.from_row({"salary_min": 70000, "salary_max": 90000,
                                           "salary_period": "year",
                                           "salary_currency": "EUR"}))
        self.assertIsNotNone(salary.from_row({"salary_min": 70000, "salary_max": 90000,
                                              "salary_period": "year"}))

    def test_the_floor_never_rejects_a_figure_it_cannot_compare(self):
        from jsa.scoring import score_job

        profile = {**PROFILE, "job_search_preferences": {
            **PROFILE["job_search_preferences"], "compensation_floor_usd": 100000}}
        prefs = Preferences.from_profile(profile)
        job = {"title": "Engineer", "description": "Python.", "location": "Boise, ID",
               "remote": "onsite", "salary_min": 50000, "salary_max": 60000,
               "salary_period": "year"}
        score_usd, reasons = score_job(job, prefs)
        self.assertEqual(score_usd, 0.0, reasons)
        score_eur, _ = score_job({**job, "salary_currency": "EUR"}, prefs)
        self.assertGreater(score_eur, 0.0)

    def test_the_dashboard_does_not_average_other_currencies(self):
        self.assertEqual(web._annual({"salary_min": 35, "salary_max": 38,
                                      "salary_period": "hour",
                                      "salary_currency": "CAD"}), (None, None))


if __name__ == "__main__":
    unittest.main()
