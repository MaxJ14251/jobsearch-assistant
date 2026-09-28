"""Which postings fold into one card on the Matches page, and what a folded
card is judged by.

n20 was asked for as "merge the duplicates". Measured, the dashboard was not
showing anything twice -- it was showing one job where there were eighteen.
dedup_key stripped every parenthetical, SpaceX puts the team there, and
"Software Engineer (Starlink)", "(Platform Team)", "(AI Data Engineering)"
and fifteen more were one card. 139 distinct titles were hidden that way.

The rule these tests hold: a qualifier folds only when it says WHERE, and a
wrong fold is the worse mistake -- it hides a job; an extra card does not.
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsa import db, places
from jsa.scoring import dedup_key, regional_qualifier


def same(a, b):
    return dedup_key(1, *a) == dedup_key(1, *b)


class TestATeamIsNotAPlace(unittest.TestCase):
    def test_spacex_teams_stay_separate(self):
        titles = ["Software Engineer (Starlink)", "Software Engineer (Starshield)",
                  "Software Engineer (Platform Team)",
                  "Software Engineer (AI Data Engineering)"]
        keys = {dedup_key(5, t, "Hawthorne, CA") for t in titles}
        self.assertEqual(len(keys), len(titles), keys)

    def test_team_names_that_are_also_towns(self):
        """Falcon, CO; Ai, OH; Farmer, SD; a town called Java. A bare
        Gazetteer lookup folded all of these, and "(Falcon)" alone hid seven
        SpaceX postings."""
        for qualifier in ("Falcon", "AI", "Oil and Gas", "Farmer",
                          "Python, Java, Rust, C#, C++"):
            with self.subTest(qualifier=qualifier):
                self.assertFalse(regional_qualifier(qualifier, "Hawthorne, CA"))
                self.assertFalse(same((f"Engineer ({qualifier})", "Hawthorne, CA"),
                                      ("Engineer", "Hawthorne, CA")))

    def test_a_two_letter_word_inside_a_town_name_is_not_that_town(self):
        """"ai" is inside "Mountain View"; a substring test folded "(AI)"."""
        self.assertFalse(regional_qualifier("AI", "Mountain View, CA"))


class TestARegionIsStillARegion(unittest.TestCase):
    def test_the_original_case(self):
        """What dedup_key was written for, and must keep doing."""
        titles = ["Forward Deployed Engineer, Agentic Platform (Korea)",
                  "Forward Deployed Engineer, Agentic Platform (West Coast)",
                  "Forward Deployed Engineer, Agentic Platform"]
        self.assertEqual(len({dedup_key(7, t) for t in titles}), 1)

    def test_a_qualifier_that_names_the_postings_own_town(self):
        self.assertTrue(same(("Deployed Engineer (Chicago)", "Chicago, IL"),
                             ("Deployed Engineer (Boston)", "Boston, MA")))

    def test_arrangement_and_city_state(self):
        for qualifier, location in (("Hybrid- Baltimore, MD", "Baltimore, MD"),
                                    ("Remote", "Remote"), ("US", "Remote (US)"),
                                    ("Lubbock, TX", "Lubbock, TX"),
                                    ("NY or MD", "New York, NY"),
                                    ("Texas", "Austin, TX")):
            with self.subTest(qualifier=qualifier):
                self.assertTrue(regional_qualifier(qualifier, location))

    def test_a_nickname_within_a_commute_of_the_posting(self):
        """"(NYC)" on a posting in Jersey City names the same place."""
        self.assertTrue(regional_qualifier("NYC", "Jersey City, NJ"))


class TestWhenUnsureItKeepsTheCard(unittest.TestCase):
    def test_a_city_that_is_not_where_the_posting_is(self):
        """"(Chicago)" on a posting in Austin is odd data. Folding it could
        hide a job; keeping it costs one extra card."""
        self.assertFalse(regional_qualifier("Chicago", "Austin, TX"))

    def test_no_location_and_a_bare_town_name(self):
        self.assertFalse(regional_qualifier("Chicago", None))


class Tracker(unittest.TestCase):
    """A throwaway tracker with the dashboard over it."""

    PROFILE = {
        "identity": {"full_name": "Dana Rivers"},
        "job_search_preferences": {
            "target_titles": ["Engineer"],
            "locations": ["Remote (US)", "Boise, ID"],
            "compensation_floor_usd": "no_floor",
        },
    }

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)
        self.next_id = 1

    def add(self, company, title, location, score, remote="onsite"):
        con = db.connect(self.path)
        con.execute("INSERT OR IGNORE INTO companies (id,name,slug) VALUES (?,?,?)",
                    (company, f"Co {company}", f"co-{company}"))
        con.execute(
            "INSERT INTO jobs (id,company_id,title,url,location,remote,match_score,"
            "track,description,dedup_key) VALUES (?,?,?,?,?,?,?,'engineering','x',?)",
            (self.next_id, company, title, f"https://t.test/{self.next_id}",
             location, remote, score, dedup_key(company, title, location)))
        con.commit()
        con.close()
        self.next_id += 1
        return self.next_id - 1

    def page(self, **query):
        from tests.test_web_documents import make_client
        client, _ = make_client(self.path, self.dir, profile=self.PROFILE)
        response = client.get("/", params=query)
        self.assertEqual(response.status_code, 200)
        return response.text


class TestTheRadiusSeesEveryCopy(Tracker):
    def setUp(self):
        super().setUp()
        # One req, two cities. Miami scores higher, so it is the card's own
        # row -- and it is 2,000 miles from Boise.
        self.add(1, "Deployed Engineer (Miami)", "Miami, FL", 0.9)
        self.add(1, "Deployed Engineer (Meridian)", "Meridian, ID", 0.8)

    def test_it_is_one_card(self):
        self.assertEqual(self.page(anywhere="1").count("Deployed Engineer ("), 1)

    def test_a_copy_inside_the_radius_keeps_the_card(self):
        text = self.page(radius="25", home="Boise, ID")
        self.assertIn("Deployed Engineer (Miami)", text)
        self.assertIn("(its nearest location)", text)

    def test_no_copy_inside_hides_it(self):
        self.assertNotIn("Deployed Engineer (",
                         self.page(radius="25", home="Seattle, WA"))

    def test_a_remote_copy_makes_the_card_pass_any_radius(self):
        self.add(1, "Deployed Engineer (Remote)", "Remote", 0.7, remote="remote")
        self.assertIn("Deployed Engineer (",
                      self.page(radius="25", home="Seattle, WA"))


class TestTheCapComesAfterTheRadius(Tracker):
    def test_far_cards_do_not_take_the_slots(self):
        """A company's three best cards are in Miami; the fourth is ten
        miles away. Capped first, Miami took the three slots, the radius hid
        them, and the nearby job never reached the page."""
        for n in range(3):
            self.add(2, f"Far Role {n}", "Miami, FL", 0.95 - n / 100)
        self.add(2, "Near Role", "Meridian, ID", 0.5)
        self.assertIn("Near Role", self.page(radius="25", home="Boise, ID"))


class TestExactDistanceNotRounded(unittest.TestCase):
    def test_a_job_just_past_the_radius_is_outside_it(self):
        """The list compared the ROUNDED mileage, so 25.4 miles passed a
        25-mile radius while the map drew it outside the circle."""
        from jsa.web import _by_distance

        boise = places.resolve("Boise", "ID")
        for town in ("Nampa", "Caldwell", "Kuna", "Eagle", "Star", "Emmett"):
            spot = places.resolve(town, "ID")
            if spot is None:
                continue
            exact = places.miles(boise, spot)
            if round(exact) < exact:               # e.g. 18.3 rounds to 18
                radius = float(round(exact))
                break
        else:
            self.skipTest("no nearby town with a fractional part below .5")
        row = {"location": f"{town}, ID", "remote": "onsite"}
        kept, hidden, _ = _by_distance([row], boise, radius)
        self.assertEqual(kept, [], f"{town} is {exact:.2f} mi, radius {radius}")
        self.assertEqual(hidden, 1)


class TestTheLiveTrackerIsCorrected(Tracker):
    def test_rekeying_is_idempotent(self):
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (9,'S','s')")
        con.execute("INSERT INTO jobs (id,company_id,title,url,location,dedup_key) "
                    "VALUES (1,9,'Software Engineer (Starlink)','u','Hawthorne, CA',"
                    "'9:software engineer')")        # the old rule's key
        con.commit()
        first, second = db.rekey(con), db.rekey(con)
        key = con.execute("SELECT dedup_key FROM jobs WHERE id=1").fetchone()[0]
        con.close()
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(key, "9:software engineer starlink")

    def test_a_view_change_reaches_an_existing_tracker(self):
        """Views used to be rebuilt only when a TABLE changed, so the column
        n20 added to v_new_matches never appeared on a tracker that already
        existed. Every test starts from an empty database, which is why none
        of them could see it."""
        con = sqlite3.connect(self.path)
        con.execute("DROP VIEW v_new_matches")
        con.execute("CREATE VIEW v_new_matches AS SELECT id AS job_id FROM jobs")
        con.commit()
        con.close()
        db.upgrade(self.path)
        con = db.connect(self.path)
        columns = [c[1] for c in con.execute("PRAGMA table_info(v_new_matches)")]
        con.close()
        self.assertIn("dedup_key", columns)


if __name__ == "__main__":
    unittest.main()
