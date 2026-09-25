"""`tools/scan_history.py`: what publishing exposes, not what HEAD shows.

The live decoy run that proved this works is described in the audit section of
the README. These tests keep the proof, without planting fake secrets in the
project's own history -- a decoy commit is unreachable after the branch is
deleted, but it is still a fake key sitting in somebody's object store.

Every case builds a throwaway repository. The one that matters is `b`: a
secret committed and then deleted is STILL exposed, because a published
repository publishes commit 11 as well as commit 12.
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa.config import ROOT
from tools import scan_history
from tools.scan_secrets import is_noreply, real_email_addresses

# Built at runtime, never written down. A literal here would make this file trip
# tools/scan_secrets.py, and the only cure would be exempting the file -- which
# is exactly the exemption a real key would later hide behind.
KEY = "nvapi-" + "FAKE" * 6 + "test"
HOME_PATH = "C:" + chr(92) + "Users" + chr(92) + "jsmith"
PLACEHOLDER_PATH = "C:" + chr(92) + "Users" + chr(92) + "you"
OUTSIDE_EMAIL = "recruiter" + "@" + "realcorp" + ".io"
NOREPLY = "noreply" + "@anthropic.com"
REAL_MAILBOX = "real.person" + "@" + "somewhere" + ".org"


def run(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=", "-c", "user.name=Test Person",
         "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
         *args],
        cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr}")
    return proc.stdout


def repo_with(files: dict[str, bytes | str], message: str = "one") -> Path:
    path = Path(tempfile.mkdtemp())
    run(path, "init", "-q", "-b", "main")
    write(path, files)
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", message)
    return path


def write(repo: Path, files: dict[str, bytes | str]) -> None:
    for name, body in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8")


def blobs(repo: Path, never=(), authorship=()) -> list[str]:
    with mock.patch.object(scan_history, "ROOT", repo):
        return scan_history.scan_blobs(list(never), list(authorship))


def messages(repo: Path, never=(), authorship=()) -> list[str]:
    with mock.patch.object(scan_history, "ROOT", repo):
        return scan_history.scan_messages(list(never), list(authorship))


def identities(repo: Path) -> list[str]:
    with mock.patch.object(scan_history, "ROOT", repo):
        return scan_history.scan_identities()


class TestAPlantedSecretIsFound(unittest.TestCase):
    """Scenario a. A scanner that has never found anything is not a scanner."""

    def test_a_key_in_a_committed_file(self):
        repo = repo_with({"notes.txt": f"NVIDIA_API_KEY={KEY}\n"})
        found = " | ".join(blobs(repo))
        self.assertIn("NVIDIA API key", found)
        self.assertIn("notes.txt", found)

    def test_a_home_path_in_a_committed_file(self):
        repo = repo_with({"log.txt": f"built at {HOME_PATH}"})
        self.assertIn("absolute home path", " | ".join(blobs(repo)))

    def test_a_generic_placeholder_home_path_is_not_a_finding(self):
        repo = repo_with({"README.md": f"put it in {PLACEHOLDER_PATH}"})
        self.assertEqual(blobs(repo), [])


class TestDeletingItDoesNotRemoveIt(unittest.TestCase):
    """Scenario b. The whole reason this tool exists.

    Watch it fail against a scanner that reads HEAD: after commit two there is
    no file to read, and the working-tree scan reports clean.
    """

    def setUp(self):
        self.repo = repo_with({"secret.txt": f"key={KEY}\n"}, "add the file")
        run(self.repo, "rm", "-q", "secret.txt")
        run(self.repo, "commit", "-q", "-m", "remove the file")

    def test_head_no_longer_has_it(self):
        self.assertNotIn("secret.txt",
                         run(self.repo, "ls-tree", "-r", "--name-only", "HEAD"))

    def test_the_history_scan_still_finds_it(self):
        found = " | ".join(blobs(self.repo))
        self.assertIn("NVIDIA API key", found)
        self.assertIn("secret.txt", found)

    def test_it_can_name_the_commits(self):
        sha = run(self.repo, "rev-list", "--objects", "--all").splitlines()
        blob = next(line.split(" ")[0] for line in sha
                    if line.endswith(" secret.txt"))
        with mock.patch.object(scan_history, "ROOT", self.repo):
            commits = scan_history.commits_touching(blob)
        self.assertEqual(len(commits), 2, commits)   # added, then removed

    def test_an_unreachable_commit_is_out_of_scope(self):
        """What `git push` sends is what a reader can fetch.

        After the branch is gone the blob is still in .git, but no ref reaches
        it, so it is not published. This is why the scan uses --all.
        """
        repo = repo_with({"ok.txt": "nothing here\n"})
        run(repo, "checkout", "-q", "-b", "scratch")
        write(repo, {"leak.txt": f"key={KEY}\n"})
        run(repo, "add", "-A")
        run(repo, "commit", "-q", "-m", "scratch")
        self.assertIn("NVIDIA API key", " | ".join(blobs(repo)))
        run(repo, "checkout", "-q", "main")
        run(repo, "branch", "-D", "scratch")
        self.assertEqual(blobs(repo), [])


class TestPathsThatAreTheFindingThemselves(unittest.TestCase):
    """Scenario c. A tracker is bytes; its path is the readable part."""

    def test_forbidden_paths(self):
        cases = {
            ".env": "NVIDIA_API_KEY=whatever\n",
            "config/tracker.db": b"SQLite format 3\x00\x01\x02",
            "profile/master_profile.yaml": "identity:\n  full_name: A Person\n",
            ".coverage": b"\x00\x01binary",
            "documents/resume.docx": b"PK\x03\x04",
            "output/run.txt": "anything",
            ".claude/session.json": "{}",
        }
        for name, body in cases.items():
            with self.subTest(path=name):
                repo = repo_with({name: body})
                found = " | ".join(blobs(repo))
                self.assertIn(name, found)

    def test_a_normal_file_is_not_forbidden(self):
        repo = repo_with({"db/schema.sql": "CREATE TABLE jobs (id INTEGER);"})
        self.assertEqual(blobs(repo), [])

    def test_a_binary_blob_is_still_read_for_strings(self):
        """A tracker's rows are bytes; the email inside them is not."""
        body = b"SQLite format 3\x00\x01\x02" + OUTSIDE_EMAIL.encode()
        repo = repo_with({"notes.bin": body})
        self.assertIn(OUTSIDE_EMAIL, " | ".join(blobs(repo)))


class TestCommitMessagesAndIdentities(unittest.TestCase):
    """Scenario d. A message carries an address as easily as a file."""

    def test_a_key_pasted_into_a_commit_message(self):
        repo = repo_with({"a.txt": "fine"}, f"fixing auth, key was {KEY}")
        self.assertIn("NVIDIA API key", " | ".join(messages(repo)))

    def test_a_personal_value_in_a_commit_message(self):
        repo = repo_with({"a.txt": "fine"}, "ship to 12 Mulberry Lane")
        found = " | ".join(messages(repo, never=["12 Mulberry Lane"]))
        self.assertIn("12 Mulberry Lane", found)

    def test_a_contactable_identity_is_reported_once_not_per_commit(self):
        path = Path(tempfile.mkdtemp())
        run(path, "init", "-q", "-b", "main")
        for n in range(4):
            write(path, {f"f{n}.txt": "x"})
            run(path, "add", "-A")
            run(path, "-c", "user.name=Real Person",
                "-c", f"user.email={REAL_MAILBOX}",
                "commit", "-q", "-m", f"commit {n}")
        found = identities(path)
        self.assertEqual(len(found), 1, found)
        self.assertIn(REAL_MAILBOX, found[0])
        self.assertIn("on 4 of 4 commits", found[0])

    def test_a_noreply_identity_is_not_a_finding(self):
        path = Path(tempfile.mkdtemp())
        run(path, "init", "-q", "-b", "main")
        write(path, {"a.txt": "x"})
        run(path, "add", "-A")
        run(path, "-c", "user.name=Someone",
            "-c", "user.email=1234+someone@users.noreply.github.com",
            "commit", "-q", "-m", "one")
        self.assertEqual(identities(path), [])


class TestTheNoreplyRule(unittest.TestCase):
    """A mailbox nobody can write to is not contact data."""

    def test_known_shapes(self):
        for address in (NOREPLY, "no-reply" + "@github.com",
                        "1+u" + "@users.noreply.github.com"):
            with self.subTest(address=address):
                self.assertTrue(is_noreply(address))
                self.assertEqual(real_email_addresses(f"to: {address}"), [])

    def test_a_real_address_still_counts(self):
        self.assertFalse(is_noreply(OUTSIDE_EMAIL))
        self.assertEqual(real_email_addresses(f"to: {OUTSIDE_EMAIL}"),
                         [OUTSIDE_EMAIL])

    def test_the_attribution_trailer_this_repo_uses_is_not_a_finding(self):
        repo = repo_with(
            {"a.txt": "x"},
            "Do the thing\n\nCo-Authored-By: Claude Opus 5 "
            "<" + NOREPLY + ">" + chr(10))
        self.assertEqual(messages(repo), [])


class TestTheAllowlistCannotSwallowTheScanner(unittest.TestCase):
    """An exemption file is the obvious way to make this tool useless.

    One `.*` line and every future leak is 'already reported'. These are the
    guards that make the allowlist safe to have at all.
    """

    def lines(self) -> list[str]:
        text = scan_history.ALLOWLIST.read_text(encoding="utf-8")
        return [l.strip() for l in text.splitlines()
                if l.strip() and not l.strip().startswith("#")]

    def test_every_line_is_a_valid_anchored_pattern(self):
        for line in self.lines():
            with self.subTest(pattern=line):
                re.compile(line)
                self.assertTrue(line.startswith("^"),
                                "anchor it, so it exempts one shape of finding")

    def test_no_line_matches_an_unrelated_finding(self):
        """A pattern broad enough to hide a key is not an exemption."""
        decoys = [
            f"config/x.env @ 0badc0de: NVIDIA API key",
            "jsa/llm.py @ 0badc0de: absolute home path 'C:/Users/someone'",
            ".env @ 0badc0de: the local environment file, which holds the "
            "API key was committed",
            "tracker.db @ 0badc0de: a tracker database was committed",
        ]
        blocking, accepted = scan_history.split_accepted(decoys)
        self.assertEqual(accepted, [], "the allowlist is too broad")
        self.assertEqual(len(blocking), len(decoys))

    def test_every_line_carries_a_reason_above_it(self):
        text = scan_history.ALLOWLIST.read_text(encoding="utf-8")
        for block in text.split("\n\n"):
            for line in block.splitlines():
                if line.strip() and not line.strip().startswith("#"):
                    self.assertIn("#", block,
                                  f"{line!r} has no explanation above it")


class TestCIScansTheWholeHistory(unittest.TestCase):
    """A shallow checkout turns this scan into a check of one commit."""

    def test_the_history_job_asks_for_every_commit(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8")
        self.assertIn("scan_history.py", workflow)
        job = workflow[workflow.index("no-secrets-in-history:"):]
        job = job[:job.index("scan_history.py")]
        self.assertIn("fetch-depth: 0", job,
                      "actions/checkout fetches one commit by default, and a "
                      "history scan of one commit is a green check that "
                      "proves nothing")


class TestThisRepository(unittest.TestCase):
    """The audit's own findings, pinned so that a NEW one cannot slip in.

    Two things in this history are known, reported, and not fixable by editing
    a file, because history is what publishing exposes:

      1. Older versions of README.md named a home town and its postal code in
         prose. HEAD no longer does. The 16 earlier blobs still do, and only
         rewriting every commit would change that -- which is the operator's
         decision, not this test's.
      2. Every commit is signed with a contactable personal address
         (scan_identities, reported separately).

    Asserting `problems == []` here would be asserting something false, so the
    known ones are named and everything else fails. When the operator decides
    what to do about (1), delete the exemption with it.
    """

    # Read from tools/history_allowlist.txt rather than repeated here. This
    # test kept its own copy once, and when the scanner was tightened on
    # 2026-09-24 and found three more things, the copy and the file disagreed
    # about what had been decided. The file is the record; this reads it.

    @classmethod
    def setUpClass(cls):
        # fresh_clone_check copies the tracked files rather than cloning, and
        # somebody who downloads a tarball has no .git either. There is no
        # history to audit there, which is not a failure.
        if not (ROOT / ".git").exists():
            raise unittest.SkipTest("not a git checkout: no history to scan")

    def problems(self) -> list[str]:
        from tools import scan_secrets
        never, authorship = scan_secrets.personal_values()
        return (scan_history.scan_blobs(never, authorship)
                + scan_history.scan_messages(never, authorship)
                + scan_history.scan_refs(never, authorship))

    def test_nothing_beyond_the_reported_findings(self):
        unexpected, accepted = scan_history.split_accepted(self.problems())
        self.assertEqual(unexpected, [], "a NEW leak is in the history")
        self.assertTrue(accepted, "the known findings vanished: if history was "
                                  "rewritten, delete the allowlist entries too")

    def test_the_working_tree_no_longer_carries_it(self):
        """The part that could be fixed, was."""
        from tools import scan_secrets
        never, authorship = scan_secrets.personal_values()
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(
            scan_secrets.scan_text("README.md", "README.md", text,
                                   never, authorship), [])

    def test_the_known_findings_are_still_there_to_be_decided_about(self):
        """If this starts failing, the history changed. Say so out loud."""
        _, accepted = scan_history.split_accepted(self.problems())
        self.assertTrue(
            any("README.md" in p and "US address" in p for p in accepted),
            "the historical README blobs are clean now -- if the history was "
            "rewritten or the repository recreated, drop this test and the "
            "allowlist entries with it")


if __name__ == "__main__":
    unittest.main()
