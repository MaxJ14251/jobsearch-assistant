"""Stages after "ready", next actions, and what is due.

Every application in the real tracker sat at 'ready' because only two stages
were reachable. These tests walk one the whole way, on a throwaway tracker.
"""

import re
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from jsa import approvals, db
from jsa.config import ROOT


def sandbox(jobs: int = 1) -> Path:
    path = Path(tempfile.mkdtemp()) / "t.db"
    db.init_db(path)
    con = db.connect(path)
    con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
    for n in range(1, jobs + 1):
        con.execute("INSERT INTO jobs (id,company_id,title,url) "
                    f"VALUES ({n},1,'Engineer {n}','https://acme.test/{n}')")
    con.commit()
    con.close()
    return path


class TestTheStageVocabulary(unittest.TestCase):
    def test_it_matches_the_schema(self):
        """Follows tests/test_enrich.py: parse the CHECK, do not trust prose.
        The seniority list drifted from its CHECK once and killed a run."""
        sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
        block = re.search(r"status\s+TEXT NOT NULL DEFAULT 'saved'\s*"
                          r"CHECK \(status IN \((.*?)\)\)", sql, re.S).group(1)
        in_schema = set(re.findall(r"'(\w+)'", block))
        self.assertEqual(set(approvals.STAGES), in_schema)

    def test_closed_stages_are_real_stages(self):
        self.assertLessEqual(approvals.CLOSED, set(approvals.STAGES))


class TestWalkingAnApplication(unittest.TestCase):
    def setUp(self):
        self.path = sandbox()
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        approvals.save_application(self.con, 1)
        self.con.commit()

    def events(self):
        return [(r["from_status"], r["to_status"], r["actor"])
                for r in self.con.execute(
                    "SELECT * FROM application_events ORDER BY id")]

    def status(self):
        return self.con.execute(
            "SELECT status FROM applications WHERE job_id = 1").fetchone()["status"]

    def test_a_saved_to_offer_and_every_step_is_human(self):
        """Scenario a."""
        for stage in ("ready", "applied", "phone_screen", "onsite", "offer"):
            approvals.set_stage(self.con, 1, stage)
        self.assertEqual(self.status(), "offer")
        self.assertEqual(
            [(e[0], e[1]) for e in self.events()],
            # The first row reads saved -> saved: the application row exists
            # (status defaults to 'saved') before save_application records it.
            [("saved", "saved"), ("saved", "ready"), ("ready", "applied"),
             ("applied", "phone_screen"), ("phone_screen", "onsite"),
             ("onsite", "offer")])
        self.assertTrue(all(actor == "human" for _, _, actor in self.events()))

    def test_last_activity_comes_from_the_trigger_not_app_code(self):
        approvals.set_stage(self.con, 1, "phone_screen")
        row = self.con.execute(
            "SELECT a.last_activity_at, "
            "       (SELECT MAX(occurred_at) FROM application_events) AS last "
            "FROM applications a").fetchone()
        self.assertIsNotNone(row["last_activity_at"])
        self.assertEqual(row["last_activity_at"], row["last"])
        import inspect
        self.assertNotIn("last_activity_at", inspect.getsource(approvals.set_stage))

    def test_b_a_rejected_application_leaves_the_pipeline_and_keeps_history(self):
        """Scenario b."""
        approvals.set_stage(self.con, 1, "applied")
        approvals.set_stage(self.con, 1, "rejected", note="no reply after onsite")
        live = self.con.execute("SELECT COUNT(*) FROM v_pipeline").fetchone()[0]
        self.assertEqual(live, 0)
        notes = [r["note"] for r in self.con.execute(
            "SELECT note FROM application_events WHERE note IS NOT NULL")]
        self.assertIn("no reply after onsite", notes)
        self.assertEqual(self.status(), "rejected")

    def test_c_an_unsaved_job_names_the_command_to_run(self):
        """Scenario c."""
        con = db.connect(sandbox(jobs=2))
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.set_stage(con, 2, "offer")
        self.assertIn("jsa save 2", str(ctx.exception))
        con.close()

    def test_d_an_unknown_stage_is_refused_and_lists_the_real_ones(self):
        """Scenario d."""
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.set_stage(self.con, 1, "promoted")
        message = str(ctx.exception)
        self.assertIn("phone_screen", message)
        self.assertIn("ghosted", message)
        self.assertEqual(self.status(), "saved", "the bad stage was stored")

    def test_f_applying_without_an_approved_document_still_records(self):
        """Scenario f. ADR 0003 decision 4: the gate stops the agent, not you."""
        _, approved = approvals.mark_applied(self.con, 1)
        self.assertFalse(approved)
        note = self.con.execute(
            "SELECT note FROM application_events ORDER BY id DESC LIMIT 1"
        ).fetchone()["note"]
        self.assertIn("no approved document", note)
        self.assertEqual(self.status(), "applied")
        self.assertIsNotNone(self.con.execute(
            "SELECT applied_at FROM applications").fetchone()["applied_at"])

    def test_the_stage_note_survives_the_applied_path(self):
        approvals.set_stage(self.con, 1, "applied", note="submitted on the site")
        note = self.con.execute(
            "SELECT note FROM application_events ORDER BY id DESC LIMIT 1"
        ).fetchone()["note"]
        self.assertIn("submitted on the site", note)
        self.assertIn("no approved document", note)


class TestNextActions(unittest.TestCase):
    def setUp(self):
        self.path = sandbox(jobs=4)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        for job in range(1, 5):
            approvals.save_application(self.con, job)
        self.con.commit()

    def test_it_records_the_action_and_the_date(self):
        approvals.set_next_action(self.con, 1, "  follow up  ", due="2026-10-01")
        row = self.con.execute(
            "SELECT next_action, next_action_due FROM applications "
            "WHERE job_id = 1").fetchone()
        self.assertEqual(tuple(row), ("follow up", "2026-10-01"))

    def test_an_intention_is_not_an_event(self):
        """Setting a next action must not move the application's status."""
        before = self.con.execute(
            "SELECT COUNT(*) FROM application_events").fetchone()[0]
        approvals.set_next_action(self.con, 1, "call back")
        after = self.con.execute(
            "SELECT COUNT(*) FROM application_events").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(self.con.execute(
            "SELECT status FROM applications WHERE job_id=1").fetchone()[0], "saved")

    def test_a_bad_date_is_refused_rather_than_stored(self):
        for bad in ("next tuesday", "2026-13-01", "01/10/2026", "2026-10"):
            with self.subTest(due=bad):
                with self.assertRaises(approvals.ApprovalError):
                    approvals.set_next_action(self.con, 1, "call", due=bad)
        self.assertIsNone(self.con.execute(
            "SELECT next_action_due FROM applications WHERE job_id=1").fetchone()[0])

    def test_an_empty_action_is_refused(self):
        with self.assertRaises(approvals.ApprovalError):
            approvals.set_next_action(self.con, 1, "   ")

    def test_e_due_lists_overdue_first_then_soonest(self):
        """Scenario e."""
        today = date(2026, 10, 1)
        plan = {1: today - timedelta(days=3), 2: today, 3: today + timedelta(days=5),
                4: None}
        for job, due in plan.items():
            approvals.set_next_action(
                self.con, job, f"action {job}",
                due=due.isoformat() if due else None)
        # Job 4 has no date and has been quiet since it was saved. Noon UTC:
        # quiet days are counted in LOCAL days, and midnight UTC is the
        # previous evening west of Greenwich.
        self.con.execute(
            "UPDATE applications SET last_activity_at = ? WHERE job_id = 4",
            ((today - timedelta(days=40)).isoformat() + "T12:00:00Z",))

        items = approvals.due_items(self.con, days=7, today=today)
        self.assertEqual([i.job_id for i in items], [1, 2, 3, 4])
        self.assertTrue(items[0].overdue)
        self.assertEqual(items[0].days_out, -3)
        self.assertEqual(items[1].days_out, 0)
        self.assertFalse(items[1].overdue)
        self.assertEqual(items[3].quiet_days, 40)

    def test_something_far_out_is_not_due_yet(self):
        today = date(2026, 10, 1)
        approvals.set_next_action(self.con, 1, "later",
                                  due=(today + timedelta(days=30)).isoformat())
        self.assertEqual(approvals.due_items(self.con, days=7, today=today), [])

    def test_a_closed_application_never_appears(self):
        today = date(2026, 10, 1)
        approvals.set_next_action(self.con, 1, "chase",
                                  due=(today - timedelta(days=2)).isoformat())
        approvals.set_stage(self.con, 1, "withdrawn")
        self.assertEqual(approvals.due_items(self.con, days=7, today=today), [])

    def test_quiet_is_a_report_not_a_verdict(self):
        """The tool must not decide a company ghosted you."""
        today = date(2026, 10, 1)
        self.con.execute(
            "UPDATE applications SET last_activity_at = ? WHERE job_id = 1",
            ((today - timedelta(days=90)).isoformat() + "T00:00:00Z",))
        items = approvals.due_items(self.con, days=7, today=today)
        self.assertEqual([i.job_id for i in items], [1])
        self.assertEqual(self.con.execute(
            "SELECT status FROM applications WHERE job_id=1").fetchone()[0], "saved")


class TestTheCommands(unittest.TestCase):
    def setUp(self):
        self.path = sandbox()
        real = db.connect
        patch = mock.patch("jsa.db.connect",
                           side_effect=lambda *a, **k: real(self.path))
        patch.start()
        self.addCleanup(patch.stop)
        self.real_connect = real
        con = real(self.path)
        approvals.save_application(con, 1)
        con.commit()
        con.close()

    def run_cmd(self, fn, **kw):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = fn(Namespace(**kw))
        return code, out.getvalue()

    def test_status_moves_and_says_what_changed(self):
        from jsa.cli import cmd_status
        code, out = self.run_cmd(cmd_status, job_id=1, stage="phone_screen",
                                 note=None)
        self.assertEqual(code, 0)
        self.assertIn("saved -> phone_screen", out)

    def test_status_on_a_closed_stage_says_it_leaves_the_pipeline(self):
        from jsa.cli import cmd_status
        _, out = self.run_cmd(cmd_status, job_id=1, stage="ghosted", note=None)
        self.assertIn("leaves the live pipeline", out)

    def test_an_unknown_stage_exits_1(self):
        from jsa.cli import cmd_status
        code, out = self.run_cmd(cmd_status, job_id=1, stage="promoted", note=None)
        self.assertEqual(code, 1)
        self.assertIn("is not a stage", out)

    def test_next_then_due(self):
        from jsa.cli import cmd_due, cmd_next
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        code, _ = self.run_cmd(cmd_next, job_id=1, action="email the recruiter",
                               due=yesterday)
        self.assertEqual(code, 0)
        code, out = self.run_cmd(cmd_due, days=7)
        self.assertEqual(code, 0)
        self.assertIn("OVERDUE by 1 day(s)", out)
        self.assertIn("email the recruiter", out)
        self.assertIn("changed anything", out)

    def test_due_with_nothing_pending(self):
        from jsa.cli import cmd_due
        code, out = self.run_cmd(cmd_due, days=7)
        self.assertEqual(code, 0)
        self.assertIn("nothing due", out)


class TestThePipelinePage(unittest.TestCase):
    def client(self, path):
        from fastapi.testclient import TestClient
        from jsa import web
        app = web.create_app(db_path=path)
        return TestClient(app, base_url="http://127.0.0.1:8765"), app

    def test_i_an_empty_tracker_renders(self):
        """Scenario i."""
        client, _ = self.client(sandbox())
        r = client.get("/pipeline")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nothing in the pipeline yet", r.text)

    def test_stages_group_and_overdue_is_flagged(self):
        path = sandbox(jobs=3)
        con = db.connect(path)
        for job in (1, 2, 3):
            approvals.save_application(con, job)
        approvals.set_stage(con, 2, "phone_screen")
        approvals.set_stage(con, 3, "rejected")
        approvals.set_next_action(con, 2, "prep answers",
                                  due=(date.today() - timedelta(days=2)).isoformat())
        con.commit()
        con.close()
        client, _ = self.client(path)
        page = client.get("/pipeline").text
        self.assertIn("phone screen · 1", page)
        self.assertIn("overdue 2d", page)
        self.assertIn("1 overdue", page)
        self.assertIn("closed · 1", page)
        self.assertLess(page.index("saved · 1"), page.index("phone screen · 1"),
                        "stages should read in order")

    def test_g_the_page_and_the_cli_write_the_same_rows(self):
        """Scenario g, the same comparison the approve test makes."""
        def snapshot(path):
            con = db.connect(path)
            try:
                return ([tuple(r) for r in con.execute(
                            "SELECT application_id, from_status, to_status, actor, "
                            "note FROM application_events ORDER BY id")],
                        [tuple(r) for r in con.execute(
                            "SELECT id, job_id, status FROM applications")])
            finally:
                con.close()

        cli_path, web_path = sandbox(), sandbox()
        for path in (cli_path, web_path):
            con = db.connect(path)
            approvals.save_application(con, 1)
            con.commit()
            con.close()

        from jsa.cli import cmd_status
        real = db.connect
        with mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(cli_path)):
            import io
            from contextlib import redirect_stdout
            with redirect_stdout(io.StringIO()):
                cmd_status(Namespace(job_id=1, stage="phone_screen", note=None))

        client, app = self.client(web_path)
        r = client.post("/job/1/stage",
                        data={"csrf": app.state.csrf_token, "stage": "phone_screen"},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(snapshot(cli_path), snapshot(web_path))

    def test_an_unknown_stage_through_the_page_is_refused(self):
        path = sandbox()
        con = db.connect(path)
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        client, app = self.client(path)
        r = client.post("/job/1/stage",
                        data={"csrf": app.state.csrf_token, "stage": "promoted"},
                        follow_redirects=False)
        page = client.get(r.headers["location"]).text
        self.assertIn("is not a stage", page)
        con = db.connect(path)
        self.assertEqual(con.execute(
            "SELECT status FROM applications").fetchone()["status"], "saved")
        con.close()

    def test_the_stage_form_carries_the_token(self):
        path = sandbox()
        con = db.connect(path)
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        client, app = self.client(path)
        page = client.get("/pipeline").text
        self.assertIn(f'name="csrf" value="{app.state.csrf_token}"', page)
        r = client.post("/job/1/stage", data={"stage": "applied"})
        self.assertEqual(r.status_code, 403)


class TestNoAgentPathMovesAnApplication(unittest.TestCase):
    """The tool cannot observe a phone screen, so it may never record one."""

    def test_only_approvals_writes_human_events(self):
        import inspect
        from jsa import cli, drafting, letter, tailor, web
        from tests.web_source import web_source
        for module in (drafting, tailor, letter, web):
            source = web_source() if module is web else inspect.getsource(module)
            self.assertNotIn("actor=\"human\"", source, module.__name__)
            self.assertNotIn("actor='human'", source, module.__name__)
        # The CLI and the dashboard reach stages only through approvals.
        self.assertIn("approvals.set_stage", inspect.getsource(cli))
        self.assertIn("approvals.set_stage", web_source())

    def test_drafting_still_records_its_event_as_the_agent(self):
        import inspect
        self.assertIn('actor="agent"', inspect.getsource(
            __import__("jsa.drafting", fromlist=["drafting"])))


if __name__ == "__main__":
    unittest.main()
