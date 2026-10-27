"""Follow-ups set from the Pipeline card (found 2026-10-27: 1 of 17
applications ever had a next action, because only `jsa next` set one).
Setting or clearing one is the person's act; the tool never does it."""

import re
import unittest
from datetime import date, timedelta
from pathlib import Path

from jsa import approvals
from tests.test_web_documents import Sandbox, make_client


class TestFollowUps(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        con = self.box.connect()
        for job_id, title in ((3, "Data Analyst"), (4, "Solutions Engineer")):
            con.execute("INSERT INTO jobs (id,company_id,title,url) VALUES (?,1,?,?)",
                        (job_id, title, f"https://x/{job_id}"))
            con.execute("INSERT INTO applications (job_id,status) VALUES (?, 'ready')",
                        (job_id,))
        con.commit()
        con.close()
        self.client, self.app = make_client(self.box.db, self.box.out, profile={"x": 1})
        self.csrf = self.app.state.csrf_token

    def post(self, job_id, **data):
        return self.client.post(f"/job/{job_id}/next", data={"csrf": self.csrf, **data},
                                follow_redirects=False)

    def next_of(self, job_id):
        con = self.box.connect()
        try:
            return tuple(con.execute("SELECT next_action, next_action_due FROM applications "
                                     "WHERE job_id = ?", (job_id,)).fetchone())
        finally:
            con.close()

    def test_set_from_the_card_and_back_to_it(self):
        r = self.post(1, action="Email the recruiter", due="2026-11-03")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].endswith("#app-1"))
        self.assertEqual(self.next_of(1), ("Email the recruiter", "2026-11-03"))
        page = self.client.get("/pipeline").text
        self.assertIn("Email the recruiter", page)
        self.assertIn('id="app-1"', page)
        self.assertIn('action="/job/1/next"', page)

    def test_clear_removes_both(self):
        self.post(1, action="Email the recruiter", due="2026-11-03")
        self.post(1, clear="1")
        self.assertEqual(self.next_of(1), (None, None))

    def test_empty_text_or_a_bad_date_writes_nothing(self):
        for data in ({"action": "  ", "due": "2026-11-03"},
                     {"action": "Call", "due": "2026-02-30"}):
            with self.subTest(data=data):
                r = self.post(1, **data)
                self.assertIn("bad=1", r.headers["location"])
                self.assertEqual(self.next_of(1), (None, None))

    def test_due_and_overdue_cards_lead_their_stage(self):
        today = date.today()
        self.post(4, action="Overdue thing", due=(today - timedelta(days=2)).isoformat())
        self.post(3, action="Soon thing", due=(today + timedelta(days=1)).isoformat())
        page = self.client.get("/pipeline").text
        order = [int(x) for x in re.findall(r'id="app-(\d+)"', page)]
        self.assertEqual(order[:3], [4, 3, 1])
        self.assertIn('id="first-due"', page)
        self.assertLess(page.index('id="first-due"'), page.index('id="app-3"'))

    def test_it_needs_the_token(self):
        r = self.client.post("/job/1/next", data={"action": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.next_of(1), (None, None))

    def test_clear_next_action_on_its_own(self):
        con = self.box.connect()
        self.addCleanup(con.close)
        approvals.set_next_action(con, 1, "call", due="2026-11-01")
        approvals.clear_next_action(con, 1)
        self.assertEqual(tuple(con.execute(
            "SELECT next_action, next_action_due FROM applications WHERE job_id = 1"
        ).fetchone()), (None, None))


class TestOnlyThePersonSetsAFollowUp(unittest.TestCase):
    """Suggest, never decide: only the CLI's `jsa next` and the dashboard's
    follow-up form call these, never drafting, Turbo, the inbox or daily."""

    def test_the_only_callers(self):
        root = Path(__file__).resolve().parent.parent / "jsa"
        callers = set()
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\b(set|clear)_next_action\(", text) and path.name != "approvals.py":
                callers.add(path.relative_to(root).as_posix())
        self.assertEqual(callers, {"cli.py", "web/routes_pipeline.py"})


if __name__ == "__main__":
    unittest.main()
