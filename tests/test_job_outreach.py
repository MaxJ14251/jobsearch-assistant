"""Outreach from the job page (found 2026-10-27: 0 contacts and 0 outreach
ever; drafting existed only as `jsa outreach-draft`). Add a contact at the
company, draft a message on request (one paid call), and it waits in
Review. Nothing is sent: the dashboard has no route that could."""

import re
import unittest
from pathlib import Path
from unittest import mock

from jsa import outreach
from jsa.llm import LLMError
from jsa.tailor import FabricationError
from tests.test_web_documents import Sandbox, make_client


class TestOutreachOnTheJobPage(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.client, self.app = make_client(self.box.db, self.box.out, profile={"x": 1})
        self.csrf = self.app.state.csrf_token

    def post(self, url, **data):
        return self.client.post(url, data={"csrf": self.csrf, **data},
                                follow_redirects=False)

    def rows(self, sql):
        con = self.box.connect()
        try:
            return [tuple(r) for r in con.execute(sql)]
        finally:
            con.close()

    def add_dana(self):
        return self.post("/job/1/contact", name="Dana Reyes", title="Support Lead",
                         relationship="warm")

    def test_add_a_contact_at_this_jobs_company(self):
        r = self.add_dana()
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/job/1?"))
        self.assertEqual(self.rows("SELECT name, title, company_id, relationship FROM contacts"),
                         [("Dana Reyes", "Support Lead", 1, "warm")])
        page = self.client.get("/job/1").text
        self.assertIn("Dana Reyes", page)
        self.assertIn('action="/job/1/outreach"', page)

    def test_a_contact_needs_a_name_and_a_known_relationship(self):
        for data in ({"name": "  "}, {"name": "Dana", "relationship": "boss"}):
            with self.subTest(data=data):
                r = self.post("/job/1/contact", **data)
                self.assertIn("bad=1", r.headers["location"])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM contacts"), [(0,)])

    def test_drafting_queues_it_for_review_and_sends_nothing(self):
        self.add_dana()
        contact = self.rows("SELECT id FROM contacts")[0][0]

        def fake_draft(con, profile, *, contact_id, channel, purpose, job_id):
            con.execute("INSERT INTO outreach (contact_id, job_id, channel, purpose, "
                        "draft_body) VALUES (?,?,?,?,?)",
                        (contact_id, job_id, channel, purpose, "Hi Dana, ..."))
            return outreach.Draft(contact_id, job_id, channel, purpose, "Hi Dana, ...")

        with mock.patch("jsa.outreach.draft", side_effect=fake_draft) as called, \
             mock.patch("jsa.outreach.mark_sent") as sent:
            r = self.post("/job/1/outreach", contact_id=contact,
                          channel="linkedin_connect", purpose="referral_ask")
        self.assertEqual(called.call_args.kwargs["job_id"], 1)
        sent.assert_not_called()
        self.assertIn("Review", r.headers["location"].replace("+", " "))
        self.assertIn("Nothing+is+sent", r.headers["location"])
        page = self.client.get("/job/1").text
        self.assertIn("linkedin connect", page)
        self.assertIn("drafted", page)

    def test_a_refusal_or_a_model_failure_is_a_message(self):
        self.add_dana()
        contact = self.rows("SELECT id FROM contacts")[0][0]
        for exc in (FabricationError("Your profile does not have the material"),
                    LLMError("rate limited"), outreach.OutreachError("no contact")):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch("jsa.outreach.draft", side_effect=exc):
                    r = self.post("/job/1/outreach", contact_id=contact,
                                  channel="linkedin_connect", purpose="referral_ask")
                self.assertEqual(r.status_code, 303)
                self.assertIn("bad=1", r.headers["location"])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM outreach"), [(0,)])

    def test_an_unknown_channel_or_purpose_never_reaches_the_model(self):
        self.add_dana()
        contact = self.rows("SELECT id FROM contacts")[0][0]
        with mock.patch("jsa.outreach.draft") as called:
            for data in ({"channel": "sms", "purpose": "referral_ask"},
                         {"channel": "email", "purpose": "sell"}):
                r = self.post("/job/1/outreach", contact_id=contact, **data)
                self.assertIn("bad=1", r.headers["location"])
        called.assert_not_called()

    def test_the_page_says_what_drafting_costs_and_how_often_it_passes(self):
        self.add_dana()
        page = self.client.get("/job/1").text
        self.assertIn("one paid model call", page)
        self.assertIn("2 of 9", page)

    def test_both_routes_need_the_token(self):
        for url in ("/job/1/contact", "/job/1/outreach"):
            with self.subTest(url=url):
                r = self.client.post(url, data={"name": "x", "contact_id": 1})
                self.assertEqual(r.status_code, 403)


class TestNoRouteSends(unittest.TestCase):
    def test_the_dashboard_never_records_or_sends_outreach(self):
        web = Path(__file__).resolve().parent.parent / "jsa" / "web"
        for path in web.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bmark_sent\(|smtplib|send_message", text),
                              path.name)


if __name__ == "__main__":
    unittest.main()
