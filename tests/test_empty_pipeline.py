"""Pipeline when nothing waits to be applied to (found 2026-10-27: 0 saved,
drafting or ready applications while 140 unreviewed matches scored 0.8 or
higher). It says so and points at those matches; it decides nothing."""

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


if __name__ == "__main__":
    unittest.main()
