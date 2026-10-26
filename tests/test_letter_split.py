"""Cover letters with the sentence-by-sentence check switched on (G7).

Off (the default), every letter prompt and check is byte-identical to before
(tests/test_prompts_unchanged.py). On: the prompt carries the fenced posting
excerpt and the sentence rules, the check holds sentences about the employer
to that excerpt and everything about the candidate exactly as before, and the
retry names the refused sentences.
"""


import unittest
from pathlib import Path
from unittest import mock

from jsa import letter
from jsa.llm import Completion, Usage
from tests.test_letter import DRAFT, GOOD, JOB, PROFILE

ROOT = Path(__file__).resolve().parents[1]
POSTING = ("Replit builds a browser-based development platform used by millions of "
           "developers. This role focuses on customer support for teams on weekends. "
           "Ensure SLA adherence. " + "Collaborate with the support team daily. " * 120)
EMPLOYER = ("Replit builds a browser-based development platform used by millions of "
            "developers.")


def with_sentence(sentence: str) -> str:
    """GOOD with one sentence added after the opening paragraph."""
    return GOOD.replace("language models.\n", "language models. " + sentence + "\n", 1)


class Split(unittest.TestCase):
    def setUp(self):
        self.job = dict(JOB, description=POSTING)
        on = mock.patch.object(letter, "SUBJECT_AWARE", True)
        on.start()
        self.addCleanup(on.stop)
        self.sent = []

    def fake(self, *texts):
        replies = list(texts)

        def complete(prompt, **k):
            self.sent.append((prompt, k.get("system") or ""))
            return Completion(text=replies.pop(0) if replies else texts[-1],
                              usage=Usage(model="t"))
        return mock.patch("jsa.letter.llm.complete", side_effect=complete)


class TestThePrompt(Split):
    def test_the_posting_excerpt_is_fenced_and_capped(self):
        with self.fake(GOOD):
            letter.write(self.job, PROFILE, DRAFT)
        prompt, system = self.sent[0]
        excerpt = letter.posting_excerpt(self.job)
        self.assertLess(len(excerpt), len(POSTING))
        inside = prompt.split("<posting_excerpt>")[1].split("</posting_excerpt>")[0]
        self.assertIn("Replit builds a browser-based", inside)
        outside = prompt.replace(inside, "")
        self.assertNotIn("browser-based", outside)
        self.assertIn("never mentions the candidate", system)
        for value in ("Dana Rivers", "dana@example.test", "555-0100", "1 Example Way"):
            self.assertNotIn(value, prompt)

    def test_off_means_no_excerpt_and_no_sentence_rules(self):
        with mock.patch.object(letter, "SUBJECT_AWARE", False), self.fake(GOOD):
            letter.write(self.job, PROFILE, DRAFT)
        self.assertNotIn("<posting_excerpt>", self.sent[0][0])
        self.assertNotIn("never mentions the candidate", self.sent[0][1])


class TestTheCheck(Split):
    def test_an_employer_sentence_in_the_excerpts_words_passes(self):
        text = with_sentence(EMPLOYER)
        excerpt = letter.posting_excerpt(self.job)
        self.assertEqual(letter.check(text, PROFILE, self.job, excerpt), [])
        # Off, the same letter is refused for the posting's words.
        self.assertTrue(letter.check(text, PROFILE, self.job))
        with self.fake(text):
            result = letter.write(self.job, PROFILE, DRAFT)
        self.assertEqual(result.source, "model")

    def test_the_same_words_as_a_claim_about_the_person_are_refused(self):
        claim = with_sentence("I built a browser-based development platform used by "
                              "millions of developers.")
        excerpt = letter.posting_excerpt(self.job)
        self.assertTrue(letter.check(claim, PROFILE, self.job, excerpt))
        with self.fake(claim, claim):
            result = letter.write(self.job, PROFILE, DRAFT)
        self.assertEqual(result.source, "composed")

    def test_degree_do_not_claim_and_ongoing_work_still_refused(self):
        excerpt = letter.posting_excerpt(self.job)
        for sentence, reason in (
                ("Replit values a bachelor's degree.", "degree"),
                ("Replit runs Kubernetes for developers.", "Kubernetes"),
                ("I built a Python pipeline that detects highlights in raw video.", "built")):
            with self.subTest(reason=reason):
                problems = letter.check(with_sentence(sentence), PROFILE, self.job, excerpt)
                self.assertTrue(any(reason.lower() in p.lower() for p in problems), problems)

    def test_words_beyond_the_excerpt_are_not_allowed(self):
        """The check reads only what the drafter was shown."""
        job = dict(self.job, description=POSTING + " Replit pioneers quantum telemetry.")
        excerpt = letter.posting_excerpt(job)
        self.assertNotIn("quantum", excerpt)
        problems = letter.check(with_sentence("Replit pioneers quantum telemetry."),
                                PROFILE, job, excerpt)
        self.assertTrue(any("quantum" in p for p in problems), problems)


class TestTheRetry(Split):
    def test_the_retry_names_the_sentence_and_carries_no_identity(self):
        claim = with_sentence("I ensured SLA adherence for every customer.")
        with self.fake(claim, GOOD):
            result = letter.write(self.job, PROFILE, DRAFT)
        self.assertEqual(result.source, "model")
        retry = self.sent[1][0]
        self.assertIn("sentence", retry)
        self.assertIn("adherence", retry)
        for value in ("Dana Rivers", "dana@example.test"):
            self.assertNotIn(value, retry)


class TestTheDashboardRecheck(unittest.TestCase):
    def test_it_uses_the_same_excerpt_when_the_switch_is_on(self):
        source = (ROOT / "jsa" / "web" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("letter.posting_excerpt(job) if letter.SUBJECT_AWARE else None", source)
        self.assertIn("letter.check(text, prof, job, excerpt)", source)


class TestTheSwitchStartsOff(unittest.TestCase):
    def test_off_until_measured(self):
        self.assertIs(letter.SUBJECT_AWARE, False)


if __name__ == "__main__":
    unittest.main()
