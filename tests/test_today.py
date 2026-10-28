"""Today, the redesign's home page (2026-10-27): one next step chosen in the
order a search moves, what is waiting on you, three numbers and the newest
strong matches. It reads only."""

import unittest

from jsa.web.routes_today import next_step
from tests.test_web_documents import Sandbox, make_client


def counts(**kw):
    base = {"replies": 0, "drafts": 0, "ready": 0, "due": 0, "outreach": 0,
            "undrafted": 0, "strong": 0, "applied": 0}
    return {**base, **kw}


class TestTheNextStep(unittest.TestCase):
    def test_the_order_a_search_moves(self):
        self.assertIn("Confirm 2 email replies", next_step(counts(replies=2, drafts=40))["text"])
        self.assertIn("Approve 40 drafts", next_step(counts(drafts=40, ready=3))["text"])
        self.assertIn("Apply to 3 ready jobs", next_step(counts(ready=3, due=1))["text"])
        self.assertIn("1 follow-up due", next_step(counts(due=1, undrafted=5))["text"])
        self.assertIn("Draft 5 saved jobs", next_step(counts(undrafted=5, strong=9))["text"])
        self.assertIn("9 strong new matches", next_step(counts(strong=9))["text"])
        self.assertEqual(next_step(counts())["text"], "You're caught up")

    def test_each_step_links_to_where_it_is_done(self):
        for kw, href in (({"replies": 1}, "/replies"), ({"drafts": 1}, "/review"),
                         ({"ready": 1}, "/pipeline"), ({"due": 1}, "/pipeline#first-due"),
                         ({"outreach": 1}, "/review"), ({"undrafted": 1}, "/pipeline"),
                         ({"strong": 1}, "/?anywhere=1"), ({}, "/")):
            with self.subTest(kw=kw):
                self.assertEqual(next_step(counts(**kw))["href"], href)

    def test_waiting_outreach_is_never_hidden(self):
        """Arena R2: with only outreach waiting, Today said "caught up"."""
        self.assertIn("Approve 2 outreach messages", next_step(counts(outreach=2))["text"])
        self.assertIn("Approve 1 draft", next_step(counts(drafts=1, outreach=2))["text"])


class TestThePage(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        con = self.box.connect()
        con.execute("INSERT INTO jobs (id,company_id,title,url,location,remote,match_score) "
                    "VALUES (30,1,'Data Engineer','https://x/30','Remote','remote',0.92)")
        con.commit()
        con.close()
        self.client, _ = make_client(self.box.db, self.box.out, profile={"x": 1})

    def test_it_renders_what_is_waiting_and_the_newest_strong_match(self):
        page = self.client.get("/today").text
        self.assertIn("Your next step", page)
        self.assertIn("Waiting on you", page)
        self.assertIn('href="/job/30"', page)
        self.assertIn('href="/today" class="on" aria-current="page"', page)
        self.assertEqual(page.count('aria-current="page"'), 1)
        self.assertNotIn('id="daily-banner"', page)          # live counts only (arena M1)

    def test_it_writes_nothing(self):
        tables = ("applications", "application_events", "approvals", "documents",
                  "draft_queue", "daily_runs", "jobs")
        def snapshot():
            con = self.box.connect()
            try:
                return [[tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                        for t in tables]
            finally:
                con.close()
        before = snapshot()
        self.client.get("/today")
        self.assertEqual(snapshot(), before)

    def test_pending_outreach_shows_as_waiting(self):
        con = self.box.connect()
        con.execute("INSERT INTO contacts (id, company_id, name) VALUES (1, 1, 'Dana')")
        con.execute("INSERT INTO outreach (id, contact_id, job_id, channel, purpose, draft_body) "
                    "VALUES (1, 1, 1, 'email', 'referral_ask', 'x')")
        con.execute("INSERT INTO approvals (subject_type, subject_id, summary) "
                    "VALUES ('outreach', 1, 'Send referral ask to Dana')")
        con.commit()
        con.close()
        page = self.client.get("/today").text
        self.assertIn("Outreach messages to approve", page)

    def test_a_job_you_have_marks_pipeline_and_one_you_dont_marks_matches(self):
        self.assertIn('href="/pipeline" class="on"', self.client.get("/job/1").text)   # saved
        self.assertIn('href="/" class="on"', self.client.get("/job/30").text)          # not yet

    def test_saved_and_ready_cards_say_where_their_drafts_are(self):
        page = self.client.get("/pipeline").text
        self.assertIn("no drafts yet", page)                 # job 1 is ready, no documents
        con = self.box.connect()
        doc = con.execute("INSERT INTO documents (job_id, kind, path, version) "
                          "VALUES (1, 'resume', 'x.docx', 1)").lastrowid
        con.execute("INSERT INTO approvals (subject_type, subject_id, summary, decision, "
                    "decided_by, decided_at, feedback) VALUES ('document', ?, 'resume v1', "
                    "'rejected', 'human', '2026-10-27T00:00:00Z', 'not this one')", (doc,))
        con.commit()
        con.close()
        page = self.client.get("/pipeline").text
        self.assertIn("1 draft, no resume approved", page)   # rejected is not "no drafts"
        self.assertNotIn("no drafts yet", page)

    def test_every_page_has_the_sidebar(self):
        for url in ("/", "/today", "/pipeline", "/review", "/replies", "/add", "/companies", "/turbo"):
            with self.subTest(url=url):
                self.assertIn('<nav class="side" aria-label="Main">', self.client.get(url).text)


if __name__ == "__main__":
    unittest.main()
