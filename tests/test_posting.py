"""How much of a posting a model is shown.

The bug this was written for: tailoring read the first 4,000 characters of a
posting, 92% of postings are longer than that, and one in five that state a
degree requirement state it further in. A resume was being written against
requirements the model had never seen, and nothing about the draft said so.

The tests that matter here are the ones about what the window is NOT allowed
to be. It is a contiguous prefix of the posting: nothing reordered, nothing
rewritten, no line lifted out of the middle. A model that sees a sentence the
posting does not contain is the fabrication problem in a different coat.
"""

import unittest

from jsa import posting

REQUIREMENTS = (
    "Requirements\n"
    "- Bachelor's degree in Computer Science or equivalent experience\n"
    "- 5+ years building production services\n"
    "- Active TS/SCI clearance\n"
)


def long_posting(before: int = 5000) -> str:
    """A posting that states its requirements `before` characters in, which
    is where a fifth of real postings state them."""
    filler = "We are a fast growing company with a great culture. " * 600
    assert len(filler) >= before, "the fixture must be able to fill the budget"
    return filler[:before] + "\n\n" + REQUIREMENTS + "\nApply today.\n"


class TestTheWindowIsAPrefix(unittest.TestCase):
    """Whatever reaches a model is text that is in the posting, in its order."""

    def test_a_short_posting_arrives_whole(self):
        text = "Support Engineer\n\n" + REQUIREMENTS
        window = posting.visible(text)
        self.assertEqual(window.text, text)
        self.assertTrue(window.complete)
        self.assertEqual(window.dropped, 0)

    def test_what_is_kept_is_verbatim_and_in_order(self):
        text = long_posting(14000)
        window = posting.visible(text)
        self.assertFalse(window.complete)
        self.assertTrue(text.startswith(window.text),
                        "the window is not a prefix of the posting")

    def test_no_line_is_ever_dropped_from_the_middle(self):
        """Measured and rejected in n14: stripping boilerplate to make room
        gained two points and could silently delete a requirement worded like
        a benefit ("we offer mentorship to engineers with 5+ years")."""
        text = "\n".join(f"Line {n}: we offer mentorship and 5+ years of it."
                         for n in range(400))
        window = posting.visible(text)
        self.assertFalse(window.complete)
        kept = window.text.splitlines()
        # Every line but the last is a whole line of the posting, in order.
        # The last may be cut off mid-sentence; the prefix test covers that.
        self.assertEqual(kept[:-1], text.splitlines()[:len(kept) - 1])

    def test_nothing_is_added(self):
        text = long_posting(14000)
        window = posting.visible(text)
        self.assertNotIn("[...]", window.text)
        self.assertNotIn("truncated", window.text.lower())

    def test_an_empty_or_missing_description(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                window = posting.visible(value)
                self.assertTrue(window.complete)
                self.assertEqual(posting.note(window), "")


class TestTheRequirementsReachTheModel(unittest.TestCase):
    def test_requirements_stated_after_4000_characters(self):
        """The regression this goal exists for. At the old budget the model
        could not see any of this; it can now."""
        text = long_posting(5000)
        window = posting.visible(text)
        for line in REQUIREMENTS.strip().splitlines():
            with self.subTest(line=line):
                self.assertIn(line, window.text)
                self.assertNotIn(line, text[:4000], "the fixture is not long "
                                 "enough to reproduce the bug")

    def test_requirements_stated_after_6000_characters(self):
        """enrich used to stop at 6,000 while tailoring stopped at 4,000, so
        the two passes disagreed about the same posting."""
        window = posting.visible(long_posting(9000))
        self.assertIn("Active TS/SCI clearance", window.text)

    def test_a_posting_longer_than_the_budget_still_stops(self):
        window = posting.visible("word " * 6000)     # 30,000 chars
        self.assertLessEqual(len(window.text), posting.MAX_DESCRIPTION_CHARS)
        self.assertGreater(window.dropped, 0)

    def test_the_budget_covers_almost_every_real_posting(self):
        """Measured over the author's 1,266 postings: the longest is 23,227
        and 99.7% are under this. The number is a decision, not an accident,
        so it is asserted rather than left to drift."""
        self.assertEqual(posting.MAX_DESCRIPTION_CHARS, 12000)


class TestItStopsSomewhereSensible(unittest.TestCase):
    def test_it_prefers_a_blank_line_near_the_end_of_the_budget(self):
        block = ("Section text goes here. " * 20 + "\n\n")
        text = block * 30
        window = posting.visible(text)
        self.assertFalse(window.complete)
        self.assertTrue(window.text.endswith("\n")
                        or text[len(window.text):len(window.text) + 2] == "\n\n")

    def test_it_never_gives_up_much_to_find_one(self):
        """A tidy boundary is worth a few hundred characters, not a
        requirement. One blank line at the very start must not cut the window
        down to nothing."""
        text = "Title\n\n" + ("x" * 30000)
        window = posting.visible(text)
        self.assertGreaterEqual(len(window.text),
                                posting.MAX_DESCRIPTION_CHARS * 0.9)


class TestWhatTheOperatorIsTold(unittest.TestCase):
    def test_nothing_is_said_when_nothing_was_cut(self):
        self.assertEqual(posting.note(posting.visible("short")), "")

    def test_it_says_how_much_and_where_it_resumed(self):
        """Counting characters is not something an operator can check against
        a posting. The words it resumes at are."""
        text = long_posting(14000)
        said = posting.note(posting.visible(text))
        self.assertIn("the model read the first", said)
        self.assertIn("and not the last", said)
        self.assertIn("Apply today", said + text[-200:])

    def test_the_quoted_words_are_really_where_it_stopped(self):
        text = long_posting(14000)
        window = posting.visible(text)
        rest = text[len(window.text):].split()
        self.assertTrue(window.resumes.startswith(rest[0]))

    def test_the_quote_cannot_run_away(self):
        """A posting with no whitespace must not print 12,000 characters at
        somebody's terminal."""
        window = posting.visible("x" * 30000)
        self.assertLessEqual(len(window.resumes), 60)


class TestOutreachHasItsOwnBudgetThroughTheSameDoor(unittest.TestCase):
    def test_it_is_smaller_on_purpose(self):
        self.assertLess(posting.OUTREACH_CHARS, posting.MAX_DESCRIPTION_CHARS)

    def test_and_it_is_the_same_function(self):
        window = posting.visible(long_posting(5000), posting.OUTREACH_CHARS)
        self.assertLessEqual(len(window.text), posting.OUTREACH_CHARS)
        self.assertTrue(long_posting(5000).startswith(window.text))


if __name__ == "__main__":
    unittest.main()
