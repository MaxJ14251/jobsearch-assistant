"""Two small intake bugs found while planning `jsa find` (Plan 21).

- A posting pasted with `jsa add --paste` is the operator's own text, as one
  pasted with `jsa fill` is, so discovery must never overwrite it.
- An aggregator posting that names an employer from companies.yaml must not
  reset that employer's hand-set priority.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from jsa import db, intake
from tests.test_fill import REAL, prefs


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        db.init_db(self.dir / "t.db")
        self.con = db.connect(self.dir / "t.db")
        self.addCleanup(self.con.close)


class TestAPastedPostingIsMarkedAsPasted(Case):
    def add(self, text=REAL):
        return intake.add_pasted(self.con, company="Acme", title="Forward Deployed Engineer",
                                 text=text, prefs=prefs(), url="https://acme.example/jobs/1",
                                 location="Remote (US)")

    def test_it_is_stored_as_pasted(self):
        added = self.add()
        row = self.con.execute("SELECT description_origin FROM jobs WHERE id = ?",
                               (added.job_id,)).fetchone()
        self.assertEqual(row["description_origin"], "pasted")

    def test_a_feed_row_with_the_same_key_does_not_overwrite_it(self):
        added = self.add()
        row = self.con.execute("SELECT * FROM jobs WHERE id = ?", (added.job_id,)).fetchone()
        db.upsert_job(self.con, {**dict(row), "description": "stub", "match_reasons": [],
                                 "description_origin": None})
        text = self.con.execute("SELECT description FROM jobs WHERE id = ?",
                                (added.job_id,)).fetchone()[0]
        self.assertEqual(text, REAL)

    def test_pasting_it_again_still_updates_it(self):
        added = self.add()
        again = self.add(REAL + " Benefits include a learning budget.")
        self.assertEqual(again.job_id, added.job_id)
        text = self.con.execute("SELECT description FROM jobs WHERE id = ?",
                                (added.job_id,)).fetchone()[0]
        self.assertTrue(text.endswith("learning budget."))


class TestPriorityIsKept(Case):
    def test_an_aggregator_naming_the_employer_keeps_its_priority(self):
        cid = db.upsert_company(self.con, name="Perplexity", slug="perplexity", priority=1)
        db.upsert_company(self.con, name="Perplexity", slug="perplexity")
        self.assertEqual(self.con.execute("SELECT priority FROM companies WHERE id = ?",
                                          (cid,)).fetchone()[0], 1)

    def test_a_passed_priority_still_changes_it(self):
        cid = db.upsert_company(self.con, name="Acme", slug="acme", priority=1)
        db.upsert_company(self.con, name="Acme", slug="acme", priority=2)
        self.assertEqual(self.con.execute("SELECT priority FROM companies WHERE id = ?",
                                          (cid,)).fetchone()[0], 2)

    def test_a_new_company_defaults_to_three(self):
        cid = db.upsert_company(self.con, name="Beta", slug="beta")
        self.assertEqual(self.con.execute("SELECT priority FROM companies WHERE id = ?",
                                          (cid,)).fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
