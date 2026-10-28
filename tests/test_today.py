"""Today, the redesign's home page (2026-10-28): one next step chosen in the
order a search moves, what is waiting on you, three numbers and the newest
strong matches. It reads only."""

import unittest

from jsa.web.routes_today import next_step
from tests.test_web_documents import Sandbox, make_client


def counts(**kw):
    base = {"replies": 0, "drafts": 0, "ready": 0, "due": 0, "undrafted": 0,
            "strong": 0, "saved": 0, "applied": 0}
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
        self.assertEqual(next_step(counts(drafts=1))["href"], "/review")
        self.assertEqual(next_step(counts(replies=1))["href"], "/replies")


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
        self.assertIn('aria-current="page"', page)

    def test_it_writes_nothing(self):
        self.client.get("/today")
        con = self.box.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM application_events").fetchone()[0], 0)
        con.close()

    def test_every_page_has_the_sidebar(self):
        for url in ("/", "/today", "/pipeline", "/review", "/replies", "/add", "/companies", "/turbo"):
            with self.subTest(url=url):
                self.assertIn('<nav class="side" aria-label="Main">', self.client.get(url).text)


if __name__ == "__main__":
    unittest.main()
