"""Company degree profiles and where they show (plan 32)."""

import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import companies, db, degree
from jsa.config import SCHEMA_PATH
from tests.test_web_documents import make_client

LONG = " Our team builds tools that customers rely on every day." * 12
TEXTS = {
    "bachelors": "Bachelor's degree in Computer Science required." + LONG,
    "bachelors_or_equiv": "Bachelor's degree or equivalent experience." + LONG,
    "masters": "PhD in Physics required." + LONG,
    "none": "You will help customers." + LONG,
}


class Tracker(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = self.dir / "t.db"
        db.init_db(self.db)
        self.con = db.connect(self.db)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Riverton Grid','riverton')")
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (2,'Small Co','small')")
        self.next_id = 1

    def job(self, company_id, text, closed=False, external=None):
        job = {"company_id": company_id, "external_id": external or f"x{self.next_id}",
               "title": f"Job {self.next_id}", "url": "https://example.com",
               "description": text, "description_hash": str(hash(text)), "location": "Remote"}
        self.next_id += 1
        job_id, _ = db.upsert_job(self.con, job)
        if closed:
            self.con.execute("UPDATE jobs SET closed_at = '2026-10-01T00:00:00Z' WHERE id = ?",
                             (job_id,))
        self.con.commit()
        return job_id

    def fill(self, company_id, spec):
        for level, n in spec.items():
            for _ in range(n):
                self.job(company_id, TEXTS[level])


class TestStoredReading(Tracker):
    def test_set_when_stored_and_again_when_the_text_changes(self):
        job_id = self.job(1, TEXTS["bachelors"], external="same")
        row = self.con.execute("SELECT degree_level, degree_evidence FROM jobs WHERE id=?",
                               (job_id,)).fetchone()
        self.assertEqual(row["degree_level"], "bachelors")
        self.assertIn("Bachelor's degree", row["degree_evidence"])
        self.job(1, TEXTS["none"], external="same")
        self.assertEqual(self.con.execute("SELECT degree_level FROM jobs WHERE id=?",
                                          (job_id,)).fetchone()[0], "none")

    def test_unchanged_text_is_not_read_again(self):
        job_id = self.job(1, TEXTS["bachelors"], external="same")
        with mock.patch("jsa.degree.columns", side_effect=AssertionError("read again")):
            self.job(1, TEXTS["bachelors"], external="same")
        self.assertEqual(self.con.execute("SELECT degree_level FROM jobs WHERE id=?",
                                          (job_id,)).fetchone()[0], "bachelors")

    def test_an_older_tracker_is_read_when_upgraded(self):
        path = self.dir / "old.db"
        con = sqlite3.connect(path)
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        for line in schema.splitlines():
            if line.strip().startswith(("degree_level", "certs_named", "degree_evidence",
                                        "r.degree_level")):
                schema = schema.replace(line + "\n", "")
        con.executescript(schema)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'A','a')")
        con.execute("INSERT INTO jobs (company_id,title,url,description) VALUES "
                    "(1,'X','u',?)", (TEXTS["masters"],))
        con.commit()
        con.close()
        applied = db.upgrade(path)
        self.assertIn("jobs.degree_level read for 1 posting(s)", applied)
        con = db.connect(path)
        self.assertEqual(con.execute("SELECT degree_level FROM jobs").fetchone()[0], "masters")
        con.close()


class TestProfile(Tracker):
    def test_shares_add_up(self):
        self.fill(1, {"bachelors": 6, "bachelors_or_equiv": 3, "masters": 1, "none": 2})
        p = companies.company_profile(self.con, 1)
        self.assertEqual(p.n, 12)
        self.assertAlmostEqual(sum(p.share(lv) for lv in degree.LEVELS), 1.0)
        self.assertAlmostEqual(p.open_share, 5 / 12)
        self.assertIn("Riverton Grid, 12 open postings in your tracker", p.line())
        self.assertIn("50% bachelor's required", p.line())

    def test_under_ten_counts_only(self):
        self.fill(2, {"bachelors": 3, "none": 2})
        p = companies.company_profile(self.con, 2)
        self.assertTrue(p.too_few)
        self.assertIsNone(p.open_share)
        self.assertIn("3 bachelor's required", p.line())
        self.assertIn("too few to compare", p.line())
        self.assertNotIn("%", p.line())

    def test_closed_and_too_short_postings_are_left_out(self):
        self.fill(1, {"bachelors": 10})
        self.job(1, TEXTS["masters"], closed=True)
        self.job(1, "Apply now.")
        p = companies.company_profile(self.con, 1)
        self.assertEqual(p.n, 10)
        self.assertEqual(p.unreadable, 1)
        self.assertIn("1 more too short to read", p.line())

    def test_certifications_are_counted(self):
        self.fill(1, {"none": 9})
        self.job(1, "CISSP and Security+ required." + LONG)
        p = companies.company_profile(self.con, 1)
        self.assertEqual(p.certs_any, 1)
        self.assertEqual(dict(p.certs), {"CISSP": 1, "CompTIA Security+": 1})

    def test_the_list_and_its_sorts(self):
        self.fill(1, {"bachelors": 8, "none": 2})
        self.fill(2, {"none": 10})
        self.job(2, "CISSP required." + LONG)
        self.job(2, "CISSP required." + LONG)
        by_open = companies.all_profiles(self.con)
        self.assertEqual([p.company for p in by_open], ["Small Co", "Riverton Grid"])
        by_certs = companies.all_profiles(self.con, sort="certs")
        self.assertEqual(by_certs[0].company, "Small Co")
        self.assertEqual(companies.all_profiles(self.con, minimum=11)[0].company, "Small Co")


class TestSurfaces(Tracker):
    def client(self):
        client, _ = make_client(self.db, self.dir / "out")
        return client

    def test_job_page_shows_the_posting_the_company_and_the_caveat(self):
        self.fill(1, {"bachelors": 9})
        job_id = self.job(1, "BS in CS or equivalent experience. CCNA a plus." + LONG)
        page = self.client().get(f"/job/{job_id}").text
        self.assertIn("This posting asks for:</strong> bachelor&#39;s or equivalent experience",
                      page)
        self.assertIn("names CCNA", page)
        self.assertIn("This company:</strong> Riverton Grid, 10 open postings", page)
        self.assertIn(degree.CAVEAT.replace("'", "&#39;"), page)

    def test_companies_page(self):
        self.fill(1, {"bachelors": 6, "none": 4})
        page = self.client().get("/companies").text
        self.assertIn("Riverton Grid", page)
        self.assertIn(degree.CAVEAT.replace("'", "&#39;"), page)
        self.assertIn("not each company", page)
        self.assertEqual(self.client().get("/companies?sort=certs").status_code, 200)
        self.assertEqual(self.client().get("/companies?sort=nonsense").status_code, 200)

    def test_the_open_filter(self):
        open_id = self.job(1, TEXTS["bachelors_or_equiv"])
        closed_door = self.job(1, TEXTS["bachelors"])
        self.con.execute("UPDATE jobs SET match_score = 0.9")
        self.con.commit()
        page = self.client().get("/?degree=open&anywhere=1").text
        self.assertIn(f"/job/{open_id}", page)
        self.assertNotIn(f"/job/{closed_door}\"", page)

    def test_cli(self):
        self.fill(1, {"bachelors": 6, "none": 4})
        from jsa import cli, config
        real = Path(config.DB_PATH)
        before = (real.stat().st_mtime_ns, real.stat().st_size) if real.exists() else None
        out = io.StringIO()
        with mock.patch.object(cli, "DB_PATH", self.db), redirect_stdout(out):
            self.assertEqual(cli.main(["companies"]), 0)
        after = (real.stat().st_mtime_ns, real.stat().st_size) if real.exists() else None
        self.assertEqual(before, after, "the real tracker was touched")
        text = out.getvalue()
        self.assertIn("Riverton Grid", text)
        self.assertIn(degree.CAVEAT, text)


if __name__ == "__main__":
    unittest.main()
