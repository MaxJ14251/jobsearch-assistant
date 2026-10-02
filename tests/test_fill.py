"""Paste the real posting into a stub, in the same job (ADR 0020).

A Hacker News item is two links and a score. Its posting lives on a page the
tool may not read, so the operator pastes it. `jsa add --paste` made a second
job and left the application and drafts on the stub; `fill` puts the text
into the job that already has them, and discovery never overwrites it.
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import approvals, db, intake, posting
from jsa.config import Preferences
from jsa.intake import IntakeError

STUB = ("Article URL: https://www.ycombinator.com/companies/acme/jobs/x\n\n"
        "Comments URL: https://news.ycombinator.com/item?id=1\n\nPoints: 0")
REAL = ("Acme is hiring a Forward Deployed Engineer to work with customers on "
        "Python integrations. You will deploy our product at customer sites, "
        "debug their data pipelines and write the tooling that makes the next "
        "deployment faster. Requirements: 2+ years of Python, comfort talking to "
        "customers. Salary range: $110,000 - $150,000 per year. Remote (US). "
        "You will own each deployment from kickoff to handoff, sit with the "
        "customer's engineers, and turn what you learn into product changes. "
        "We are a small team, so expect to write code every day.")


def prefs() -> Preferences:
    return Preferences.from_profile({"job_search_preferences": {
        "target_titles": ["Forward Deployed Engineer"],
        "locations": ["Remote (US)"],
        "max_years_experience": 3,
        "compensation_floor_usd": "no_floor",
    }, "ats_keywords": {"have": ["Python"]}})


class FillCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        db.init_db(self.dir / "t.db")
        self.con = db.connect(self.dir / "t.db")
        self.addCleanup(self.con.close)
        self.company = db.upsert_company(self.con, name="Acme", slug="acme")
        self.source = db.upsert_source(self.con, name="hn-hackernews", kind="rss",
                                       url="https://hacker-news.example", company_id=None)
        self.job_id, _ = db.upsert_job(self.con, self.feed_row(STUB))
        self.con.commit()

    def feed_row(self, description, **extra):
        return {"company_id": self.company, "source_id": self.source,
                "external_id": "hn-1", "title": "Forward Deployed Engineer",
                "location": "Remote (US)", "remote": "remote",
                "url": "https://www.ycombinator.com/companies/acme/jobs/x",
                "description": description, "description_hash": str(hash(description)),
                "match_score": 0.4, "match_reasons": ["stub"], **extra}

    def job(self) -> sqlite3.Row:
        return self.con.execute("SELECT * FROM jobs WHERE id = ?",
                                (self.job_id,)).fetchone()


class TestFillInPlace(FillCase):
    def test_the_same_job_keeps_its_application_and_drafts(self):
        app_id, _ = approvals.save_application(self.con, self.job_id)
        self.con.execute("INSERT INTO documents (job_id, kind, path) VALUES (?, 'resume', 'r.docx')",
                         (self.job_id,))
        jobs_before = self.con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        added = intake.fill(self.con, self.job_id, REAL, prefs())
        self.assertEqual(added.job_id, self.job_id)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], jobs_before)
        self.assertEqual(self.job()["description"], REAL)
        self.assertEqual(self.job()["description_origin"], "pasted")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM documents WHERE job_id = ?", (self.job_id,)).fetchone()[0], 1)
        event = self.con.execute(
            "SELECT actor, from_status, to_status, note FROM application_events "
            "WHERE application_id = ? ORDER BY id DESC", (app_id,)).fetchone()
        self.assertEqual(event["actor"], "human")
        self.assertEqual(event["from_status"], event["to_status"], "a fill is not a stage change")
        self.assertIn("pasted", event["note"])

    def test_pay_score_and_track_are_read_from_the_text(self):
        added = intake.fill(self.con, self.job_id, REAL, prefs())
        row = self.job()
        self.assertEqual((row["salary_min"], row["salary_max"]), (110000, 150000))
        self.assertEqual(row["match_score"], added.score)
        self.assertNotEqual(row["match_score"], 0.4)
        self.assertIsNotNone(row["track"])

    def test_enrichment_is_cleared_so_it_reads_the_new_text(self):
        self.con.execute("UPDATE jobs SET enriched_at = '2026-01-01' WHERE id = ?",
                         (self.job_id,))
        intake.fill(self.con, self.job_id, REAL, prefs())
        self.assertIsNone(self.job()["enriched_at"])

    def test_a_boards_own_pay_field_still_wins(self):
        self.con.execute("UPDATE jobs SET salary_min = 90000, salary_max = 95000, "
                         "salary_period = 'year', salary_source = 'field' WHERE id = ?",
                         (self.job_id,))
        intake.fill(self.con, self.job_id, REAL, prefs())
        row = self.job()
        self.assertEqual((row["salary_min"], row["salary_max"], row["salary_source"]),
                         (90000, 95000, "field"))

    def test_a_location_replaces_the_stubs_and_decides_remote(self):
        """Kyber's stub was stored remote with no location; it is on-site in NYC."""
        self.assertEqual(self.job()["remote"], "remote")
        onsite = REAL.replace("Remote (US).", "In our office.")
        added = intake.fill(self.con, self.job_id, onsite, prefs(),
                            location="New York, NY, US")
        row = self.job()
        self.assertEqual((row["location"], row["remote"]), ("New York, NY, US", "onsite"))
        self.assertFalse(any("remote is acceptable" in r for r in added.reasons))

    def test_without_a_location_the_stubs_is_kept(self):
        intake.fill(self.con, self.job_id, REAL, prefs())
        self.assertEqual((self.job()["location"], self.job()["remote"]),
                         ("Remote (US)", "remote"))

    def test_a_short_paste_is_refused(self):
        with self.assertRaises(IntakeError):
            intake.fill(self.con, self.job_id, "Forward Deployed Engineer, remote.", prefs())
        self.assertEqual(self.job()["description"], STUB)

    def test_an_unknown_job_is_refused(self):
        with self.assertRaises(IntakeError):
            intake.fill(self.con, 9999, REAL, prefs())


class TestTheCommand(FillCase):
    def test_jsa_fill_reads_a_file_and_reports_the_score_change(self):
        import io
        from argparse import Namespace
        from contextlib import redirect_stdout
        from jsa import cli

        text_file = self.dir / "posting.txt"
        text_file.write_text(REAL, encoding="utf-8")
        real_connect = db.connect
        out = io.StringIO()
        with mock.patch("jsa.db.upgrade") as upgrade, \
             mock.patch("jsa.db.connect",
                        side_effect=lambda *a, **k: real_connect(self.dir / "t.db")), \
             mock.patch("jsa.cli.load_profile", return_value={"job_search_preferences": {
                 "target_titles": ["Forward Deployed Engineer"],
                 "locations": ["Remote (US)"], "compensation_floor_usd": "no_floor"}}), \
             redirect_stdout(out):
            code = cli.cmd_fill(Namespace(job_id=self.job_id, file=str(text_file), location=None,
                                          no_enrich=True))
        self.assertEqual(code, 0)
        upgrade.assert_called_once()
        said = out.getvalue()
        self.assertIn(f"filled: job {self.job_id}", said)
        self.assertIn("score 0.40 ->", said)
        self.assertIn("discovery will not overwrite", said)
        con = real_connect(self.dir / "t.db")
        self.addCleanup(con.close)
        self.assertEqual(con.execute(
            "SELECT description FROM jobs WHERE id = ?", (self.job_id,)).fetchone()[0], REAL)


class TestDiscoveryNeverOverwritesIt(FillCase):
    def test_rediscovery_keeps_the_pasted_text_and_reopens_the_listing(self):
        intake.fill(self.con, self.job_id, REAL, prefs())
        filled = dict(self.job())
        self.con.execute("UPDATE jobs SET closed_at = '2026-10-01' WHERE id = ?", (self.job_id,))
        job_id, new = db.upsert_job(self.con, self.feed_row(STUB, match_score=0.1))
        self.assertEqual((job_id, new), (self.job_id, False))
        row = self.job()
        self.assertEqual(row["description"], REAL)
        self.assertEqual(row["match_score"], filled["match_score"])
        self.assertEqual(row["salary_min"], filled["salary_min"])
        self.assertIsNone(row["closed_at"])

    def test_an_unfilled_stub_is_still_updated_as_before(self):
        db.upsert_job(self.con, self.feed_row(STUB + "\n\n# Comments: 3", match_score=0.2))
        self.assertIn("# Comments: 3", self.job()["description"])


class TestAnOlderTrackerGetsTheColumn(unittest.TestCase):
    def test_migrate_adds_description_origin(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        self.addCleanup(shutil.rmtree, path.parent, True)
        schema = (db.SCHEMA_PATH.read_text(encoding="utf-8")
                  .replace("    description_origin TEXT,", "", 1))
        self.assertNotIn("description_origin TEXT,", schema)
        con = sqlite3.connect(path)
        con.executescript(schema)
        con.close()
        con = db.connect(path)
        self.addCleanup(con.close)
        db.migrate(con)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)")}
        self.assertIn("description_origin", cols)


class TestTheWarningNamesTheJob(unittest.TestCase):
    def test_with_a_number(self):
        self.assertIn("`jsa fill 1087`", posting.thin(STUB, 1087))

    def test_without_one(self):
        self.assertIn("jsa fill <job#>", posting.thin(STUB))


class TestTheJobPageForm(unittest.TestCase):
    def setUp(self):
        from tests.test_web_documents import Sandbox, make_client
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.client, self.app = make_client(
            self.box.db, self.box.out,
            profile={"job_search_preferences": {
                "target_titles": ["Forward Deployed Engineer"],
                "locations": ["Remote (US)"], "compensation_floor_usd": "no_floor"}})
        self.csrf = self.app.state.csrf_token

    def set_description(self, text):
        con = self.box.connect()
        con.execute("UPDATE jobs SET description = ? WHERE id = 2", (text,))
        con.commit()
        con.close()

    def test_the_form_appears_only_on_a_thin_posting(self):
        self.set_description(STUB)
        self.assertIn('action="/job/2/fill"', self.client.get("/job/2").text)
        self.set_description(REAL * 3)
        self.assertNotIn('action="/job/2/fill"', self.client.get("/job/2").text)

    def test_it_fills_in_place(self):
        self.set_description(STUB)
        with mock.patch.object(intake, "enrich", return_value="not checked: test"):
            r = self.client.post("/job/2/fill", data={"csrf": self.csrf, "text": REAL},
                                 follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/job/2?"))
        page = self.client.get(r.headers["location"]).text
        self.assertIn("Posting saved to this job", page)
        self.assertIn("discovery will not overwrite it", page)
        self.assertNotIn('action="/job/2/fill"', page)

    def test_a_short_paste_comes_back_with_the_reason(self):
        self.set_description(STUB)
        r = self.client.post("/job/2/fill", data={"csrf": self.csrf, "text": "too short"},
                             follow_redirects=False)
        page = self.client.get(r.headers["location"]).text
        self.assertIn("Paste the whole posting", page)


if __name__ == "__main__":
    unittest.main()
