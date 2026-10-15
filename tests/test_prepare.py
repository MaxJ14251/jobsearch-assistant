"""The panel's "Add, save and draft a resume" button (jsa/prepare.py).

One press adds the job from the board's API if needed, saves it (the
person's act) and drafts one resume, checked like any other. It never
approves, never records an application, and keeps a daily limit.
"""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jsa import intake, prepare
from tests.test_ext import GREENHOUSE, Ext
from tests.test_extension import EXT, body_of, code

UNKNOWN = "https://job-boards.greenhouse.io/riverton/jobs/555"
UNSAVED = "https://job-boards.greenhouse.io/riverton/jobs/999"


class Prepare(Ext):
    def setUp(self):
        super().setUp()
        self.drafts = []

        def fake_draft(con, job_id, kind, *, force, profile):
            self.drafts.append((job_id, kind, force))
            return SimpleNamespace(version=1)

        patches = [mock.patch("jsa.drafting.draft_document", side_effect=fake_draft),
                   mock.patch("jsa.turbo.drafting_blocker", return_value=None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def press(self, url, confirm="draft", key=True):
        return self.post("/ext/prepare", {"url": url, "confirm": confirm}, key=key)

    def status(self, job_id):
        row = self.con.execute("SELECT status FROM applications WHERE job_id = ?",
                               (job_id,)).fetchone()
        return row["status"] if row else None

    def give_text(self, job_id):
        self.con.execute("UPDATE jobs SET description = ? WHERE id = ?",
                         ("Help customers with the product. " * 60, job_id))
        self.con.commit()


class TestTheButton(Prepare):
    def test_an_unsaved_job_is_saved_and_one_resume_drafted(self):
        self.give_text(2)
        body = self.press(UNSAVED).json()
        self.assertEqual(body["state"], "drafted", body)
        self.assertEqual(self.drafts, [(2, "resume", False)])
        self.assertEqual(self.status(2), "saved")
        self.assertIn("Review", body["message"])
        self.assertTrue(body["review_url"])
        actor = self.con.execute("SELECT e.actor FROM application_events e JOIN applications a "
                                 "ON a.id = e.application_id WHERE a.job_id = 2 "
                                 "AND e.to_status = 'saved'").fetchone()["actor"]
        self.assertEqual(actor, "human")

    def test_an_unknown_job_is_added_from_the_board_first(self):
        def fake_add(con, url, prefs, company=None):
            con.execute("INSERT INTO jobs (id,company_id,external_id,title,url,description) "
                        "VALUES (3,1,'555','Field Engineer',?,?)",
                        (url, "Install and support systems on site. " * 60))
            return intake.Added(3, True, "Field Engineer", "Riverton Grid", 0.5)

        with mock.patch("jsa.intake.add_link", side_effect=fake_add) as add:
            body = self.press(UNKNOWN).json()
        self.assertEqual(add.call_args.args[1], UNKNOWN)
        self.assertEqual(body["state"], "drafted", body)
        self.assertTrue(body["message"].startswith("Added and saved"))
        self.assertEqual(self.drafts, [(3, "resume", False)])

    def test_a_board_it_cannot_read_adds_nothing(self):
        with mock.patch("jsa.intake.add_link",
                        side_effect=intake.IntakeError("the board said 404")):
            body = self.press(UNKNOWN).json()
        self.assertEqual(body["state"], "failed")
        self.assertEqual(self.drafts, [])

    def test_nothing_without_the_buttons_confirm_or_the_key(self):
        self.give_text(2)
        self.assertEqual(self.press(UNSAVED, confirm="").json()["state"], "failed")
        self.assertEqual(self.press(UNSAVED, key=False).status_code, 401)
        r = self.client.post("/ext/prepare", data={
            "csrf": self.app.state.csrf_token, "url": UNSAVED, "confirm": "draft"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.drafts, [])
        self.assertIsNone(self.status(2))

    def test_other_pages_are_refused(self):
        for url in ("https://www.linkedin.com/jobs/view/1", "https://example.com/apply", ""):
            with self.subTest(url=url):
                self.assertEqual(self.press(url).json()["state"], "unsupported")
        self.assertEqual(self.drafts, [])


class TestWhenItDoesNotDraft(Prepare):
    def test_a_resume_that_exists_is_not_drafted_again(self):
        self.doc(approve=False)
        body = self.press(GREENHOUSE).json()
        self.assertEqual(body["state"], "exists")
        self.assertIn("v1", body["message"])
        self.assertEqual(self.drafts, [])

    def test_a_posting_with_almost_no_text_is_saved_only(self):
        body = self.press(UNSAVED).json()          # job 2 has no description
        self.assertEqual(body["state"], "saved")
        self.assertIn("almost no text", body["message"])
        self.assertEqual(self.status(2), "saved")
        self.assertEqual(self.drafts, [])

    def test_undecided_preferences_or_no_key_save_only(self):
        self.give_text(2)
        with mock.patch("jsa.turbo.drafting_blocker", return_value="no model API key is set"):
            body = self.press(UNSAVED).json()
        self.assertEqual(body["state"], "saved")
        self.assertIn("no model API key", body["message"])
        self.assertEqual(self.drafts, [])

    def test_the_daily_limit(self):
        self.give_text(2)
        self.profile["extension"] = {"daily_drafts": 1}
        self.con.execute("INSERT INTO ext_drafts (job_id) VALUES (1)")
        self.con.commit()
        body = self.press(UNSAVED).json()
        self.assertEqual(body["state"], "saved")
        self.assertIn("limit of 1", body["message"])
        self.assertEqual(self.drafts, [])
        self.assertEqual(prepare.left_today(self.con, self.profile), 0)

    def test_an_application_already_sent_is_left_alone(self):
        self.con.execute("UPDATE applications SET status = 'applied' WHERE job_id = 1")
        self.con.commit()
        body = self.press(GREENHOUSE).json()
        self.assertEqual(body["state"], "past")
        self.assertEqual(self.drafts, [])

    def test_a_failed_draft_still_leaves_it_saved(self):
        from jsa.drafting import DraftError
        self.give_text(2)
        with mock.patch("jsa.drafting.draft_document",
                        side_effect=DraftError("invented a skill", refused=True)):
            body = self.press(UNSAVED).json()
        self.assertEqual(body["state"], "saved")
        self.assertIn("refused by a safety check", body["message"])
        self.assertEqual(self.status(2), "saved")


class TestItNeverApproves(Prepare):
    def test_no_approval_and_no_application_recorded(self):
        self.give_text(2)
        self.press(UNSAVED)
        decided = self.con.execute("SELECT COUNT(*) FROM approvals WHERE decided_by IS NOT NULL "
                                   "AND decided_at IS NOT NULL").fetchone()[0]
        self.assertEqual(decided, 0)
        self.assertNotIn(self.status(2), ("applied", "phone_screen", "rejected"))

    def test_the_module_reaches_no_approval_or_submission(self):
        source = Path(prepare.__file__).read_text(encoding="utf-8")
        for name in ("approvals.approve", "approvals.reject", "mark_applied", "set_stage",
                     "decided_by", "enrich("):
            self.assertNotIn(name, source)

    def test_the_drafts_left_count_is_offered(self):
        body = self.get("/ext/fill", url=UNSAVED).json()
        self.assertEqual(body["state"], "not_saved")
        self.assertEqual(body["drafts_left"], prepare.EXT_DRAFT_DAILY)


class TestOnlyFromTheButton(unittest.TestCase):
    """The panel sends "prepare" only from its own button's click."""

    def setUp(self):
        self.source = code(EXT / "content.js")

    def test_the_prepare_message_is_sent_only_from_its_button(self):
        self.assertEqual(self.source.count('type: "prepare"'), 1)
        self.assertIn('type: "prepare"', body_of(self.source, "prepareJob"))
        wired = re.findall(r"button\([^;]*?,\s*prepareJob\)", self.source)
        calls = re.findall(r"(?<![.\w])prepareJob\s*\(", self.source)
        self.assertEqual(len(wired), 1)
        self.assertEqual(len(calls), 1)            # its definition only

    def test_the_background_sends_only_the_page_address_and_the_confirm(self):
        background = code(EXT / "background.js")
        body = body_of(background, "prepare")
        self.assertIn("sender && sender.url", body)
        self.assertIn('message.confirm === "draft"', body)
        self.assertIn('"/ext/prepare"', body)


if __name__ == "__main__":
    unittest.main()
