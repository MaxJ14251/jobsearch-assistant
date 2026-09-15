"""The human approval gate.

TestAgentCannotSelfApprove is the project's central safety claim. It attempts
the write a buggy or over-eager agent would attempt and asserts the database
refuses it. If someone ever drops the trigger, this fails loudly.

DO NOT DELETE THESE TESTS. They are the difference between "designed for human
control" and "human control is enforced".
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsa import approvals, db
from jsa.approvals import ApprovalError, approve, is_approved, pending, queue, reject


class ApprovalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        self.con = db.connect(self.dbfile)
        self.con.execute(
            "INSERT INTO companies (id, name, slug) VALUES (1,'Example','example')")
        self.con.execute(
            "INSERT INTO jobs (id, company_id, title, url) "
            "VALUES (1,1,'Engineer','https://example.com/j/1')")
        self.con.execute(
            "INSERT INTO applications (id, job_id, status) VALUES (1,1,'ready')")
        self.approval_id = queue(self.con, "application", 1, "Apply to Example")

    def tearDown(self):
        self.con.close()


class TestAgentCannotSelfApprove(ApprovalCase):
    """The central safety guarantee, tested at the database level."""

    def test_bare_update_is_refused(self):
        """Exactly what a buggy agent would run."""
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.con.execute(
                "UPDATE approvals SET decision='approved' WHERE id = ?",
                (self.approval_id,),
            )
        self.assertIn("decided_by=human", str(ctx.exception))

    def test_claiming_a_non_human_actor_is_refused(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "UPDATE approvals SET decision='approved', decided_by='agent', "
                "decided_at='2026-09-14T00:00:00Z' WHERE id = ?",
                (self.approval_id,),
            )

    def test_human_without_a_timestamp_is_refused(self):
        """decided_by alone isn't enough — an approval must be dated."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "UPDATE approvals SET decision='approved', decided_by='human' "
                "WHERE id = ?", (self.approval_id,),
            )

    def test_rejection_is_equally_guarded(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "UPDATE approvals SET decision='rejected' WHERE id = ?",
                (self.approval_id,),
            )

    def test_the_trigger_still_exists(self):
        """If this fails, every test above is silently meaningless."""
        row = self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_approval_requires_human'"
        ).fetchone()
        self.assertIsNotNone(row, "the human-approval trigger has been removed")

    def test_pending_is_the_only_default(self):
        row = self.con.execute(
            "SELECT decision, decided_by FROM approvals WHERE id = ?",
            (self.approval_id,)).fetchone()
        self.assertEqual(row["decision"], "pending")
        self.assertIsNone(row["decided_by"])

    def test_is_approved_is_false_until_a_human_decides(self):
        self.assertFalse(is_approved(self.con, "application", 1))


class TestHumanPath(ApprovalCase):
    def test_approve_records_actor_and_timestamp(self):
        approve(self.con, self.approval_id, note="looks good")
        row = self.con.execute(
            "SELECT * FROM approvals WHERE id = ?", (self.approval_id,)).fetchone()
        self.assertEqual(row["decision"], "approved")
        self.assertEqual(row["decided_by"], "human")
        self.assertTrue(row["decided_at"])
        self.assertTrue(is_approved(self.con, "application", 1))

    def test_reject_requires_feedback(self):
        with self.assertRaises(ApprovalError):
            reject(self.con, self.approval_id, "")
        with self.assertRaises(ApprovalError):
            reject(self.con, self.approval_id, "   ")

    def test_reject_stores_feedback(self):
        reject(self.con, self.approval_id, "Tone is too formal for this team.")
        row = self.con.execute(
            "SELECT decision, feedback FROM approvals WHERE id = ?",
            (self.approval_id,)).fetchone()
        self.assertEqual(row["decision"], "rejected")
        self.assertIn("too formal", row["feedback"])

    def test_a_decision_cannot_be_silently_changed(self):
        approve(self.con, self.approval_id)
        with self.assertRaises(ApprovalError) as ctx:
            reject(self.con, self.approval_id, "changed my mind")
        self.assertIn("already approved", str(ctx.exception))

    def test_new_version_needs_its_own_approval(self):
        """A redraft must never ride on the previous yes."""
        approve(self.con, self.approval_id)
        second = queue(self.con, "application", 1, "Apply to Example (v2)")
        self.assertNotEqual(second, self.approval_id)
        ids = [p.approval_id for p in pending(self.con)]
        self.assertEqual(ids, [second])

    def test_pending_empties_once_decided(self):
        approve(self.con, self.approval_id)
        self.assertEqual(pending(self.con), [])


class TestEventTrail(ApprovalCase):
    def test_status_change_is_recorded_with_actor(self):
        approvals.record_event(self.con, 1, "applied", actor="human",
                               note="submitted by hand")
        row = self.con.execute(
            "SELECT * FROM application_events WHERE application_id = 1 "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["from_status"], "ready")
        self.assertEqual(row["to_status"], "applied")
        self.assertEqual(row["actor"], "human")
        status = self.con.execute(
            "SELECT status FROM applications WHERE id = 1").fetchone()[0]
        self.assertEqual(status, "applied")

    def test_trigger_touches_last_activity(self):
        approvals.record_event(self.con, 1, "phone_screen")
        row = self.con.execute(
            "SELECT last_activity_at FROM applications WHERE id = 1").fetchone()
        self.assertTrue(row["last_activity_at"])



class TestOnePendingPerSubject(ApprovalCase):
    """Regression: the old UNIQUE keyed on a second-resolution timestamp.

    Queueing a redraft within the same second as the original raised
    IntegrityError — breaking the reject-then-re-render flow entirely. The
    constraint now expresses the real rule instead: many decided approvals per
    subject over time, but only one awaiting a decision.
    """

    def test_redraft_in_the_same_second_is_allowed(self):
        approve(self.con, self.approval_id)
        for n in range(3):                       # all within one second
            aid = queue(self.con, "application", 1, f"redraft {n}")
            approve(self.con, aid)
        total = self.con.execute(
            "SELECT COUNT(*) FROM approvals WHERE subject_id = 1").fetchone()[0]
        self.assertEqual(total, 4)

    def test_two_pending_for_one_subject_is_refused(self):
        """Two open asks for the same thing is genuinely ambiguous."""
        with self.assertRaises(sqlite3.IntegrityError):
            queue(self.con, "application", 1, "a second pending ask")

    def test_different_subjects_may_both_be_pending(self):
        self.con.execute(
            "INSERT INTO jobs (id, company_id, title, url) "
            "VALUES (2,1,'Other','https://example.com/j/2')")
        self.con.execute(
            "INSERT INTO applications (id, job_id, status) VALUES (2,2,'ready')")
        second = queue(self.con, "application", 2, "Apply to Other")
        self.assertEqual(len(pending(self.con)), 2)
        self.assertNotEqual(second, self.approval_id)

    def test_the_partial_index_exists(self):
        row = self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_approvals_one_pending'").fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
