"""Interview prep tests.

TestDegreeDenyList is the one that matters. A model asked to write a confident
answer about education reaches for fluent phrasing — "my degree in Computer
Science", "after I graduated" — which is natural, persuasive, and false. The
candidate completed coursework; the degree was never conferred.

Fluent and false is exactly what a model produces unprompted, so every
generated string is checked before it is stored.
"""

import json
import tempfile
import unittest
from pathlib import Path

from jsa import db, prep
from jsa.prep import (
    DEGREE_DENY,
    DegreeClaimError,
    Prep,
    Question,
    assert_no_degree_claim,
    degree_answer,
    standard_drills,
)
from tests.test_tailor import PROFILE


class TestDegreeDenyList(unittest.TestCase):
    """Phrasings that assert a credential that was never awarded."""

    # Written the way a model actually writes them: fluent and plausible.
    FABRICATED = [
        "My degree in Computer Science gave me the fundamentals for this work.",
        "After I graduated from State University, I moved into sales.",
        "I graduated with a strong foundation in algorithms.",
        "I hold a degree in Computer Science from State.",
        "I have a bachelor's in CS and three years of industry experience.",
        "I earned my degree while working part time.",
        "I completed my degree in 2020 before moving into the field.",
        "Since obtaining my degree I've focused on applied AI.",
        "I have a PhD-level interest in distributed systems.",
        "My alma mater has a strong engineering program.",
        "I received my degree from State University.",
        "I finished my degree and immediately started at Riverton.",
    ]

    def test_every_fabricated_phrasing_is_caught(self):
        for text in self.FABRICATED:
            with self.subTest(text=text[:44]):
                with self.assertRaises(DegreeClaimError):
                    assert_no_degree_claim(text)

    def test_case_and_spacing_do_not_evade_the_check(self):
        for text in ["MY DEGREE IN computer science",
                     "my   degree   in  CS",
                     "My\nDegree\nIn CS"]:
            with self.subTest(text=text[:30]):
                with self.assertRaises(DegreeClaimError):
                    assert_no_degree_claim(text)

    def test_honest_phrasings_are_allowed(self):
        for text in [
            "I studied Computer Science at State University and "
            "completed coursework, though I did not finish the degree.",
            "I have coursework in Computer Science but no conferred degree.",
            "Where a posting asks for a degree or equivalent experience, the "
            "equivalent experience is what I'd point to.",
            "I took two years of CS classes before moving into the workforce.",
        ]:
            with self.subTest(text=text[:44]):
                self.assertEqual(assert_no_degree_claim(text), text)

    def test_deny_list_covers_the_obvious_families(self):
        joined = " ".join(DEGREE_DENY)
        for stem in ("graduated", "degree", "bachelor", "phd", "doctorate"):
            self.assertIn(stem, joined)


class TestPreparedDegreeAnswer(unittest.TestCase):
    """The answer itself is built from the profile, not generated."""

    def setUp(self):
        self.answer = degree_answer(PROFILE)

    def test_it_passes_its_own_deny_list(self):
        self.assertEqual(assert_no_degree_claim(self.answer), self.answer)

    def test_it_states_the_field_of_study(self):
        """The strong half of the fact — most postings say 'or equivalent'."""
        self.assertIn("Computer Science", self.answer)

    def test_it_says_the_degree_was_not_finished(self):
        lowered = self.answer.lower()
        self.assertTrue(
            any(p in lowered for p in ("did not finish", "not conferred",
                                       "did not complete")),
            self.answer)

    def test_it_names_the_institution_and_years_from_the_profile(self):
        edu = PROFILE["education"][0]
        self.assertIn(edu["institution"], self.answer)
        if edu.get("start"):
            self.assertIn(str(edu["start"]), self.answer)

    def test_it_pivots_to_equivalent_experience(self):
        self.assertIn("equivalent experience", self.answer.lower())


class TestStandardDrills(unittest.TestCase):
    def test_both_drills_are_always_present(self):
        questions = standard_drills(PROFILE)
        joined = " ".join(q.question.lower() for q in questions)
        self.assertIn("gap", joined)
        self.assertIn("educational", joined)

    def test_every_drill_answer_passes_the_deny_list(self):
        for q in standard_drills(PROFILE):
            assert_no_degree_claim(q.answer_notes)
            assert_no_degree_claim(q.question)

    def test_the_gap_drill_reads_the_profiles_own_dates(self):
        """It said "November 2023" whatever the profile held (plan 17)."""
        from jsa.facts import gap_since
        gap = gap_since(PROFILE)
        drills = standard_drills(PROFILE)
        if gap is None:
            self.assertEqual(len(drills), 1)
        else:
            self.assertIn(gap[0], drills[0].question)
            self.assertIn(gap[0], drills[0].answer_notes)


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        self.con = db.connect(self.dbfile)
        self.con.execute(
            "INSERT INTO companies (id, name, slug) VALUES (1,'Example','example')")
        self.con.execute(
            "INSERT INTO jobs (id, company_id, title, url) "
            "VALUES (1,1,'Engineer','https://example.com/j/1')")
        self.con.execute(
            "INSERT INTO applications (id, job_id, status) VALUES (1,1,'applied')")

    def tearDown(self):
        self.con.close()

    def test_prep_round_trips(self):
        p = Prep(application_id=1, round="phone_screen",
                 questions=standard_drills(PROFILE), company_brief="A team.")
        prep.save(self.con, p)
        row = self.con.execute(
            "SELECT * FROM interview_prep WHERE application_id = 1").fetchone()
        stored = json.loads(row["questions"])
        self.assertEqual(len(stored), len(p.questions))
        self.assertIn("answer_notes", stored[0])

    def test_regenerating_adds_a_row_rather_than_overwriting(self):
        """Earlier prep is history worth keeping."""
        p = Prep(application_id=1, round="phone_screen",
                 questions=[Question("Q?", "why", "notes")])
        prep.save(self.con, p)
        prep.save(self.con, p)
        count = self.con.execute(
            "SELECT COUNT(*) FROM interview_prep WHERE application_id = 1"
        ).fetchone()[0]
        self.assertEqual(count, 2)



class TestConjugationCoverage(unittest.TestCase):
    """Regression: a literal phrase list cannot keep up with tense.

    "obtained my degree" was on the list; "obtaining my degree" was not, and
    it passed. Same claim, different conjugation. The check is now pattern-
    based over verb families.
    """

    CONJUGATIONS = [
        "Since obtaining my degree I've focused on applied AI.",
        "Obtaining my degree took four years.",
        "I am earning my degree part time.",
        "Having completed my degree, I moved into sales.",
        "He received his degree in 2020.",
        "Upon finishing my bachelor's I joined Riverton.",
        "I'm graduating this spring.",
        "Graduating gave me the fundamentals.",
        "I attained my degree through night classes.",
    ]

    def test_all_tenses_are_caught(self):
        for text in self.CONJUGATIONS:
            with self.subTest(text=text[:40]):
                with self.assertRaises(DegreeClaimError):
                    assert_no_degree_claim(text)

    def test_the_error_names_what_it_matched(self):
        with self.assertRaises(DegreeClaimError) as ctx:
            assert_no_degree_claim("Since obtaining my degree I moved on.")
        self.assertIn("obtaining my degree", str(ctx.exception))

    def test_unrelated_graduate_words_are_not_over_caught(self):
        """'Graduate programme' as a role type must still be discussable."""
        # The candidate may legitimately apply to roles labelled "new grad".
        self.assertEqual(
            assert_no_degree_claim("The posting is for a new grad role."),
            "The posting is for a new grad role.")

    def test_coursework_framing_still_passes(self):
        text = ("I completed two years of Computer Science coursework at West "
                "Virginia University; the degree was not conferred.")
        self.assertEqual(assert_no_degree_claim(text), text)


if __name__ == "__main__":
    unittest.main()
