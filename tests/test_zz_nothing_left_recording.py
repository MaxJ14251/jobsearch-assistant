"""Runs last (by name): no earlier test may leave the model-call ledger on.

`jsa serve` and `cli.main` point `llm.recorder` at the real tracker. A test
that runs either must put it back, or a later test's fake model call is
counted in the owner's ledger (six such rows were found on 2026-10-24).
"""

import unittest

from jsa import llm


class TestNothingLeftRecording(unittest.TestCase):
    def test_no_test_left_the_ledger_pointed_at_a_tracker(self):
        self.assertIsNone(llm.recorder,
                          "a test left llm.recorder set: wrap serve()/main() calls in "
                          "mock.patch.object(llm, 'recorder', None)")


if __name__ == "__main__":
    unittest.main()
