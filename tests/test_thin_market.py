"""Most of the country is not a tech metro, and the tool has to say so.

Measured over 9,451 postings from all 50 shipped feeds, scored against four
profiles (one discovery pass, each profile scored against the same stored
postings, which is what each would have kept):

    profile              kept  remote  in state  top 20
    Los Angeles, CA       695     247       250  17 remote, 3 in state
    Seattle, WA           728     247       168  17 remote, 3 in state
    Columbus, OH          667     247         5  20 remote
    Boise, ID             652     247         0  20 remote

The shipped feed list is a few dozen large employers. They post wherever they
have offices, so Seattle gets coverage nobody configured, and Columbus gets
five. Neither outcome is a bug. Letting the reader in Columbus find out over a
week of empty local results is.

So: a thin market must still produce a usable ranking, and `jsa doctor` must
say what the tracker actually holds near them before they spend that week.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsa import db, doctor
from jsa.config import Preferences
from jsa.scoring import score_job

PROFILE = {
    "identity": {"full_name": "Dana Rivers", "email": "dana@example.test",
                 "phone": "+1 (555) 555-0100"},
    "job_search_preferences": {
        "target_titles": ["Software Engineer"],
        "locations": ["Remote (US)", "Columbus, OH"],
        "work_authorization": "US citizen",
        "compensation_floor_usd": "no_floor",
        "max_years_experience": 3,
    },
    "summaries": [{"id": "s", "family": "general", "text": "A summary."}],
    "experience": [{"id": "e", "company": "Acme", "title": "Engineer",
                    "family": "technical_field", "bullets": [
                        {"id": "b1", "text": "Built things.",
                         "tags": ["python"], "strength": 1}]}],
    "education": [{"institution": "State University",
                   "credential": "BS Computer Science, 2020"}],
}

# What a thin market really looks like: plenty of postings, almost none near
# you, a handful remote.
FAR = [("San Francisco, CA", "onsite")] * 30 + \
      [("Hawthorne, CA", "onsite")] * 20 + \
      [("Redmond, WA", "onsite")] * 15
REMOTE = [("Remote - US", "remote")] * 8
NEAR = [("Columbus, OH", "onsite")] * 2


def tracker(listings) -> Path:
    path = Path(tempfile.mkdtemp()) / "t.db"
    db.init_db(path)
    con = db.connect(path)
    con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
    for n, (location, remote) in enumerate(listings):
        con.execute(
            "INSERT INTO jobs (company_id,title,url,description,location,remote) "
            "VALUES (1,'Software Engineer',?,?,?,?)",
            (f"https://acme.test/{n}", "python work", location, remote))
    con.commit()
    con.close()
    return path


def findings(report):
    return " | ".join(f.what + " " + f.fix for f in report.findings)


class TestAThinMarketIsReportedNotHidden(unittest.TestCase):
    """Scenario f."""

    def setUp(self):
        self.con = db.connect(tracker(FAR + REMOTE + NEAR))
        self.addCleanup(self.con.close)
        self.report = doctor.run(dict(PROFILE), self.con)

    def test_it_says_how_little_is_near_them(self):
        text = findings(self.report)
        self.assertIn("OH", text)
        self.assertIn("on-site posting", text)

    def test_it_points_at_the_remote_roles_they_do_have(self):
        self.assertIn("remote role", findings(self.report))

    def test_it_says_what_would_change_it(self):
        self.assertIn("companies.yaml", findings(self.report))

    def test_a_thin_market_is_advice_not_a_blocker(self):
        """Nothing here is broken. Exiting 1 would say it was."""
        self.assertTrue(self.report.ok, findings(self.report))

    def test_the_reader_is_told_before_they_look(self):
        checked = " | ".join(self.report.checked)
        self.assertIn("near you", checked)


class TestAHealthyMarketIsNotNagged(unittest.TestCase):
    def test_no_finding_when_plenty_is_local(self):
        profile = dict(PROFILE)
        profile["job_search_preferences"] = {
            **PROFILE["job_search_preferences"],
            "locations": ["Remote (US)", "San Francisco, CA"]}
        con = db.connect(tracker(FAR + REMOTE))
        self.addCleanup(con.close)
        report = doctor.run(profile, con)
        self.assertNotIn("on-site posting", findings(report))

    def test_an_empty_tracker_says_nothing_about_markets(self):
        """check_tracker already says 'run discover'. Two messages is noise."""
        con = db.connect(tracker([]))
        self.addCleanup(con.close)
        report = doctor.run(dict(PROFILE), con)
        self.assertNotIn("on-site posting", findings(report))

    def test_locations_with_no_state_are_called_out(self):
        profile = dict(PROFILE)
        profile["job_search_preferences"] = {
            **PROFILE["job_search_preferences"],
            "locations": ["Remote (US)", "Columbus"]}
        con = db.connect(tracker(FAR + REMOTE + NEAR))
        self.addCleanup(con.close)
        self.assertIn("name no state", findings(doctor.run(profile, con)))


class TestTheRankingStillWorksThere(unittest.TestCase):
    """A thin market must not mean an empty screen."""

    def setUp(self):
        self.prefs = Preferences.from_profile(dict(PROFILE))

    def scored(self, listings):
        out = []
        for location, remote in listings:
            job = {"title": "Software Engineer", "description": "python work",
                   "location": location, "remote": remote,
                   "salary_min": None, "salary_max": None,
                   "salary_period": None, "salary_text": None}
            out.append(score_job(job, self.prefs))
        return out

    def test_remote_roles_carry_the_ranking(self):
        remote = [s for s, _ in self.scored(REMOTE)]
        far = [s for s, _ in self.scored(FAR)]
        self.assertTrue(all(r > max(far) for r in remote),
                        "remote must outrank a city they did not name")

    def test_their_own_city_still_wins(self):
        near = [s for s, _ in self.scored(NEAR)]
        far = [s for s, _ in self.scored(FAR)]
        self.assertTrue(all(n > max(far) for n in near))

    def test_nothing_is_dropped_to_zero_just_for_being_far(self):
        """A far posting is ranked down, not deleted: relocation is a choice
        the reader makes, not one the scorer makes for them."""
        for score, reasons in self.scored(FAR):
            self.assertGreater(score, 0.0, reasons)


class TestRemoteIsNotAPlaceholder(unittest.TestCase):
    """Found by running `jsa doctor` against a real tracker.

    The example profile ships `- "Remote (US)"` alongside stand-ins like
    "Your City, ST". `_is_placeholder` treats every location string in the
    example as a stand-in, so doctor told the reader to "replace or delete"
    the one entry that makes remote roles score 1.0 instead of 0.7 -- and for
    a reader in Columbus or Boise, remote roles are the entire product.

    A placeholder is a value you are meant to overwrite. "Remote (US)" is a
    value you are meant to keep.
    """

    def test_remote_is_never_reported_as_a_placeholder(self):
        for entry in ("Remote (US)", "Remote - US", "remote (us)", "Remote"):
            with self.subTest(entry=entry):
                self.assertFalse(doctor._is_placeholder(entry))

    def test_the_example_stand_ins_still_are(self):
        for entry in ("Your City, ST", "A Nearby City, ST",
                      "A City You Would Relocate To, ST"):
            with self.subTest(entry=entry):
                self.assertTrue(doctor._is_placeholder(entry))

    def test_doctor_does_not_tell_you_to_delete_remote(self):
        profile = dict(PROFILE)
        profile["job_search_preferences"] = {
            **PROFILE["job_search_preferences"],
            "locations": ["Remote (US)", "Columbus, OH"]}
        con = db.connect(tracker(FAR + REMOTE + NEAR))
        self.addCleanup(con.close)
        self.assertNotIn("placeholder", findings(doctor.run(profile, con)))


if __name__ == "__main__":
    unittest.main()
