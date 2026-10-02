"""The role search box (Plan 7): by title, word starts, commas mean "or"."""

import unittest

from jsa.scoring import title_matches


class TestTitleMatches(unittest.TestCase):
    def yes(self, title, query):
        self.assertTrue(title_matches(title, query), f"{query!r} should match {title!r}")

    def no(self, title, query):
        self.assertFalse(title_matches(title, query), f"{query!r} should not match {title!r}")

    def test_a_word_matches_the_start_of_a_word(self):
        self.yes("Software Engineer", "engineer")
        self.yes("Engineering Manager", "engineer")
        self.yes("Data Analyst", "data")
        self.no("Metadata Specialist", "data")
        self.yes("AI Engineer", "ai")
        self.no("Maintenance Technician", "ai")
        self.no("Retail Associate", "ai")

    def test_words_in_a_term_all_have_to_match_in_any_order(self):
        self.yes("Technical Support Engineer", "support engineer")
        self.yes("Technical Support Engineer", "engineer support")
        self.no("Support Specialist", "support engineer")

    def test_commas_mean_or(self):
        q = "support engineer, solutions architect"
        self.yes("Support Engineer II", q)
        self.yes("Senior Solutions Architect", q)
        self.no("Account Executive", q)

    def test_case_does_not_matter(self):
        self.yes("forward deployed engineer", "Forward Deployed")
        self.yes("FORWARD DEPLOYED ENGINEER", "forward deployed")

    def test_regex_characters_are_literal(self):
        self.yes("C++ Developer", "c++")
        self.no("C Developer", "c++")
        self.yes("ASP.NET Engineer", ".net")
        self.no("Network Engineer", ".net")

    def test_an_empty_search_matches_everything(self):
        for q in ("", "   ", ",", None):
            self.yes("Anything At All", q)

    def test_a_missing_title_matches_only_an_empty_search(self):
        self.yes(None, "")
        self.no(None, "engineer")


if __name__ == "__main__":
    unittest.main()
