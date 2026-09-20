"""A posting that names a city and no state is still somewhere.

Measured on the author's tracker before this was fixed: 143 of 514 postings
(27%) named no state, across only 16 distinct strings, and 117 of those were
one employer's feed writing "San Francisco".

Every one of them scored 0.0 -- "outside your list" -- including for a reader
whose own profile said "San Francisco, CA". The same score as a job in another
state, for a job in their own city.

The cause was a rule that was right for a different problem. `_matches_city`
required the posting to name the state as well as the city, because "York, PA"
otherwise matches inside "New York, NY". That is a real hazard, but only when
the posting names a state. When it names none, there is no ambiguity to
resolve, and demanding one throws the posting away.
"""

import unittest

from jsa.scoring import _matches_city, in_state, is_non_us, location_score


class Prefs:
    """Only the field location_score reads."""

    def __init__(self, *locations):
        self.locations = list(locations)


class TestYourOwnCityWithNoStateAttached(unittest.TestCase):
    """Watch this fail before the fix: every case returns 0.0."""

    def test_the_city_matches_when_the_posting_names_no_state(self):
        self.assertTrue(_matches_city("san francisco", "San Francisco, CA"))

    def test_it_scores_as_your_city(self):
        score, why = location_score("San Francisco", "",
                                    Prefs("San Francisco, CA"))
        self.assertEqual(score, 1.0, why)

    def test_a_decorated_version_of_the_same_city(self):
        for stated in ("Hybrid - San Francisco", "San Francisco Bay Area"):
            with self.subTest(location=stated):
                score, why = location_score(stated, "",
                                            Prefs("San Francisco, CA"))
                self.assertEqual(score, 1.0, why)


class TestTheAmbiguityItWasGuardingAgainst(unittest.TestCase):
    """The relaxation must not reopen the bug the rule was written for."""

    def test_york_pa_does_not_match_new_york_ny(self):
        self.assertFalse(_matches_city("new york, ny", "York, PA"))

    def test_a_stated_state_still_has_to_agree(self):
        score, why = location_score("San Francisco, CA", "",
                                    Prefs("San Antonio, TX"))
        self.assertEqual(score, 0.0, why)

    def test_a_city_in_the_wrong_state_is_not_your_city(self):
        self.assertFalse(_matches_city("columbus, ga", "Columbus, OH"))

    def test_a_bare_city_that_is_not_yours_still_scores_nothing(self):
        score, why = location_score("Seattle", "", Prefs("San Francisco, CA"))
        self.assertEqual(score, 0.0, why)


class TestInStateCannotBeCalledWronglyInSilence(unittest.TestCase):
    """`preferred_states` emits 'ca'; `in_state` only ever accepted 'ca'.

    Passing "CA" returned False with nothing saying why. One caller does it
    right, so nothing was broken -- and that is exactly the shape of a
    function that breaks its next caller instead.
    """

    def test_either_case_works(self):
        for state in ("ca", "CA", "Ca"):
            with self.subTest(state=state):
                self.assertTrue(in_state("Hawthorne, CA", state))

    def test_the_full_name_works_in_either_case(self):
        self.assertTrue(in_state("Los Angeles, California", "CA"))

    def test_a_wrong_state_is_still_false(self):
        self.assertFalse(in_state("Hawthorne, CA", "WA"))

    def test_berlin_de_is_not_delaware(self):
        """The guard is upstream, and belongs there.

        `in_state("Berlin, DE", "de")` is True on its own, and always was --
        ", DE" is exactly what the rule looks for. What stops Berlin counting
        as Delaware is `location_score` calling `is_non_us` first and
        returning before the state check runs. Asserting it on `in_state`
        would be testing a promise this function does not make.
        """
        self.assertTrue(is_non_us("Berlin, DE"))
        score, why = location_score("Berlin, DE", "", Prefs("Dover, DE"))
        self.assertEqual(score, 0.0, why)
        self.assertIn("outside the US", why)


class TestNotEverywhereIsInTheUS(unittest.TestCase):
    """Found while counting: three shapes the non-US filter let through.

    They were scored as US locations, so a reader's own-state rule had to
    decide about Cambridge, England.
    """

    def test_shapes_the_filter_missed(self):
        for location in ("England - Cambridge", "Middle East",
                         "São Paulo", "London"):
            with self.subTest(location=location):
                self.assertTrue(is_non_us(location))

    def test_it_still_lets_the_us_through(self):
        for location in ("San Francisco", "United States - Boston Office",
                         "Hawthorne, CA", "New York, NY", "Remote (US)",
                         "USA CT - Canaan"):
            with self.subTest(location=location):
                self.assertFalse(is_non_us(location))


if __name__ == "__main__":
    unittest.main()
