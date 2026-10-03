"""Suggest interview prep at an interview stage (Plan 14).

The tool suggests; it never runs prep and never sets a next action on its
own. The dashboard button is the person asking, like Tailor.
"""

import copy
import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import unquote_plus

from fastapi.testclient import TestClient

from jsa import approvals, cli, db, prep, web
from tests.test_prep_cmd import PROFILE, fake_questions

BASE = "http://127.0.0.1:8765"


class Tracker(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute("INSERT INTO jobs (id,company_id,title,url,description) VALUES "
                    "(1,1,'Support Engineer','https://acme.test/1',?)",
                    (" ".join(["Python support for customers."] * 30),))
        self.app_id, _ = approvals.save_application(con, 1)
        approvals.set_stage(con, 1, "applied")
        con.commit()
        con.close()
        real = db.connect
        patch = mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path))
        patch.start()
        self.addCleanup(patch.stop)
        self.real = real

    def con(self):
        con = self.real(self.path)
        self.addCleanup(con.close)
        return con

    def reply(self, kind, stage, mid):
        con = self.con()
        cur = con.execute(
            "INSERT INTO inbox_replies (message_id,application_id,received_at,subject,"
            "kind,suggested_stage) VALUES (?,?,?,?,?,?)",
            (mid, self.app_id, "2026-10-02T10:00:00Z", "Acme: next steps", kind, stage))
        con.commit()
        return cur.lastrowid

    def dashboard(self, profile=PROFILE):
        app = web.create_app(db_path=self.path, profile_loader=lambda: profile)
        client = TestClient(app, base_url=BASE)
        self.addCleanup(client.close)
        return client, app.state.csrf_token

    def untouched(self):
        con = self.con()
        preps = con.execute("SELECT COUNT(*) FROM interview_prep").fetchone()[0]
        nxt = con.execute("SELECT next_action, next_action_due FROM applications "
                          "WHERE id = ?", (self.app_id,)).fetchone()
        self.assertEqual(preps, 0, "a prep row was created without being asked")
        self.assertEqual(tuple(nxt), (None, None), "the tool set a next action")


class TestSuggestion(Tracker):
    def test_an_interview_stage_without_prep_names_the_command(self):
        self.assertEqual(prep.suggestion(self.con(), self.app_id, "technical"),
                         f"Interview stage: draft prep with `jsa prep {self.app_id} "
                         "--round technical`")

    def test_existing_prep_for_the_round_is_pointed_to(self):
        con = self.con()
        con.execute("INSERT INTO interview_prep (id,application_id,round) VALUES (7,?,?)",
                    (self.app_id, "phone_screen"))
        self.assertEqual(prep.suggestion(con, self.app_id, "phone_screen"),
                         "Prep for this round exists: `jsa prep-show 7`")
        self.assertIn("--round onsite", prep.suggestion(con, self.app_id, "onsite"))

    def test_other_stages_get_nothing(self):
        for stage in ("offer", "rejected", "applied", "withdrawn"):
            self.assertIsNone(prep.suggestion(self.con(), self.app_id, stage))


class TestWhereStagesChange(Tracker):
    def test_cli_confirm_of_an_interview_reply_suggests_and_runs_nothing(self):
        rid = self.reply("interview", "phone_screen", "<m1>")
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_inbox(Namespace(action="confirm", reply_id=rid, stage=None))
        self.assertIn(f"`jsa prep {self.app_id} --round phone_screen`", out.getvalue())
        self.untouched()

    def test_cli_confirm_of_a_rejection_suggests_nothing(self):
        rid = self.reply("rejection", "rejected", "<m2>")
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_inbox(Namespace(action="confirm", reply_id=rid, stage=None))
        self.assertNotIn("prep", out.getvalue())

    def test_cli_status_suggests(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_status(Namespace(job_id=1, stage="technical", note=None))
        self.assertIn("--round technical", out.getvalue())
        self.untouched()

    def test_dashboard_confirm_and_stage_carry_the_suggestion(self):
        client, token = self.dashboard()
        rid = self.reply("interview", "phone_screen", "<m3>")
        r = client.post(f"/inbox/{rid}/confirm", data={"csrf": token, "stage": "phone_screen"},
                        follow_redirects=False)
        self.assertIn("--round phone_screen", unquote_plus(r.headers["location"]))
        r = client.post("/job/1/stage", data={"csrf": token, "stage": "onsite"},
                        follow_redirects=False)
        self.assertIn("--round onsite", unquote_plus(r.headers["location"]))
        self.untouched()


class TestTheButton(Tracker):
    def test_shown_only_at_an_interview_stage(self):
        client, token = self.dashboard()
        self.assertNotIn("Draft interview prep", client.get("/job/1").text)
        client.post("/job/1/stage", data={"csrf": token, "stage": "technical"})
        page = client.get("/job/1").text
        self.assertIn("Draft interview prep", page)
        self.assertIn('<option value="technical" selected>', page)

    def test_pressing_it_drafts_prep_and_opens_it(self):
        client, token = self.dashboard()
        client.post("/job/1/stage", data={"csrf": token, "stage": "phone_screen"})
        with mock.patch("jsa.prep.llm.complete_json", side_effect=fake_questions):
            r = client.post("/job/1/prep", data={"csrf": token, "round": "phone_screen"},
                            follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertRegex(r.headers["location"], r"^/prep/\d+$")
        self.assertEqual(self.con().execute(
            "SELECT round FROM interview_prep").fetchone()[0], "phone_screen")

    def test_without_the_token_it_is_refused(self):
        client, _ = self.dashboard()
        with mock.patch("jsa.prep.llm.complete_json") as call:
            r = client.post("/job/1/prep", data={"round": "phone_screen"})
        self.assertEqual(r.status_code, 403)
        call.assert_not_called()

    def test_an_undecided_profile_comes_back_with_the_message_and_no_call(self):
        undecided = copy.deepcopy(PROFILE)
        undecided["job_search_preferences"]["work_authorization"] = None
        client, token = self.dashboard(undecided)
        with mock.patch("jsa.prep.llm.complete_json") as call:
            r = client.post("/job/1/prep", data={"csrf": token, "round": "technical"},
                            follow_redirects=False)
        call.assert_not_called()
        self.assertIn("work_authorization", unquote_plus(r.headers["location"]))
        self.untouched()


if __name__ == "__main__":
    unittest.main()
