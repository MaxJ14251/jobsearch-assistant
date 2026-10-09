"""`jsa verify --write` changes `verified:` values and nothing else.

Found 2026-10-09: the old rewrite went through yaml.safe_dump, which dropped
every comment from the shipped companies.yaml, and one failed run of The Muse
set it to false, so the next discovery skipped it.
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import cli, discover
from jsa.discover import SourceReport

FILE = """\
# Header comment: kept byte for byte.
sources:

  # A board that worked before.
  - company: Riverton Grid
    slug: riverton
    kind: greenhouse
    board: riverton
    verified: true          # 12 listings

  # Not checked yet.
  - company: "Example (Labs)"
    slug: example
    kind: lever
    board: example
    verified: false   # waiting

  - company: No Flag Yet
    slug: noflag
    kind: ashby
    board: noflag

  - company: Off
    slug: off
    kind: manual
    enabled: false
    verified: false
"""


def report(company, status="ok", fetched=5):
    return SourceReport(company, "x", status, fetched=fetched)


class TestRecordVerified(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "companies.yaml"
        self.path.write_text(FILE, encoding="utf-8", newline="")

    def test_only_the_values_change(self):
        on, kept = discover.record_verified(self.path, [
            report("Riverton Grid"), report("Example (Labs)"), report("No Flag Yet"),
            report("Off", status="disabled", fetched=0)])
        self.assertEqual(on, ["Example (Labs)", "No Flag Yet"])
        self.assertEqual(kept, [])
        expected = (FILE.replace("verified: false   # waiting", "verified: true   # waiting")
                    .replace("    board: noflag\n", "    board: noflag\n")
                    .replace("  - company: No Flag Yet\n",
                             "  - company: No Flag Yet\n    verified: true\n"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), expected)

    def test_a_failed_run_never_unverifies(self):
        on, kept = discover.record_verified(self.path, [
            report("Riverton Grid", status="FAIL timed out", fetched=0),
            report("Example (Labs)", fetched=0)])
        self.assertEqual((on, kept), ([], ["Riverton Grid"]))
        self.assertEqual(self.path.read_text(encoding="utf-8"), FILE)

    def test_crlf_and_comments_survive(self):
        self.path.write_bytes(FILE.replace("\n", "\r\n").encode("utf-8"))
        discover.record_verified(self.path, [report("Example (Labs)")])
        data = self.path.read_bytes()
        self.assertIn(b"verified: true   # waiting\r\n", data)
        self.assertIn(b"# Header comment: kept byte for byte.\r\n", data)
        self.assertNotIn(b"\r\r", data)

    def test_a_file_it_cannot_line_up_is_refused(self):
        self.path.write_text("sources:\n  - {company: A, verified: false}\n",
                             encoding="utf-8")
        with self.assertRaises(ValueError):
            discover.record_verified(self.path, [report("A")])

    def test_the_command_reports_and_writes_through_it(self):
        reports = [report("Riverton Grid", status="FAIL 429", fetched=0),
                   report("Example (Labs)")]
        out = io.StringIO()
        with mock.patch.object(cli, "COMPANIES_PATH", self.path), \
             mock.patch.object(discover, "verify_sources", return_value=reports), \
             redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.cmd_verify(argparse.Namespace(write=True))
        self.assertEqual(code, 0)
        self.assertIn("1 newly verified (Example (Labs))", out.getvalue())
        self.assertIn("Riverton Grid failed this run and was left verified", out.getvalue())
        self.assertIn("# A board that worked before.", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
