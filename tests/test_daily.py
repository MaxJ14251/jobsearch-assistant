"""`jsa daily` (plan 18, ADR 0028), and the two fixes it needed first:
discovery no longer holds a write lock across the network, and `jsa inbox`
reports a network error instead of a traceback."""

import io
import json
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from jsa import daily, db, discover, sources, web

PROFILE = {"job_search_preferences": {"target_titles": ["Support Engineer"],
                                      "locations": ["Remote (US)"]}}


class Tracker(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "jobsearch.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.commit()
        con.close()
        real = db.connect
        patch = mock.patch("jsa.db.connect",
                           side_effect=lambda *a, **k: real(self.path))
        patch.start()
        self.addCleanup(patch.stop)
        self.real = real

    def con(self):
        con = self.real(self.path)
        self.addCleanup(con.close)
        return con


class TestNoLockAcrossTheNetwork(Tracker):
    def test_a_write_during_a_slow_fetch_succeeds(self):
        wrote = []

        def slow_fetch(_entry):
            # Another writer (a dashboard save) while the feed is "slow".
            other = sqlite3.connect(self.path, timeout=0.2)
            try:
                other.execute("INSERT INTO companies (name, slug) VALUES ('Beta', 'beta')")
                other.commit()
                wrote.append(True)
            finally:
                other.close()
            return sources.FetchResult(ok=True, jobs=[], status="ok")

        entry = {"company": "Acme", "slug": "acme", "kind": "greenhouse",
                 "verified": True, "board": "acme"}
        with mock.patch("jsa.discover.load_profile", return_value=PROFILE), \
             mock.patch("jsa.discover.load_sources", return_value=[entry]), \
             mock.patch("jsa.db.upgrade", return_value=[]), \
             mock.patch("jsa.sources.fetch", side_effect=slow_fetch):
            discover.discover()
        self.assertEqual(wrote, [True])

    def test_every_connection_waits_before_giving_up(self):
        self.assertGreaterEqual(db.BUSY_TIMEOUT_S, 10)


class TestInboxNetworkError(Tracker):
    def test_a_network_error_is_a_message_not_a_traceback(self):
        from jsa import cli
        err = io.StringIO()
        with mock.patch("jsa.inbox.fetch", side_effect=TimeoutError("timed out")), \
             mock.patch("jsa.db.upgrade", return_value=[]), \
             redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = cli.cmd_inbox(Namespace(action="list", no_fetch=False))
        self.assertEqual(code, 1)
        self.assertIn("Couldn't reach the mail server: timed out", err.getvalue())


class TestTheRun(Tracker):
    def run_daily(self, *, discover_ok=True, inbox_set=True, skip=()):
        order = []

        def fake_backup(db_path):
            order.append("backup")
            return daily.Step("backup", "ok", "verified")

        def fake_discover(db_path=None):
            order.append("discover")
            if not discover_ok:
                raise RuntimeError("Lever: HTTP 503")
            return daily.Step("discover", "ok", "1 feeds, 0 new posting(s)")

        def fake_inbox(db_path):
            order.append("inbox")
            return daily.Step("inbox", "ok", "0 new repl(ies)")

        with mock.patch("jsa.daily._backup", side_effect=fake_backup), \
             mock.patch("jsa.daily._discover", side_effect=fake_discover), \
             mock.patch("jsa.daily._inbox", side_effect=fake_inbox), \
             mock.patch("jsa.inbox.settings", return_value={} if inbox_set else None):
            result = daily.run(skip, db_path=self.path)
        return result, order

    def test_the_steps_run_in_order_and_are_recorded(self):
        result, order = self.run_daily()
        self.assertEqual(order, ["backup", "discover", "inbox"])
        self.assertTrue(result.ok)
        row = self.con().execute("SELECT * FROM daily_runs").fetchone()
        self.assertEqual(row["ok"], 1)
        self.assertEqual([s["name"] for s in json.loads(row["steps_json"])],
                         ["backup", "discover", "inbox"])

    def test_a_failed_discovery_still_checks_the_inbox_and_exits_1(self):
        from jsa import cli
        with mock.patch("jsa.daily._backup", return_value=daily.Step("backup", "ok")), \
             mock.patch("jsa.daily._discover", side_effect=RuntimeError("Lever: HTTP 503")), \
             mock.patch("jsa.daily._inbox", return_value=daily.Step("inbox", "ok")) as inbox, \
             mock.patch("jsa.daily._db_path", return_value=self.path), \
             redirect_stdout(io.StringIO()) as out:
            code = cli.cmd_daily(Namespace(schedule_help=False, skip="", at="07:00"))
        self.assertEqual(code, 1)
        inbox.assert_called_once()
        self.assertIn("discover  failed   Lever: HTTP 503", out.getvalue())

    def test_an_unset_inbox_is_skipped(self):
        with mock.patch("jsa.inbox.settings", return_value=None):
            self.assertEqual(daily._inbox(self.path), daily.Step("inbox", "skipped", "not set up"))

    def test_skip(self):
        result, order = self.run_daily(skip=("discover",))
        self.assertEqual(order, ["backup", "inbox"])
        self.assertEqual(result.steps[1].state, "skipped")

    def test_the_summary_counts_since_the_last_run(self):
        con = self.con()
        old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for job_id, found in ((1, old), (2, None), (3, None)):
            con.execute("INSERT INTO jobs (id,company_id,title,url,match_score,discovered_at) "
                        "VALUES (?,1,?,?,?,COALESCE(?, strftime('%Y-%m-%dT%H:%M:%SZ','now')))",
                        (job_id, f"Role {job_id}", f"https://acme.test/{job_id}",
                         0.5 + job_id / 10, found))
        con.execute("INSERT INTO inbox_replies (message_id, kind) VALUES ('<a>', 'interview')")
        con.commit()
        result, _ = self.run_daily()                 # first run: the last 24 hours
        self.assertEqual(result.summary["new_matches"], 2)
        self.assertEqual([t["job_id"] for t in result.summary["top"]], [3, 2])
        self.assertEqual(result.summary["replies"], 1)
        second, _ = self.run_daily()
        self.assertEqual(second.summary["new_matches"], 0)
        finished = self.con().execute("SELECT finished_at FROM daily_runs ORDER BY id "
                                      "LIMIT 1").fetchone()[0]
        self.assertEqual(second.summary["since"], finished)

    def test_the_log_is_utf8_with_the_dash_intact(self):
        result, _ = self.run_daily()
        log = next((self.dir / "logs").glob("daily-*.log"))
        text = log.read_text(encoding="utf-8")
        self.assertIn("jsa daily -- ", text)
        self.assertIn("·", text)
        old = self.dir / "logs" / "daily-2020-01-01.log"
        old.write_text("x", encoding="utf-8")
        daily.write_log(result, self.path)
        self.assertFalse(old.exists())

    def test_schedule_help_prints_and_runs_nothing(self):
        from jsa import cli
        with mock.patch("subprocess.run") as ran, mock.patch("os.system") as system, \
             redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.cmd_daily(Namespace(schedule_help=True, skip="", at="06:45")), 0)
        ran.assert_not_called()
        system.assert_not_called()
        self.assertIn("scheduled nothing", out.getvalue())
        self.assertIn("06:45" if "schtasks" in out.getvalue() else "45 6 ", out.getvalue())
        self.assertEqual(self.con().execute("SELECT COUNT(*) FROM daily_runs").fetchone()[0], 0)

    def test_daily_backups_are_kept_apart(self):
        from jsa import backup
        self.assertIn("daily", backup.LABELS)
        self.assertEqual(backup.KEEP["daily"], 7)


class TestBannerAndDoctor(TestTheRun):
    def client(self):
        app = web.create_app(db_path=self.path, profile_loader=lambda: PROFILE)
        c = TestClient(app, base_url="http://127.0.0.1:8765")
        self.addCleanup(c.close)
        return c, app.state.csrf_token

    def test_the_banner_shows_until_got_it(self):
        self.run_daily()
        c, token = self.client()
        self.assertIn("Since your last daily run", c.get("/pipeline").text)
        run_id = self.con().execute("SELECT id FROM daily_runs").fetchone()[0]
        r = c.post("/daily/seen", data={"run_id": run_id, "back": "/pipeline"})
        self.assertEqual(r.status_code, 403)                 # no token
        c.post("/daily/seen", data={"csrf": token, "run_id": run_id, "back": "/pipeline"})
        self.assertNotIn("Since your last daily run", c.get("/pipeline").text)

    def test_doctor_names_a_failed_step_and_an_old_run(self):
        from jsa import doctor
        self.run_daily(discover_ok=False)
        report = doctor.Report()
        with mock.patch("jsa.backup.DB_PATH", self.path):
            doctor.check_tracker(self.con(), {}, report)
        self.assertTrue(any("last daily run failed: discover" in f.what
                            for f in report.findings))
        con = self.con()
        con.execute("UPDATE daily_runs SET ok = 1, started_at = '2026-01-01T07:00:00Z'")
        con.commit()
        report = doctor.Report()
        with mock.patch("jsa.backup.DB_PATH", self.path):
            doctor.check_tracker(con, {}, report)
        self.assertTrue(any("last daily run was on" in f.what for f in report.findings))


if __name__ == "__main__":
    unittest.main()


class TestDiscoveryUsesTheRunsTracker(Tracker):
    """Review R-30: `run(db_path=...)` discovered into the configured tracker."""

    def test_the_path_is_passed_on(self):
        with mock.patch("jsa.discover.discover", return_value=[]) as found, \
             mock.patch("jsa.daily._backup", return_value=daily.Step("backup", "ok", "")), \
             mock.patch("jsa.inbox.settings", return_value=None):
            daily.run((), db_path=self.path)
        self.assertEqual(found.call_args.kwargs.get("db_path"), self.path)
