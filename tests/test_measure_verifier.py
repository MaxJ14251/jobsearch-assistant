"""The verifier measurement tool's report, on fictional saved drafts (plan 37).
No model call: drafting is the paid half and is run by hand."""

import io
import sys
import unittest
from contextlib import redirect_stdout

from jsa.config import ROOT
from tests.test_answers import GOOD, ready

sys.path.insert(0, str(ROOT / "tools"))
import measure_verifier  # noqa: E402

POSTING = "Riverton Grid builds microgrid orchestration software for utilities."
EMPLOYER = "Riverton Grid builds microgrid orchestration software for utilities. "


class TestReport(unittest.TestCase):
    def test_counts_and_newly_passing_drafts(self):
        job = {"id": 1, "title": "Support Engineer", "company": "Riverton Grid",
               "location": None, "track": "engineering", "description": POSTING}
        saved = [{"job_id": 1, "question": "Why us?", "job": job, "excerpt": POSTING,
                  "first": {"role": EMPLOYER + GOOD, "project": GOOD,
                            "company": "I built microgrid orchestration software. " + GOOD},
                  "retried": {"role": EMPLOYER + GOOD, "project": GOOD,
                              "company": "I built microgrid orchestration software. " + GOOD}}]
        out = io.StringIO()
        with redirect_stdout(out):
            counts, newly = measure_verifier.report(ready(), saved)
        self.assertEqual((counts["role"]["old"], counts["role"]["new"]), (0, 1))
        self.assertEqual((counts["project"]["old"], counts["project"]["new"]), (1, 1))
        self.assertEqual(counts["company"]["new"], 0)        # a claim stays refused
        self.assertEqual([(a, label) for _, a, label, _ in newly],
                         [("role", "first"), ("role", "retry")])
        self.assertIn("Pass ONLY under the new check: 2", out.getvalue())

    def test_the_drafts_go_to_the_gitignored_logs_folder(self):
        source = (ROOT / "tools" / "measure_verifier.py").read_text(encoding="utf-8")
        self.assertIn('config.HOME / "logs"', source)
        self.assertIn("logs/", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
