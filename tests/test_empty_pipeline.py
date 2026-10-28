"""Pipeline when nothing waits to be applied to (found 2026-10-27: 0 saved,
drafting or ready applications while 140 unreviewed matches scored 0.8 or
higher). It says so and points at those matches; it decides nothing."""

import re
import unittest

from tests.test_web_documents import Sandbox, make_client


class TestEmptyPipeline(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        con = self.box.connect()
        for job_id, score in ((10, 0.91), (11, 0.84), (12, 0.6)):
            con.execute("INSERT INTO jobs (id,company_id,title,url,location,remote,match_score) "
                        "VALUES (?,1,?,?,'Remote','remote',?)",
                        (job_id, f"Engineer {job_id}", f"https://x/{job_id}", score))
        con.commit()
        con.close()
        self.client, _ = make_client(self.box.db, self.box.out, profile={"x": 1})

    def set_status(self, status):
        con = self.box.connect()
        con.execute("UPDATE applications SET status = ? WHERE job_id = 1", (status,))
        con.commit()
        con.close()

    def test_nothing_to_apply_to_points_at_the_strong_matches(self):
        self.set_status("applied")
        page = self.client.get("/pipeline").text
        self.assertIn("Nothing is waiting to be applied to", page)
        self.assertIn("2 unreviewed matches score 0.8 or higher", page)
        self.assertIn('href="/turbo"', page)

    def test_a_ready_application_means_no_note(self):
        self.set_status("ready")
        self.assertNotIn("Nothing is waiting to be applied to", self.client.get("/pipeline").text)

    def test_no_strong_matches_says_so_plainly(self):
        self.set_status("applied")
        con = self.box.connect()
        con.execute("UPDATE jobs SET match_score = 0.5 WHERE id IN (10, 11)")
        con.commit()
        con.close()
        page = self.client.get("/pipeline").text
        self.assertIn("Nothing is waiting to be applied to", page)
        self.assertIn("no unreviewed match scores 0.8 or higher", page)


class TestClosedCardsCanMove(unittest.TestCase):
    """Found 2026-10-27: closed cards (rejected, withdrawn, ghosted) had no
    stage control, so a mistaken "ghosted" meant opening the job page."""

    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        con = self.box.connect()
        con.execute("UPDATE applications SET status = 'ghosted' WHERE job_id = 1")
        con.commit()
        con.close()
        self.client, self.app = make_client(self.box.db, self.box.out, profile={"x": 1})

    def test_a_closed_card_has_the_same_stage_control(self):
        page = self.client.get("/pipeline").text
        closed = page[page.index("<h2>closed"):]
        self.assertIn('id="app-1"', closed)
        self.assertIn('action="/job/1/stage"', closed)
        self.assertRegex(closed, r'<option value="ghosted"\s+selected')

    def test_moving_it_records_the_person(self):
        self.client.post("/job/1/stage", data={"csrf": self.app.state.csrf_token,
                                               "stage": "withdrawn"}, follow_redirects=False)
        con = self.box.connect()
        self.addCleanup(con.close)
        row = con.execute("SELECT a.status, e.actor FROM applications a JOIN "
                          "application_events e ON e.application_id = a.id "
                          "WHERE a.job_id = 1 ORDER BY e.id DESC").fetchone()
        self.assertEqual(tuple(row), ("withdrawn", "human"))



class TestPostedDate(unittest.TestCase):
    """Every format the boards were storing on 2026-10-27, read as a date."""

    def test_each_boards_format(self):
        from datetime import date
        from jsa.db import posted_date
        cases = {
            "2023-09-22T01:31:37.849+00:00": date(2023, 9, 22),       # Ashby
            "2026-07-16T17:13:00+0000": date(2026, 7, 16),            # custom feeds
            "2026-06-16T16:06:27-04:00": date(2026, 6, 16),           # Greenhouse
            "1634141954197": date(2021, 10, 13),                      # Lever, ms
            "Fri, 11 Sep 2026 07:30:48 +0000": date(2026, 9, 11),     # RSS
            "2024-12-10T18:37:15Z": date(2024, 12, 10),               # The Muse
            "2026-02-06T00:00:00.0000": date(2026, 2, 6),             # USAJOBS
            "2026-05-29": date(2026, 5, 29),                          # Workable, Workday
        }
        for stamp, want in cases.items():
            with self.subTest(stamp=stamp):
                self.assertEqual(posted_date(stamp), want)
        for junk in (None, "", "soon", "12"):
            self.assertIsNone(posted_date(junk))


class TestSortSavedByExperience(unittest.TestCase):
    """The saved group sorted by the years a posting asks for, either way;
    a posting that states none goes last in both (asked 2026-10-27)."""

    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        con = self.box.connect()
        con.execute("UPDATE applications SET status = 'saved' WHERE job_id = 1")
        con.execute("UPDATE jobs SET years_required = 3 WHERE id = 1")
        for job_id, years in ((20, 5), (21, None), (22, 1)):
            con.execute("INSERT INTO jobs (id,company_id,title,url,years_required) "
                        "VALUES (?,1,?,?,?)", (job_id, f"Job {job_id}", f"https://x/{job_id}", years))
            con.execute("INSERT INTO applications (job_id,status) VALUES (?, 'saved')", (job_id,))
        con.commit()
        con.close()
        self.client, _ = make_client(self.box.db, self.box.out, profile={"x": 1})

    def order(self, query=""):
        page = self.client.get("/pipeline" + query).text
        saved = page[page.index("<h2>saved"):]
        end = saved.find("<h2>", 4)
        return [int(x) for x in re.findall(r'id="app-(\d+)"', saved[:end if end > 0 else None])]

    def test_fewest_years_first_with_unstated_last(self):
        self.assertEqual(self.order("?saved_sort=years_asc"), [22, 1, 20, 21])

    def test_most_years_first_with_unstated_still_last(self):
        self.assertEqual(self.order("?saved_sort=years_desc"), [20, 1, 22, 21])

    def test_the_control_and_the_years_show(self):
        page = self.client.get("/pipeline?saved_sort=years_desc").text
        self.assertIn('name="saved_sort"', page)
        self.assertRegex(page, r'<option value="years_desc"\s+selected')
        self.assertIn("5+ yrs", page)
        self.assertIn("years not stated", page)

    def test_by_posting_date_either_way_with_undated_last(self):
        con = self.box.connect()
        for job_id, posted in ((1, "2026-10-20T10:00:00-04:00"), (20, "2026-10-01T10:00:00Z"),
                               (22, "2026-10-25T09:00:00Z")):
            con.execute("UPDATE jobs SET posted_at = ? WHERE id = ?", (posted, job_id))
        con.commit()
        con.close()
        self.assertEqual(self.order("?saved_sort=posted_desc"), [22, 1, 20, 21])
        self.assertEqual(self.order("?saved_sort=posted_asc"), [20, 1, 22, 21])
        page = self.client.get("/pipeline?saved_sort=posted_desc").text
        self.assertIn("posted 2026-10-25", page)
        self.assertIn("posting date unknown", page)

    def test_an_unknown_sort_keeps_the_usual_order(self):
        self.assertEqual(sorted(self.order("?saved_sort=nonsense")), [1, 20, 21, 22])


if __name__ == "__main__":
    unittest.main()
