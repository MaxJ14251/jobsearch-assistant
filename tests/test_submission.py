"""What was sent. Fixed when `jsa applied` runs, never moved by drafting after.

Before this, the only record was applications.resume_doc_id, which means
"latest drafted" and is rewritten on every draft. Run against that pointer,
scenario k printed:

    sent doc 1; after a redraft the application says 2: MOVED

and the Scale AI application, with two approved resumes, could not answer
"what did I send them?" at all. See docs/decisions/0012-what-applied-means.md.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsa import approvals, db
from jsa.approvals import ApprovalError, NOT_SENT


class SubmissionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        path = self.tmp / "t.db"
        db.init_db(path)
        self.con = db.connect(path)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        self.con.execute("INSERT INTO jobs (id,company_id,title,url) "
                         "VALUES (5,1,'Support Engineer','https://acme.test/5')")
        self.app, _ = approvals.save_application(self.con, 5)

    def draft(self, kind="resume", *, approve=False, bullets=("b1",)) -> int:
        """What drafting does, minus the model: file, row, pointer, queue."""
        version = self.con.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM documents WHERE job_id=5 "
            "AND kind=?", (kind,)).fetchone()[0]
        file = self.tmp / f"{kind}-v{version}.docx"
        file.write_bytes(f"{kind} {version}".encode())
        doc = int(self.con.execute(
            "INSERT INTO documents (job_id,kind,path,version,bullet_ids) "
            "VALUES (5,?,?,?,?)",
            (kind, str(file), version, json.dumps(list(bullets)))).lastrowid)
        approvals.set_document_pointer(self.con, self.app, kind, doc)
        approval = approvals.queue(self.con, "document", doc, f"{kind} v{version}")
        if approve:
            approvals.approve(self.con, approval)
        return doc

    def sent(self) -> dict[str, approvals.Sent]:
        return {s.kind: s for s in approvals.submitted(self.con, self.app)}


class TestK_DraftingAfterApplyingMovesNothing(SubmissionCase):
    def test_the_record_holds_while_the_pointer_moves(self):
        resume = self.draft(approve=True)
        approvals.mark_applied(self.con, 5)
        later = self.draft()

        pointer, = self.con.execute(
            "SELECT resume_doc_id FROM applications").fetchone()
        self.assertEqual(pointer, later, "the pointer means latest drafted")
        self.assertEqual(self.sent()["resume"].document_id, resume,
                         "what was sent moved with it")

    def test_the_database_refuses_an_edit_to_the_record(self):
        self.draft(approve=True)
        approvals.mark_applied(self.con, 5)
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("UPDATE submitted_documents SET document_id = 99")


class TestL_AnUnapprovedLetterIsNotApproval(SubmissionCase):
    def setUp(self):
        super().setUp()
        self.resume = self.draft(approve=True)
        self.letter = self.draft("cover_letter")          # pending

    def test_it_is_never_assumed_sent(self):
        _, all_approved = approvals.mark_applied(self.con, 5)
        self.assertEqual(set(self.sent()), {"resume"})
        self.assertTrue(all_approved, "everything RECORDED was approved")
        left = approvals.unsent_drafts(self.con, 5, list(self.sent().values()))
        self.assertEqual([r["id"] for r in left], [self.letter],
                         "the letter is named back to the operator")

    def test_named_it_is_recorded_as_exactly_what_it_was(self):
        _, all_approved = approvals.mark_applied(self.con, 5, cover=self.letter)
        self.assertFalse(all_approved,
                         "the old check said approved: ANY document was")
        self.assertTrue(self.sent()["resume"].approved)
        self.assertFalse(self.sent()["cover_letter"].approved)
        note, = self.con.execute(
            "SELECT note FROM application_events WHERE to_status='applied'"
        ).fetchone()
        self.assertIn("cover letter", note)
        self.assertIn("NOT approved", note)

    def test_it_can_be_added_afterwards_but_never_changed(self):
        approvals.mark_applied(self.con, 5)
        approvals.set_stage(self.con, 5, "phone_screen")
        approvals.mark_applied(self.con, 5, cover=self.letter)
        self.assertEqual(self.sent()["cover_letter"].document_id, self.letter)
        status, = self.con.execute("SELECT status FROM applications").fetchone()
        self.assertEqual(status, "phone_screen", "an addition reset the stage")
        with self.assertRaises(ApprovalError):
            approvals.mark_applied(self.con, 5, resume=self.draft(approve=True))

    def test_no_cover_means_none_even_when_one_is_approved(self):
        self.draft("cover_letter", approve=True)
        approvals.mark_applied(self.con, 5, cover=NOT_SENT)
        self.assertNotIn("cover_letter", self.sent())


class TestM_TwoApprovedResumesNameTheOneSent(SubmissionCase):
    def setUp(self):
        super().setUp()
        self.v1 = self.draft(approve=True)
        self.v2 = self.draft(approve=True)

    def test_two_approved_and_none_named_is_refused(self):
        """The Scale AI case. A permanent record is not written on a guess."""
        with self.assertRaises(ApprovalError) as ctx:
            approvals.mark_applied(self.con, 5)
        self.assertIn(f"--resume {self.v2}", str(ctx.exception))
        self.assertEqual(self.sent(), {}, "a refusal left a partial record")
        applied, = self.con.execute(
            "SELECT applied_at FROM applications").fetchone()
        self.assertIsNone(applied)

    def test_a_refusal_on_the_letter_writes_no_resume_either(self):
        self.draft("cover_letter", approve=True)
        self.draft("cover_letter", approve=True)
        with self.assertRaises(ApprovalError):
            approvals.mark_applied(self.con, 5, resume=self.v1)
        self.assertEqual(self.sent(), {})

    def test_the_newer_one_can_be_named(self):
        approvals.mark_applied(self.con, 5, resume=self.v2)
        self.assertEqual(self.sent()["resume"].document_id, self.v2)

    def test_the_older_one_can_be_named(self):
        approvals.mark_applied(self.con, 5, resume=self.v1)
        self.assertEqual(self.sent()["resume"].version, 1)


class TestTheDefaultIsNeverTheNewestDraft(SubmissionCase):
    def test_a_newer_unapproved_draft_is_passed_over(self):
        approved = self.draft(approve=True)
        self.draft()
        approvals.mark_applied(self.con, 5)
        self.assertEqual(self.sent()["resume"].document_id, approved)


class TestTheRecordIsExact(SubmissionCase):
    def test_the_file_contents_are_fingerprinted(self):
        import hashlib
        doc = self.draft(approve=True)
        path, = self.con.execute("SELECT path FROM documents WHERE id=?",
                                 (doc,)).fetchone()
        approvals.mark_applied(self.con, 5)
        self.assertEqual(self.sent()["resume"].sha256,
                         hashlib.sha256(Path(path).read_bytes()).hexdigest())

    def test_a_missing_file_is_recorded_as_missing_not_invented(self):
        doc = self.draft(approve=True)
        Path(self.con.execute("SELECT path FROM documents WHERE id=?",
                              (doc,)).fetchone()[0]).unlink()
        approvals.mark_applied(self.con, 5)
        self.assertIsNone(self.sent()["resume"].sha256)

    def test_another_jobs_document_cannot_be_named(self):
        self.con.execute("INSERT INTO jobs (id,company_id,title,url) "
                         "VALUES (6,1,'Other','https://acme.test/6')")
        other = int(self.con.execute(
            "INSERT INTO documents (job_id,kind,path,version) "
            "VALUES (6,'resume','/x.docx',1)").lastrowid)
        with self.assertRaises(ApprovalError):
            approvals.mark_applied(self.con, 5, resume=other)

    def test_a_letter_cannot_be_named_as_the_resume(self):
        letter = self.draft("cover_letter", approve=True)
        with self.assertRaises(ApprovalError):
            approvals.mark_applied(self.con, 5, resume=letter)

    def test_applying_twice_is_refused_not_overwritten(self):
        self.draft(approve=True)
        approvals.mark_applied(self.con, 5)
        with self.assertRaises(ApprovalError):
            approvals.mark_applied(self.con, 5)

    def test_an_edit_after_approval_is_reported(self):
        import os
        doc = self.draft(approve=True)
        self.con.execute("UPDATE approvals SET decided_at='2000-01-01T00:00:00Z' "
                         "WHERE subject_id=?", (doc,))
        approvals.mark_applied(self.con, 5)
        item = self.sent()["resume"]
        self.assertTrue(approvals.changed_since_approval(self.con, item))
        os.utime(item.path, (0, 0))
        self.assertFalse(approvals.changed_since_approval(self.con, item))


class TestPrepDrillsWhatWasSent(SubmissionCase):
    PROFILE = {"experience": [{
        "company": "Acme", "title": "Engineer",
        "bullets": [{"id": "b1", "text": "Ran the support queue."},
                    {"id": "b2", "text": "Built a data pipeline."}]}]}

    def test_only_the_sent_resumes_claims(self):
        from jsa.prep import sent_bullets
        self.draft(approve=True, bullets=("b1",))
        self.draft(bullets=("b2",))                      # drafted after
        approvals.mark_applied(self.con, 5)
        bullets, source = sent_bullets(self.con, self.app, self.PROFILE)
        self.assertEqual([b.id for b in bullets], ["b1"])
        self.assertIn("v1", source)

    def test_nothing_sent_says_it_is_the_whole_profile(self):
        from jsa.prep import sent_bullets
        bullets, source = sent_bullets(self.con, self.app, self.PROFILE)
        self.assertEqual(len(bullets), 2)
        self.assertIn("whole profile", source)


class TestTheUpgradeIsAdditive(unittest.TestCase):
    def test_an_old_tracker_gains_the_table_and_keeps_its_rows(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        db.init_db(path)
        con = db.connect(path)
        con.execute("DROP TABLE submitted_documents")
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'A','a')")
        con.commit()
        con.close()
        db.init_db(path)
        con = db.connect(path)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)
        con.execute("SELECT * FROM submitted_documents").fetchall()


class TestTimesAreShownLocally(unittest.TestCase):
    """Stored UTC. At 6:54 pm in California the first real application read
    "2026-09-22 01:54" and looked like it had happened tomorrow."""

    def test_a_utc_stamp_is_shown_in_local_time(self):
        from datetime import datetime, timezone
        stamp = "2026-09-22T01:54:47Z"
        expected = datetime(2026, 9, 22, 1, 54, 47, tzinfo=timezone.utc).astimezone()
        self.assertEqual(db.local_time(stamp), expected.strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(db.local_date(stamp), expected.date())

    def test_something_unreadable_is_shown_as_it_is_not_invented(self):
        self.assertEqual(db.local_time("not a time"), "not a time")
        self.assertIsNone(db.local_date(None))
        self.assertEqual(db.local_time(None), "")


class TestAppliedSaysWhatComesNext(SubmissionCase):
    def run_cli(self, *argv):
        import contextlib
        import io
        from unittest import mock
        from jsa import cli
        self.con.commit()
        out = io.StringIO()
        real_connect = db.connect
        path = self.tmp / "t.db"
        with mock.patch.object(cli.db, "connect", lambda *a, **k: real_connect(path)):
            with contextlib.redirect_stdout(out):
                code = cli.main(list(argv))
        return code, out.getvalue()

    def test_it_suggests_a_follow_up_and_sets_none(self):
        self.draft(approve=True)
        code, out = self.run_cli("applied", "5")
        self.assertEqual(code, 0)
        self.assertIn("jsa next 5", out)
        self.assertIn("(your local time)", out)
        con = db.connect(self.tmp / "t.db")
        self.addCleanup(con.close)
        action, = con.execute("SELECT next_action FROM applications").fetchone()
        self.assertIsNone(action, "an intention is the operator's to state")

    def test_check_writes_nothing(self):
        self.draft(approve=True)
        code, out = self.run_cli("applied", "5", "--check")
        self.assertEqual(code, 0)
        self.assertIn("nothing written", out)
        con = db.connect(self.tmp / "t.db")
        self.addCleanup(con.close)
        self.assertEqual(con.execute(
            "SELECT COUNT(*) FROM submitted_documents").fetchone()[0], 0)
        self.assertIsNone(con.execute(
            "SELECT applied_at FROM applications").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
