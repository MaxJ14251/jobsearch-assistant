"""The tool must work for someone who does not live where its author does.

Written against the version that scored any California job 0.8 regardless of
where the user lived, and against an example profile that shipped with the
author's own cities. Both made the tool quietly wrong for 49 states.
"""

import unittest

import yaml

from jsa.config import Preferences
from jsa.scoring import location_score, score_job

EXAMPLE = "jsa/resources/master_profile.example.yaml"


def operator_places() -> tuple[set[str], set[str]]:
    """(town and postal-code strings, region names) from the LIVE profile.

    Read at run time, never written down: these tests used to list the
    author's own towns and ZIP code as a forbidden list, which is checking
    for a leak by writing the leak (ADR 0008). Skips on a fresh clone, and
    when the profile IS the example (as in CI), where the comparison would
    prove nothing.
    """
    from jsa.config import ROOT

    live, example = ROOT / "profile" / "master_profile.yaml", ROOT / EXAMPLE
    if not live.exists():
        raise unittest.SkipTest("no live profile on this machine")
    text = live.read_text(encoding="utf-8")
    if text == example.read_text(encoding="utf-8"):
        raise unittest.SkipTest("the live profile is the example")
    data = yaml.safe_load(text) or {}
    prefs = data.get("job_search_preferences") or {}
    location = (data.get("identity") or {}).get("location") or {}
    raw = list(prefs.get("locations") or [])
    raw += [town for towns in (prefs.get("regions") or {}).values() for town in towns or []]
    raw += [prefs.get("home_location"), location.get("city"), location.get("postal_code")]
    towns = set()
    for value in raw:
        if not isinstance(value, str) or "remote" in value.lower():
            continue
        head = value.split(",")[0].strip()
        if len(head) >= 4:
            towns.add(head)
    return towns, set((prefs.get("regions") or {}).keys())


def texan() -> Preferences:
    return Preferences.from_profile({"job_search_preferences": {
        "target_titles": ["Software Engineer"],
        "locations": ["Remote (US)", "Austin, TX"],
        "max_years_experience": 3,
        "regions": {"atx": ["Austin"]},
        "compensation_floor_usd": "no_floor",
    }, "ats_keywords": {"have": ["Python"]}})


class TestLocationIsNotCalifornian(unittest.TestCase):
    def setUp(self):
        self.prefs = texan()

    def score(self, location, remote="onsite"):
        return location_score(location, remote, self.prefs)

    def test_your_own_city_scores_full(self):
        value, reason = self.score("Austin, TX")
        self.assertEqual(value, 1.0)
        self.assertIn("Austin", reason)

    def test_california_is_not_special(self):
        """The bug: "Los Angeles, CA" scored 0.8 for a user in Texas."""
        for location in ("Los Angeles, CA", "San Francisco, California",
                         "San Diego, CA"):
            with self.subTest(location=location):
                value, reason = self.score(location)
                self.assertEqual(value, 0.0, reason)

    def test_your_own_state_scores_between(self):
        value, reason = self.score("Dallas, TX")
        self.assertGreater(value, 0.0)
        self.assertLess(value, 1.0)
        self.assertIn("Texas", reason)

    def test_the_state_rule_works_for_any_state(self):
        for pref, elsewhere in (("Columbus, OH", "Cleveland, OH"),
                                ("Boise, ID", "Nampa, ID"),
                                ("Burlington, VT", "Montpelier, VT")):
            with self.subTest(pref=pref):
                prefs = Preferences.from_profile({"job_search_preferences": {
                    "target_titles": ["Engineer"], "locations": [pref],
                    "max_years_experience": 3, "regions": {},
                    "compensation_floor_usd": "no_floor"}})
                value, _ = location_score(elsewhere, "onsite", prefs)
                self.assertGreater(value, 0.0, f"{elsewhere} near {pref}")

    def test_remote_and_unstated_are_unchanged(self):
        self.assertEqual(self.score("Remote - US", "remote")[0], 1.0)
        self.assertEqual(self.score("", "onsite")[0], 0.4)

    def test_outside_the_us_still_scores_zero(self):
        self.assertEqual(self.score("Berlin, Germany")[0], 0.0)

    def test_a_whole_job_ranks_by_the_users_own_place(self):
        job = {"title": "Software Engineer", "description": "Python work.",
               "remote": "onsite"}
        home, _ = score_job(dict(job, location="Austin, TX"), self.prefs)
        away, _ = score_job(dict(job, location="Los Angeles, CA"), self.prefs)
        self.assertGreater(home, away)

    def test_no_city_is_hardcoded_in_the_scorer(self):
        from jsa.config import ROOT
        source = (ROOT / "jsa" / "scoring.py").read_text(encoding="utf-8")
        body = source[source.index("def location_score"):
                      source.index("def keyword_score")]
        # Code only: the comment explaining the old California bonus should
        # stay, so the next reader knows why the rule looks like this.
        code = "\n".join(line.split("#")[0]
                         for line in body.splitlines()).lower()
        for city in ("los angeles", "san diego", "santa monica", "california"):
            self.assertNotIn(city, code)


class TestTheExampleProfileBelongsToNobody(unittest.TestCase):
    """It is what a stranger copies. It must not describe the author."""

    @classmethod
    def setUpClass(cls):
        from jsa.config import ROOT
        cls.text = (ROOT / EXAMPLE).read_text(encoding="utf-8")
        cls.data = yaml.safe_load(cls.text)

    def test_no_real_place_from_the_authors_search(self):
        towns, _ = operator_places()
        self.assertTrue(towns, "the live profile names no place to check against")
        for place in towns:
            self.assertNotIn(place, self.text, "a place from the live profile")

    def test_regions_are_not_named_after_the_authors_regions(self):
        _, names = operator_places()
        regions = self.data["job_search_preferences"]["regions"]
        self.assertFalse(names & set(regions), "a region name from the live profile")

    def test_it_still_shows_how_the_fields_work(self):
        prefs = self.data["job_search_preferences"]
        self.assertTrue(prefs["locations"], "a stranger needs an example")
        self.assertTrue(any("remote" in str(x).lower() for x in prefs["locations"]))
        self.assertTrue(prefs["regions"], "regions need an example too")
        for name, places in prefs["regions"].items():
            self.assertTrue(places, f"region {name} has no places")

    def test_it_still_parses_as_preferences(self):
        prefs = Preferences.from_profile(self.data)
        self.assertTrue(prefs.target_titles)


if __name__ == "__main__":
    unittest.main()
