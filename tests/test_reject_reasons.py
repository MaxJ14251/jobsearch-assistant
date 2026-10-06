"""Reason codes on a rejection (Plan 11 part 6, ADR 0024).

The reviewer picks the code; nothing classifies feedback automatically, and
the code never reaches a prompt.
"""

import io
import json
import re
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import approvals, db
from jsa.config import ROOT
from tests.test_feedback import add_version, tracker


class TestTheCode(unittest.TestCase):
    def setUp(self):
        self.con = tracker()
        self.addCleanup(self.con.close)

    def reason_of(self, approval):
        return self.con.execute("SELECT reason, feedback, decided_by FROM approvals "
                                "WHERE id = ?", (approval,)).fetchone()

    def test_a_picked_code_is_stored_beside_the_text(self):
        _, a = add_version(self.con)
        approvals.reject(self.con, a, "Lead with the support work.", "wrong_bullets")
        row = self.reason_of(a)
        self.assertEqual(tuple(row), ("wrong_bullets", "Lead with the support work.", "human"))

    def test_the_code_is_optional(self):
        _, a = add_version(self.con)
        approvals.reject(self.con, a, "Too long.")
        self.assertIsNone(self.reason_of(a)["reason"])

    def test_an_unknown_code_is_refused_and_nothing_is_decided(self):
        _, a = add_version(self.con)
        with self.assertRaises(approvals.ApprovalError):
            approvals.reject(self.con, a, "Too long.", "too_long")
        self.assertEqual(self.con.execute("SELECT decision FROM approvals WHERE id = ?",
                                          (a,)).fetchone()[0], "pending")

    def test_feedback_is_still_required(self):
        _, a = add_version(self.con)
        with self.assertRaises(approvals.ApprovalError):
            approvals.reject(self.con, a, "  ", "formatting")


class TestTheColumnArrivesOnOldTrackers(unittest.TestCase):
    def test_migrate_adds_reason_without_a_rebuild(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        schema = (ROOT / "jsa" / "resources" / "schema.sql").read_text(encoding="utf-8")
        old = "\n".join(ln for ln in schema.splitlines()
                        if not ln.strip().startswith("reason          TEXT"))
        con = sqlite3.connect(path)
        con.executescript(old)
        con.close()
        con = db.connect(path)
        self.addCleanup(con.close)
        self.assertNotIn("reason", [r["name"] for r in con.execute("PRAGMA table_info(approvals)")])
        db.migrate(con)
        self.assertIn("reason", [r["name"] for r in con.execute("PRAGMA table_info(approvals)")])


class TestWhereYouPickIt(unittest.TestCase):
    def test_cli_reject_takes_a_reason(self):
        from jsa import cli
        con = tracker()
        _, a = add_version(con)
        path = Path(con.execute("PRAGMA database_list").fetchone()["file"])
        con.close()
        real = db.connect
        with mock.patch("jsa.db.connect", side_effect=lambda *x, **k: real(path)), \
             redirect_stdout(io.StringIO()):
            code = cli.main(["reject", str(a), "--feedback", "Wrong summary.",
                             "--reason", "wrong_summary"])
        self.assertEqual(code, 0)
        con = real(path)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT reason FROM approvals WHERE id = ?",
                                     (a,)).fetchone()[0], "wrong_summary")

    def test_the_review_page_offers_the_codes_and_stores_one(self):
        from fastapi.testclient import TestClient
        from jsa import web
        con = tracker()
        _, a = add_version(con)
        path = Path(con.execute("PRAGMA database_list").fetchone()["file"])
        con.close()
        app = web.create_app(db_path=path, profile_loader=lambda: {})
        with TestClient(app, base_url="http://127.0.0.1:8765") as c:
            page = c.get("/review").text
            self.assertIn('<option value="wrong_bullets">wrong bullets</option>', page)
            c.post("/reject", data={"csrf": app.state.csrf_token, "approval_id": a,
                                    "feedback": "Bullets miss the support work.",
                                    "reason": "wrong_bullets"}, follow_redirects=False)
        con = db.connect(path)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT reason FROM approvals WHERE id = ?",
                                     (a,)).fetchone()[0], "wrong_bullets")


class TestRedraftHint(unittest.TestCase):
    def test_last_times_bullets_come_back_only_after_wrong_bullets(self):
        from jsa.drafting import wrong_bullets_before
        con = tracker()
        self.addCleanup(con.close)
        doc1, a1 = add_version(con, version=1)
        con.execute("UPDATE documents SET bullet_ids = ? WHERE id = ?",
                    (json.dumps(["b_old_1", "b_old_2"]), doc1))
        self.assertEqual(wrong_bullets_before(con, 5, "resume", 2), [])
        approvals.reject(con, a1, "Not these bullets.", "wrong_bullets")
        self.assertEqual(wrong_bullets_before(con, 5, "resume", 2), ["b_old_1", "b_old_2"])
        self.assertEqual(wrong_bullets_before(con, 5, "resume", 3), [])
        self.assertEqual(wrong_bullets_before(con, 5, "cover_letter", 2), [])


class TestNeverInAPrompt(unittest.TestCase):
    def test_no_module_feeds_a_reason_code_into_a_prompt(self):
        offenders = []
        for path in sorted((ROOT / "jsa").rglob("*.py")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("#"):
                    continue
                if re.search(r"reject_reason|REJECT_REASONS|last_wrong_bullets|a\.reason", line) \
                        and re.search(r"prompt|complete\(|PROMPT\.format", line):
                    offenders.append(f"{path.name}: {line.strip()[:70]}")
        self.assertEqual(offenders, [])

    def test_the_prompt_builders_never_read_the_reason(self):
        for name in ("tailor.py", "letter.py", "outreach.py", "prep.py", "llm.py"):
            text = (ROOT / "jsa" / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"reject_reason|REJECT_REASONS|wrong_bullets", name)


if __name__ == "__main__":
    unittest.main()
