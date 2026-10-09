"""What a posting asks for in education, read with no model (plan 32).

Fictional sentences in the shapes real postings use. Measured on the owner's
tracker (2026-10-08, ADR 0032): where the model's `degree_required` followed
its own rule, this agrees 202 of 203 times.
"""

import unittest

from jsa import degree


def level(text):
    return degree.classify(text).level


class TestLevels(unittest.TestCase):
    CASES = {
        # nothing for the candidate
        "You will build tools for our support team.": "none",
        "No degree required. We hire for skills.": "none",
        "A college degree is not required for this role.": "none",
        "You don't need a degree to thrive here.": "none",
        "Our engineers hold PhDs from leading universities.": "none",
        "High school diploma or equivalent.": "none",
        "Proficiency with MS Office and Excel.": "none",
        # an associate's
        "Associate's degree in electronics technology.": "associate",
        "Associate degree or two-year degree in a technical field.": "associate",
        # a bachelor's, with an alternative or only preferred
        "Bachelor's degree in Computer Science or equivalent experience.": "bachelors_or_equiv",
        "BS in Computer Science or equivalent practical experience.": "bachelors_or_equiv",
        "Bachelor's degree preferred.": "bachelors_or_equiv",
        "A degree in Computer Science is a plus.": "bachelors_or_equiv",
        ("Bachelor's degree in computer science, engineering or math;\n"
         "OR 2+ years of professional experience building software in lieu of a degree"):
            "bachelors_or_equiv",
        "Bachelor's degree or an equivalent combination of education and experience.":
            "bachelors_or_equiv",
        "Master's degree preferred.": "bachelors_or_equiv",
        # a bachelor's, required
        "Bachelor's degree in Electrical Engineering.": "bachelors",
        "B.S. in Mechanical Engineering required.": "bachelors",
        "BA/BS in Economics or Finance.": "bachelors",
        "4-year degree from an accredited university.": "bachelors",
        "Degree in Software Engineering or Computer Science.": "bachelors",
        "Currently pursuing a degree in Computer Science.": "bachelors",
        "Requirements:\nBachelor's degree in Physics": "bachelors",
        # a bachelor's required, a graduate degree preferred or offered
        "BS in CS required. MS preferred.": "masters_preferred",
        "Requirements:\nBachelor's in Engineering\nPreferred qualifications:\nMaster's degree":
            "masters_preferred",
        "BS or MS in Computer Science.": "masters_preferred",
        # a graduate degree, required
        "PhD in Machine Learning or a related field.": "masters",
        "Master's degree in Statistics required.": "masters",
        "Medical degree required (MD, DO) with board certification.": "masters",
        "JD or foreign equivalent from an accredited law school.": "masters",
    }

    def test_each_sentence(self):
        for text, expected in self.CASES.items():
            with self.subTest(text=text[:60]):
                self.assertEqual(level(text), expected)

    def test_there_are_about_thirty(self):
        self.assertGreaterEqual(len(self.CASES), 30)


class TestDetails(unittest.TestCase):
    def test_evidence_is_the_sentence_and_short(self):
        facts = degree.classify("We move fast.\nBachelor's degree in Computer Science "
                                "required.\nYou like puzzles.")
        self.assertEqual(facts.evidence, "Bachelor's degree in Computer Science required.")
        long = degree.classify("Bachelor's degree in " + "engineering, " * 40)
        self.assertLessEqual(len(long.evidence), degree.MAX_EVIDENCE)

    def test_equivalent_and_graduate_flags(self):
        facts = degree.classify("BS in CS or equivalent experience. MS preferred.")
        self.assertTrue(facts.equivalent_ok)
        self.assertEqual(facts.masters, "preferred")
        self.assertIsNone(degree.classify("No degree required.").masters)

    def test_certifications_case_and_abbreviation(self):
        facts = degree.classify("CompTIA Security+ or CISSP required; aws certified a plus. "
                                "PMP preferred. ccna helpful.")
        self.assertEqual(sorted(facts.certifications),
                         ["AWS certification", "CCNA", "CISSP", "CompTIA Security+", "PMP"])
        self.assertEqual(degree.classify("Write Python.").certifications, [])

    def test_lookalikes_are_not_certifications(self):
        # Found on the owner's tracker, 2026-10-08: "CSM" as Customer
        # Success Manager, and Six Sigma as a method, not a certificate.
        self.assertEqual(degree.certifications(
            "The Customer Success Manager (CSM) owns renewals. Knowledge of Lean, "
            "Six Sigma and root cause analysis."), [])
        self.assertEqual(degree.certifications("Six Sigma Green Belt; Certified ScrumMaster"),
                         ["Certified ScrumMaster", "Six Sigma"])

    def test_columns_and_the_degree_question(self):
        cols = degree.columns("Bachelor's degree in Physics required. CISSP preferred.")
        self.assertEqual(cols["degree_level"], "bachelors")
        self.assertEqual(cols["certs_named"], '["CISSP"]')
        self.assertTrue(degree.requires_degree("masters_preferred"))
        self.assertFalse(degree.requires_degree("bachelors_or_equiv"))
        self.assertIsNone(degree.requires_degree(None))

    def test_empty_text(self):
        for text in (None, ""):
            facts = degree.classify(text)
            self.assertEqual((facts.level, facts.certifications, facts.evidence),
                             ("none", [], ""))

    def test_no_model_and_no_network(self):
        from pathlib import Path
        source = Path(degree.__file__).read_text(encoding="utf-8")
        for word in ("llm", "httpx", "requests", "urllib"):
            self.assertNotIn(f"import {word}", source)
            self.assertNotIn(f"from .{word}", source)


if __name__ == "__main__":
    unittest.main()
