"""Outreach drafting tests.

TestCannotSend is the important one. "This tool drafts but never sends" is only
worth something if it can be checked, so these tests inspect the module's own
source and imports rather than trusting the docstring. An auditor can confirm
the claim in seconds.
"""

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsa import approvals, db, outreach
from jsa.outreach import (
    CHANNEL_LIMITS,
    LINKEDIN_CONNECT_LIMIT,
    OutreachError,
    add_contact,
    enforce_limit,
    mark_sent,
    verify_message,
)
from jsa.tailor import FabricationError
from tests.test_tailor import PROFILE

# Anything that could put a message on the wire.
SENDING_MODULES = {
    "smtplib", "email.message", "email.mime", "imaplib", "poplib",
    "sendgrid", "mailgun", "boto3", "ses", "twilio", "slack_sdk",
    "selenium", "playwright", "pyautogui", "webbrowser", "requests",
}


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestCannotSend(unittest.TestCase):
    """Structural proof, not a promise."""

    PATH = Path(outreach.__file__)

    def test_imports_no_sending_library(self):
        imported = module_imports(self.PATH)
        offenders = {
            name for name in imported
            if any(name == m or name.startswith(m + ".") for m in SENDING_MODULES)
        }
        self.assertEqual(
            offenders, set(),
            f"outreach must not import anything that can send: {offenders}")

    def test_no_smtplib_anywhere_in_the_source(self):
        src = self.PATH.read_text(encoding="utf-8").lower()
        for needle in ("smtplib", "sendmail", "send_message(", "smtp("):
            self.assertNotIn(needle, src)

    def test_does_not_post_over_http(self):
        """It may call the LLM to draft; it must never POST a message out."""
        src = self.PATH.read_text(encoding="utf-8")
        for needle in ("httpx.post", "requests.post", "urlopen", "http.client"):
            self.assertNotIn(needle, src)

    def test_only_the_llm_client_is_used_for_network(self):
        imported = module_imports(self.PATH)
        self.assertNotIn("httpx", imported)
        self.assertNotIn("urllib.request", imported)

    def test_mark_sent_only_records_it_never_sends(self):
        import inspect
        src = inspect.getsource(mark_sent)
        self.assertIn("UPDATE outreach", src)
        for needle in ("send", "post", "smtp"):
            self.assertNotIn(f"{needle}(", src.lower().replace("mark_sent(", ""))


class OutreachCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        self.con = db.connect(self.dbfile)
        self.con.execute(
            "INSERT INTO companies (id, name, slug) VALUES (1,'Example','example')")
        self.contact_id = add_contact(
            self.con, name="Jordan Lee", company_id=1,
            title="Engineering Manager", relationship="cold")

    def tearDown(self):
        self.con.close()


class TestCharacterLimits(unittest.TestCase):
    def test_linkedin_connect_limit_is_300(self):
        self.assertEqual(LINKEDIN_CONNECT_LIMIT, 300)
        self.assertEqual(CHANNEL_LIMITS["linkedin_connect"], 300)

    def test_over_limit_is_refused(self):
        with self.assertRaises(OutreachError) as ctx:
            enforce_limit("x" * 301, "linkedin_connect")
        self.assertIn("300", str(ctx.exception))

    def test_exactly_at_limit_is_allowed(self):
        body = "x" * 300
        self.assertEqual(enforce_limit(body, "linkedin_connect"), body)

    def test_limit_counts_characters_not_words(self):
        body = "word " * 61          # 305 characters, 61 words
        self.assertGreater(len(body), 300)
        with self.assertRaises(OutreachError):
            enforce_limit(body, "linkedin_connect")


class TestMessageVerification(unittest.TestCase):
    def test_banned_claim_is_rejected(self):
        for term in ("Kubernetes", "PyTorch", "C++"):
            with self.subTest(term=term):
                with self.assertRaises(FabricationError):
                    verify_message(f"I have deep {term} experience.", PROFILE)

    def test_implied_degree_is_rejected(self):
        for phrase in ("my degree in computer science",
                       "I graduated with honors",
                       "I hold a degree in CS"):
            with self.subTest(phrase=phrase):
                with self.assertRaises(FabricationError):
                    verify_message(phrase, PROFILE)

    def test_honest_message_passes(self):
        body = ("I build LLM automation pipelines in Python with Claude and "
                "Gemini, and spent three years in technical sales before that.")
        self.assertEqual(verify_message(body, PROFILE), body)


class TestSendRequiresApproval(OutreachCase):
    def _queue(self):
        cur = self.con.execute(
            "INSERT INTO outreach (contact_id, channel, purpose, draft_body) "
            "VALUES (?,?,?,?)",
            (self.contact_id, "linkedin_connect", "referral_ask", "Hello."))
        oid = int(cur.lastrowid)
        approvals.queue(self.con, "outreach", oid, "Send to Jordan Lee")
        return oid

    def test_cannot_mark_sent_without_approval(self):
        oid = self._queue()
        with self.assertRaises(OutreachError) as ctx:
            mark_sent(self.con, oid)
        self.assertIn("not been approved", str(ctx.exception))

    def test_marks_sent_after_human_approval(self):
        oid = self._queue()
        pending = approvals.pending(self.con)[0]
        approvals.approve(self.con, pending.approval_id)
        mark_sent(self.con, oid)
        row = self.con.execute(
            "SELECT status, sent_at FROM outreach WHERE id = ?", (oid,)).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertTrue(row["sent_at"])

    def test_agent_still_cannot_approve_outreach(self):
        self._queue()
        aid = approvals.pending(self.con)[0].approval_id
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "UPDATE approvals SET decision='approved' WHERE id = ?", (aid,))


class TestContacts(OutreachCase):
    def test_contact_round_trips(self):
        row = self.con.execute(
            "SELECT * FROM contacts WHERE id = ?", (self.contact_id,)).fetchone()
        self.assertEqual(row["name"], "Jordan Lee")
        self.assertEqual(row["relationship"], "cold")


if __name__ == "__main__":
    unittest.main()
