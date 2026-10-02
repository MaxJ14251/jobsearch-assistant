"""`jsa matches` shows the number every later command takes.

Found by walking the README's first hour as a newcomer (n29): step 7 says
`jsa save 421`, but `matches` printed no job numbers at all, so there was no
way to know which number to type. 421 was a job in the author's own tracker.
"""

import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import cli, db


class TestMatchesShowsTheJobNumber(unittest.TestCase):
    def setUp(self):
        self.dbfile = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(self.dbfile)
        con = db.connect(self.dbfile)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute(
            "INSERT INTO jobs (id,company_id,title,url,remote,match_score,track) "
            "VALUES (37,1,'Support Engineer','https://acme.test/37','remote',0.8,"
            "'engineering')")
        con.commit()
        con.close()
        real_connect = db.connect
        patch = mock.patch("jsa.db.connect",
                           side_effect=lambda *a, **k: real_connect(self.dbfile))
        patch.start()
        self.addCleanup(patch.stop)

    def test_each_row_carries_its_number_and_says_what_it_is_for(self):
        out = io.StringIO()
        args = Namespace(near=None, remote=False, track=None, limit=20,
                         per_company=3)
        with redirect_stdout(out):
            self.assertEqual(cli.cmd_matches(args), 0)
        text = out.getvalue()
        self.assertIn("#37 Acme", text)
        self.assertIn("jsa save <#>", text)



class TestSearchFromTheCommandLine(TestMatchesShowsTheJobNumber):
    """`jsa matches --search` uses the dashboard's search rules (Plan 7)."""

    def setUp(self):
        super().setUp()
        con = db.connect(self.dbfile)
        con.execute(
            "INSERT INTO jobs (id,company_id,title,url,remote,match_score,track) "
            "VALUES (38,1,'Data Analyst','https://acme.test/38','remote',0.7,'engineering')")
        con.commit()
        con.close()

    def listed(self, search):
        out = io.StringIO()
        args = Namespace(near=None, remote=False, track=None, limit=20,
                         per_company=3, search=search)
        with redirect_stdout(out):
            cli.cmd_matches(args)
        return out.getvalue()

    def test_a_search_narrows_the_list(self):
        text = self.listed("support")
        self.assertIn("#37 Acme", text)
        self.assertNotIn("#38", text)

    def test_commas_mean_or(self):
        text = self.listed("support, data")
        self.assertIn("#37", text)
        self.assertIn("#38", text)

    def test_no_search_lists_everything(self):
        text = self.listed(None)
        self.assertIn("#37", text)
        self.assertIn("#38", text)


if __name__ == "__main__":
    unittest.main()
