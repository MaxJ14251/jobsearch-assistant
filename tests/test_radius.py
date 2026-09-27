"""The radius filter on the dashboard: "within N miles of here".

n17 put postings on a map. This is the control that uses it, and the
questions it has to answer honestly are about what it HIDES: a remote job
has no distance, and a job whose location could not be placed is unknown
rather than far away. Neither should quietly vanish behind a number.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from jsa import db

PROFILE = {
    "identity": {"full_name": "Dana Rivers"},
    "job_search_preferences": {
        "target_titles": ["Engineer"],
        "locations": ["Remote (US)", "Boise, ID"],
        "compensation_floor_usd": "no_floor",
    },
}

# Distances from Boise: Meridian ~10, Twin Falls ~110, Miami ~2,100.
JOBS = [
    (1, "Nearby Engineer", "Meridian, ID", "onsite"),
    (2, "Next County Engineer", "Twin Falls, ID", "onsite"),
    (3, "Far Engineer", "Miami, FL", "onsite"),
    (4, "Remote Engineer", "Redmond, WA", "remote"),
    (5, "Mystery Engineer", "Starbase, TX", "onsite"),
]


class RadiusCase(unittest.TestCase):
    def setUp(self):
        from tests.test_web_documents import make_client

        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        path = self.dir / "t.db"
        db.init_db(path)
        con = db.connect(path)
        # One company each: the matches page caps a single employer at three
        # rows, which quietly hid two of these the first time.
        for job_id, title, location, remote in JOBS:
            con.execute("INSERT INTO companies (id,name,slug) VALUES (?,?,?)",
                        (job_id, f"Company {job_id}", f"company-{job_id}"))
            con.execute(
                "INSERT INTO jobs (id,company_id,title,url,location,remote,"
                "match_score,track,description) VALUES (?,?,?,?,?,?,0.8,"
                "'engineering','Python work.')",
                (job_id, job_id, title, f"https://acme.test/{job_id}",
                 location, remote))
        con.commit()
        con.close()
        self.client, _ = make_client(path, self.dir, profile=PROFILE)

    def page(self, query=""):
        response = self.client.get("/" + query)
        self.assertEqual(response.status_code, 200)
        return response.text

    def titles(self, query=""):
        text = self.page(query)
        return [title for _id, title, _loc, _rem in JOBS if title in text]


class TestWhatItShows(RadiusCase):
    def test_no_radius_shows_everything(self):
        self.assertEqual(len(self.titles()), len(JOBS))

    def test_a_radius_keeps_what_is_inside_it(self):
        shown = self.titles("?radius=25")
        self.assertIn("Nearby Engineer", shown)
        self.assertNotIn("Next County Engineer", shown)
        self.assertNotIn("Far Engineer", shown)

    def test_a_wider_radius_reaches_further(self):
        self.assertIn("Next County Engineer", self.titles("?radius=250"))

    def test_remote_always_passes(self):
        """A remote job has no distance. Hiding it behind a radius would hide
        the jobs that are open to everybody."""
        for query in ("?radius=10", "?radius=25", "?radius=250"):
            with self.subTest(query=query):
                self.assertIn("Remote Engineer", self.titles(query))

    def test_the_origin_comes_from_the_profile_when_the_box_is_empty(self):
        self.assertIn("Boise", self.page("?radius=25"))

    def test_the_box_wins_over_the_profile(self):
        shown = self.titles("?radius=25&home=Miami, FL")
        self.assertIn("Far Engineer", shown)
        self.assertNotIn("Nearby Engineer", shown)

    def test_a_zip_works_as_the_origin(self):
        self.assertIn("Far Engineer", self.titles("?radius=25&home=33101"))


class TestWhatItSaysAboutWhatItHid(RadiusCase):
    def test_it_counts_what_the_radius_hid(self):
        self.assertIn("hidden by the radius", self.page("?radius=25"))

    def test_an_unplaceable_posting_is_named_not_just_dropped(self):
        """"Starbase, TX" incorporated in 2025 and is not in the 2024 Census
        file. Unknown is not the same as far away, and the page says so."""
        text = self.page("?radius=25")
        self.assertNotIn("Mystery Engineer", text)
        self.assertIn("could not find", text)

    def test_there_is_a_way_back_to_everything(self):
        self.assertIn("Show them anyway", self.page("?radius=25"))

    def test_a_place_it_cannot_resolve_says_so_and_filters_nothing(self):
        text = self.page("?radius=25&home=nowhere at all")
        self.assertIn("not a ZIP code", text)
        self.assertEqual(len(self.titles("?radius=25&home=nowhere at all")),
                         len(JOBS), "it filtered on an origin it did not have")

    def test_it_says_how_many_of_the_shown_jobs_are_actually_near(self):
        """Without this the page can say "within 25 miles" over a list that
        is mostly remote work."""
        self.assertIn("near you", self.page("?radius=25"))


class TestTheDistanceOnACard(RadiusCase):
    def test_a_nearby_job_shows_its_distance(self):
        self.assertRegex(self.page("?radius=25"), r"\d+ mi away")

    def test_a_remote_job_does_not(self):
        """It often names an office as well; that mileage is not what you
        would travel."""
        text = self.page("?radius=250")
        remote_card = text.split("Remote Engineer", 1)[1][:400]
        self.assertNotIn("mi away", remote_card)


if __name__ == "__main__":
    unittest.main()
