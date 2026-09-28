"""The live Matches page (n21): map, list and analytics in one view.

The script on the page draws; the server decides. These tests pin the half
Python owns: the numbers the script is given must be the numbers the
server's own filter used, the list must still come from the server alone,
and the page must still reach nothing but this machine.
"""

import json
import math
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from jsa import approvals, db, mapview, places, web
from tests.test_map import PROFILE, SPREAD, home


class TestThePointsAreTheFiltersNumbers(unittest.TestCase):
    """While the handle moves, the browser sorts dots into inside and
    outside by `d`. So `d` must be the very distance the radius filter
    compares -- not a recomputation, not a rounding of it."""

    def test_d_is_places_nearest_exactly(self):
        points, _, _ = mapview.points(SPREAD, home())
        by_job = {p["job"]: p for p in points}
        for row in SPREAD:
            if row["job_id"] not in by_job:
                continue
            with self.subTest(location=row["location"]):
                self.assertEqual(by_job[row["job_id"]]["d"],
                                 places.nearest(home(), row["location"]))

    def test_x_and_y_put_it_at_that_distance(self):
        """In an azimuthal equidistant projection about home, the length of
        (x, y) IS the distance: the drawn circle and `d` cannot disagree
        by more than the rounding of x and y."""
        points, _, _ = mapview.points(SPREAD, home())
        for p in points:
            with self.subTest(place=p["place"]):
                self.assertAlmostEqual(math.hypot(p["x"], p["y"]), p["d"], delta=0.003)

    def test_remote_and_unplaced_are_counted_never_drawn(self):
        points, remote, unplaced = mapview.points(SPREAD, home())
        drawn = {p["job"] for p in points}
        self.assertNotIn(6, drawn)          # remote
        self.assertNotIn(7, drawn)          # a place the data does not have
        self.assertEqual((remote, unplaced), (1, 1))

    def test_no_titles_in_the_map_data(self):
        """Every posting is in this list, inside the circle or not; a title
        the radius hides must not be on the page (tests/test_radius)."""
        points, _, _ = mapview.points(SPREAD, home())
        self.assertTrue(all("title" not in p for p in points))


class TestAnnualPay(unittest.TestCase):
    def test_hourly_is_multiplied_out(self):
        self.assertEqual(web._annual({"salary_min": 40, "salary_max": 50,
                                      "salary_period": "hour"}), (83200, 104000))

    def test_yearly_is_as_stated(self):
        self.assertEqual(web._annual({"salary_min": 90000, "salary_max": 120000,
                                      "salary_period": "year"}), (90000, 120000))

    def test_none_is_none(self):
        self.assertEqual(web._annual({}), (None, None))

    def test_one_sided_is_a_point(self):
        self.assertEqual(web._annual({"salary_min": 100000, "salary_period": "year"}),
                         (100000, 100000))


class TestStatusGroups(unittest.TestCase):
    def test_every_live_stage_has_a_colour_and_no_closed_one_does(self):
        live = set(approvals.STAGES) - set(approvals.CLOSED)
        self.assertEqual(set(web.STATUS_GROUP), live)
        self.assertEqual(set(web.STATUS_GROUP.values()),
                         {"saved", "applied", "interview"})


class TestThePage(unittest.TestCase):
    def setUp(self):
        from tests.test_web_documents import make_client

        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        path = self.dir / "t.db"
        db.init_db(path)
        con = db.connect(path)
        for row in SPREAD:
            con.execute("INSERT INTO companies (id,name,slug) VALUES (?,?,?)",
                        (row["job_id"], f"Co {row['job_id']}", f"co-{row['job_id']}"))
            con.execute(
                "INSERT INTO jobs (id,company_id,title,url,location,remote,"
                "match_score,track,description,salary_min,salary_max,salary_period) "
                "VALUES (?,?,?,?,?,?,0.8,'engineering','Python work.',90000,120000,'year')",
                (row["job_id"], row["job_id"], row["title"],
                 f"https://acme.test/{row['job_id']}", row["location"], row["remote"]))
        # Four applications at one company near home, and a closed one.
        con.execute("INSERT INTO companies (id,name,slug) VALUES "
                    "(50,'</script><script>alert(1)</script>','x')")
        for i, stage in enumerate(("saved", "applied", "phone_screen", "offer",
                                   "rejected"), start=50):
            con.execute(
                "INSERT INTO jobs (id,company_id,title,url,location,remote,match_score,"
                "track,description) VALUES (?,50,?,?,'Meridian, ID','onsite',0.5,"
                "'engineering','x')", (i, f"Mine {stage}", f"https://acme.test/{i}"))
            con.execute("INSERT INTO applications (job_id,status) VALUES (?,?)", (i, stage))
        con.commit()
        con.close()
        self.client, _ = make_client(path, self.dir, profile=PROFILE)

    def page(self, query="?radius=25"):
        response = self.client.get("/" + query)
        self.assertEqual(response.status_code, 200)
        return response.text

    def data(self, text):
        blocks = re.findall(r'<script type="application/json" id="map-data">(.*?)</script>',
                            text, re.S)
        self.assertEqual(len(blocks), 1)
        return json.loads(blocks[0])

    def test_the_script_is_given_the_radius_and_every_point(self):
        live = self.data(self.page())
        self.assertEqual(live["radius"], 25)
        # 5 new postings placeable + 4 live applications; the rejected one is not.
        self.assertEqual(len(live["points"]), 5 + 4)

    def test_dots_inside_are_exactly_the_listed_placed_cards(self):
        """The browser's inside test (d <= radius), run here, gives the same
        set of cards the server listed -- for new matches, where nothing but
        the radius is in play."""
        live = self.data(self.page())
        inside = {p["key"] for p in live["points"] if p["d"] <= live["radius"]}
        listed = {k for k, c in live["cards"].items() if not c["remote"]}
        self.assertEqual(inside, listed)

    def test_live_applications_are_listed_with_their_stage(self):
        text = self.page()
        for stage in ("saved", "applied", "phone screen", "offer"):
            self.assertIn(f'title="Your application">{stage}<', text)
        self.assertNotIn("Mine rejected", text)

    def test_the_company_cap_is_for_new_matches_only(self):
        """Three per company keeps a big board from filling the page. Your
        own four applications at one company are not a board."""
        live = self.data(self.page())
        mine = [c for c in live["cards"].values() if c["status"] != "new"]
        self.assertEqual(len(mine), 4)

    def test_status_groups_reach_the_page(self):
        live = self.data(self.page())
        statuses = {c["status"] for c in live["cards"].values()}
        self.assertEqual(statuses, {"new", "saved", "applied", "interview"})

    def test_a_company_name_cannot_end_the_data_block(self):
        text = self.page()
        live = self.data(text)          # parses: the block was not cut short
        self.assertIn("</script><script>alert(1)</script>",
                      {c["company"] for c in live["cards"].values()})
        self.assertNotIn("<script>alert(1)", text)

    def test_nothing_is_loaded_from_another_host(self):
        text = self.page()
        self.assertIsNone(re.search(
            r"<(script|link|img|iframe)[^>]+(src|href)=[\"']?(https?:)?//", text, re.I))
        self.assertNotIn("@import", text)
        self.assertNotIn("fonts.g", text)

    def test_without_a_radius_there_is_no_live_map(self):
        """"Anywhere" is the national picture the server drew; the script
        falls back to what it did before n21."""
        text = self.page("?anywhere=1")
        self.assertNotIn('id="map-data"', text)
        self.assertIn('class="land"', text)

    def test_pay_reaches_the_analytics(self):
        live = self.data(self.page())
        new = [c for c in live["cards"].values() if c["status"] == "new"]
        self.assertTrue(new)
        self.assertTrue(all(c["pay"] == [90000, 120000] for c in new))


if __name__ == "__main__":
    unittest.main()
