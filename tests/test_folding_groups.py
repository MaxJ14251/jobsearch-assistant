"""A posting's copies leave the matches together (plan 16, change 1).

v_new_matches folds copies of one posting into one card by dedup_key. It
used to filter BEFORE folding, so saving the best copy brought the next
copy back as a "new" card. The map's own query repeated the mistake.
"""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from jsa import approvals, db
from tests.test_map import PROFILE


class TestFolding(unittest.TestCase):
    def setUp(self):
        from tests.test_web_documents import make_client

        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        # Three copies of one posting (one dedup_key), and one other posting.
        for job_id, score, location in ((1, 0.9, "Meridian, ID"), (2, 0.8, "Boise, ID"),
                                        (3, 0.7, "Nampa, ID")):
            con.execute("INSERT INTO jobs (id,company_id,title,url,location,remote,"
                        "match_score,dedup_key) VALUES (?,1,'Support Engineer',?,?,"
                        "'onsite',?,'acme|support engineer')",
                        (job_id, f"https://acme.test/{job_id}", location, score))
        con.execute("INSERT INTO jobs (id,company_id,title,url,location,remote,match_score) "
                    "VALUES (4,1,'Data Analyst','https://acme.test/4','Boise, ID',"
                    "'onsite',0.6)")
        con.commit()
        self.con = con
        self.addCleanup(con.close)
        self.client, _ = make_client(self.path, self.dir, profile=PROFILE)

    def cards(self):
        return [r["job_id"] for r in self.con.execute("SELECT job_id FROM v_new_matches")]

    def map_keys(self):
        page = self.client.get("/?radius=100").text
        block = re.search(r'<script type="application/json" id="map-data">(.*?)</script>',
                          page, re.S)
        live = json.loads(block.group(1))
        return {p["key"] for p in live["points"] if p.get("status", "new") == "new"}

    def test_one_card_for_the_group(self):
        self.assertEqual(sorted(self.cards()), [1, 4])
        self.assertIn("acme|support engineer", self.map_keys())

    def test_saving_any_copy_hides_the_whole_group(self):
        for saved in (1, 3):                      # the best copy, or a lesser one
            with self.subTest(saved=saved):
                self.con.execute("DELETE FROM applications")
                approvals.save_application(self.con, saved)
                self.con.commit()
                self.assertEqual(self.cards(), [4])
                self.assertNotIn("acme|support engineer", self.map_keys())

    def test_other_groups_are_unchanged(self):
        approvals.save_application(self.con, 4)
        self.con.commit()
        self.assertEqual(self.cards(), [1])


if __name__ == "__main__":
    unittest.main()
