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


class TestLetterReport(unittest.TestCase):
    """--letters (G7): the old and new checks on saved cover letters."""

    def test_a_letter_passing_only_the_new_check_is_listed_for_reading(self):
        from tests.test_letter import GOOD as LETTER, JOB, PROFILE
        posting = ("Replit builds a browser-based development platform used by "
                   "millions of developers.")
        job = dict(JOB, description=posting)
        with_employer = LETTER.replace("language models.\n",
                                       "language models. " + posting + "\n", 1)
        claim = LETTER.replace("language models.\n", "language models. I built a "
                               "browser-based development platform.\n", 1)
        saved = [{"job_id": 1, "job": job, "excerpt": posting, "letter": True,
                  "first": {"letter": with_employer}, "retried": {"letter": with_employer}},
                 {"job_id": 2, "job": job, "excerpt": posting, "letter": True,
                  "first": {"letter": claim}, "retried": {"letter": claim}}]
        out = io.StringIO()
        with redirect_stdout(out):
            counts, newly, _ = measure_verifier.report_letters(PROFILE, saved)
        self.assertEqual((counts["old_first"], counts["new_first"]), (0, 1))
        self.assertEqual([(j, label) for j, label, _ in newly], [(1, "first"), (1, "after")])
        self.assertIn("Letters passing ONLY under the new check: 2", out.getvalue())


class TestOutreachReport(unittest.TestCase):
    """--outreach (G8): the full check on saved messages, signed as sent."""

    def test_only_messages_passing_the_full_check_are_listed(self):
        from tests.test_outreach_split import CONTACT, JOB, PROFILE
        ok = "Hi Jordan, your team at Globex uses Zendesk. I resolved forty customer tickets a day."
        claim = "I have used Zendesk for years."
        saved = [{"job_id": 1, "channel": "linkedin_dm", "job": JOB, "contact": CONTACT,
                  "first": claim, "after": ok, "outreach": True}]
        out = io.StringIO()
        with redirect_stdout(out):
            counts, passing, shape = measure_verifier.report_outreach(PROFILE, saved)
        self.assertEqual((counts["first"], counts["after"]), (0, 1))
        self.assertEqual([(j, ch, label) for j, ch, label, _ in passing],
                         [(1, "linkedin_dm", "after")])
        self.assertIn("passing the full check 0->1/1", out.getvalue())


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
            counts, newly, shape = measure_verifier.report(ready(), saved)
        self.assertEqual((counts["role"]["old"], counts["role"]["new"]), (0, 1))
        self.assertEqual((counts["project"]["old"], counts["project"]["new"]), (1, 1))
        self.assertEqual(counts["company"]["new"], 0)        # a claim stays refused
        self.assertEqual([(a, label) for _, a, label, _ in newly],
                         [("role", "first"), ("role", "retry")])
        self.assertIn("Pass ONLY under the new check: 2", out.getvalue())
        self.assertEqual(shape["employer"], 1)          # the role draft's first sentence

    def test_questions_from_a_saved_run(self):
        import json
        import tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "saved.json"
        path.write_text(json.dumps([
            {"job_id": 421, "question": "Why us?"}, {"job_id": 421, "question": "Why us?"},
            {"job_id": 484, "question": "A project?"},
            {"job_id": 484, "question": "(written answers)", "written": True}]),
            encoding="utf-8")
        self.assertEqual(measure_verifier._rows_from(path),
                         [(421, "Why us?"), (484, "A project?")])

    def test_sentence_shapes_and_length_only_refusals(self):
        job = {"title": "Support Engineer", "company": "Riverton Grid", "location": None}
        got = measure_verifier.shapes(
            EMPLOYER + "My work mirrors Riverton Grid's software. Shipped it. " + GOOD,
            ready(), job, POSTING)
        self.assertEqual(got["employer"], 1)
        self.assertEqual(got["mixed"], 1)
        self.assertEqual(got["unclear"], 1)
        self.assertGreaterEqual(got["about you"], 1)
        self.assertTrue(measure_verifier.length_only(["16 words, outside 40-150"]))
        self.assertFalse(measure_verifier.length_only(["16 words, outside 40-150",
                                                       "mentions a degree"]))

    def test_the_drafts_go_to_the_gitignored_logs_folder(self):
        source = (ROOT / "tools" / "measure_verifier.py").read_text(encoding="utf-8")
        self.assertIn('config.HOME / "logs"', source)
        self.assertIn("logs/", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
