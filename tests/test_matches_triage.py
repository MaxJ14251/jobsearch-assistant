"""Save and "Not for me" on the Matches cards (found 2026-10-27: 0 passes
ever, so "unreviewed" only grew and learning from swipes never started).
Both reuse existing calls: the job page's save, Turbo's pass and unpass.
Neither queues a draft: no model call from Matches."""

import re
import unittest

from fastapi.testclient import TestClient

from jsa import web
from tests.test_turbo import BASE, PROFILE, Tracker


class TestTriageOnMatches(Tracker):
    def setUp(self):
        super().setUp()
        self.app = web.create_app(db_path=self.path, output_dir=self.tmp / "output",
                                  profile_loader=lambda: PROFILE)
        self.client = TestClient(self.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.app.state.csrf_token

    def listed(self, query="anywhere=1"):
        page = self.client.get(f"/?{query}").text
        return [int(x) for x in re.findall(r'<span class="title"><a class="plain" '
                                           r'href="/job/(\d+)"', page)]

    def post(self, url, **data):
        return self.client.post(url, data={"csrf": self.csrf, **data},
                                follow_redirects=False)

    def test_each_new_card_has_save_and_not_for_me(self):
        page = self.client.get("/?anywhere=1").text
        for job in (1, 3, 4):
            self.assertIn(f'action="/job/{job}/save"', page)
        self.assertIn('action="/turbo/pass"', page)
        self.assertIn("Not for me", page)

    def test_save_returns_to_matches_with_the_filters_and_queues_nothing(self):
        r = self.post("/job/3/save", back="anywhere=1", page="matches", next="4")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/?anywhere=1&"))
        self.assertTrue(r.headers["location"].endswith("#triage-note"))
        self.assertIn("at=4", r.headers["location"])
        self.assertEqual(self.con().execute(
            "SELECT status FROM applications WHERE job_id = 3").fetchone()[0], "saved")
        self.assertEqual(self.queue(), [])                    # no drafts, no model call
        page = self.client.get(r.headers["location"]).text
        self.assertIn("Saved", page)
        self.assertNotIn('action="/job/3/save"', page)       # it is yours now, no buttons

    def test_not_for_me_hides_the_group_and_undo_brings_it_back(self):
        r = self.post("/turbo/pass", job_id=1, back="anywhere=1", page="matches", next="3")
        self.assertTrue(r.headers["location"].startswith("/?anywhere=1&"))
        self.assertNotIn(1, self.listed())
        self.assertNotIn(2, self.listed())                    # its copy goes too
        page = self.client.get(r.headers["location"]).text
        self.assertIn('action="/turbo/unpass"', page)        # undo, right there
        self.assertIn("at=3", r.headers["location"])         # shown above the next card
        self.assertLess(page.index('id="triage-note"'), page.index('id="job-3"'))
        r = self.post("/turbo/unpass", job_id=1, back="anywhere=1", page="matches")
        self.assertTrue(r.headers["location"].startswith("/?anywhere=1&"))
        self.assertIn(1, self.listed())
        self.assertEqual(self.queue(), [])

    def test_turbo_still_returns_to_turbo(self):
        r = self.post("/turbo/pass", job_id=1, back="anywhere=1")
        self.assertTrue(r.headers["location"].startswith("/turbo?anywhere=1&"))

    def test_the_page_says_how_far_learning_is(self):
        self.post("/turbo/pass", job_id=1, back="", page="matches")
        self.assertIn("starts after 30 (1 so far)", self.client.get("/?anywhere=1").text)

    def test_the_buttons_need_the_token(self):
        self.assertEqual(self.client.post("/job/3/save",
                                          data={"page": "matches"}).status_code, 403)
        self.assertEqual(self.client.post("/turbo/pass",
                                          data={"job_id": 3, "page": "matches"}).status_code, 403)

    def test_no_route_from_matches_calls_interested(self):
        page = self.client.get("/?anywhere=1").text
        self.assertNotIn("/turbo/interested", page)



class TestNewSinceTheLastRun(TestTriageOnMatches):
    """The banner's "N new match(es)" opened Matches in its usual order; the
    Newest sort reordered only the 60 cards already shown. Now it opens
    exactly the postings found since the last daily run, cards and map."""

    SINCE = "2026-10-20T07:00:00Z"

    def setUp(self):
        super().setUp()
        con = self.con()
        con.execute("UPDATE jobs SET discovered_at = '2026-10-01T07:00:00Z' WHERE id IN (1, 2)")
        con.execute("UPDATE jobs SET discovered_at = '2026-10-21T07:00:00Z' WHERE id IN (3, 4)")
        con.execute("INSERT INTO daily_runs (started_at, finished_at, ok, steps_json, "
                    "summary_json) VALUES ('2026-10-27T07:00:00Z', '2026-10-27T07:20:00Z', "
                    "1, '[]', ?)", ('{"since": "%s", "new_matches": 2}' % self.SINCE,))
        con.commit()

    def test_since_filters_the_cards_and_the_map(self):
        self.assertEqual(sorted(self.listed(f"anywhere=1&since={self.SINCE}")), [3, 4])
        page = self.client.get(f"/?anywhere=1&since={self.SINCE}").text
        self.assertIn("2 unreviewed found since 2026-10-20", page)
        self.assertEqual(sorted(self.listed()), [1, 3, 4])           # without it, all

    def test_the_banner_link_opens_exactly_those(self):
        page = self.client.get("/pipeline").text
        link = re.search(r'<a class="plain" href="([^"]+)">2 new match', page).group(1)
        self.assertIn("since=", link)
        self.assertEqual(sorted(self.listed(link.split("?", 1)[1].replace("&amp;", "&"))),
                         [3, 4])

    def test_a_malformed_since_is_ignored(self):
        self.assertEqual(sorted(self.listed("anywhere=1&since=yesterday")), [1, 3, 4])

    def test_save_keeps_the_since_filter(self):
        page = self.client.get(f"/?anywhere=1&since={self.SINCE}").text
        self.assertIn('name="back" value="anywhere=1&amp;since=', page)


if __name__ == "__main__":
    unittest.main()
