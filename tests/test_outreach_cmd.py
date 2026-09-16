"""`jsa outreach-*` — drafting messages without ever being able to send one.

See docs/decisions/0004-outreach.md.

TestNothingCanSend is the load-bearing test. The older version in
tests/test_outreach.py walks only the top-level file, so a sending library
imported one level down would have sailed through it. This one walks the whole
transitive graph.
"""

import ast
import io
import sqlite3
import subprocess
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import approvals, db, outreach
from jsa.config import ROOT
from jsa.outreach import (
    CHANNEL_LIMITS,
    LINKEDIN_CONNECT_LIMIT,
    SHORTEN_ATTEMPTS,
    OutreachError,
    enforce_limit,
    verify_message,
)
from jsa.tailor import FabricationError
from tests.test_prep_cmd import PROFILE

JSA = ROOT / "jsa"

# Anything that could put a message on a wire. urllib and httpx are handled
# separately: the LLM client legitimately needs one of them.
SENDING = {
    "smtplib", "imaplib", "poplib", "ftplib", "telnetlib", "socket",
    "requests", "email",
}


def module_imports(path: Path) -> set[str]:
    """Every module name a file imports, including `from . import a, b`.

    An earlier version of this walk missed relative multi-imports entirely,
    because `from . import approvals, db, llm` puts the names in node.names
    rather than node.module. It reported a two-module graph for what is
    actually six, and would have called a clean bill of health on code it had
    never looked at.
    """
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.update(alias.name for alias in node.names)
            if node.module:
                found.add(node.module)
    return found


def transitive_graph(start: str) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    stack, seen = [start], set()
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        path = JSA / f"{module}.py"
        if not path.exists():
            continue
        graph[module] = module_imports(path)
        for name in graph[module]:
            base = name.lstrip(".").split(".")[0]
            if (JSA / f"{base}.py").exists():
                stack.append(base)
    return graph


class TestNothingCanSend(unittest.TestCase):
    """The guarantee, proven structurally rather than promised in a docstring."""

    def setUp(self):
        self.graph = transitive_graph("outreach")

    def test_the_graph_is_the_one_we_think_it_is(self):
        """A walk that reaches too little proves nothing about what it missed."""
        self.assertIn("outreach", self.graph)
        for expected in ("approvals", "db", "llm", "tailor", "config"):
            self.assertIn(expected, self.graph,
                          "the import walk did not reach the whole module graph")

    def test_no_sending_library_anywhere_in_the_graph(self):
        offenders = []
        for module, imports in self.graph.items():
            for name in imports:
                base = name.split(".")[0]
                if base in SENDING:
                    offenders.append(f"{module}.py imports {name}")
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_only_the_llm_client_touches_the_network(self):
        """httpx is required to call the model. Nothing else may have it."""
        with_http = {
            module for module, imports in self.graph.items()
            if any(n.split(".")[0] in ("httpx", "urllib") for n in imports)
        }
        self.assertEqual(with_http, {"llm"},
                         f"unexpected network-capable modules: {with_http}")

    def test_the_llm_client_only_posts_to_the_configured_endpoint(self):
        """Network access exists to draft, never to deliver."""
        source = (JSA / "llm.py").read_text(encoding="utf-8")
        posts = [line.strip() for line in source.splitlines()
                 if ".post(" in line or "client.post" in line]
        self.assertTrue(posts, "expected the LLM client to post somewhere")
        following = source.split(".post(")
        for chunk in following[1:]:
            head = chunk[:120]
            self.assertIn("BASE_URL", head,
                          f"a POST goes somewhere other than the LLM endpoint: {head!r}")

    def test_mark_sent_only_records(self):
        import inspect
        source = inspect.getsource(outreach.mark_sent)
        self.assertIn("UPDATE outreach", source)
        for needle in ("post", "send(", "smtp", "connect("):
            self.assertNotIn(needle, source.lower().replace("connection", ""))


class TestTheVerbIsUnambiguous(unittest.TestCase):
    """ADR 0004 decision 1.

    `jsa outreach sent 3` reads as an imperative -- send this -- and it is the
    exact opposite. Five extra characters buy a command that cannot be misread.
    """

    def test_the_command_is_named_mark_sent(self):
        source = (JSA / "cli.py").read_text(encoding="utf-8")
        self.assertIn('"outreach-mark-sent"', source)

    def test_the_draft_command_says_it_did_not_send(self):
        source = (JSA / "cli.py").read_text(encoding="utf-8")
        self.assertIn("NOT SENT", source,
                      "the draft command must say plainly that nothing was sent")


class TestContactPrivacy(unittest.TestCase):
    """The first guard here that protects somebody other than the operator."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        self.con = db.connect(self.dbfile)
        self.con.execute(
            "INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        self.con.execute(
            "INSERT INTO jobs (id,company_id,title,url,description,tech_stack) "
            "VALUES (1,1,'Support Engineer','https://acme.test/1',?,?)",
            ("We want Python and customer support skills.", '["Python"]'))
        self.contact_id = outreach.add_contact(
            self.con, name="Dana Rivers", company_id=1,
            title="Engineering Manager",
            linkedin_url="https://linkedin.com/in/dana-rivers-example",
            email="dana.rivers@example.org", relationship="cold")
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def _capture(self):
        captured = {}

        def spy(prompt, **kwargs):
            captured["sent"] = (prompt + str(kwargs.get("system", ""))).lower()
            return mock.Mock(text="A short note about the role.", model="test/model")

        with mock.patch("jsa.outreach.llm.complete", side_effect=spy):
            outreach.draft(self.con, PROFILE, contact_id=self.contact_id,
                           job_id=1, channel="email")
        return captured["sent"]

    def test_name_and_title_reach_the_prompt(self):
        """A referral ask that cannot name its recipient is not outreach."""
        sent = self._capture()
        self.assertIn("dana rivers", sent)
        self.assertIn("engineering manager", sent)

    def test_email_and_linkedin_never_reach_the_prompt(self):
        """Contact mechanics, not message content. The free tier logs prompts."""
        sent = self._capture()
        self.assertNotIn("dana.rivers@example.org", sent)
        self.assertNotIn("linkedin.com/in/dana-rivers-example", sent)

    def test_the_operators_own_identity_never_reaches_it_either(self):
        sent = self._capture()
        ident = PROFILE.get("identity") or {}
        for value in (ident.get("full_name"), ident.get("email"),
                      ident.get("phone")):
            if isinstance(value, str) and value.strip():
                self.assertNotIn(value.lower(), sent)


class TestApprovalIsTheWeakestLink(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        self.con = db.connect(self.dbfile)
        self.con.execute(
            "INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        self.contact_id = outreach.add_contact(
            self.con, name="Sam Okafor", company_id=1, relationship="alum")
        cur = self.con.execute(
            "INSERT INTO outreach (contact_id, channel, purpose, draft_body) "
            "VALUES (?,'email','referral_ask','Hello.')", (self.contact_id,))
        self.outreach_id = int(cur.lastrowid)
        self.approval_id = approvals.queue(
            self.con, "outreach", self.outreach_id, "Send it?")

    def tearDown(self):
        self.con.close()

    def test_mark_sent_refuses_before_approval(self):
        with self.assertRaises(OutreachError):
            outreach.mark_sent(self.con, self.outreach_id)

    def test_approval_without_a_human_raises_at_the_database(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "UPDATE approvals SET decision = 'approved' WHERE id = ?",
                (self.approval_id,))

    def test_one_pending_approval_per_outreach(self):
        with self.assertRaises(sqlite3.IntegrityError):
            approvals.queue(self.con, "outreach", self.outreach_id, "again")

    def test_mark_sent_works_once_a_human_approved(self):
        approvals.approve(self.con, self.approval_id)
        outreach.mark_sent(self.con, self.outreach_id)
        row = self.con.execute(
            "SELECT status, sent_at FROM outreach WHERE id = ?",
            (self.outreach_id,)).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertIsNotNone(row["sent_at"])


class TestChannelLimit(unittest.TestCase):
    """Measured, not assumed.

    Ten real linkedin_connect drafts with "HARD LIMIT: 300 characters. Count
    them." already in the prompt came back 432-556 characters, 10 of 10 over.
    Models cannot count characters. Handing back the measured length works:
    8 of 10 then landed under the limit, and none over.
    """

    def test_the_limit_is_data_with_a_source(self):
        self.assertEqual(CHANNEL_LIMITS["linkedin_connect"],
                         LINKEDIN_CONNECT_LIMIT)
        source = (JSA / "outreach.py").read_text(encoding="utf-8")
        self.assertIn("SHORTEN_ATTEMPTS", source)
        self.assertGreaterEqual(SHORTEN_ATTEMPTS, 1)

    def test_over_limit_raises_rather_than_truncating(self):
        """A message cut mid-sentence is worse than no message."""
        with self.assertRaises(OutreachError) as ctx:
            enforce_limit("x" * 400, "linkedin_connect")
        self.assertIn("300", str(ctx.exception))

    def test_the_failure_says_what_to_do(self):
        with self.assertRaises(OutreachError) as ctx:
            enforce_limit("x" * 400, "linkedin_connect")
        self.assertIn("again", str(ctx.exception).lower())

    def test_within_limit_passes(self):
        enforce_limit("x" * 280, "linkedin_connect")


class TestMessageVerification(unittest.TestCase):
    def test_banned_terms_are_refused(self):
        with self.assertRaises(FabricationError):
            verify_message("I have run Kubernetes clusters in production.",
                           PROFILE)

    def test_implied_degree_is_refused(self):
        for phrase in ("my degree in Computer Science",
                       "graduated with a CS degree"):
            with self.subTest(phrase=phrase):
                with self.assertRaises(FabricationError):
                    verify_message(f"Hello -- {phrase}, and I build pipelines.",
                                   PROFILE)

    def test_an_honest_message_passes(self):
        verify_message(
            "I build cloud-native Python pipelines with Claude and Gemini.",
            PROFILE)


class TestScannerCoversContactData(unittest.TestCase):
    """ADR 0004 decision 3. The database is gitignored; the risk is a fixture."""

    def test_a_real_address_is_flagged(self):
        """Assembled from parts, because writing it out would make this very
        file a violation -- which is how the first draft of this test failed."""
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        from scan_secrets import real_email_addresses
        address = "dana.rivers" + "@" + "somecompany" + "." + "com"
        self.assertTrue(real_email_addresses(f"write to {address}"))

    def test_reserved_placeholder_domains_are_not_flagged(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        from scan_secrets import real_email_addresses
        for address in ("you@example.com", "someone@example.org",
                        "a@b.test", "x@y.invalid"):
            with self.subTest(address=address):
                self.assertFalse(real_email_addresses(address))

    def test_the_repository_is_clean_under_this_rule(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        from scan_secrets import real_email_addresses
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
        ).stdout.split()
        offenders = []
        for name in tracked:
            path = ROOT / name
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if real_email_addresses(text):
                offenders.append(name)
        self.assertEqual(offenders, [], f"real addresses committed: {offenders}")


if __name__ == "__main__":
    unittest.main()
