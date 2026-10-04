"""Compensation floor and work authorization.

See docs/decisions/0001-compensation-and-work-authorization.md. The decision
that shapes these tests: scoring gets NO salary term, not even a disabled one,
because every jobs.salary_min is null and a floor wired in today would be a
no-op that looked like a feature.

So the tests here are about shape validation, drift, and a tripwire — not
about ranking, which this change deliberately does not touch.
"""

import sqlite3
import unittest
from pathlib import Path

import yaml

from jsa.config import (
    DB_PATH,
    NO_FLOOR,
    ROOT,
    CompFloor,
    ConfigError,
    Preferences,
    load_profile,
    parse_compensation_floor,
    parse_tristate,
)

EXAMPLE_PATH = ROOT / "profile" / "master_profile.example.yaml"
REAL_PATH = ROOT / "profile" / "master_profile.yaml"
REGIONS = {"la": [], "sd": [], "pa": []}


def _prefs(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("job_search_preferences") or {}


def _has_rows() -> bool:
    if not DB_PATH.exists():
        return False
    con = sqlite3.connect(DB_PATH)
    try:
        return con.execute("select count(*) from jobs").fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        con.close()


class TestCompFloorShape(unittest.TestCase):
    """Three states must stay distinguishable: undecided, none, a floor."""

    def test_null_is_undecided_not_no_floor(self):
        """The whole point of the field. Conflating these is the old bug."""
        self.assertIsNone(parse_compensation_floor(None, REGIONS))

    def test_no_floor_is_explicit(self):
        floor = parse_compensation_floor(NO_FLOOR, REGIONS)
        self.assertTrue(floor.no_floor)
        self.assertIsNone(floor.for_region("sd"))

    def test_scalar(self):
        self.assertEqual(parse_compensation_floor(95000, REGIONS).for_region(), 95000)

    def test_per_region_map(self):
        floor = parse_compensation_floor(
            {"default": 95000, "sd": 110000}, REGIONS)
        self.assertEqual(floor.for_region("sd"), 110000)
        self.assertEqual(floor.for_region("pa"), 95000, "falls back to default")
        self.assertEqual(floor.for_region(None), 95000)

    def test_malformed_raises_configerror_not_keyerror(self):
        for value in ("lots", -5, {"xx": 90000}, {}, [1, 2], {"sd": "high"}, 3.5):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    parse_compensation_floor(value, REGIONS)

    def test_true_is_not_a_floor(self):
        """isinstance(True, int) is True in Python. A bool must not read as 1."""
        with self.assertRaises(ConfigError):
            parse_compensation_floor(True, REGIONS)

    def test_tristate_rejects_non_boolean(self):
        self.assertIsNone(parse_tristate(None, "willing_to_relocate"))
        self.assertIs(parse_tristate(False, "willing_to_relocate"), False)
        with self.assertRaises(ConfigError):
            parse_tristate("yes", "willing_to_relocate")


class TestExampleProfileStillWorks(unittest.TestCase):
    """Hard constraint: fresh_clone_check copies the example and runs discover.

    An earlier draft of the ADR had from_profile raise on null. That would have
    broken every fresh clone, because the example ships these fields unset so a
    stranger can see what to fill in.
    """

    def test_example_loads_with_fields_unset(self):
        prefs = Preferences.from_profile(load_profile(EXAMPLE_PATH))
        self.assertIsNone(prefs.compensation_floor)
        self.assertIsNone(prefs.work_authorization)

    def test_example_declares_all_four_fields(self):
        keys = _prefs(EXAMPLE_PATH)
        for name in ("compensation_floor_usd", "work_authorization",
                     "needs_visa_sponsorship", "willing_to_relocate"):
            self.assertIn(name, keys, f"{name} missing from the example")


class TestProfileDrift(unittest.TestCase):
    """Parse both files and assert they agree, rather than trusting prose.

    Follows tests/test_enrich.py, which parses db/schema.sql and asserts the
    seniority vocabulary matches the CHECK constraint — added after those two
    drifted and killed an enrichment run mid-pass on an IntegrityError.
    """

    @unittest.skipUnless(REAL_PATH.exists(), "no personal profile on this machine")
    def test_key_sets_match(self):
        real, example = set(_prefs(REAL_PATH)), set(_prefs(EXAMPLE_PATH))
        self.assertEqual(
            real, example,
            "job_search_preferences drifted:\n"
            f"  only in master_profile.yaml:         {sorted(real - example)}\n"
            f"  only in master_profile.example.yaml: {sorted(example - real)}",
        )


class TestPayIsWiredDeliberately(unittest.TestCase):
    """Replaces two tests from ADR 0001, both retired by ADR 0006.

    TestNotWiredIntoScoring asserted scoring.py never mentioned salary.
    TestSalaryTripwire asserted jobs.salary_min was entirely NULL, and was
    written to fail exactly once, in front of someone who had to decide what
    pay should do. It failed on 2026-09-17 when extraction landed; that
    decision is ADR 0006.

    What must now stay true is what the ADR decided, asserted here and in
    tests/test_salary.py -- including TestSalaryStaysExtracted, which fails if
    salary silently STOPS being extracted.
    """

    def test_the_decision_is_recorded(self):
        adr = list((ROOT / "docs" / "decisions").glob("0006-*.md"))
        self.assertEqual(len(adr), 1, "ADR 0006 is missing")
        text = adr[0].read_text(encoding="utf-8")
        self.assertIn("MIDPOINT", text.upper())

    def test_the_floor_never_reaches_a_prompt(self):
        """ADR 0001 decision 6 still holds now that scoring reads the floor:
        scoring makes no model call, and nothing that builds a prompt reads
        the floor."""
        for module in ("tailor.py", "prep.py", "outreach.py", "enrich.py", "salary.py",
                       "letter.py", "answers.py", "facts.py"):
            source = (ROOT / "jsa" / module).read_text(encoding="utf-8")
            self.assertNotIn("compensation_floor", source, module)
        scoring_src = (ROOT / "jsa" / "scoring.py").read_text(encoding="utf-8")
        self.assertNotIn("llm", scoring_src)


if __name__ == "__main__":
    unittest.main()
