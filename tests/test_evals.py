"""Drafting evals in CI (plan 25).

Every fictional posting in tests/evals/cases/ is run through the model-free
half of drafting (bullets, summary, role kind, skill order, keyword report,
resume report), and every written expectation is checked. One failure lists
every miss: case, check, expected, got. `tools/eval_report.py` prints the
scorecard. Change an expectation only with a reason, in its `why:` and in
the commit message.
"""

import time
import unittest

from tests.evals import harness
from tests.test_example_profile import REAL, bullets, has_a_personal_profile, load, summaries


class TestDraftingEvals(unittest.TestCase):
    def test_every_expectation_holds(self):
        started = time.perf_counter()
        results = harness.run_all()
        elapsed = time.perf_counter() - started
        misses = [str(m) for r in results for m in r.misses]
        self.assertEqual(misses, [], "\n" + "\n".join(misses))
        self.assertGreaterEqual(sum(len(r.checks) for r in results), 60)
        self.assertLess(elapsed, 5.0, "the evals should stay fast enough for every run")

    def test_every_case_says_why(self):
        for case in harness.cases():
            expect = case.get("expect") or {}
            self.assertTrue(str(expect.get("why") or "").strip(), case["name"])
            self.assertGreater(len(expect), 1, f"{case['name']} checks nothing")

    def test_the_corpus_is_big_enough_to_weigh_tags(self):
        from jsa import tailor
        prof = harness.profile()
        weights = tailor.tag_weights(None, tailor.vocabulary(prof), corpus=harness.corpus())
        self.assertTrue(weights, "under 20 documents gives every tag the same weight")
        self.assertLess(weights["systems"], weights["commissioning"])


class TestTheProfileIsFiction(unittest.TestCase):
    @unittest.skipUnless(has_a_personal_profile(), "no personal profile to compare against")
    def test_it_shares_nothing_with_a_personal_profile(self):
        real, fixture = load(REAL), harness.profile()
        self.assertEqual(set(bullets(fixture)) & set(bullets(real)), set())
        self.assertEqual(set(summaries(fixture)) & set(summaries(real)), set())
        names = {e.get("company") for e in real.get("experience") or []} | \
                {p.get("name") for p in real.get("projects") or []}
        mine = {e.get("company") for e in fixture.get("experience") or []} | \
               {p.get("name") for p in fixture.get("projects") or []}
        self.assertEqual((names & mine) - {None}, set())


if __name__ == "__main__":
    unittest.main()
