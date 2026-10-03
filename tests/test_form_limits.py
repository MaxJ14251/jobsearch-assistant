"""A size limit on every dashboard form (Plan 15).

Every POST states its length and stays under a limit, checked before the
body is read. Pasted postings have a maximum, enforced in intake for the CLI
and the dashboard alike.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from jsa import db, intake, web
from jsa.config import Preferences
from jsa.llm import LLMError

BASE = "http://127.0.0.1:8765"
PREFS_PROFILE = {"job_search_preferences": {"target_titles": ["Support Engineer"],
                                            "locations": ["Remote (US)"]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute("INSERT INTO jobs (id,company_id,title,url) "
                    "VALUES (1,1,'Support Engineer','https://acme.test/1')")
        con.commit()
        con.close()
        self.app = web.create_app(db_path=self.path, output_dir=self.path.parent / "out",
                                  profile_loader=lambda: PREFS_PROFILE)
        self.client = TestClient(self.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.app.state.csrf_token


class TestTheGuard(Base):
    def test_an_oversized_form_is_refused_before_the_route_runs(self):
        with mock.patch("jsa.web.MAX_FORM_BYTES", 500), \
             mock.patch("jsa.intake.add_pasted") as route:
            r = self.client.post("/add/paste", data={
                "csrf": self.csrf, "company": "Acme", "title": "x", "text": "y" * 600})
        self.assertEqual(r.status_code, 413)
        route.assert_not_called()

    def test_no_length_is_refused(self):
        def chunks():
            yield f"csrf={self.csrf}".encode()
        r = self.client.post("/job/1/save", content=chunks(),
                             headers={"content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status_code, 411)

    def test_a_lying_length_does_not_get_past(self):
        """A server that doesn't enforce Content-Length (h11 does) still can't
        hand the route more than the limit: the guard measures what it read."""
        sent = []

        async def receive():
            if not sent:
                sent.append(1)
                return {"type": "http.request", "body": b"csrf=x&t=" + b"y" * 2000,
                        "more_body": False}
            return {"type": "http.disconnect"}

        out = []

        async def send(message):
            out.append(message)

        scope = {"type": "http", "method": "POST", "path": "/job/1/save",
                 "raw_path": b"/job/1/save", "query_string": b"", "root_path": "",
                 "scheme": "http", "server": ("127.0.0.1", 8765),
                 "client": ("127.0.0.1", 5000), "http_version": "1.1",
                 "headers": [(b"host", b"127.0.0.1:8765"),
                             (b"content-type", b"application/x-www-form-urlencoded"),
                             (b"content-length", b"10")]}
        with mock.patch("jsa.web.MAX_FORM_BYTES", 500):
            asyncio.run(self.app(scope, receive, send))
        start = next(m for m in out if m["type"] == "http.response.start")
        self.assertEqual(start["status"], 413)

    def test_every_form_route_still_passes_with_a_realistic_body(self):
        posting = "Support engineer. " * 600          # ~11k chars, the 99th percentile
        bodies = {
            "/job/1/save": {}, "/job/1/tailor": {"kind": "resume"},
            "/job/1/fill": {"text": posting, "location": "Remote"},
            "/job/1/prep": {"round": "phone_screen"},
            "/add/link": {"url": "https://example.com/not-a-board"},
            "/add/paste": {"company": "Beta", "title": "Support", "text": posting},
            "/inbox/fetch": {}, "/inbox/1/confirm": {"stage": "applied"},
            "/inbox/1/dismiss": {}, "/job/1/stage": {"stage": "saved"},
            "/approve": {"approval_id": 99}, "/reject": {"approval_id": 99, "feedback": "x"},
        }
        routes = {r.path.replace("{job_id}", "1").replace("{reply_id}", "1")
                  for r in self.app.routes if "POST" in getattr(r, "methods", set())}
        routes.discard("/import")                       # multipart: tests/test_web_import.py
        self.assertEqual(routes, set(bodies), "a POST route is missing from this test")
        with mock.patch("jsa.llm.complete", side_effect=LLMError("no model in tests")), \
             mock.patch("jsa.inbox.settings", return_value=None), \
             mock.patch("jsa.intake.enrich", return_value=""):
            for url, body in bodies.items():
                with self.subTest(url=url):
                    r = self.client.post(url, data={"csrf": self.csrf, **body},
                                         follow_redirects=False)
                    self.assertNotIn(r.status_code, (403, 411, 413), r.text[:200])


class TestPastedPostings(Base):
    def test_intake_refuses_a_paste_over_the_maximum(self):
        prefs = Preferences.from_profile(PREFS_PROFILE)
        con = db.connect(self.path)
        self.addCleanup(con.close)
        huge = "word " * (intake.MAX_PASTED_CHARS // 5 + 10)
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.add_pasted(con, company="Acme", title="Support", text=huge, prefs=prefs)
        self.assertIn("longer than any real posting", str(ctx.exception))
        with self.assertRaises(intake.IntakeError):
            intake.fill(con, 1, huge, prefs)

    def test_the_dashboard_says_so_too(self):
        huge = "word " * (intake.MAX_PASTED_CHARS // 5 + 10)
        r = self.client.post("/add/paste", data={"csrf": self.csrf, "company": "Acme",
                                                 "title": "Support", "text": huge})
        self.assertIn("longer than any real posting", r.text)

    def test_the_textareas_and_feedback_carry_maxlength(self):
        page = self.client.get("/add").text
        self.assertIn(f'maxlength="{intake.MAX_PASTED_CHARS}"', page)
        from jsa import approvals
        con = db.connect(self.path)
        con.execute("INSERT INTO documents (id,job_id,kind,path,version) "
                    "VALUES (1,1,'resume','r.docx',1)")
        approvals.queue(con, "document", 1, "resume v1")
        con.commit()
        con.close()
        self.assertIn('maxlength="2000"', self.client.get("/review").text)

    def test_the_limits_leave_room_for_any_real_posting(self):
        self.assertGreaterEqual(intake.MAX_PASTED_CHARS, 5 * 23_227)
        self.assertGreaterEqual(web.MAX_FORM_BYTES, 3 * intake.MAX_PASTED_CHARS)


if __name__ == "__main__":
    unittest.main()
