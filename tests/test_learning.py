"""Ranking nudged by your own swipes (plan 19, ADR 0029): local, bounded,
explained, resettable; stored scores never change."""

import ast
import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from jsa import approvals, db, learning, turbo, web
from jsa.config import ROOT

PROFILE = {"job_search_preferences": {"target_titles": ["Engineer"],
                                      "locations": ["Remote (US)"]}}


class Tracker(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(self.path)
        self.con = db.connect(self.path)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        self.con.commit()
        self.next = 1

    def job(self, title, score=0.5, remote="remote", stack=None):
        job_id = self.next
        self.next += 1
        self.con.execute(
            "INSERT INTO jobs (id,company_id,title,url,remote,match_score,track,tech_stack) "
            "VALUES (?,1,?,?,?,?,'engineering',?)",
            (job_id, title, f"https://acme.test/{job_id}", remote, score,
             __import__("json").dumps(stack or [])))
        return job_id

    def swipes(self, title, saves, passes, **kw):
        for _ in range(saves):
            approvals.save_application(self.con, self.job(title, **kw))
        for _ in range(passes):
            turbo.pass_job(self.con, self.job(title, **kw))
        self.con.commit()

    def fill(self, n=30):
        """Neutral swipes to reach the threshold."""
        for i in range(n):
            j = self.job(f"Neutral Role {i % 2}", remote="onsite")
            if i % 2:
                approvals.save_application(self.con, j)
            else:
                turbo.pass_job(self.con, j)
        self.con.commit()


class TestTheModel(Tracker):
    def test_below_the_threshold_nothing_changes(self):
        self.swipes("Support Engineer", 6, 1)
        model = learning.build(self.con)
        self.assertFalse(model.active)
        self.assertEqual(learning.nudge({"title": "Support Engineer"}, model), (0.0, []))

    def test_a_favoured_feature_nudges_up_and_a_passed_one_down(self):
        self.fill()
        self.swipes("Support Engineer", 6, 1)
        self.swipes("Sales Executive", 1, 6)
        model = learning.build(self.con)
        up, why_up = learning.nudge({"title": "Support Engineer", "track": "engineering"}, model)
        down, why_down = learning.nudge({"title": "Sales Executive", "track": "sales"}, model)
        self.assertGreater(up, 0)
        self.assertLess(down, 0)
        self.assertRegex(why_up[0], r"^\+0\.\d\d: you saved \d+ of \d+ ")
        self.assertRegex(why_down[0], r"^-0\.\d\d: you passed \d+ of \d+ ")

    def test_a_feature_seen_four_times_is_ignored(self):
        self.fill()
        self.swipes("Quantum Wrangler", 4, 0)
        model = learning.build(self.con)
        self.assertEqual(model.contribution(("word", "quantum")), 0.0)

    def test_the_total_is_clamped(self):
        self.fill()
        self.swipes("Support Engineer", 20, 0, stack=["Python", "SQL", "Go"])
        model = learning.build(self.con)
        delta, _ = learning.nudge({"title": "Support Engineer", "remote": "remote",
                                   "tech_stack": ["Python", "SQL", "Go"]}, model)
        self.assertLessEqual(abs(delta), learning.MAX_NUDGE)

    def test_an_unpassed_job_stops_counting(self):
        self.fill()
        before = learning.build(self.con).passes
        turbo.unpass(self.con, 1)                 # fill()'s first job was passed
        self.con.commit()
        self.assertEqual(learning.build(self.con).passes, before - 1)

    def test_reset_ignores_older_swipes_and_deletes_nothing(self):
        self.fill()
        self.con.execute("UPDATE applications SET saved_at = '2026-01-01T00:00:00Z'")
        self.con.execute("UPDATE jobs SET passed_at = '2026-01-01T00:00:00Z' "
                         "WHERE passed_at IS NOT NULL")
        learning.reset(self.con)
        self.con.commit()
        self.assertEqual(learning.build(self.con).signals, 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 15)


class TestRank(Tracker):
    def rows(self):
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM v_new_matches ORDER BY match_score DESC")]

    def test_off_or_under_the_threshold_the_order_is_identical(self):
        for i in range(5):
            self.job(f"Role {i}", score=0.5 + i / 100)
        self.con.commit()
        before = self.rows()
        self.assertEqual(learning.rank(self.con, self.rows(), PROFILE), before)
        self.fill()
        off = {**PROFILE, "ranking": {"learn_from_swipes": False}}
        rows = self.rows()
        self.assertEqual(learning.rank(self.con, rows, off), rows)

    def test_a_near_tie_flips_and_a_wide_gap_does_not(self):
        self.fill()
        self.swipes("Support Engineer", 8, 0)
        self.swipes("Sales Executive", 0, 8)
        a = self.job("Support Engineer Two", score=0.70)
        b = self.job("Sales Executive Two", score=0.72)
        c = self.job("Sales Executive Three", score=0.95)
        d = self.job("Support Engineer Three", score=0.60)
        self.con.commit()
        rows = [r for r in self.rows() if r["job_id"] in (a, b, c, d)]
        order = [r["job_id"] for r in learning.rank(self.con, rows, PROFILE)]
        self.assertLess(order.index(a), order.index(b))      # near-tie flips
        self.assertLess(order.index(c), order.index(d))      # 0.35 gap holds
        stored = dict(self.con.execute("SELECT id, match_score FROM jobs "
                                       "WHERE id IN (?,?)", (a, b)).fetchall())
        self.assertEqual(stored, {a: 0.70, b: 0.72})          # nothing written

    def test_the_card_says_why(self):
        self.fill()
        self.swipes("Support Engineer", 8, 0)
        j = self.job("Support Engineer Two", score=0.7)
        self.con.commit()
        row = [r for r in learning.rank(self.con, self.rows(), PROFILE) if r["job_id"] == j][0]
        self.assertTrue(any(x.startswith("learned from your swipes, +") for x in row["reasons"]))


class TestControls(Tracker):
    def test_jsa_learned_below_and_reset(self):
        from jsa import cli
        real = db.connect
        out = io.StringIO()
        with mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path)), \
             mock.patch("jsa.db.upgrade", return_value=[]), \
             mock.patch("jsa.cli.load_profile", return_value=PROFILE), \
             redirect_stdout(out):
            cli.cmd_learned(Namespace(reset=False))
            cli.cmd_learned(Namespace(reset=True))
        self.assertIn("Learning starts after 30 swipes (0 so far)", out.getvalue())
        self.assertIsNotNone(learning.reset_at(self.con))

    def test_the_reset_button_needs_the_token(self):
        self.con.commit()
        app = web.create_app(db_path=self.path, profile_loader=lambda: PROFILE)
        c = TestClient(app, base_url="http://127.0.0.1:8765")
        self.addCleanup(c.close)
        self.assertEqual(c.post("/learning/reset", data={"back": "/turbo"}).status_code, 403)
        r = c.post("/learning/reset", data={"csrf": app.state.csrf_token, "back": "/turbo"},
                   follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIsNotNone(learning.reset_at(self.con))

    def test_no_model(self):
        tree = ast.parse((ROOT / "jsa" / "learning.py").read_text(encoding="utf-8"))
        names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        self.assertFalse([n for n in names if "llm" in n])


if __name__ == "__main__":
    unittest.main()
