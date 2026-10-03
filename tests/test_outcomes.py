"""`jsa outcomes` (Plan 13): what happened to each application. Read-only."""

import ast
import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from jsa import db, outcomes
from jsa.config import ROOT

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def stamp(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Tracker(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        self.con.execute("INSERT INTO sources (id,name,kind,url) "
                         "VALUES (1,'acme-gh','greenhouse','https://x.test/gh')")
        self.con.execute("INSERT INTO sources (id,name,kind,url) "
                         "VALUES (2,'acme-lever','lever','https://x.test/lv')")
        self.next_job = 1

    def app(self, *stages, days_ago=10, source=1, title="Support Engineer",
            discovered_days_ago=12):
        """An application that went through `stages`, one day apart, ending
        `days_ago`. Returns its id."""
        job = self.next_job
        self.next_job += 1
        self.con.execute(
            "INSERT INTO jobs (id,company_id,source_id,external_id,title,url,discovered_at) "
            "VALUES (?,1,?,?,?,?,?)",
            (job, source, f"x{job}", title, f"https://acme.test/{job}",
             stamp(discovered_days_ago)))
        applied_at = None
        cur = self.con.execute("INSERT INTO applications (job_id,status) VALUES (?, 'saved')",
                               (job,))
        app_id = cur.lastrowid
        prev = "saved"
        for n, stage in enumerate(stages):
            when = stamp(days_ago + len(stages) - 1 - n)
            self.con.execute(
                "INSERT INTO application_events (application_id,from_status,to_status,"
                "occurred_at,actor) VALUES (?,?,?,?, 'human')", (app_id, prev, stage, when))
            if stage == "applied" and applied_at is None:
                applied_at = when
            prev = stage
        self.con.execute("UPDATE applications SET status = ?, applied_at = ? WHERE id = ?",
                         (prev, applied_at if prev != "ready" else None, app_id))
        self.con.commit()
        return app_id

    def point(self, app_id):
        o = outcomes.furthest(self.con, app_id, NOW)
        return None if o is None else (o.point, o.closed, o.quiet)


class TestFurthest(Tracker):
    def test_applied_only(self):
        self.assertEqual(self.point(self.app("ready", "applied")), ("applied", False, False))

    def test_a_reply_means_heard_back_but_a_receipt_does_not(self):
        a = self.app("ready", "applied")
        self.con.execute("INSERT INTO inbox_replies (message_id,application_id,kind) "
                         "VALUES ('<r1>', ?, 'received')", (a,))
        self.assertEqual(self.point(a)[0], "applied")
        self.con.execute("INSERT INTO inbox_replies (message_id,application_id,kind) "
                         "VALUES ('<r2>', ?, 'interview')", (a,))
        self.assertEqual(self.point(a)[0], "heard_back")

    def test_a_dismissed_reply_does_not_count(self):
        a = self.app("ready", "applied")
        self.con.execute("INSERT INTO inbox_replies (message_id,application_id,kind,state) "
                         "VALUES ('<r3>', ?, 'interview', 'dismissed')", (a,))
        self.assertEqual(self.point(a)[0], "applied")

    def test_phone_screen_then_rejected(self):
        self.assertEqual(self.point(self.app("applied", "phone_screen", "rejected")),
                         ("interview", True, False))

    def test_a_rejection_is_hearing_back(self):
        self.assertEqual(self.point(self.app("applied", "rejected")),
                         ("heard_back", True, False))

    def test_an_undone_stage_does_not_count(self):
        self.assertIsNone(self.point(self.app("ready", "applied", "ready")))
        self.assertEqual(self.point(self.app("applied", "technical", "applied")),
                         ("applied", False, False))

    def test_offer(self):
        self.assertEqual(self.point(self.app("applied", "phone_screen", "onsite", "offer"))[0],
                         "offer")

    def test_quiet_after_21_days_is_a_report(self):
        self.assertEqual(self.point(self.app("applied", days_ago=21)), ("applied", False, True))
        self.assertEqual(self.point(self.app("applied", days_ago=20)), ("applied", False, False))


class TestTable(Tracker):
    def test_source_kind_groups_by_the_first_source(self):
        self.app("applied", source=1)
        self.app("applied", "phone_screen", source=2)
        rows = {r.group: r for r in outcomes.table(self.con, "source_kind", NOW)}
        self.assertEqual(rows["greenhouse"].counts["applied"], 1)
        self.assertEqual(rows["lever"].counts["interview"], 1)

    def test_a_job_found_twice_is_counted_once(self):
        # upsert_job keeps the first source; the same job never gets a second row.
        self.app("applied", source=2)
        self.assertEqual(sum(r.n for r in outcomes.table(self.con, "source_kind", NOW)), 1)

    def test_role_kind_and_speed(self):
        self.app("applied", title="Account Executive", discovered_days_ago=11)
        self.app("applied", title="Support Engineer", discovered_days_ago=40)
        kinds = {r.group for r in outcomes.table(self.con, "role_kind", NOW)}
        self.assertEqual(kinds, {"sales", "support"})
        speed = {r.group: r.n for r in outcomes.table(self.con, "speed", NOW)}
        self.assertEqual(speed, {"within 3 days of finding it": 1, "later": 1})

    def test_cover_letter_and_redrafted(self):
        a = self.app("applied")
        self.con.execute("INSERT INTO documents (id,job_id,kind,path,version) "
                         "VALUES (1,1,'resume','r.docx',2)")
        self.con.execute("INSERT INTO submitted_documents (application_id,kind,document_id,"
                         "version,approved,submitted_at) VALUES (?, 'resume', 1, 2, 1, ?)",
                         (a, stamp(1)))
        self.assertEqual([r.group for r in outcomes.table(self.con, "cover_letter", NOW)],
                         ["resume only"])
        self.assertEqual([r.group for r in outcomes.table(self.con, "redrafted", NOW)],
                         ["redrafted first"])

    def test_nine_show_no_rate_and_ten_do(self):
        for _ in range(9):
            self.app("applied")
        self.assertIsNone(outcomes.table(self.con, "source_kind", NOW)[0].rate)
        self.app("applied", "phone_screen")
        row = outcomes.table(self.con, "source_kind", NOW)[0]
        self.assertEqual((row.n, row.rate), (10, 0.1))


class TestReadOnly(Tracker):
    def test_the_command_writes_nothing(self):
        from jsa import cli
        self.app("applied")
        real = db.connect
        before = self.path.read_bytes()
        out = io.StringIO()
        with mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path)), \
             redirect_stdout(out):
            self.assertEqual(cli.cmd_outcomes(Namespace(by=None)), 0)
            self.assertEqual(cli.cmd_outcomes(Namespace(by="source_kind")), 0)
        said = out.getvalue()
        self.assertIn("1 application(s)", said)
        self.assertIn("too few to compare", said)
        self.assertIn("Nothing here changed anything.", said)
        self.assertEqual(self.path.read_bytes(), before)

    def test_the_pipeline_page_renders_with_none_and_with_some(self):
        from fastapi.testclient import TestClient
        from jsa import web
        app = web.create_app(db_path=self.path, profile_loader=lambda: {})
        with TestClient(app, base_url="http://127.0.0.1:8765") as c:
            self.assertIn("No applications have gone out yet.", c.get("/pipeline").text)
            self.app("applied")
            page = c.get("/pipeline").text
        self.assertIn("What happened · 1 application(s)", page)

    def test_no_model(self):
        tree = ast.parse((ROOT / "jsa" / "outcomes.py").read_text(encoding="utf-8"))
        modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        self.assertFalse([m for m in modules if "llm" in m])


if __name__ == "__main__":
    unittest.main()
