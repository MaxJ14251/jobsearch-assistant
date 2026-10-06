"""Turbo mode (plan 16, ADR 0026): a swipe decides interest; nothing submits.

The model is mocked as in tests/test_tailor_cmd.py. A cover letter whose
model call fails falls back to composed text, so only the resume call needs
a fake.
"""

import ast
import copy
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from jsa import approvals, db, turbo, web
from jsa.config import ROOT
from jsa.llm import LLMError
from tests.test_tailor_cmd import PROFILE as DISK_PROFILE, fake_completion, some_bullet_ids

BASE = "http://127.0.0.1:8765"
POSTING = " ".join(["We want Python, customer-facing support and API work."] * 20)


def profile_ready():
    p = copy.deepcopy(DISK_PROFILE)
    prefs = p.setdefault("job_search_preferences", {})
    prefs["work_authorization"] = "US citizen"
    prefs.setdefault("compensation_floor_usd", "no_floor")
    return p


PROFILE = profile_ready()


class Tracker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "t.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        for job_id, desc, key in ((1, POSTING, "acme|support"), (2, POSTING, "acme|support"),
                                  (3, POSTING, None), (4, "Points: 0", None)):
            con.execute("INSERT INTO jobs (id,company_id,title,url,location,remote,"
                        "match_score,description,dedup_key,track) VALUES "
                        "(?,1,?,?,'Remote','remote',?,?,?,'engineering')",
                        (job_id, f"Support Engineer {job_id}", f"https://acme.test/{job_id}",
                         1 - job_id / 10, desc, key))
        con.commit()
        con.close()
        for patch in (
            mock.patch("jsa.tailor.llm.complete_json",
                       return_value=fake_completion(some_bullet_ids(PROFILE))),
            mock.patch("jsa.llm.complete", side_effect=LLMError("no letter model in tests")),
            mock.patch("jsa.llm.api_key", return_value="test-key"),
            mock.patch("jsa.render.OUTPUT_DIR", self.tmp / "output"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def con(self):
        con = db.connect(self.path)
        self.addCleanup(con.close)
        return con

    def worker(self):
        return turbo.Worker(self.path, lambda: PROFILE)

    def queue(self):
        return [tuple(r) for r in self.con().execute(
            "SELECT job_id, kind, state FROM draft_queue ORDER BY id")]


class TestPass(Tracker):
    def test_pass_and_unpass_a_whole_group(self):
        con = self.con()
        self.assertEqual(turbo.pass_job(con, 1), 2)
        con.commit()
        cards = [r[0] for r in con.execute("SELECT job_id FROM v_new_matches")]
        self.assertNotIn(1, cards)
        self.assertNotIn(2, cards)
        self.assertEqual([r["job_id"] for r in turbo.passed(con)], [1])
        turbo.unpass(con, 2)
        con.commit()
        self.assertIn(1, [r[0] for r in con.execute("SELECT job_id FROM v_new_matches")])

    def test_a_pass_survives_rediscovery(self):
        con = self.con()
        turbo.pass_job(con, 3)
        con.execute("UPDATE jobs SET external_id = 'x3', source_id = NULL WHERE id = 3")
        con.commit()
        db.upsert_job(con, {"company_id": 1, "external_id": "x3", "source_id": None,
                            "title": "Support Engineer 3", "url": "https://acme.test/3",
                            "description": POSTING + " (edited)"})
        con.commit()
        self.assertIsNotNone(con.execute("SELECT passed_at FROM jobs WHERE id = 3").fetchone()[0])

    def test_the_cli(self):
        from jsa import cli
        real = db.connect
        out = io.StringIO()
        with mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path)), \
             redirect_stdout(out):
            self.assertEqual(cli.main(["pass", "3"]), 0)
            self.assertEqual(cli.main(["passed"]), 0)
            self.assertEqual(cli.main(["unpass", "3"]), 0)
        said = out.getvalue()
        self.assertIn("passed job 3", said)
        self.assertIn("[3]", said)
        self.assertIn("back in the matches", said)


class TestInterested(Tracker):
    def test_a_right_swipe_saves_queues_two_and_the_worker_drafts_both(self):
        con = self.con()
        result = turbo.interested(con, 3, PROFILE)
        con.commit()
        self.assertTrue(result.queued)
        self.assertEqual(self.queue(), [(3, "resume", "queued"), (3, "cover_letter", "queued")])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(self.worker().drain(), 2)
        self.assertEqual(self.queue(), [(3, "resume", "done"), (3, "cover_letter", "done")])
        pending = self.con().execute(
            "SELECT COUNT(*) FROM approvals WHERE decision = 'pending'").fetchone()[0]
        self.assertEqual(pending, 2)
        app = self.con().execute("SELECT status FROM applications WHERE job_id = 3").fetchone()
        self.assertEqual(app["status"], "ready")
        actors = {r["to_status"]: r["actor"] for r in self.con().execute(
            "SELECT to_status, actor FROM application_events e JOIN applications a "
            "ON a.id = e.application_id WHERE a.job_id = 3")}
        self.assertEqual(actors["saved"], "human")
        self.assertEqual(actors["ready"], "agent")

    def test_a_failure_is_stored_not_raised(self):
        con = self.con()
        turbo.interested(con, 3, PROFILE)
        con.commit()
        with mock.patch("jsa.drafting.draft_document", side_effect=RuntimeError("boom\nmore")):
            self.worker().drain()
        states = {(k, s) for _, k, s in self.queue()}
        self.assertEqual(states, {("resume", "failed"), ("cover_letter", "failed")})
        error = self.con().execute("SELECT error FROM draft_queue LIMIT 1").fetchone()[0]
        self.assertEqual(error, "boom")

    def test_restart_requeues_running_items(self):
        con = self.con()
        turbo.enqueue(con, 3)
        con.execute("UPDATE draft_queue SET state = 'running'")
        con.commit()
        self.assertEqual(turbo.requeue_stale(con), 2)

    def test_the_daily_limit_saves_without_queueing(self):
        limited = {**PROFILE, "turbo": {"daily_jobs": 1}}
        con = self.con()
        self.assertTrue(turbo.interested(con, 3, limited).queued)
        second = turbo.interested(con, 1, limited)
        self.assertFalse(second.queued)
        self.assertIn("Today's drafting limit (1) is reached", second.message)
        self.assertIn("Nothing is queued for tomorrow", second.message)
        self.assertIsNotNone(con.execute(
            "SELECT 1 FROM applications WHERE job_id = 1").fetchone())

    def test_a_thin_posting_saves_without_queueing(self):
        result = turbo.interested(self.con(), 4, PROFILE)
        self.assertFalse(result.queued)
        self.assertIn("Saved, not drafted", result.message)
        self.assertEqual(self.queue(), [])

    def test_no_key_or_an_undecided_profile_saves_only(self):
        con = self.con()
        with mock.patch("jsa.llm.api_key", side_effect=LLMError("no key")):
            result = turbo.interested(con, 3, PROFILE)
        self.assertIn("saves only: no model API key", result.message)
        undecided = copy.deepcopy(PROFILE)
        undecided["job_search_preferences"]["work_authorization"] = None
        result = turbo.interested(con, 1, undecided)
        con.commit()
        self.assertIn("saves only", result.message)
        self.assertEqual(self.queue(), [])

    def test_a_repeat_swipe_does_not_double_queue(self):
        con = self.con()
        turbo.interested(con, 3, PROFILE)
        again = turbo.interested(con, 3, PROFILE)
        con.commit()
        self.assertFalse(again.queued)
        self.assertEqual(len(self.queue()), 2)


class TestNeverSubmits(Tracker):
    def test_turbo_imports_no_network_or_mail_module_and_no_model(self):
        tree = ast.parse((ROOT / "jsa" / "turbo.py").read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                  for a in n.names]
        for banned in ("httpx", "requests", "urllib", "socket", "smtplib", "imaplib",
                       "http", "webbrowser", "subprocess"):
            self.assertFalse([n for n in names if n.split(".")[0] == banned], banned)
        # Model work only through drafting.draft_document; api_key is only
        # read to say whether drafting can run.
        self.assertEqual(sorted({a.name for n in ast.walk(tree)
                                 if isinstance(n, ast.ImportFrom) and n.module == "llm"
                                 for a in n.names}), ["LLMError", "api_key"])
        source = (ROOT / "jsa" / "turbo.py").read_text(encoding="utf-8")
        for call in ("complete(", "complete_json(", "mark_applied", "set_stage",
                     "'applied'", '"applied"'):
            self.assertNotIn(call, source)

    def test_no_application_reaches_applied_through_turbo(self):
        con = self.con()
        for job in (1, 3, 4):
            turbo.interested(con, job, PROFILE)
        turbo.pass_job(con, 2)
        con.commit()
        with redirect_stderr(io.StringIO()):
            self.worker().drain()
        statuses = {r[0] for r in self.con().execute("SELECT status FROM applications")}
        self.assertTrue(statuses <= {"saved", "ready"}, statuses)
        self.assertEqual(self.con().execute(
            "SELECT COUNT(*) FROM submitted_documents").fetchone()[0], 0)


class TestRoutes(Tracker):
    def setUp(self):
        super().setUp()
        self.app = web.create_app(db_path=self.path, output_dir=self.tmp / "output",
                                  profile_loader=lambda: PROFILE)
        self.client = TestClient(self.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.app.state.csrf_token

    def test_the_page_deals_the_same_cards_as_matches(self):
        page = self.client.get("/turbo?anywhere=1").text
        dealt = [int(x) for x in __import__("re").findall(r'class="tcard" data-job="(\d+)"', page)]
        listed = self.client.get("/?anywhere=1").text
        self.assertEqual(dealt, [1, 3, 4])                    # 2 folds into 1
        for job in dealt:
            self.assertIn(f'href="/job/{job}"', listed)
        self.assertIn("almost no posting text", page)          # job 4

    def test_every_turbo_post_needs_the_token(self):
        for url in ("/turbo/pass", "/turbo/unpass", "/turbo/interested", "/turbo/cancel"):
            with self.subTest(url=url):
                r = self.client.post(url, data={"job_id": 3})
                self.assertEqual(r.status_code, 403)

    def test_json_for_the_script_and_a_redirect_without_it(self):
        r = self.client.post("/turbo/interested",
                             data={"csrf": self.csrf, "job_id": 3, "js": "1"})
        self.assertEqual(r.json()["queued"], True)
        self.assertIn("Drafting a resume and cover letter", r.json()["message"])
        r = self.client.post("/turbo/pass", data={"csrf": self.csrf, "job_id": 1,
                                                  "back": "?anywhere=1"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/turbo?anywhere=1&msg="))
        status = self.client.get("/turbo/status").json()
        self.assertEqual((status["queued"], status["running"]), (2, 0))
        r = self.client.post("/turbo/cancel", data={"csrf": self.csrf, "js": "1"})
        self.assertIn("Cancelled 2", r.json()["message"])

    def test_the_tailor_route_turns_a_model_failure_into_a_message(self):
        approvals.save_application(self.con(), 3)
        with mock.patch("jsa.drafting.draft_document", side_effect=LLMError("rate limited")):
            r = self.client.post("/job/3/tailor", data={"csrf": self.csrf, "kind": "resume"},
                                 follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("rate+limited", r.headers["location"])


if __name__ == "__main__":
    unittest.main()


class TestDraftingNeverMovesALaterStageBack(Tracker):
    """Review R-01: a queued draft that runs after the person applied must not
    move the application back to "ready"."""

    def test_an_interview_stage_survives_a_late_draft(self):
        con = self.con()
        turbo.interested(con, 3, PROFILE)
        con.commit()
        approvals.set_stage(con, 3, "applied")
        approvals.set_stage(con, 3, "phone_screen")
        con.commit()
        with redirect_stderr(io.StringIO()):
            self.worker().drain()
        con = self.con()
        status = con.execute("SELECT status FROM applications WHERE job_id = 3").fetchone()[0]
        self.assertEqual(status, "phone_screen")
        agent_moves = con.execute(
            "SELECT from_status, to_status FROM application_events e "
            "JOIN applications a ON a.id = e.application_id "
            "WHERE a.job_id = 3 AND e.actor = 'agent'").fetchall()
        self.assertTrue(agent_moves, "the drafts are still recorded")
        self.assertTrue(all(r[0] == r[1] == "phone_screen" for r in agent_moves),
                        [tuple(r) for r in agent_moves])


class TestTheWorkerFinishesItsItems(Tracker):
    """Review R-26: a lost final update left an item 'running'."""

    def test_a_locked_finish_is_retried(self):
        con = self.con()
        turbo.interested(con, 3, PROFILE)
        con.commit()
        worker = self.worker()
        real_connect = worker._connect
        failures = [1]

        class Flaky:
            def __init__(self, inner):
                self.inner = inner

            def execute(self, sql, *args):
                if sql.startswith("UPDATE draft_queue SET state = ?") and failures:
                    failures.pop()
                    raise sqlite3.OperationalError("database is locked")
                return self.inner.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with mock.patch.object(worker, "_connect", side_effect=lambda: Flaky(real_connect())), \
             mock.patch("jsa.turbo.time.sleep"), redirect_stderr(io.StringIO()):
            worker.drain()
        self.assertNotIn("running", [state for _, _, state in self.queue()])
        self.assertEqual(failures, [])
