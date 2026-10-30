"""Two gaps found on the 2026-10-27 review.

- 7 of 14 applications had no channel: it was only ever recorded at the
  moment of marking applied, so older records could not say where they
  went. The job page now fills a missing channel (the person's act); a
  recorded one is never changed.
- An applied job whose posting has closed (often: filled) looked like any
  other on Pipeline. The card now says the posting was taken down."""

import unittest

from jsa import approvals
from tests.test_web_documents import Sandbox, make_client


class Base(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        con = self.box.connect()
        con.execute("UPDATE applications SET status = 'applied', "
                    "applied_at = '2026-10-01T12:00:00Z', applied_via = NULL WHERE job_id = 1")
        con.commit()
        con.close()
        self.client, self.app = make_client(self.box.db, self.box.out, profile={"x": 1})
        self.csrf = self.app.state.csrf_token

    def via(self):
        con = self.box.connect()
        try:
            return con.execute("SELECT applied_via FROM applications WHERE job_id = 1").fetchone()[0]
        finally:
            con.close()


class TestFillAMissingChannel(Base):
    def test_the_job_page_offers_it_only_when_missing(self):
        page = self.client.get("/job/1").text
        self.assertIn('action="/job/1/via"', page)
        self.client.post("/job/1/via", data={"csrf": self.csrf, "via": "linkedin"})
        self.assertEqual(self.via(), "linkedin")
        page = self.client.get("/job/1").text
        self.assertNotIn('action="/job/1/via"', page)
        self.assertIn("via LinkedIn", page)

    def test_a_recorded_channel_is_never_changed(self):
        self.client.post("/job/1/via", data={"csrf": self.csrf, "via": "linkedin"})
        r = self.client.post("/job/1/via", data={"csrf": self.csrf, "via": "indeed"},
                             follow_redirects=False)
        self.assertIn("bad=1", r.headers["location"])
        self.assertEqual(self.via(), "linkedin")

    def test_not_applied_or_unknown_channel_is_refused(self):
        con = self.box.connect()
        self.addCleanup(con.close)
        with self.assertRaises(approvals.ApprovalError):
            approvals.fill_applied_via(con, 1, "carrier pigeon")
        con.execute("UPDATE applications SET applied_at = NULL WHERE job_id = 1")
        with self.assertRaises(approvals.ApprovalError):
            approvals.fill_applied_via(con, 1, "employer")

    def test_it_writes_no_event_and_keeps_the_stage(self):
        con = self.box.connect()
        events = con.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
        con.close()
        self.client.post("/job/1/via", data={"csrf": self.csrf, "via": "employer"})
        con = self.box.connect()
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM application_events").fetchone()[0],
                         events)
        self.assertEqual(con.execute("SELECT status FROM applications WHERE job_id = 1")
                         .fetchone()[0], "applied")

    def test_it_needs_the_token(self):
        self.assertEqual(self.client.post("/job/1/via", data={"via": "employer"}).status_code, 403)
        self.assertIsNone(self.via())


class TestPostingTakenDown(Base):
    def test_a_closed_posting_is_flagged_on_its_card(self):
        self.assertNotIn("posting taken down", self.client.get("/pipeline").text)
        con = self.box.connect()
        con.execute("UPDATE jobs SET closed_at = '2026-10-20T12:00:00Z' WHERE id = 1")
        con.commit()
        con.close()
        page = self.client.get("/pipeline").text
        card = page[page.index('id="app-1"'):]
        self.assertIn("posting taken down", card[:card.index("</form>")])

    def source(self, kind, polled):
        con = self.box.connect()
        con.execute("INSERT INTO sources (id,name,kind,url,company_id,last_polled_at) "
                    "VALUES (90,'s',?,'https://x/feed',1,?)", (kind, polled))
        con.execute("UPDATE jobs SET source_id = 90 WHERE id = 1")
        con.commit()
        con.close()

    def close(self):
        con = self.box.connect()
        con.execute("UPDATE jobs SET closed_at = '2026-10-20T12:00:00Z' WHERE id = 1")
        con.commit()
        con.close()

    def both_pages(self):
        return self.client.get("/pipeline").text, self.client.get("/job/1").text

    def test_a_board_that_dropped_it_says_taken_down_on_both_pages(self):
        self.source("greenhouse", "2026-10-27T00:00:00Z")
        for page in self.both_pages():
            self.assertNotIn("posting taken down", page)
            self.assertNotIn("not checked automatically", page)
        self.close()
        for page in self.both_pages():
            self.assertIn("posting taken down", page)
            self.assertIn("2026-10-20", page)

    def test_a_feed_that_dropped_it_says_it_may_still_be_open(self):
        self.source("rss", "2026-10-27T00:00:00Z")
        self.close()
        for page in self.both_pages():
            self.assertIn("dropped off the feed", page)
            self.assertIn("may still be open", page)
            self.assertNotIn("posting taken down", page)

    def test_a_job_nothing_rechecks_says_so(self):
        self.source("manual", None)
        for page in self.both_pages():
            self.assertIn("not checked automatically", page)

    def test_the_flag_never_changes_the_stage(self):
        self.source("greenhouse", "2026-10-27T00:00:00Z")
        self.close()

        def stage():
            con = self.box.connect()
            try:
                return con.execute("SELECT status FROM applications WHERE job_id = 1").fetchone()[0]
            finally:
                con.close()
        before = stage()
        self.both_pages()
        self.assertEqual(stage(), before)


if __name__ == "__main__":
    unittest.main()
