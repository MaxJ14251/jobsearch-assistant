"""The job page's "Your application" section: the stage, what was sent, the
history, and a control to move the stage (found 2026-10-27: job 421 listed two
approved resumes and the page did not say which one went out)."""

import unittest

from jsa import approvals
from tests.test_web_documents import Sandbox, make_client


class TestYourApplication(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        first = self.box.out / "r1.docx"
        second = self.box.out / "r2.docx"
        for path in (first, second):
            path.write_bytes(b"PK\x03\x04 fictional")
        self.v1 = self.box.add_document(first, version=1)
        self.v2 = self.box.add_document(second, version=2)
        con = self.box.connect()
        approvals.set_stage(con, 1, "applied", via="employer", resume=self.v1,
                            note="submitted on the site")
        con.commit()
        con.close()
        self.client, self.app = make_client(self.box.db, self.box.out, profile={"x": 1})
        self.csrf = self.app.state.csrf_token

    def section(self, job_id=1):
        page = self.client.get(f"/job/{job_id}").text
        start = page.find('id="your-application"')
        return page[start:page.find("<h2>Documents</h2>", start)] if start >= 0 else ""

    def status(self):
        con = self.box.connect()
        try:
            return con.execute("SELECT status FROM applications WHERE job_id = 1").fetchone()[0]
        finally:
            con.close()

    def test_it_names_the_resume_that_was_sent_and_the_one_that_was_not(self):
        text = self.section()
        self.assertIn("Applied", text)
        self.assertIn("via the employer&#39;s site", text)
        self.assertIn(f'href="/document/{self.v1}"', text)
        self.assertIn("Resume v1", text)
        self.assertIn("Drafted, not sent: resume v2", text)
        self.assertNotIn(f'href="/document/{self.v2}"', text)

    def test_drafts_older_than_the_one_sent_are_not_listed(self):
        box = Sandbox()
        self.addCleanup(box.cleanup)
        for v in (1, 2):
            path = box.out / f"o{v}.docx"
            path.write_bytes(b"PK fictional")
            newest = box.add_document(path, version=v)
        con = box.connect()
        approvals.set_stage(con, 1, "applied", via="employer", resume=newest)
        con.commit()
        con.close()
        client, _ = make_client(box.db, box.out, profile={"x": 1})
        page = client.get("/job/1").text
        self.assertIn("Resume v2", page)
        self.assertNotIn("Drafted, not sent", page)

    def test_the_history_says_who_moved_it_and_why(self):
        text = self.section()
        self.assertIn("ready → applied", text)
        self.assertIn("you", text)
        self.assertIn("submitted on the site", text)

    def test_moving_the_stage_here_comes_back_here_as_the_human(self):
        r = self.client.post("/job/1/stage", data={"csrf": self.csrf,
                                                   "stage": "phone_screen", "back": "job"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/job/1?"))
        self.assertEqual(self.status(), "phone_screen")
        con = self.box.connect()
        actor = con.execute("SELECT actor FROM application_events "
                            "ORDER BY id DESC").fetchone()[0]
        con.close()
        self.assertEqual(actor, "human")
        self.assertIn("applied → phone screen", self.section())

    def test_the_tools_drafting_entries_are_counted_not_listed(self):
        con = self.box.connect()
        con.execute("INSERT INTO application_events (application_id, from_status, "
                    "to_status, actor, note) VALUES (1, 'applied', 'applied', 'agent', "
                    "'resume v3 drafted, awaiting approval')")
        con.commit()
        con.close()
        text = self.section()
        self.assertNotIn("resume v3 drafted", text)
        self.assertIn("Plus 1 draft by the tool", text)
        self.assertIn("ready → applied", text)

    def test_moving_back_before_applied_asks_first(self):
        for back in ("job", ""):                 # the job page and Pipeline
            with self.subTest(back=back):
                r = self.client.post("/job/1/stage", data={
                    "csrf": self.csrf, "stage": "saved", "back": back},
                    follow_redirects=False)
                self.assertEqual(self.status(), "applied")
                page = self.client.get(r.headers["location"]).text
                self.assertIn("back to saved, as if you had not applied", page)
                self.assertIn('name="confirm_back" value="1"', page)
        self.client.post("/job/1/stage", data={"csrf": self.csrf, "stage": "saved",
                                                "back": "job", "confirm_back": "1"},
                         follow_redirects=False)
        self.assertEqual(self.status(), "saved")

    def test_only_a_move_back_is_ever_asked_about(self):
        self.assertNotIn('name="confirm_back"',
                         self.client.get("/job/1?ask_back=rejected").text)

    def test_the_pipeline_form_still_returns_to_the_pipeline(self):
        r = self.client.post("/job/1/stage", data={"csrf": self.csrf, "stage": "phone_screen"},
                             follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/pipeline?"))

    def test_the_stage_control_needs_the_token(self):
        r = self.client.post("/job/1/stage", data={"stage": "rejected", "back": "job"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.status(), "applied")

    def test_a_job_with_no_application_has_no_section(self):
        self.assertEqual(self.section(job_id=2), "")


if __name__ == "__main__":
    unittest.main()
