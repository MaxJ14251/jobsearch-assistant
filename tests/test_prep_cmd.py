"""`jsa prep` — interview preparation, and the degree guard meeting real use.

The model is mocked so the suite runs offline. The guard being exercised is
real: the same assert_no_degree_claim, the same scrub_prompt.

283 of the 514 enriched jobs in the tracker state a degree requirement, and the
degree was not conferred. That answer has to be right every single time, so the
tests here check the text it produces, not merely that nothing raised.
"""

import copy
import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import approvals, db, prep
from jsa.config import ROOT
from jsa.prep import (
    MAX_DESCRIPTION_CHARS,
    DegreeClaimError,
    assert_no_degree_claim,
    standard_drills,
)
from tests.test_tailor import PROFILE as DISK_PROFILE

JD = (
    "You will develop software for robotic platforms and motion-control "
    "systems, integrating with motors, encoders and sensors. Experience with "
    "ROS 2 required. Bachelor's degree in Computer Science or equivalent "
    "experience."
)


def profile_with_auth() -> dict:
    """Whatever profile is on disk, with the one preference drafting needs.

    On a fresh clone that is the example, where work_authorization is null and
    require_decided_preferences correctly refuses.
    """
    profile = copy.deepcopy(DISK_PROFILE)
    prefs = profile.setdefault("job_search_preferences", {})
    if not (prefs.get("work_authorization") or "").strip():
        prefs["work_authorization"] = "US citizen - no sponsorship required"
    return profile


PROFILE = profile_with_auth()


def fake_questions(*_args, **_kwargs):
    payload = {
        "questions": [
            {"question": "What is your experience with ROS 2?",
             "why": "Experience with ROS 2 required",
             "answer_notes": "I have not used ROS 2. I build cloud-native "
                             "Python pipelines and would draw on that."},
        ],
        "company_brief": "A robotics team building motion-control software.",
    }
    return payload, mock.Mock(model="test/model")


class TestDegreeGuard(unittest.TestCase):
    """A fluent, natural, false sentence is what a model writes unprompted.

    "after I graduated" is not a lie the model is trying to tell -- it is the
    idiomatic way to phrase the sentence, which is exactly why a literal
    deny-list alone was not enough and the verb-family patterns exist.
    """

    FALSE_CLAIMS = [
        "after I graduated from State University",
        "my degree in Computer Science",
        "BS in Computer Science, State University",
        "while obtaining my degree",
        "graduated with honors",
    ]

    HONEST = [
        "I did not finish the degree at State",
        "I completed two years of coursework toward a CS degree",
        "I never completed my degree",
        "Undergraduate coursework completed (degree not conferred)",
    ]

    def test_false_claims_raise(self):
        for text in self.FALSE_CLAIMS:
            with self.subTest(text=text):
                with self.assertRaises(DegreeClaimError):
                    assert_no_degree_claim(text)

    def test_honest_phrasing_survives(self):
        """The negation window. Over-correcting here is its own failure --
        a guard that rejects the truthful answer leaves nothing to say."""
        for text in self.HONEST:
            with self.subTest(text=text):
                assert_no_degree_claim(text)


class TestStandardDrills(unittest.TestCase):
    """Two questions come up in every screen and must never be improvised."""

    def setUp(self):
        self.drills = standard_drills(PROFILE)

    def test_both_drills_are_present(self):
        text = " ".join(d.question.lower() for d in self.drills)
        self.assertIn("gap", text)
        self.assertTrue(any("education" in d.question.lower() for d in self.drills))

    def test_the_degree_answer_meets_the_or_equivalent_wording(self):
        """Most postings that require a degree say "or equivalent experience".

        An answer that dodges the question passes a no-claim check and still
        leaves the candidate with nothing to say.
        """
        answer = next(d.answer_notes for d in self.drills
                      if "education" in d.question.lower())
        self.assertIn("equivalent", answer.lower())
        self.assertTrue(
            any(p in answer.lower() for p in ("did not finish", "not conferred",
                                              "never completed")),
            "the answer must state plainly that the degree was not conferred",
        )

    def test_every_drill_answer_passes_the_guard(self):
        for drill in self.drills:
            assert_no_degree_claim(drill.answer_notes)


class PrepCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        real_connect = db.connect
        self._real = real_connect
        con = real_connect(self.dbfile)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute(
            "INSERT INTO jobs (id,company_id,title,url,description,degree_required) "
            "VALUES (1,1,'Robotics Engineer','https://acme.test/1',?,1)", (JD,))
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        patcher = mock.patch(
            "jsa.db.connect", side_effect=lambda *a, **k: real_connect(self.dbfile))
        patcher.start()
        self.addCleanup(patcher.stop)

    def con(self):
        return self._real(self.dbfile)

    def run_prep(self, application_id=1, round="phone_screen"):
        from jsa.cli import cmd_prep
        with mock.patch("jsa.prep.llm.complete_json", side_effect=fake_questions), \
             mock.patch("jsa.cli.load_profile", return_value=PROFILE), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cmd_prep(Namespace(application_id=application_id, round=round))


class TestPrepCommand(PrepCase):
    def test_writes_a_real_row_with_the_drills_first(self):
        self.assertEqual(self.run_prep(), 0)
        con = self.con()
        row = con.execute("SELECT * FROM interview_prep").fetchone()
        con.close()
        self.assertIsNotNone(row, "no interview_prep row was written")
        questions = json.loads(row["questions"])
        self.assertGreaterEqual(len(questions), 3)
        joined = " ".join(q["question"].lower() for q in questions[:2])
        self.assertIn("gap", joined, "the standard drills should lead")

    def test_unknown_application_is_refused(self):
        self.assertEqual(self.run_prep(application_id=999), 1)

    def test_running_twice_appends_rather_than_overwrites(self):
        self.run_prep()
        self.run_prep(round="technical")
        con = self.con()
        rows = con.execute(
            "SELECT id, round FROM interview_prep ORDER BY id").fetchall()
        con.close()
        self.assertEqual(len(rows), 2, "prior prep was overwritten")
        self.assertEqual([r["round"] for r in rows], ["phone_screen", "technical"])

    def test_identity_never_reaches_the_prompt(self):
        """Assert what left the machine, not that the scrubber was called."""
        captured = {}

        def spy(prompt, **kwargs):
            captured["sent"] = (prompt + str(kwargs.get("system", ""))).lower()
            return fake_questions()

        con = self.con()
        with mock.patch("jsa.prep.llm.complete_json", side_effect=spy):
            prep.generate(con, 1, profile=PROFILE)
        con.close()

        ident = PROFILE.get("identity") or {}
        loc = ident.get("location") or {}
        for value in (ident.get("full_name"), ident.get("email"),
                      ident.get("phone"), loc.get("street"),
                      loc.get("postal_code")):
            if isinstance(value, str) and value.strip():
                self.assertNotIn(value.lower(), captured["sent"],
                                 f"{value!r} reached the outbound prompt")

    def test_questions_are_grounded_in_the_posting(self):
        """A ROS question for a posting that never mentions ROS is a failure."""
        con = self.con()
        with mock.patch("jsa.prep.llm.complete_json", side_effect=fake_questions):
            result = prep.generate(con, 1, profile=PROFILE)
        con.close()
        generated = result.questions[2:]
        self.assertTrue(generated)
        for question in generated:
            self.assertTrue(
                question.why.strip(),
                "every generated question must cite the line that prompts it")


class TestPromptAsksForUsableText(unittest.TestCase):
    """Found by reading the real output, not by a failing assertion.

    The two standard drills are written in the first person; the generated
    notes came back in the third ("The candidate has no ROS experience"). A
    reader switching voice halfway down a page has to translate it back under
    pressure, five minutes before a call.
    """

    def test_the_prompt_requests_first_person(self):
        self.assertIn("FIRST PERSON", prep.PROMPT)

    def test_the_prompt_still_forbids_inventing_experience(self):
        combined = (prep.PROMPT + prep.SYSTEM).lower()
        self.assertIn("invent", combined)
        self.assertIn("not conferred", combined.replace("was not conferred",
                                                        "not conferred"))


class TestTruncationIsNamed(unittest.TestCase):
    def test_the_limit_is_a_constant_not_a_magic_number(self):
        source = (ROOT / "jsa" / "prep.py").read_text(encoding="utf-8")
        self.assertIsInstance(MAX_DESCRIPTION_CHARS, int)
        self.assertNotIn('[:4000]', source)


if __name__ == "__main__":
    unittest.main()
