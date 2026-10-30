"""Today's US behavior, held still while it moves into a country pack.

`tests/country_baseline.py` runs fixed fictional inputs through place
parsing, the outside-the-US reject, scoring, pay reading and the resume
import's contact redaction; the answers are in
`tests/fixtures/us_baseline.json`. A difference here means the split changed
what the app does for a US user. Rewrite the fixture
(`python -m tests.country_baseline --write`) only for a change that is meant,
and say why in the commit.
"""

import json
import unittest

from tests import country_baseline


class TestUSBaseline(unittest.TestCase):
    maxDiff = None

    def test_us_results_are_unchanged(self):
        expected = json.loads(country_baseline.FIXTURE.read_text(encoding="utf-8"))
        got = country_baseline.results()
        self.assertEqual(sorted(got), sorted(expected))
        for section in expected:
            with self.subTest(section=section):
                self.assertEqual(got[section], expected[section])

    def test_the_resume_contact_lines_are_removed(self):
        # The part that guards privacy, said plainly: no contact value from the
        # fictional resume survives in the text that would reach a prompt.
        got = country_baseline.results()["split_identity"]
        text = "\n".join(got["redacted"])
        for value in ("Jordan Riverton", "123 Maple Street", "(555) 555-0142",
                      "jordan.riverton@example.com", "linkedin.com/in/jordanriverton",
                      country_baseline.ZIP, "555-555-0199"):
            self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
