"""Application lifecycle and the approval surface.

See docs/decisions/0003-application-lifecycle.md.

TestBothSurfacesEnforceHumanApproval is the one that must never be deleted.
tests/test_approvals.py already proves the database refuses a self-approval;
this proves that BOTH ways a decision can be reached — the CLI and the web
route — go through that same refusal, rather than one of them having quietly
grown a second path.
"""

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsa import approvals, db
from jsa.approvals import (
    ApprovalError,
    mark_applied,
    require_application,
    save_application,
    set_document_pointer,
)
from jsa.config import ROOT


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        self.con = db.connect(self.dbfile)
        self.con.execute(
            "INSERT INTO companies (id, name, slug) VALUES (1,'Example','example')")
        for job_id in (1, 2):
            self.con.execute(
                "INSERT INTO jobs (id, company_id, title, url) VALUES (?,1,?,?)",
                (job_id, f"Engineer {job_id}", f"https://example.com/j/{job_id}"),
            )

    def tearDown(self):
        self.con.close()


class TestSaveCreatesTheApplication(LifecycleCase):
    """Decision 1: an application is created explicitly, never implicitly."""

    def test_save_creates_one(self):
        app_id, created = save_application(self.con, 1)
        self.assertTrue(created)
        row = self.con.execute(
            "SELECT job_id, status FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        self.assertEqual(row["job_id"], 1)
        self.assertEqual(row["status"], "saved")

    def test_saving_twice_returns_the_same_application(self):
        """applications.job_id is NOT NULL UNIQUE — a duplicate is impossible."""
        first, created_first = save_application(self.con, 1)
        second, created_second = save_application(self.con, 1)
        self.assertEqual(first, second)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        count = self.con.execute(
            "SELECT count(*) c FROM applications WHERE job_id = 1").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_save_leaves_an_event_trail(self):
        app_id, _ = save_application(self.con, 1, note="worth pursuing")
        row = self.con.execute(
            "SELECT to_status, actor, note FROM application_events "
            "WHERE application_id = ?", (app_id,)
        ).fetchone()
        self.assertEqual(row["to_status"], "saved")
        self.assertEqual(row["actor"], "human")
        self.assertEqual(row["note"], "worth pursuing")

    def test_saving_an_unknown_job_is_refused(self):
        with self.assertRaises(ApprovalError):
            save_application(self.con, 999999)

    def test_require_application_names_the_command_to_run(self):
        with self.assertRaises(ApprovalError) as ctx:
            require_application(self.con, 1)
        self.assertIn("jsa save 1", str(ctx.exception),
                      "the refusal must say what to run")
        save_application(self.con, 1)
        self.assertIsInstance(require_application(self.con, 1), int)


class TestDocumentPointer(LifecycleCase):
    """Decision 2: documents.job_id is authoritative; the pointer is a cache.

    The schema calls those columns "kept in sync by the app layer", which is
    the exact phrase that precedes two copies of a fact drifting apart. One
    writer, and it refuses to cross jobs.
    """

    def _document(self, job_id: int) -> int:
        cur = self.con.execute(
            "INSERT INTO documents (job_id, kind, path, format) "
            "VALUES (?,'resume',?,'docx')", (job_id, f"/tmp/r{job_id}.docx"))
        return int(cur.lastrowid)

    def test_pointer_is_set(self):
        app_id, _ = save_application(self.con, 1)
        doc_id = self._document(1)
        set_document_pointer(self.con, app_id, "resume", doc_id)
        row = self.con.execute(
            "SELECT resume_doc_id FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        self.assertEqual(row["resume_doc_id"], doc_id)

    def test_pointer_across_jobs_is_refused(self):
        """The corruption this invariant exists to prevent."""
        app_id, _ = save_application(self.con, 1)
        other_doc = self._document(2)          # belongs to a different job
        with self.assertRaises(ApprovalError) as ctx:
            set_document_pointer(self.con, app_id, "resume", other_doc)
        self.assertIn("job", str(ctx.exception).lower())

    def test_unknown_kind_is_refused(self):
        app_id, _ = save_application(self.con, 1)
        doc_id = self._document(1)
        with self.assertRaises(ApprovalError):
            set_document_pointer(self.con, app_id, "portfolio_note", doc_id)


class TestMarkApplied(LifecycleCase):
    """Decision 4: the tracker records reality; it does not police the human."""

    def test_applied_without_an_approval_is_recorded_not_refused(self):
        """The approval gate stops the AGENT acting, not the human choosing."""
        save_application(self.con, 1)
        app_id, approved = mark_applied(self.con, 1)
        self.assertFalse(approved)
        row = self.con.execute(
            "SELECT status, applied_at FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        self.assertEqual(row["status"], "applied")
        self.assertIsNotNone(row["applied_at"])

    def test_the_event_note_says_there_was_no_approved_document(self):
        """Permissive, but the trail stays honest."""
        save_application(self.con, 1)
        mark_applied(self.con, 1)
        note = self.con.execute(
            "SELECT note FROM application_events WHERE to_status = 'applied'"
        ).fetchone()["note"]
        self.assertIn("no approved document", note)

    def test_applied_on_an_unsaved_job_is_an_error(self):
        """Never a silent create — one of the two actions is a mistake."""
        with self.assertRaises(ApprovalError):
            mark_applied(self.con, 1)

    def test_explicit_date_is_honoured(self):
        save_application(self.con, 1)
        app_id, _ = mark_applied(self.con, 1, when="2026-01-02T03:04:05Z")
        row = self.con.execute(
            "SELECT applied_at FROM applications WHERE id = ?", (app_id,)).fetchone()
        self.assertEqual(row["applied_at"], "2026-01-02T03:04:05Z")


class TestBothSurfacesEnforceHumanApproval(LifecycleCase):
    """DO NOT DELETE.

    tests/test_approvals.py proves the database refuses a self-approval. This
    proves that neither surface has grown a way around it.
    """

    def setUp(self):
        super().setUp()
        save_application(self.con, 1)
        self.approval_id = approvals.queue(self.con, "application", 1, "Apply?")

    def test_raw_update_without_decided_by_is_refused(self):
        """Exactly what a buggy agent would run."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "UPDATE approvals SET decision = 'approved' WHERE id = ?",
                (self.approval_id,),
            )

    def test_decided_by_agent_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "UPDATE approvals SET decision = 'approved', decided_by = 'agent', "
                "decided_at = '2026-01-01T00:00:00Z' WHERE id = ?",
                (self.approval_id,),
            )

    def test_cli_path_reaches_the_same_function(self):
        from jsa import cli
        self.assertTrue(hasattr(cli, "cmd_approve"))
        source = (ROOT / "jsa" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("approvals.approve(", source,
                      "the CLI must delegate, not re-implement")
        self.assertIn("approvals.reject(", source)

    def test_web_path_reaches_the_same_function(self):
        source = (ROOT / "jsa" / "web.py").read_text(encoding="utf-8")
        self.assertIn("approvals.approve(", source,
                      "the web route must delegate, not re-implement")
        self.assertIn("approvals.reject(", source)

    def test_only_approvals_module_writes_decided_by(self):
        """Decision 5, locked mechanically.

        A future contributor adding a convenient shortcut breaks the build
        rather than the guarantee. Comments are stripped first — cli.py
        legitimately explains the rule in prose.
        """
        offenders = []
        for path in sorted((ROOT / "jsa").glob("*.py")):
            if path.name == "approvals.py":
                continue
            code = "\n".join(
                re.sub(r"#.*$", "", line)
                for line in path.read_text(encoding="utf-8").splitlines()
            )
            if "decided_by" in code:
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            f"{offenders} write decided_by. approvals._decide() is the only "
            "place a human decision may be recorded.",
        )


class TestRejectionFlow(LifecycleCase):
    """A decision is per version and never inherited."""

    def setUp(self):
        super().setUp()
        save_application(self.con, 1)
        self.approval_id = approvals.queue(self.con, "application", 1, "Apply?")

    def test_empty_feedback_is_refused_with_guidance(self):
        with self.assertRaises(ApprovalError) as ctx:
            approvals.reject(self.con, self.approval_id, "   ")
        self.assertIn("feedback", str(ctx.exception).lower())

    def test_a_settled_approval_cannot_be_re_decided(self):
        approvals.reject(self.con, self.approval_id, "wrong team")
        with self.assertRaises(ApprovalError):
            approvals.approve(self.con, self.approval_id)

    def test_requeue_creates_a_new_row_and_inherits_nothing(self):
        approvals.reject(self.con, self.approval_id, "wrong team")
        new_id = approvals.queue(self.con, "application", 1, "redrafted")
        self.assertNotEqual(new_id, self.approval_id)
        row = self.con.execute(
            "SELECT decision, decided_by, feedback FROM approvals WHERE id = ?",
            (new_id,)).fetchone()
        self.assertEqual(row["decision"], "pending")
        self.assertIsNone(row["decided_by"])
        self.assertIsNone(row["feedback"])

    def test_two_pending_for_one_subject_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            approvals.queue(self.con, "application", 1, "duplicate")

    def test_nothing_pending_once_everything_is_decided(self):
        approvals.approve(self.con, self.approval_id)
        self.assertEqual(approvals.pending(self.con), [])


if __name__ == "__main__":
    unittest.main()
