"""Drafting the next batch of saved jobs from Pipeline (found 2026-10-27:
48 saved jobs had no drafts, because Turbo drafts only on a right swipe).
The same queue, daily limit and thin-posting rule as Turbo; pressing the
button is the request. Nothing is approved and nothing is sent."""

from unittest import mock

from fastapi.testclient import TestClient

from jsa import approvals, turbo, web
from tests.test_turbo import BASE, PROFILE, Tracker


class TestDraftBatch(Tracker):
    def setUp(self):
        super().setUp()
        con = self.con()
        for job_id in (1, 3, 4):                      # 4 has almost no posting text
            approvals.save_application(con, job_id)
        con.commit()

    def test_queues_saved_jobs_without_drafts_best_first_and_skips_thin(self):
        con = self.con()
        result = turbo.draft_batch(con, PROFILE)
        con.commit()
        self.assertEqual((result.queued, result.thin, result.blocker), (2, 1, None))
        self.assertEqual(sorted({job for job, _, _ in self.queue()}), [1, 3])
        self.assertEqual(len(self.queue()), 4)       # a resume and a cover letter each

    def test_the_daily_limit_holds(self):
        con = self.con()
        result = turbo.draft_batch(con, {**PROFILE, "turbo": {"daily_jobs": 1}})
        con.commit()
        self.assertEqual((result.queued, result.left), (1, 1))
        self.assertEqual({job for job, _, _ in self.queue()}, {1})   # the best match

    def test_a_job_already_drafted_or_queued_is_not_queued_again(self):
        con = self.con()
        turbo.draft_batch(con, PROFILE)
        con.commit()
        again = turbo.draft_batch(con, PROFILE)
        self.assertEqual(again.queued, 0)

    def test_no_key_queues_nothing_and_says_why(self):
        from jsa.llm import LLMError
        con = self.con()
        with mock.patch("jsa.llm.api_key", side_effect=LLMError("no key")):
            result = turbo.draft_batch(con, PROFILE)
        self.assertEqual(result.queued, 0)
        self.assertIn("API key", result.blocker)
        self.assertEqual(self.queue(), [])

    def test_only_saved_jobs_are_drafted(self):
        con = self.con()
        con.execute("UPDATE applications SET status = 'applied' WHERE job_id = 1")
        con.commit()
        turbo.draft_batch(con, PROFILE)
        self.assertNotIn(1, {job for job, _, _ in self.queue()})


class TestTheButton(Tracker):
    def setUp(self):
        super().setUp()
        con = self.con()
        for job_id in (1, 3):
            approvals.save_application(con, job_id)
        con.commit()
        self.app = web.create_app(db_path=self.path, output_dir=self.tmp / "output",
                                  profile_loader=lambda: PROFILE)
        self.client = TestClient(self.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.app.state.csrf_token

    def test_pipeline_offers_it_with_the_count_and_the_cost(self):
        page = self.client.get("/pipeline").text
        self.assertIn('action="/pipeline/draft-batch"', page)
        self.assertIn("Draft the next 2 saved jobs", page)
        self.assertIn("about 4 model calls", page)

    def test_pressing_it_queues_and_says_so(self):
        with mock.patch.object(self.app.state.worker, "wake") as woke:
            r = self.client.post("/pipeline/draft-batch", data={"csrf": self.csrf},
                                 follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("Queued+2", r.headers["location"])
        woke.assert_called_once()
        self.assertEqual(len(self.queue()), 4)
        self.assertNotIn("Draft the next", self.client.get("/pipeline").text)

    def test_it_needs_the_token(self):
        self.assertEqual(self.client.post("/pipeline/draft-batch").status_code, 403)
        self.assertEqual(self.queue(), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
