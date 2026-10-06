"""Applying by hand, and recording where (Plan 22).

The job page gathers what an application form asks for, in order, and an
"I applied" button records the date, the documents and the channel through
the same set_stage -> mark_applied path `jsa applied` uses. The tool still
submits nothing, and only the person's own action records "applied".
"""

import ast
import io
import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from jsa import approvals, cli, db, outcomes, web
from jsa.approvals import channel

BASE = "http://127.0.0.1:8765"
REAL_CONNECT = db.connect
ROOT = Path(__file__).resolve().parent.parent


class TestChannel(unittest.TestCase):
    def test_hosts(self):
        cases = {
            "https://www.linkedin.com/jobs/view/123": "linkedin",
            "https://uk.linkedin.com/jobs/view/123": "linkedin",
            "https://www.indeed.com/viewjob?jk=abc": "indeed",
            "https://boards.greenhouse.io/acme/jobs/1": "employer",
            "https://job-boards.greenhouse.io/acme/jobs/1": "employer",
            "https://jobs.lever.co/acme/abc": "employer",
            "https://jobs.ashbyhq.com/acme/abc": "employer",
            "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/x": "employer",
            "https://apply.workable.com/acme/j/1": "employer",
            "https://news.ycombinator.com/item?id=1": "other",
            "https://www.linkedin.com.evil.test/jobs/1": "other",
            "not a link": "other",
            "": "other",
            None: "other",
        }
        for url, want in cases.items():
            with self.subTest(url=url):
                self.assertEqual(channel(url, "Acme"), want)

    def test_the_companys_own_domain(self):
        self.assertEqual(channel("https://careers.duolingo.com/jobs/1", "Duolingo"), "employer")
        self.assertEqual(channel("https://www.acmerobotics.com/careers/1", "Acme Robotics Inc"),
                         "employer")
        self.assertEqual(channel("https://jobs.example.org/1", "Globex",
                                 careers_url="https://jobs.example.org/"), "employer")
        self.assertEqual(channel("https://jobs.example.org/1", "Globex"), "other")


class Tracker(unittest.TestCase):
    URL = "https://www.linkedin.com/jobs/view/123"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "t.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        self.con.execute("INSERT INTO jobs (id,company_id,title,location,url,description) "
                         "VALUES (5,1,'Support Engineer','Austin, TX',?,'x')", (self.URL,))
        self.app, _ = approvals.save_application(self.con, 5)
        self.con.commit()

    def draft(self, kind="resume", *, approve=False) -> int:
        version = self.con.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM documents WHERE job_id=5 AND kind=?",
            (kind,)).fetchone()[0]
        file = self.tmp / f"{kind}-v{version}.docx"
        file.write_bytes(f"{kind} {version}".encode())
        doc = int(self.con.execute(
            "INSERT INTO documents (job_id,kind,path,version,bullet_ids) VALUES (5,?,?,?,?)",
            (kind, str(file), version, json.dumps(["b1"]))).lastrowid)
        approvals.set_document_pointer(self.con, self.app, kind, doc)
        approval = approvals.queue(self.con, "document", doc, f"{kind} v{version}")
        if approve:
            approvals.approve(self.con, approval)
        self.con.commit()
        return doc

    def application(self):
        return self.con.execute("SELECT * FROM applications WHERE id = ?",
                                (self.app,)).fetchone()


class TestMarkApplied(Tracker):
    def test_it_stores_the_channel_and_says_so(self):
        self.draft(approve=True)
        approvals.mark_applied(self.con, 5, via="linkedin")
        self.assertEqual(self.application()["applied_via"], "linkedin")
        event = self.con.execute("SELECT actor, note FROM application_events "
                                 "WHERE to_status = 'applied'").fetchone()
        self.assertEqual(event["actor"], "human")
        self.assertIn("submitted by hand via LinkedIn", event["note"])

    def test_an_unknown_channel_is_refused_before_anything_is_written(self):
        with self.assertRaises(approvals.ApprovalError):
            approvals.mark_applied(self.con, 5, via="carrier pigeon")
        self.assertIsNone(self.application()["applied_at"])

    def test_without_a_channel_it_is_not_recorded(self):
        approvals.mark_applied(self.con, 5)
        self.assertIsNone(self.application()["applied_via"])

    def test_an_older_tracker_gets_the_column(self):
        path = self.tmp / "old.db"
        schema = db.SCHEMA_PATH.read_text(encoding="utf-8")
        schema = re.sub(r"\n\s*applied_via\s+TEXT,", "", schema, count=1)
        self.assertNotIn("applied_via", schema.split("CREATE TABLE IF NOT EXISTS applications")[1]
                         .split(");")[0])
        con = sqlite3.connect(path)
        con.executescript(schema)
        con.close()
        con = db.connect(path)
        self.addCleanup(con.close)
        db.migrate(con)
        self.assertIn("applied_via", {r["name"] for r in con.execute(
            "PRAGMA table_info(applications)")})


class TestTheCommand(Tracker):
    def run_cli(self, *argv):
        out, opened = io.StringIO(), []

        def connect(*a, **k):
            opened.append(REAL_CONNECT(self.path))
            return opened[-1]
        with mock.patch("jsa.db.connect", side_effect=connect), redirect_stdout(out):
            code = cli.main(["applied", *argv])
        for con in opened:
            con.close()
        return code, out.getvalue()

    def test_the_channel_defaults_to_the_jobs_link(self):
        self.draft(approve=True)
        code, out = self.run_cli("5")
        self.assertEqual(code, 0, out)
        self.assertIn("via LinkedIn", out)
        self.assertIn("read from the job's link", out)
        self.assertEqual(self.application()["applied_via"], "linkedin")

    def test_via_overrides_it(self):
        self.draft(approve=True)
        code, out = self.run_cli("5", "--via", "employer")
        self.assertEqual(code, 0, out)
        self.assertNotIn("read from the job's link", out)
        self.assertEqual(self.application()["applied_via"], "employer")

    def test_check_writes_nothing(self):
        self.run_cli("5", "--check")
        self.assertIsNone(self.application()["applied_via"])


class Page(Tracker):
    def setUp(self):
        super().setUp()
        self.web = web.create_app(db_path=self.path, output_dir=self.tmp,
                                  profile_loader=lambda: None)
        self.client = TestClient(self.web, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.web.state.csrf_token

    def page(self):
        return self.client.get("/job/5").text

    def applied(self, **form):
        return self.client.post("/job/5/applied", data={"csrf": self.csrf, **form},
                                follow_redirects=True)


class TestTheChecklist(Page):
    def test_it_shows_for_a_saved_job_in_order(self):
        page = self.page()
        steps = ["<h3>Where to apply", "<h3>Resume", "<h3>Cover letter",
                 'id="answers">Application answers', "<h3>I applied"]
        at = [page.index(s) for s in steps]
        self.assertEqual(at, sorted(at))
        self.assertIn("Open the listing on LinkedIn", page)
        self.assertIn('<option value="linkedin" selected>', page)

    def test_it_shows_the_newest_approved_resume_and_warns_of_a_newer_draft(self):
        self.draft(approve=True)
        self.draft(approve=True)
        waiting = self.draft()
        page = self.page()
        self.assertIn("v2, approved", page)
        self.assertNotIn("v1, approved", page)
        self.assertIn("1 older approved version(s)", page)
        self.assertIn(f"A newer draft, v3, is waiting", page)
        self.assertIn(f"/review#doc-{waiting}", page)

    def test_no_approved_resume_says_so_and_asks_to_confirm(self):
        self.draft()
        page = self.page()
        self.assertIn("No approved resume yet", page)
        self.assertIn('name="confirm"', page)

    def test_it_leaves_once_applied(self):
        self.draft(approve=True)
        approvals.mark_applied(self.con, 5)
        self.con.commit()
        page = self.page()
        self.assertNotIn("Apply by hand", page)

    def test_it_leads_with_the_employers_own_posting(self):
        self.con.execute("INSERT INTO jobs (id,company_id,title,location,url) VALUES "
                         "(6,1,'Support Engineer','Austin, TX',"
                         "'https://boards.greenhouse.io/acme/jobs/9')")
        self.con.commit()
        page = self.page()
        self.assertIn("Apply on the employer's site instead", page)
        self.assertIn('href="/job/6"', page)


class TestIApplied(Page):
    def test_it_records_date_documents_channel_and_who(self):
        resume = self.draft(approve=True)
        r = self.applied(via="indeed", resume=str(resume), cover="0")
        self.assertIn("Recorded: you applied via Indeed", r.text)
        self.assertIn("This tool submitted nothing", r.text)
        app = self.application()
        self.assertEqual((app["status"], app["applied_via"]), ("applied", "indeed"))
        self.assertIsNotNone(app["applied_at"])
        sent = approvals.submitted(self.con, self.app)
        self.assertEqual([(s.kind, s.document_id, s.approved) for s in sent],
                         [("resume", resume, True)])
        event = self.con.execute("SELECT actor FROM application_events "
                                 "WHERE to_status = 'applied'").fetchone()
        self.assertEqual(event["actor"], "human")

    def test_an_unapproved_resume_needs_the_second_confirmation(self):
        draft = self.draft()
        r = self.applied(via="linkedin", resume=str(draft), cover="0")
        self.assertIn("Nothing recorded", r.text)
        self.assertIsNone(self.application()["applied_at"])
        r = self.applied(via="linkedin", resume=str(draft), cover="0", confirm="1")
        self.assertIn("Recorded", r.text)
        sent = approvals.submitted(self.con, self.app)
        self.assertEqual([(s.document_id, s.approved) for s in sent], [(draft, False)])

    def test_my_own_resume_needs_it_too(self):
        r = self.applied(via="other", resume="0", cover="0")
        self.assertIn("Nothing recorded", r.text)
        r = self.applied(via="other", resume="0", cover="0", confirm="1")
        self.assertIn("no document recorded", r.text)
        self.assertEqual(self.application()["status"], "applied")

    def test_a_bad_channel_records_nothing(self):
        resume = self.draft(approve=True)
        r = self.applied(via="fax", resume=str(resume))
        self.assertIn("Nothing recorded", r.text)
        self.assertIsNone(self.application()["applied_at"])

    def test_the_token_is_required(self):
        r = self.client.post("/job/5/applied", data={"via": "linkedin", "confirm": "1"})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.application()["applied_at"])


class TestOnlyThePersonRecordsApplied(unittest.TestCase):
    """mark_applied is reached only from set_stage and `jsa applied`, and
    set_stage only from a human's command, route or confirmed reply."""

    def calls(self, name):
        """{file: {enclosing function, ...}} for every real call of `name`."""
        found = {}
        for path in sorted((ROOT / "jsa").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for call in ast.walk(node):
                    func = getattr(call, "func", None)
                    called = getattr(func, "attr", None) or getattr(func, "id", None)
                    if isinstance(call, ast.Call) and called == name:
                        found.setdefault(path.name, set()).add(node.name)
        return found

    def test_mark_applied(self):
        # Nested functions count for their outer one too; only routes and
        # commands a person runs may appear.
        found = self.calls("mark_applied")
        self.assertEqual(set(found), {"approvals.py", "cli.py"})
        self.assertEqual(found["approvals.py"], {"set_stage"})
        self.assertEqual(found["cli.py"], {"cmd_applied"})

    def test_set_stage(self):
        found = self.calls("set_stage")
        self.assertEqual(set(found), {"cli.py", "inbox.py", "web.py"})
        self.assertEqual(found["web.py"], {"create_app", "do_stage", "do_applied"})
        self.assertEqual(found["inbox.py"], {"confirm"})
        self.assertEqual(found["cli.py"], {"cmd_status"})


class TestOutcomesByChannel(Tracker):
    def test_grouped_by_where_you_applied(self):
        self.draft(approve=True)
        approvals.mark_applied(self.con, 5, via="linkedin")
        self.con.commit()
        rows = outcomes.table(self.con, "via")
        self.assertEqual([(r.group, r.n) for r in rows], [("LinkedIn", 1)])
        self.assertIsNone(rows[0].rate, "too few to compare")

    def test_an_older_application_is_not_recorded(self):
        approvals.mark_applied(self.con, 5)
        self.con.commit()
        self.assertEqual(outcomes.table(self.con, "via")[0].group, "not recorded")

    def test_ten_get_a_rate(self):
        self.con.execute("DELETE FROM applications")
        for i in range(10):
            self.con.execute("INSERT INTO jobs (id,company_id,title,url) VALUES (?,1,'X','u')",
                             (100 + i,))
            approvals.save_application(self.con, 100 + i)
            approvals.mark_applied(self.con, 100 + i, via="employer")
        self.con.commit()
        rows = outcomes.table(self.con, "via")
        self.assertEqual((rows[0].group, rows[0].n), ("the employer's site", 10))
        self.assertIsNotNone(rows[0].rate)


if __name__ == "__main__":
    unittest.main()
