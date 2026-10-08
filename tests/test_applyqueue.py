"""The apply session's queue (plan 31): who is ready, in what order, and the
form each one opens. Reads only."""

import unittest
from urllib.parse import urlparse

from jsa import applyqueue, approvals, db, intake, pairing
from tests.test_ext import Ext

BOARD_HOSTS = {"job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}


class Queue(Ext):
    def add_job(self, job_id, kind, board, external_id, score=0.5, closed=False):
        api = {"greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
               "lever": f"https://api.lever.co/v0/postings/{board}?mode=json",
               "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{board}",
               "workday": "https://x.wd1.myworkdayjobs.com/wday/cxs/x/y/jobs",
               "manual": "manual"}[kind]
        src = db.upsert_source(self.con, name=f"{board}-{kind}", kind=kind, url=api,
                               company_id=1)
        self.con.execute(
            "INSERT INTO jobs (id,company_id,source_id,external_id,title,url,match_score,"
            "closed_at) VALUES (?,1,?,?,?,?,?,?)",
            (job_id, src, external_id, f"Job {job_id}", "https://example.com/careers",
             score, "2026-10-01T00:00:00Z" if closed else None))
        approvals.save_application(self.con, job_id)
        self.con.commit()

    def queue(self):
        return applyqueue.ready_to_apply(self.con)


class TestWhoIsReady(Queue):
    def test_an_approved_resume_is_needed(self):
        q = self.queue()
        self.assertEqual(q["items"], [])
        self.assertEqual(q["left_out"]["needs_approval"], 1)
        self.doc(approve=False)
        self.assertEqual(self.queue()["left_out"]["needs_approval"], 1)
        self.doc()
        q = self.queue()
        self.assertEqual([i["job_id"] for i in q["items"]], [1])
        self.assertEqual(q["left_out"]["needs_approval"], 0)

    def test_boards_the_extension_cant_fill_are_by_hand(self):
        self.add_job(10, "workday", "x", "R-1")
        self.add_job(11, "manual", "pasted", "p-1")
        self.doc(job_id=10)
        self.doc(job_id=11)
        self.assertEqual(self.queue()["left_out"]["by_hand"], 2)

    def test_a_closed_posting_is_left_out(self):
        self.add_job(12, "lever", "riverton", "0a1b2c3d-0000-4000-8000-000000000012",
                     closed=True)
        self.doc(job_id=12)
        q = self.queue()
        self.assertEqual(q["left_out"]["closed"], 1)
        self.assertNotIn(12, [i["job_id"] for i in q["items"]])

    def test_applied_jobs_are_not_in_it(self):
        self.doc()
        approvals.mark_applied(self.con, 1, via="employer")
        self.con.commit()
        self.assertEqual(self.queue()["items"], [])

    def test_highest_match_first_then_the_earliest_approval(self):
        self.con.execute("UPDATE jobs SET match_score = 0.6 WHERE id = 1")
        self.add_job(20, "lever", "riverton", "0a1b2c3d-0000-4000-8000-000000000020", 0.9)
        self.add_job(21, "ashby", "riverton", "0a1b2c3d-0000-4000-8000-000000000021", 0.6)
        for job in (21, 1, 20):          # 21 approved before 1
            self.doc(job_id=job)
            self.con.execute("UPDATE approvals SET decided_at = ? WHERE subject_id = "
                             "(SELECT MAX(id) FROM documents)",
                             (f"2026-10-0{ {21: 1, 1: 2, 20: 3}[job] }T00:00:00Z",))
        self.con.commit()
        self.assertEqual([i["job_id"] for i in self.queue()["items"]], [20, 21, 1])

    def test_a_newer_draft_is_a_warning(self):
        self.doc()
        self.doc(approve=False)
        item = self.queue()["items"][0]
        self.assertEqual(item["resume_version"], 1)
        self.assertTrue(any("newer resume draft" in w for w in item["warnings"]))


class TestApplyUrls(Queue):
    def test_the_boards_own_form_and_back_to_the_same_job(self):
        self.add_job(30, "lever", "riverton", "0a1b2c3d-0000-4000-8000-000000000030")
        self.add_job(31, "ashby", "riverton", "0a1b2c3d-0000-4000-8000-000000000031")
        for job in (1, 30, 31):
            self.doc(job_id=job)
        items = self.queue()["items"]
        self.assertEqual(len(items), 3)
        for item in items:
            with self.subTest(job=item["job_id"]):
                self.assertIn(urlparse(item["apply_url"]).hostname, BOARD_HOSTS)
                link = intake.parse_link(item["apply_url"])
                self.assertEqual(db.job_for_link(self.con, link), item["job_id"])
        greenhouse = next(i for i in items if i["job_id"] == 1)
        # The board-hosted form, not the employer's careers page in jobs.url.
        self.assertEqual(greenhouse["apply_url"],
                         "https://job-boards.greenhouse.io/riverton/jobs/4012345")

    def test_the_board_comes_from_the_api_address_not_the_slug(self):
        self.assertEqual(applyqueue.board_of(
            "greenhouse", "https://boards-api.greenhouse.io/v1/boards/rocketlab/jobs?x=1"),
            "rocketlab")
        self.assertIsNone(applyqueue.board_of("greenhouse", "https://example.com/"))
        self.assertIsNone(applyqueue.apply_url("greenhouse", "x", "not-a-number"))


class TestTheRoute(Queue):
    def test_needs_the_key_and_writes_nothing(self):
        self.doc()
        self.assertEqual(self.get("/ext/queue", key=False).status_code, 401)
        before = self.con.total_changes
        body = self.get("/ext/queue").json()
        self.assertEqual([i["job_id"] for i in body["items"]], [1])
        self.assertEqual(set(body["left_out"]), {"needs_approval", "by_hand", "closed"})
        self.assertEqual(self.con.total_changes, before)


class TestPipeline(Queue):
    def test_start_applying_when_paired(self):
        self.doc()
        page = self.client.get("/pipeline").text
        self.assertIn("Start applying (1 ready)", page)
        self.assertIn('href="https://job-boards.greenhouse.io/riverton/jobs/4012345'
                      '#jsa-apply-session"', page)

    def test_connect_first_when_not(self):
        self.doc()
        pairing.disconnect(self.con)
        self.con.commit()
        page = self.client.get("/pipeline").text
        self.assertNotIn("Start applying", page)
        self.assertIn("Connect the extension first", page)

    def test_left_out_counts(self):
        self.add_job(40, "workday", "x", "R-40")
        page = self.client.get("/pipeline").text
        self.assertIn("1 need an approved resume", page)
        self.assertIn("1 to apply by hand", page)


if __name__ == "__main__":
    unittest.main()
