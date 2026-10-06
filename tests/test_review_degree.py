"""Degree and fact fixes from the October review.

R-11: prep told the model "NOT conferred" for everyone, and refused to run
for anyone whose credential states a degree they hold. R-21: gaps in the
degree checks. R-22: JSON retries lost the caller's system prompt. R-28:
negative years of experience. Profiles are fictional.
"""

import copy
import datetime as dt
import unittest
from unittest import mock

from jsa import answers, facts, llm, prep
from jsa.llm import Usage
from tests.test_prep_cmd import PROFILE, PrepCase, fake_questions


def holding(credential: str) -> dict:
    profile = copy.deepcopy(PROFILE)
    profile["education"] = [{"institution": "State University", "field": "Physics",
                             "credential": credential}]
    return profile


class TestPrepForADegreeHolder(PrepCase):
    def test_prep_runs_and_does_not_say_not_conferred(self):
        seen = {}

        def spy(prompt, **kw):
            seen["prompt"] = prompt
            return fake_questions()
        con = self.con()
        self.addCleanup(con.close)
        with mock.patch("jsa.prep.llm.complete_json", side_effect=spy):
            result = prep.generate(con, 1, profile=holding("PhD, Physics"))
        self.assertNotIn("not conferred", seen["prompt"].lower())
        self.assertTrue(any("PhD, Physics" in q.answer_notes for q in result.questions))

    def test_without_one_the_model_is_still_held_to_it(self):
        def claims(*_a, **_k):
            return ({"questions": [{"question": "Tell me about your degree.", "why": "x",
                                    "answer_notes": "I graduated with a degree in physics."}],
                     "company_brief": ""}, mock.Mock(model="m"))
        con = self.con()
        self.addCleanup(con.close)
        with mock.patch("jsa.prep.llm.complete_json", side_effect=claims):
            with self.assertRaises(prep.DegreeClaimError):
                prep.generate(con, 1, profile=holding(
                    "Coursework completed (degree not conferred)"))


class TestCredentialWording(unittest.TestCase):
    def test_not_completed_wordings(self):
        for line in ("Unconferred B.S.", "Non-conferred BS", "Withdrew in final year",
                     "3 years toward a B.S.", "BS candidate", "ABD",
                     "Attended; left before graduating",
                     "Coursework completed (degree not conferred)"):
            self.assertFalse(facts.claims_a_degree(line), line)

    def test_degrees_held(self):
        for line in ("B.A., no honors", "BS Computer Science, 2020", "PhD, Physics"):
            self.assertTrue(facts.claims_a_degree(line), line)


class TestAnswersNeverMentionADegree(unittest.TestCase):
    JOB = {"title": "Support Engineer", "description": "Python support.", "track": "engineering"}

    def test_a_mention_is_refused_when_none_is_held(self):
        profile = holding("Coursework completed (degree not conferred)")
        for text in ("I apply my CS degree daily.", "With a bachelor's in CS, I help.",
                     "As someone with a BS, I build tools."):
            problems = answers.check(text, profile, self.JOB)
            self.assertTrue(any("degree" in p for p in problems), (text, problems))


class TestJsonRetriesKeepTheSystemPrompt(unittest.TestCase):
    def test_every_attempt_gets_it(self):
        systems = []

        def fake(prompt, *, system=None, **kw):
            systems.append(system)
            return llm.Completion("not json" if len(systems) == 1 else "{}", Usage())
        with mock.patch("jsa.llm.complete", side_effect=fake):
            llm.complete_json("p", system="NEVER mention a degree", attempts=2)
        self.assertEqual(systems, ["NEVER mention a degree"] * 2)


class TestYearsOfExperience(unittest.TestCase):
    TODAY = dt.date(2026, 10, 5)

    def years(self, *entries):
        return facts.years_of_experience({"experience": list(entries)}, self.TODAY)

    def test_never_negative(self):
        months, _ = self.years({"start": "2024-05", "end": "2023-01"})
        self.assertGreaterEqual(months, 1)
        months, _ = self.years({"start": "2030-01", "current": True})
        self.assertGreaterEqual(months, 1)

    def test_present_as_an_end_is_current(self):
        months, _ = self.years({"start": "2024-10", "end": "Present"})
        self.assertEqual(months, 25)
        self.assertIsNone(facts.gap_since(
            {"experience": [{"start": "2021-01", "end": "Present"}]}, self.TODAY))


if __name__ == "__main__":
    unittest.main()
