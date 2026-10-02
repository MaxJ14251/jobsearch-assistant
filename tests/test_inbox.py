"""Reading replies from the mailbox (ADR 0021): read-only, local, suggest-only.

No network: FakeIMAP stands in for the server and records every command, so
the read-only promise is checked against what was actually sent, as well as
against the source.
"""

import ast
import re
import shutil
import tempfile
import unittest
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

from jsa import approvals, db, inbox
from jsa.config import ROOT

SRC = (ROOT / "jsa" / "inbox.py").read_text(encoding="utf-8")


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
            if node.level:
                found.update(a.name for a in node.names)
    return found


class TestReadOnly(unittest.TestCase):
    """Structural, like TestCannotSend for outreach."""

    def test_nothing_that_can_send(self):
        for name in imports_of(ROOT / "jsa" / "inbox.py"):
            self.assertFalse(name.startswith(("smtplib", "email.mime", "httpx", "requests")),
                             f"inbox.py imports {name}")
        self.assertNotIn("smtplib", SRC)

    def test_the_only_mailbox_calls_are_read_ones(self):
        """Every method called on the connection, and every UID command."""
        calls = set(re.findall(r"\bimap(?:lib\.IMAP4_SSL\(|)\.(\w+)\(", SRC))
        self.assertLessEqual(calls, {"login", "select", "uid", "logout"}, calls)
        commands = set(re.findall(r"\.uid\(\s*\"(\w+)\"", SRC))
        self.assertEqual(commands, {"SEARCH", "FETCH"})

    def test_every_select_is_read_only(self):
        lines = [l for l in SRC.splitlines() if ".select(" in l]
        self.assertTrue(lines)
        for line in lines:
            self.assertIn("readonly=True", line)

    def test_every_fetch_is_a_peek(self):
        self.assertIn("BODY.PEEK[", inbox.HEADER_SPEC)
        self.assertIn("BODY.PEEK[", inbox.TEXT_SPEC)
        self.assertEqual(SRC.count('uid("FETCH"'), 1, "one fetch site, in _fetch_part")
        for spec in re.findall(r"_fetch_part\([^)]*,\s*(\w+)\)", SRC):
            self.assertIn(spec, ("HEADER_SPEC", "TEXT_SPEC"))

    def test_it_never_reaches_a_model(self):
        self.assertFalse({"llm", "jsa.llm", ".llm"} & imports_of(ROOT / "jsa" / "inbox.py"))

    def test_only_this_module_imports_imaplib_and_none_imports_smtplib(self):
        for path in sorted((ROOT / "jsa").glob("*.py")):
            names = imports_of(path)
            self.assertNotIn("smtplib", names, path.name)
            if path.name != "inbox.py":
                self.assertNotIn("imaplib", names, path.name)


def message(*, sender, subject, body, mid, when="2026-10-03T15:00:00+00:00"):
    head = (f"From: {sender}\r\nSubject: {subject}\r\n"
            f"Date: {format_datetime(datetime.fromisoformat(when))}\r\n"
            f"Message-ID: <{mid}@example.test>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n").encode()
    return head, body.encode()


class FakeIMAP:
    """Answers SEARCH and FETCH from a dict; records every command."""

    capabilities = ("IMAP4REV1", "X-GM-EXT-1")

    def __init__(self, messages):
        self.messages = {str(i).encode(): m for i, m in enumerate(messages, 1)}
        self.commands = []
        self.logged_out = False

    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == "SEARCH":
            return "OK", [b" ".join(self.messages)]
        if command == "FETCH":
            uid, spec = args
            head, body = self.messages[uid]
            return "OK", [(b"1 (UID " + uid + b")", head if "HEADER" in spec else body), b")"]
        raise AssertionError(f"unexpected command {command}")

    def logout(self):
        self.logged_out = True


class InboxCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        db.init_db(self.dir / "t.db")
        self.con = db.connect(self.dir / "t.db")
        self.addCleanup(self.con.close)
        self.apps = {}
        for job_id, company, title in ((1, "Acme Robotics", "Software Engineer"),
                                       (2, "Globex", "Support Engineer"),
                                       (3, "Globex", "Data Analyst")):
            self.con.execute("INSERT OR IGNORE INTO companies (name, slug) VALUES (?, ?)",
                             (company, company.lower().replace(" ", "-")))
            cid = self.con.execute("SELECT id FROM companies WHERE name = ?", (company,)).fetchone()[0]
            self.con.execute("INSERT INTO jobs (id, company_id, title, url) VALUES (?,?,?,?)",
                             (job_id, cid, title, f"https://x/{job_id}"))
            cur = self.con.execute(
                "INSERT INTO applications (job_id, status, applied_at) "
                "VALUES (?, 'applied', '2026-10-01T12:00:00Z')", (job_id,))
            self.apps[job_id] = cur.lastrowid
        self.con.commit()

    def run_fetch(self, messages):
        self.imap = FakeIMAP(messages)
        return inbox.fetch(self.con, imap=self.imap)

    def replies(self):
        return self.con.execute("SELECT * FROM inbox_replies ORDER BY id").fetchall()


class TestClassification(unittest.TestCase):
    def test_rules_in_order(self):
        cases = {
            ("Your application", "Thank you for applying. We have decided to move "
             "forward with other candidates."): "rejection",
            ("Next steps", "We'd like to schedule a call. Please share your availability."): "interview",
            ("Offer", "We are pleased to offer you the role."): "offer",
            ("Thanks", "We received your application and will review it."): "received",
            ("Newsletter", "Read our latest blog post."): "other",
        }
        for (subject, text), kind in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(inbox.classify(subject, text)[0], kind)

    def test_a_receipt_that_mentions_interviews_is_not_an_invitation(self):
        self.assertEqual(inbox.classify(
            "Application received", "Thank you for applying. If selected for an "
            "interview, a recruiter will contact you.")[0], "received")

    def test_suggested_stages(self):
        self.assertEqual(inbox.suggest("rejection", "phone_screen"), "rejected")
        self.assertEqual(inbox.suggest("interview", "applied"), "phone_screen")
        self.assertEqual(inbox.suggest("interview", "phone_screen"), "technical")
        self.assertIsNone(inbox.suggest("received", "applied"))

    def test_an_aggregator_job_is_matched_by_the_employer_in_its_title(self):
        self.assertEqual(inbox.employer_name(
            "(aggregator) Hacker News jobs",
            "Kyber (YC W23) Is Hiring a Forward Deployed Engineer"), "Kyber")


class TestFetch(InboxCase):
    def test_suggests_and_never_moves_an_application(self):
        report = self.run_fetch([message(
            sender="Acme Robotics <no-reply@acmerobotics.com>",
            subject="Your application to Acme Robotics",
            body="Unfortunately we will not be moving forward.", mid="a1")])
        (row,) = self.replies()
        self.assertEqual((row["kind"], row["suggested_stage"], row["state"]),
                         ("rejection", "rejected", "pending"))
        self.assertEqual(row["application_id"], self.apps[1])
        self.assertEqual(self.con.execute(
            "SELECT status FROM applications WHERE job_id = 1").fetchone()[0], "applied")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM application_events").fetchone()[0], 0)
        self.assertEqual(report.stored, 1)
        self.assertTrue(self.imap.logged_out)

    def test_every_command_sent_was_a_search_or_a_peek(self):
        self.run_fetch([message(sender="no-reply@greenhouse.io", subject="Thanks",
                                body="Acme Robotics received your application.", mid="a2")])
        for command, args in self.imap.commands:
            self.assertIn(command, ("SEARCH", "FETCH"))
            if command == "FETCH":
                self.assertIn("BODY.PEEK[", args[1])

    def test_the_gmail_search_names_the_hiring_systems_and_the_employers(self):
        self.run_fetch([])
        (command, args), = self.imap.commands
        self.assertEqual(args[0], "X-GM-RAW")
        self.assertIn("from:greenhouse.io", args[1])
        self.assertIn('\\"Acme Robotics\\"', args[1])
        self.assertIn("after:2026/09/30", args[1])

    def test_a_hiring_system_mail_names_the_employer_in_its_body(self):
        self.run_fetch([message(sender="Hiring <no-reply@ashbyhq.com>", subject="Update",
                                body="Acme Robotics would like to schedule a call with you.",
                                mid="a3")])
        (row,) = self.replies()
        self.assertEqual((row["kind"], row["suggested_stage"]), ("interview", "phone_screen"))

    def test_a_word_in_some_other_mails_body_is_not_a_match(self):
        self.run_fetch([message(sender="news@example.org", subject="Weekly digest",
                                body="Globex shares rose today.", mid="a4")])
        self.assertEqual(self.replies(), [])

    def test_two_applications_at_one_employer_choose_by_title(self):
        self.run_fetch([message(sender="Globex Careers <no-reply@globex.com>",
                                subject="Your Support Engineer application",
                                body="We regret to inform you.", mid="a5")])
        (row,) = self.replies()
        self.assertEqual(row["application_id"], self.apps[2])

    def test_still_ambiguous_is_stored_without_an_application(self):
        self.run_fetch([message(sender="Globex Careers <no-reply@globex.com>",
                                subject="Your application", body="Thank you for applying.",
                                mid="a6")])
        (row,) = self.replies()
        self.assertIsNone(row["application_id"])
        self.assertEqual(sorted(eval(row["candidates"])), sorted([self.apps[2], self.apps[3]]))
        with self.assertRaises(inbox.InboxError):
            inbox.confirm(self.con, row["id"])

    def test_mail_from_before_the_application_is_ignored(self):
        self.run_fetch([message(sender="Acme Robotics <no-reply@acmerobotics.com>",
                                subject="Acme Robotics newsletter", body="Hello.",
                                mid="a7", when="2026-09-01T10:00:00+00:00")])
        self.assertEqual(self.replies(), [])

    def test_a_second_run_does_not_duplicate_or_refetch(self):
        mail = [message(sender="Acme Robotics <no-reply@acmerobotics.com>",
                        subject="Acme Robotics: application received",
                        body="Thank you for applying.", mid="a8")]
        self.run_fetch(mail)
        report = self.run_fetch(mail)
        self.assertEqual(len(self.replies()), 1)
        self.assertEqual(report.already, 1)
        fetched = [a[1] for c, a in self.imap.commands if c == "FETCH"]
        self.assertTrue(all("HEADER" in spec for spec in fetched),
                        "a known message's text was fetched again")

    def test_no_body_is_stored(self):
        self.run_fetch([message(sender="Acme Robotics <no-reply@acmerobotics.com>",
                                subject="Acme Robotics", body="SECRET-BODY-TEXT regret to inform",
                                mid="a9")])
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(inbox_replies)")}
        self.assertNotIn("body", cols)
        dump = "\n".join(self.con.iterdump())
        self.assertNotIn("SECRET-BODY-TEXT", dump)


class TestConfirmAndDismiss(InboxCase):
    def setUp(self):
        super().setUp()
        self.run_fetch([message(sender="Acme Robotics <no-reply@acmerobotics.com>",
                                subject="Acme Robotics: next steps",
                                body="Please share your availability.", mid="b1")])
        self.reply = self.replies()[0]["id"]

    def test_confirming_moves_the_stage_as_the_human_with_the_email_named(self):
        job, previous, stage = inbox.confirm(self.con, self.reply)
        self.assertEqual((job, previous, stage), (1, "applied", "phone_screen"))
        event = self.con.execute("SELECT actor, to_status, note FROM application_events "
                                 "ORDER BY id DESC").fetchone()
        self.assertEqual((event["actor"], event["to_status"]), ("human", "phone_screen"))
        self.assertIn("from an email of 2026-10-03", event["note"])
        self.assertEqual(self.replies()[0]["state"], "confirmed")
        with self.assertRaises(inbox.InboxError):
            inbox.confirm(self.con, self.reply)

    def test_the_owner_can_choose_another_stage(self):
        inbox.confirm(self.con, self.reply, stage="technical")
        self.assertEqual(self.con.execute(
            "SELECT status FROM applications WHERE job_id = 1").fetchone()[0], "technical")

    def test_dismissing_changes_nothing_else(self):
        inbox.dismiss(self.con, self.reply)
        self.assertEqual(self.replies()[0]["state"], "dismissed")
        self.assertEqual(self.con.execute(
            "SELECT status FROM applications WHERE job_id = 1").fetchone()[0], "applied")


class TestTheDashboard(unittest.TestCase):
    """The Replies section on /pipeline: confirm and dismiss, token-guarded."""

    def setUp(self):
        from tests.test_web_documents import Sandbox, make_client
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        con = self.box.connect()
        con.execute("UPDATE applications SET status = 'applied', "
                    "applied_at = '2026-10-01T12:00:00Z' WHERE id = 1")
        con.execute("INSERT INTO inbox_replies (id, message_id, application_id, "
                    "received_at, sender_domain, subject, kind, suggested_stage, rule) "
                    "VALUES (7, '<m@x>', 1, '2026-10-03T15:00:00Z', 'acme.test', "
                    "'Acme: next steps', 'interview', 'phone_screen', 'invitation')")
        con.commit()
        con.close()
        self.client, self.app = make_client(self.box.db, self.box.out, profile={})
        self.csrf = self.app.state.csrf_token

    def status(self):
        con = self.box.connect()
        try:
            return con.execute("SELECT status FROM applications WHERE id = 1").fetchone()[0]
        finally:
            con.close()

    def test_the_pending_reply_is_shown_with_its_suggestion(self):
        page = self.client.get("/pipeline").text
        self.assertIn("Replies from your email", page)
        self.assertIn("Acme: next steps", page)
        self.assertIn('action="/inbox/7/confirm"', page)
        self.assertRegex(page, r'<option value="phone_screen"\s+selected')

    def test_confirm_moves_the_stage_as_the_human(self):
        r = self.client.post("/inbox/7/confirm", data={"csrf": self.csrf, "stage": "phone_screen"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.status(), "phone_screen")
        con = self.box.connect()
        actor = con.execute("SELECT actor FROM application_events ORDER BY id DESC").fetchone()[0]
        con.close()
        self.assertEqual(actor, "human")
        self.assertNotIn('action="/inbox/7/confirm"', self.client.get("/pipeline").text)

    def test_dismiss_changes_nothing_else(self):
        self.client.post("/inbox/7/dismiss", data={"csrf": self.csrf}, follow_redirects=False)
        self.assertEqual(self.status(), "applied")
        self.assertIn("No replies waiting on you", self.client.get("/pipeline").text)

    def test_every_inbox_route_needs_the_token(self):
        for url in ("/inbox/fetch", "/inbox/7/confirm", "/inbox/7/dismiss"):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, data={"stage": "rejected"}).status_code, 403)
        self.assertEqual(self.status(), "applied")

    def test_check_email_without_settings_says_how_to_set_up(self):
        from unittest import mock
        with mock.patch.object(inbox, "settings", return_value=None):
            r = self.client.post("/inbox/fetch", data={"csrf": self.csrf}, follow_redirects=False)
            page = self.client.get(r.headers["location"]).text
        self.assertIn("app password", page)


class TestNotConfigured(InboxCase):
    def test_without_settings_it_says_how_to_set_up(self):
        from unittest import mock
        with mock.patch.object(inbox, "settings", return_value=None):
            with self.assertRaises(inbox.InboxError) as caught:
                inbox.fetch(self.con)
        self.assertIn("app password", str(caught.exception))

    def test_the_address_is_masked(self):
        self.assertEqual(inbox.mask("someone@example.com"), "s…@example.com")


class TestMigration(unittest.TestCase):
    def test_an_older_tracker_gets_the_table(self):
        import sqlite3
        path = Path(tempfile.mkdtemp()) / "old.db"
        self.addCleanup(shutil.rmtree, path.parent, True)
        schema = db.SCHEMA_PATH.read_text(encoding="utf-8")
        start = schema.index("CREATE TABLE IF NOT EXISTS inbox_replies")
        old = schema[:start] + schema[schema.index(");", start) + 2:]
        con = sqlite3.connect(path)
        con.executescript(old)
        con.close()
        db.init_db(path)
        con = db.connect(path)
        self.addCleanup(con.close)
        self.assertTrue(con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'inbox_replies'").fetchone())


if __name__ == "__main__":
    unittest.main()
