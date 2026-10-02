"""Pay extraction and pay ranking. See docs/decisions/0006-*.

Every fixture is synthetic text shaped like a posting that was actually in the
tracker when this was written, so each test names the real failure it pins.
"""

import copy
import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import db, salary, scoring
from jsa.config import DB_PATH, CompFloor, Preferences
from jsa.salary import extract

FILLER = "We build reliable systems and care about craft. " * 3


class TestExtraction(unittest.TestCase):
    def test_a_clean_range(self):
        s = extract(FILLER + "Pay Range: California $112,000-$195,000 USD")
        self.assertEqual((s.minimum, s.maximum, s.period), (112_000, 195_000, "year"))
        self.assertIn("$112,000", s.text)

    def test_b_a_lunch_stipend_is_not_a_salary(self):
        """Scenario b. The posting that made this a design question."""
        text = (FILLER + "PERKS: - A weekly lunch stipend of $75/£75 or equivalent "
                "in your city. - Everyone receives a $500 home office stipend.")
        self.assertIsNone(extract(text))

    def test_funding_rounds_are_not_salaries(self):
        for text in ("We recently raised our $1.5B Series F from investors.",
                     "With $125M raised at Series B, we are growing.",
                     "We raised a $355M Series C at a $4.65B valuation and "
                     "crossed $300M+ ARR.",
                     "Proven success with a $1M+ annual quota."):
            with self.subTest(text=text[:30]):
                self.assertIsNone(extract(FILLER + text))

    def test_a_benefit_is_not_a_salary(self):
        self.assertIsNone(extract(
            FILLER + "Parental leave, up to $20k in fertility services, and a "
            "401(k) match up to $6,000 - $8,000."))

    def test_c_multi_level_combines_to_the_envelope(self):
        """Scenario c. ADR 0006 decision 2: lowest minimum, highest maximum."""
        s = extract(FILLER + "COMPENSATION AND BENEFITS: Pay Range: "
                    "Level I: $135,000.00 - $160,000.00/per year\n"
                    "Level II: $155,000.00 - $185,000.00/per year\n"
                    "Your actual level and base salary will be determined.")
        self.assertEqual((s.minimum, s.maximum), (135_000, 185_000))
        self.assertIn("1 more range", s.text)

    def test_a_list_of_cities_keeps_every_city(self):
        """The hand-check found the third city dropped: its pay wording was
        more than 250 characters up the list."""
        text = (FILLER + "The pay range for this position is below. "
                "$77,000.00 - $131,000.00 for the location of: Maryland, "
                "Colorado, Washington and remote workers "
                "$84,500.00 - $144,000.00 for the location of: Washington, D.C. "
                "$96,500.00 - $164,000.00 for the location of: New York")
        s = extract(text)
        self.assertEqual((s.minimum, s.maximum), (77_000, 164_000))

    def test_a_k_written_once_applies_to_both_ends(self):
        s = extract(FILLER + "Compensation: - $150-$195k + equity")
        self.assertEqual((s.minimum, s.maximum), (150_000, 195_000))

    def test_an_en_dash_and_trailing_usd(self):
        s = extract(FILLER + "Base Pay Range (CO Only) $85,000—$100,000 USD")
        self.assertEqual((s.minimum, s.maximum), (85_000, 100_000))

    def test_hourly_is_stored_as_hourly(self):
        s = extract(FILLER + "the good faith hourly rate estimate for this role "
                    "is Zone 1: $21.01 USD - $31.49 USD Applicable for: CA")
        self.assertEqual((s.minimum, s.maximum, s.period), (21, 31, "hour"))
        self.assertEqual(s.annual_minimum(), 21 * salary.HOURS_PER_YEAR)

    def test_annual_wins_when_a_posting_gives_both(self):
        s = extract(FILLER + "the estimated hourly rate is $33.65 - $38.70 per hour "
                    "with a 30k annual commission target, annualized to salary "
                    "range of $100,000 - $115,000 per year.")
        self.assertEqual((s.minimum, s.maximum, s.period), (100_000, 115_000, "year"))

    def test_an_hourly_sized_range_needs_the_word_hour(self):
        """"$30 - $40" alone could be a ticket price."""
        self.assertIsNone(extract(FILLER + "Pay attention: tickets cost $30 - $40."))

    def test_single_stated_wages(self):
        cases = {
            "The US base pay for this position is $24.04 per hour.": (24, 24, "hour"),
            "Software Engineering Intern: $30.00/hour and Masters: $38.00/hour": (30, 38, "hour"),
            "- On Target Earnings: $125,000 Compensation Philosophy": (125_000, 125_000, "year"),
            "Competitive pay starting at $75,000 with $5,000 sign on bonus": (75_000, 75_000, "year"),
            "Approximately 20 hours per week at $20 USD/hour.": (20, 20, "hour"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:30]):
                s = extract(FILLER + text)
                self.assertIsNotNone(s, text)
                self.assertEqual((s.minimum, s.maximum, s.period), expected)

    def test_hourly_zones_keep_every_zone(self):
        """Zone 2 sits past a list of states, far from the word "hourly"."""
        s = extract(FILLER + "the good faith hourly rate estimate for this role is "
                    "Zone 1: $28.85 USD Applicable for: CA, CO, CT, DC, HI, IL, MD, "
                    "MA, NJ, NY, VA, and WA Zone 2: $26.93 USD Applicable for: all "
                    "other states")
        self.assertEqual((s.minimum, s.maximum, s.period), (27, 29, "hour"))

    def test_base_is_kept_and_variable_pay_is_not(self):
        s = extract(FILLER + "a starting hourly rate of $25.24 ($52,500 per year), "
                    "plus variable compensation of $37,500 for on target earnings "
                    "(Total compensation with OTE of $90,000).")
        self.assertEqual(s.minimum, 52_500, "the variable part became the minimum")

    def test_bonuses_and_commissions_alone_are_not_pay(self):
        for text in ("Eligible for a signing bonus of $10,000.",
                     "Eligible for a target commission of $27,000.00 annually."):
            with self.subTest(text=text):
                self.assertIsNone(extract(FILLER + text))

    def test_a_range_wider_than_four_times_is_refused(self):
        self.assertIsNone(extract(FILLER + "Salary: $30,000 - $400,000 depending"))

    def test_no_pay_wording_no_salary(self):
        self.assertIsNone(extract(FILLER + "Our customers saved $50,000 - $90,000."))

    def test_empty_and_none(self):
        self.assertIsNone(extract(""))
        self.assertIsNone(extract(None))

    def test_a_known_miss_stays_known(self):
        """A posting's own typo: "$143,00 to $210,000". Not guessed at. If this
        starts passing, the extractor learned something; check it was right."""
        self.assertIsNone(extract(
            FILLER + "The base salary range for this role is $143,00 to $210,000."))


def prefs(floor=None, regions=None):
    p = Preferences.from_profile({"job_search_preferences": {
        "target_titles": ["Software Engineer"],
        "fallback_titles": ["Account Executive"],
        "locations": ["Remote (US)", "San Diego, CA"],
        "max_years_experience": 3,
        "regions": regions or {"sd": ["San Diego"], "co": ["Boulder, CO"]},
        "compensation_floor_usd": floor if floor is not None else "no_floor",
    }, "ats_keywords": {"have": ["Python"]}})
    return p


def job(pay=None, **kw):
    base = {"title": "Software Engineer", "location": "San Diego, CA",
            "remote": "onsite", "description": "Python work."}
    base.update(kw)
    base.update(salary.columns(pay))
    return base


def pay(low, high=None, period="year"):
    return salary.Salary(low, high if high is not None else low, period, "Pay Range")


class TestARangeLabelledAsMoreThanBase(unittest.TestCase):
    """Rocket Lab states total pay and base pay as two ranges; both were
    combined, so the maximum carried the equity (n22 finding, 23 postings)."""

    ROCKET = ("Pay commensurate with skills and experience. Total Compensation "
              "(base and equity) $93,275–$132,025 USD Base Salary "
              "$83,200–$114,400 USD")

    def test_the_total_range_is_not_base_pay(self):
        pay = extract(self.ROCKET)
        self.assertEqual((pay.minimum, pay.maximum), (83200, 114400))

    def test_other_ways_of_saying_it(self):
        for label in ("Total target compensation:", "Total comp", "Base + equity:",
                      "Salary plus bonus range:", "Package including equity:"):
            with self.subTest(label=label):
                pay = extract(f"{label} $150,000 - $210,000. Base salary $120,000 - $160,000.")
                self.assertEqual((pay.minimum, pay.maximum), (120000, 160000))

    def test_equity_mentioned_before_a_base_range_does_not_veto_it(self):
        pay = extract("You will be eligible for equity. The base salary range for "
                      "this role is $130,000 - $170,000.")
        self.assertEqual((pay.minimum, pay.maximum), (130000, 170000))

    def test_ote_is_still_stored_as_stated(self):
        """ADR 0006 decision 3: OTE is kept, and says so in salary_text."""
        pay = extract("OTE: $180,000 - $220,000 per year.")
        self.assertEqual((pay.minimum, pay.maximum), (180000, 220000))


class TestHourlyWithoutTheWordHour(unittest.TestCase):
    """n25 (owner's decision 2026-09-29): an hourly-sized range that never
    says "hour" is hourly when it is written to the cent, or the posting
    says the ROLE is paid hourly. Size alone still is not enough."""

    def test_cents_written_out(self):
        pay = extract("COMPENSATION AND BENEFITS: Pay Range: Level 1: $23.00 - $27.00 "
                      "Level 2: $26.00 - $33.00")
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (23, 33, "hour"))

    def test_a_non_exempt_position(self):
        pay = extract("This will be a full-time, non-exempt position located in Long "
                      "Beach. Pay Range: California $26–$36 USD")
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (26, 36, "hour"))

    def test_eligible_for_overtime_or_a_shift_differential(self):
        for words in ("This job is eligible for overtime pay.",
                      "A shift differential applies to nights."):
            with self.subTest(words=words):
                pay = extract(f"{words} Pay range: $24 - $30")
                self.assertEqual(pay.period, "hour")

    def test_benefits_boilerplate_is_not_evidence(self):
        """Vast's benefits paragraph is in its salaried postings too."""
        for words in ("up to 10+ days of vacation for non-exempt staff",
                      "willingness to work overtime, or weekends"):
            with self.subTest(words=words):
                self.assertIsNone(extract(f"Benefits: {words}. Pay Range: California $55–$70 USD"))

    def test_size_alone_is_still_refused(self):
        self.assertIsNone(extract("Pay Range: Level 1: $33 - $39 Level 2: $37 - $44"))

    def test_a_zone_list_to_the_cent_is_read_whole(self):
        """ServiceTitan: "hourly" sat too far above Zone 2 for it to count."""
        pay = extract("the good faith hourly rate estimate for this role is\n\nZone 1: "
                      "$21.01 USD - $31.49 USD Applicable for: CA, CT, DC, MD, MA, NJ, NY, "
                      "VA, and WA\n\nZone 2: $19.61 USD - $29.42 USD Applicable for: All "
                      "other US locations.")
        self.assertEqual((pay.minimum, pay.maximum), (20, 31))


class TestAStartupBoardsCompensationLine(unittest.TestCase):
    """Y Combinator: "$110K - $150K • 0.05% - 0.15% • New York, NY, US".

    No pay word anywhere near it. The equity range right after is the cue.
    Found on Kyber (job 1087); measured on all 1,750 postings, it was the
    only one whose pay changed.
    """

    def test_the_line_as_pasted_and_as_spaced(self):
        for text in ("$110K - $150K•0.05% - 0.15%•New York, NY, US",
                     "$110K - $150K • 0.05% - 0.15% • New York, NY, US"):
            with self.subTest(text=text):
                pay = extract(text)
                self.assertEqual((pay.minimum, pay.maximum, pay.period),
                                 (110000, 150000, "year"))

    def test_a_bare_range_still_needs_a_cue(self):
        self.assertIsNone(extract("$110K - $150K"))

    def test_a_funding_round_is_still_refused(self):
        self.assertIsNone(extract("We raised $110M - $150M • 5% - 10% growth"))

    def test_a_percentage_that_is_not_a_range_is_not_a_cue(self):
        self.assertIsNone(extract("$110K - $150K • 20% of the team is remote"))


class TestPayRanking(unittest.TestCase):
    def test_e_unknown_pay_scores_exactly_the_midpoint(self):
        """Scenario e. ADR 0001 decision 4's trap, tested directly."""
        midpoint = (scoring.PAY_LOW + scoring.PAY_HIGH) // 2
        known, _ = scoring.score_job(job(pay(midpoint, midpoint + 40_000)), prefs())
        unknown, _ = scoring.score_job(job(None), prefs())
        self.assertEqual(known, unknown)
        self.assertGreater(unknown, 0)

    def test_the_trap_would_be_caught(self):
        """If unknown scored zero, the test above must fail."""
        with mock.patch.object(scoring, "PAY_UNKNOWN", 0.0):
            known, _ = scoring.score_job(job(pay(125_000)), prefs())
            unknown, _ = scoring.score_job(job(None), prefs())
        self.assertNotEqual(known, unknown)

    def test_higher_pay_ranks_higher(self):
        low, _ = scoring.score_job(job(pay(70_000)), prefs())
        high, _ = scoring.score_job(job(pay(160_000)), prefs())
        self.assertGreater(high, low)

    def test_the_minimum_is_what_counts(self):
        """A senior level at the top of a range must not lift an entry match."""
        narrow, _ = scoring.score_job(job(pay(100_000, 110_000)), prefs())
        wide, _ = scoring.score_job(job(pay(100_000, 400_000)), prefs())
        self.assertEqual(narrow, wide)

    def test_pay_cannot_beat_a_much_better_fit(self):
        """A well-paid role that fits worse must not outrank a good fit."""
        good_fit_low_pay, _ = scoring.score_job(job(pay(50_000)), prefs())
        weak_fit_high_pay, _ = scoring.score_job(
            job(pay(400_000), title="Engineer, Software Platform",
                location="Austin, TX"), prefs())
        self.assertGreater(good_fit_low_pay, weak_fit_high_pay)
        swing = scoring.W_COMPENSATION * 1.0
        self.assertLessEqual(swing, 0.10)

    def test_unknown_pay_keeps_the_old_order(self):
        """Fit is scaled, not re-weighted: with pay unknown, ranking is the
        same as before pay existed. Any movement is caused by pay alone."""
        p = prefs()
        jobs = [job(None, title=t, location=l) for t, l in [
            ("Software Engineer", "San Diego, CA"), ("Software Engineer", "Austin, TX"),
            ("Account Executive", "San Diego, CA"), ("Engineer", "Remote"),
            ("Software Engineer II", "Boulder, CO")]]
        with mock.patch.object(scoring, "W_FIT", 1.0), \
                mock.patch.object(scoring, "W_COMPENSATION", 0.0):
            before = [scoring.score_job(j, p)[0] for j in jobs]
        after = [scoring.score_job(j, p)[0] for j in jobs]
        rank = lambda xs: sorted(range(len(xs)), key=lambda i: -xs[i])
        self.assertEqual(rank(before), rank(after))

    def test_hourly_is_annualised_for_ranking(self):
        hourly, _ = scoring.score_job(job(pay(60, 60, "hour")), prefs())
        yearly, _ = scoring.score_job(job(pay(124_800)), prefs())
        self.assertEqual(hourly, yearly)

    def test_the_reason_says_what_it_paid(self):
        _, reasons = scoring.score_job(job(pay(135_000, 185_000)), prefs())
        self.assertIn("pays $135,000–$185,000/yr (ranked on the low end)", reasons)
        _, reasons = scoring.score_job(job(None), prefs())
        self.assertIn("pay not stated (scored neutral)", reasons)

    def test_weights(self):
        self.assertAlmostEqual(scoring.W_TITLE + scoring.W_LOCATION
                               + scoring.W_KEYWORDS + scoring.W_SENIORITY, 1.0)
        self.assertAlmostEqual(scoring.W_FIT + scoring.W_COMPENSATION, 1.0)

    def test_the_sales_discount_does_not_touch_the_pay_term(self):
        """Otherwise unknown-pay sales and engineering jobs could swap."""
        p = prefs()
        for title in ("Software Engineer", "Account Executive"):
            with self.subTest(title=title):
                known, _ = scoring.score_job(job(pay(125_000), title=title), p)
                unknown, _ = scoring.score_job(job(None, title=title), p)
                self.assertEqual(known, unknown)

    def test_sales_track_is_still_scaled_below(self):
        eng, _ = scoring.score_job(job(pay(290_000)), prefs())
        sales, _ = scoring.score_job(job(pay(290_000), title="Account Executive"), prefs())
        self.assertLess(sales, eng)


class TestFloor(unittest.TestCase):
    """The operator has no floor. These exercise the path with synthetic ones."""

    REGIONAL = {"default": 95_000, "co": 70_000, "sd": 110_000}

    def test_below_the_floor_is_rejected_and_says_why(self):
        score, reasons = scoring.score_job(job(pay(80_000, 90_000)), prefs(95_000))
        self.assertEqual(score, 0.0)
        self.assertIn("below your floor of $95,000", reasons[0])

    def test_the_top_of_the_range_decides(self):
        """A range reaching the floor could pay it; only the top is compared."""
        score, _ = scoring.score_job(job(pay(80_000, 100_000)), prefs(95_000))
        self.assertGreater(score, 0)

    def test_unknown_pay_is_never_rejected(self):
        score, _ = scoring.score_job(job(None), prefs(1_000_000))
        self.assertGreater(score, 0)

    def test_the_region_floor_applies(self):
        p = prefs(self.REGIONAL)
        sd, reasons = scoring.score_job(job(pay(100_000)), p)
        self.assertEqual(sd, 0.0)
        self.assertIn("for sd", reasons[0])
        pa, _ = scoring.score_job(job(pay(100_000), location="Boulder, CO"), p)
        self.assertGreater(pa, 0)
        remote, _ = scoring.score_job(job(pay(90_000), location="Remote - US",
                                          remote="remote"), p)
        self.assertEqual(remote, 0.0, "the default floor applies outside regions")

    def test_hourly_is_compared_annualised(self):
        score, _ = scoring.score_job(job(pay(40, 45, "hour")), prefs(95_000))
        self.assertEqual(score, 0.0)          # 45 * 2080 = 93,600
        score, _ = scoring.score_job(job(pay(40, 50, "hour")), prefs(95_000))
        self.assertGreater(score, 0)          # 50 * 2080 = 104,000

    def test_no_floor_rejects_nothing_for_pay(self):
        score, _ = scoring.score_job(job(pay(20_000)), prefs())
        self.assertGreater(score, 0)

    def test_a_floor_does_not_change_scores_above_it(self):
        """A threshold, not a gradient (ADR 0001 decision 7)."""
        with_floor, _ = scoring.score_job(job(pay(150_000)), prefs(95_000))
        without, _ = scoring.score_job(job(pay(150_000)), prefs())
        self.assertEqual(with_floor, without)


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        self.addCleanup(self.con.close)

    def test_discover_stores_what_it_scored(self):
        from jsa import discover
        self.con.commit()
        self.con.close()
        posting = {"external_id": "x1", "title": "Software Engineer",
                   "url": "https://acme.test/1", "location": "San Diego, CA",
                   "remote": "onsite",
                   "description": FILLER + "Pay Range: $120,000 - $150,000 USD"}
        entry = {"company": "Acme", "slug": "acme", "kind": "greenhouse", "verified": True}
        from jsa.sources import FetchResult
        result = FetchResult(True, [posting], "ok")
        real = db.connect
        with mock.patch("jsa.discover.load_profile", return_value={}), \
             mock.patch("jsa.discover.Preferences.from_profile", return_value=prefs()), \
             mock.patch("jsa.discover.load_sources", return_value=[entry]), \
             mock.patch("jsa.discover.sources.fetch", return_value=result), \
             mock.patch("jsa.discover.sources.feed_url", return_value=""), \
             mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path)):
            discover.discover(min_score=0.0)
        with closing(real(self.path)) as con:
            row = con.execute(
                "SELECT salary_min, salary_max, salary_period, salary_text, match_reasons "
                "FROM jobs").fetchone()
        self.assertEqual(tuple(row)[:3], (120_000, 150_000, "year"))
        self.assertIn("$120,000", row["salary_text"])
        self.assertIn("pays $120,000–$150,000/yr (ranked on the low end)",
                      json.loads(row["match_reasons"]))

    def test_rescore_reads_pay_without_the_network(self):
        from jsa import discover
        self.con.execute(
            "INSERT INTO jobs (id,company_id,title,url,location,remote,description,match_score) "
            "VALUES (1,1,'Software Engineer','https://a/1','San Diego, CA','onsite',?,0.5)",
            (FILLER + "Salary: $90,000 - $100,000",))
        with mock.patch("jsa.sources.fetch", side_effect=AssertionError("network")):
            report = discover.rescore(self.con, prefs(95_000))
        self.assertEqual((report.jobs, report.with_pay, report.rejected_by_floor), (1, 1, 0))
        report = discover.rescore(self.con, prefs(150_000))
        self.assertEqual(report.rejected_by_floor, 1)
        row = self.con.execute("SELECT match_score, match_reasons FROM jobs").fetchone()
        self.assertEqual(row["match_score"], 0.0)
        self.assertIn("floor", json.loads(row["match_reasons"])[0])

    def test_a_bad_period_is_refused_by_the_schema(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO jobs (company_id,title,url,salary_period) "
                "VALUES (1,'x','https://a/2','month')")

    def test_an_existing_tracker_gains_the_columns(self):
        """init_db on a tracker from before this change adds them."""
        from jsa.db import SCHEMA_PATH
        old = Path(tempfile.mkdtemp()) / "old.db"
        lines = SCHEMA_PATH.read_text(encoding="utf-8").splitlines()
        before = chr(10).join(
            line for line in lines
            if not line.strip().startswith(("salary_period", "salary_text")))
        con = sqlite3.connect(old)
        con.executescript(before)
        con.close()
        with closing(sqlite3.connect(old)) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
        self.assertNotIn("salary_period", cols, "fixture should predate the columns")
        db.init_db(old)
        with closing(sqlite3.connect(old)) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
        self.assertLessEqual({"salary_period", "salary_text"}, cols)


def _live_rows():
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT id, description, salary_min, salary_max, salary_period, "
                "salary_source FROM jobs WHERE archived_at IS NULL").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


LIVE = _live_rows()


@unittest.skipUnless(LIVE, "no job data on this machine")
class TestSalaryStaysExtracted(unittest.TestCase):
    """Scenario g. Replaces the ADR 0001 tripwire, which asserted salary_min was
    entirely NULL and failed, as designed, the day this landed.

    The replacement guards the other direction: salary silently STOPPING being
    extracted -- a feed change, a regex edit, a discover path that skips the
    extractor -- fails here, loudly.
    """

    def test_the_tracker_holds_pay_where_postings_state_it(self):
        expected = [r for r in LIVE if extract(r["description"]) is not None]
        stored = [r for r in LIVE if r["salary_min"] is not None]
        self.assertGreater(len(expected), 0, "no posting states pay: check the extractor")
        self.assertGreaterEqual(
            len(stored), 0.95 * len(expected),
            f"{len(expected)} postings state pay but only {len(stored)} have it "
            "stored. Run `jsa rescore`; if that does not fix it, discovery has "
            "stopped extracting salary.")

    def test_stored_pay_matches_what_the_text_says(self):
        """A figure from the board's own pay field (n22) is not the text's to
        match; the text is only the fallback for those."""
        mismatched = []
        for r in LIVE:
            if r["salary_source"] == "field":
                continue
            s = extract(r["description"])
            got = (r["salary_min"], r["salary_max"], r["salary_period"])
            want = (s.minimum, s.maximum, s.period) if s else (None, None, None)
            if got != want:
                mismatched.append(r["id"])
        self.assertLessEqual(len(mismatched), 0.05 * len(LIVE),
                             f"stored pay is stale for jobs {mismatched[:10]}; "
                             "run `jsa rescore`")


if __name__ == "__main__":
    unittest.main()
