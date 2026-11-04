"""Reading a draft and approving or denying it from the extension's panel.

The owner asked for it on 2026-11-04. A decision is the person's button press
and calls exactly what Review's buttons call; it reaches only a waiting draft
of the job whose application page asked, and never without the panel's confirm.
"""

import re
import unittest

from docx import Document

from jsa import approvals
from tests.test_ext import GREENHOUSE, Ext
from tests.test_extension import EXT as EXT_DIR, body_of, code

OTHER = "https://job-boards.greenhouse.io/riverton/jobs/999"


class Decide(Ext):
    def draft(self, job_id=1, lines=("SUMMARY", "Helped customers fix their systems.")):
        doc = self.doc(approve=False, job_id=job_id)
        path = self.con.execute("SELECT path FROM documents WHERE id = ?", (doc,)).fetchone()[0]
        word = Document()
        for line in lines:
            word.add_paragraph(line)
        word.save(path)
        approval = self.con.execute("SELECT id FROM approvals WHERE subject_type = 'document' "
                                    "AND subject_id = ?", (doc,)).fetchone()[0]
        return doc, approval

    def decide(self, approval, decision="approve", confirm="decide", url=GREENHOUSE, **extra):
        return self.post("/ext/decide", {"url": url, "approval_id": approval,
                                         "decision": decision, "confirm": confirm, **extra})

    def decision(self, approval):
        return self.con.execute("SELECT decision, decided_by, feedback FROM approvals "
                                "WHERE id = ?", (approval,)).fetchone()


class TestReading(Decide):
    def test_the_waiting_draft_is_listed_with_its_text(self):
        doc, approval = self.draft()
        body = self.get("/ext/review", url=GREENHOUSE).json()
        self.assertEqual(body["state"], "ok")
        [waiting] = body["waiting"]
        self.assertEqual((waiting["approval_id"], waiting["document_id"], waiting["kind"]),
                         (approval, doc, "resume"))
        self.assertIn("Helped customers fix their systems.", waiting["paragraphs"])

    def test_reading_writes_nothing(self):
        self.draft()
        before = self.con.total_changes
        self.get("/ext/review", url=GREENHOUSE)
        self.assertEqual(self.con.total_changes, before)

    def test_an_approved_draft_is_not_waiting(self):
        self.doc(approve=True)
        self.assertEqual(self.get("/ext/review", url=GREENHOUSE).json()["waiting"], [])

    def test_a_job_you_are_not_applying_to_lists_nothing(self):
        self.assertEqual(self.get("/ext/review", url=OTHER).json()["state"], "not_ready")


class TestDeciding(Decide):
    def test_approve_records_your_decision(self):
        _, approval = self.draft()
        r = self.decide(approval)
        self.assertEqual(r.json(), {"ok": True, "decision": "approve"})
        self.con.commit()
        self.assertEqual(tuple(self.decision(approval))[:2], ("approved", "human"))

    def test_deny_records_your_reason(self):
        _, approval = self.draft()
        self.decide(approval, "reject", feedback="Wrong project for this role.")
        row = self.decision(approval)
        self.assertEqual((row["decision"], row["feedback"]),
                         ("rejected", "Wrong project for this role."))

    def test_deny_without_a_reason_still_says_where_it_came_from(self):
        _, approval = self.draft()
        self.decide(approval, "reject")
        self.assertIn("extension", self.decision(approval)["feedback"])

    def test_nothing_without_the_confirm(self):
        _, approval = self.draft()
        for confirm in ("", "yes", "approve"):
            with self.subTest(confirm=confirm):
                self.assertEqual(self.decide(approval, confirm=confirm).status_code, 400)
        self.assertEqual(self.decision(approval)["decision"], "pending")

    def test_only_a_draft_of_this_pages_job(self):
        # A draft of another job: refused from this job's page.
        approvals.save_application(self.con, 2)
        self.con.commit()
        _, other = self.draft(job_id=2)
        r = self.decide(other, url=GREENHOUSE)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.decision(other)["decision"], "pending")

    def test_a_decided_draft_is_not_decided_again(self):
        _, approval = self.draft()
        self.decide(approval)
        self.assertEqual(self.decide(approval, "reject").status_code, 400)
        self.assertEqual(self.decision(approval)["decision"], "approved")

    def test_it_needs_the_key(self):
        _, approval = self.draft()
        r = self.post("/ext/decide", {"url": GREENHOUSE, "approval_id": approval,
                                      "decision": "approve", "confirm": "decide"}, key=False)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.get("/ext/review", key=False, url=GREENHOUSE).status_code, 401)

    def test_a_denied_resume_is_never_filled(self):
        _, approval = self.draft()
        self.decide(approval, "reject")
        body = self.get("/ext/fill", url=GREENHOUSE).json()
        self.assertIsNone(body["documents"]["resume"])
        self.assertTrue(any("denied" in w for w in body["warnings"]))


class TestOnlyFromYourButtons(unittest.TestCase):
    """The panel sends "decide" only from Approve and Deny, after a confirm."""

    def setUp(self):
        self.source = code(EXT_DIR / "content.js")

    def test_the_decide_message_is_sent_only_from_two_buttons(self):
        self.assertEqual(self.source.count('type: "decide"'), 2)
        for name in ("approveDraft", "denyDraft"):
            body = body_of(self.source, name)
            with self.subTest(function=name):
                self.assertIn('type: "decide"', body)
                self.assertLess(body.index("window.confirm"), body.index('type: "decide"'))
                wired = re.findall(r'button\("(Approve|Deny)", \(\) => ' + name + r"\(",
                                   self.source)
                calls = re.findall(r"(?<![.\w])" + name + r"\s*\(", self.source)
                self.assertEqual(len(wired), 1)
                self.assertEqual(len(calls), 2)     # its definition and its button

    def test_the_background_sends_the_page_address_and_the_confirm(self):
        body = body_of(code(EXT_DIR / "background.js"), "decide")
        self.assertIn("sender && sender.url", body)
        self.assertIn('message.confirm === "decide"', body)
        self.assertIn('"/ext/decide"', body)


if __name__ == "__main__":
    unittest.main()
