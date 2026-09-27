"""Placing a posting on the map, from shipped data, without guessing.

The location strings below are real ones from the author's tracker, sampled
2026-09-26 across 1,228 open postings. The parser was written against them
rather than against what a location field ought to look like.

The rule that matters: a posting placed in the WRONG town is worse than one
left unplaced, because the operator cannot see that it happened. Everything
here that declines to place something is doing its job.
"""

import unittest

from jsa import places


class TestTheShippedData(unittest.TestCase):
    def test_it_is_here(self):
        self.assertTrue(places.data_is_present(),
                        "data/us_places.csv.gz and us_zips.csv.gz must ship")

    def test_a_zip_resolves(self):
        home = places.from_zip("[postal code]")           # Example Town, CA
        self.assertIsNotNone(home)
        self.assertAlmostEqual(home.lat, 34.0, delta=0.3)
        self.assertAlmostEqual(home.lon, -118.5, delta=0.3)

    def test_an_unknown_zip_is_none_not_a_guess(self):
        self.assertIsNone(places.from_zip("00000"))
        self.assertIsNone(places.from_zip("abcde"))

    def test_nothing_here_touches_the_network(self):
        """A geocoding API would send the operator's home ZIP to a stranger."""
        import inspect

        source = inspect.getsource(places)
        for term in ("httpx", "requests", "urllib", "socket", "http://", "https://"):
            self.assertNotIn(term, source, f"{term} in the placing path")


class TestDistance(unittest.TestCase):
    def test_a_known_pair(self):
        """Example Town to Example Town is about 5 miles."""
        a, b = places.origin("[postal code]"), places.origin("Example Town, CA")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertLess(places.miles(a, b), 9)

    def test_a_long_one(self):
        """Los Angeles to New York is about 2,450 miles."""
        a = places.resolve("Los Angeles", "CA")
        b = places.resolve("New York", "NY")
        self.assertAlmostEqual(places.miles(a, b), 2450, delta=60)

    def test_zero_to_itself(self):
        a = places.resolve("Boise", "ID")
        self.assertAlmostEqual(places.miles(a, a), 0, places=6)

    def test_a_posting_scores_on_its_nearest_location(self):
        home = places.resolve("Boise", "ID")
        near = places.nearest(home, "New York, NY; Boise, ID; Miami, FL")
        self.assertLess(near, 5)

    def test_a_posting_with_no_place_has_no_distance(self):
        home = places.resolve("Boise", "ID")
        self.assertIsNone(places.nearest(home, "Remote (US)"))
        self.assertIsNone(places.nearest(home, ""))


class TestRealLocationStrings(unittest.TestCase):
    """Every shape counted in the 2026-09-26 sample."""

    def placed(self, text) -> list[str]:
        return [str(p) for p in places.parse(text).places]

    def test_city_and_state_code(self):
        self.assertEqual(self.placed("Los Angeles, CA"), ["Los Angeles, CA"])

    def test_city_and_state_name(self):
        self.assertEqual(self.placed("Bellevue, Washington"), ["Bellevue, WA"])

    def test_several_places_in_one_string(self):
        self.assertEqual(
            self.placed("San Francisco, CA | New York City, NY"),
            ["San Francisco, CA", "New York, NY"])

    def test_a_country_wrapper_is_stripped(self):
        self.assertEqual(self.placed("US - Example Town, United States"),
                         ["Example Town, CA"])
        self.assertEqual(self.placed("Long Beach, California, United States"),
                         ["Long Beach, CA"])

    def test_an_alias_the_census_does_not_use(self):
        self.assertEqual(self.placed("NYC (SoHo)"), ["New York, NY"])
        self.assertEqual(self.placed("Washington DC"), ["Washington, DC"])

    def test_a_unique_bare_city(self):
        self.assertEqual(self.placed("Seattle"), ["Seattle, WA"])

    def test_an_ambiguous_bare_city_is_left_alone(self):
        """Bellevue is a real city in six states. Picking one would put a job
        a thousand miles from where the operator thinks it is."""
        parsed = places.parse("Bellevue")
        self.assertEqual(parsed.places, [])
        self.assertEqual(parsed.unplaced, ["Bellevue"])

    def test_remote_is_remote_and_not_a_place(self):
        for text in ("Remote (US)", "US Remote; US FL Remote", "Remote - USA",
                     "United States - Remote", "Flexible / Remote"):
            with self.subTest(text=text):
                parsed = places.parse(text)
                self.assertTrue(parsed.remote, text)
                self.assertEqual(parsed.places, [], text)

    def test_remote_alongside_a_real_office(self):
        parsed = places.parse("Example Town, CA; US Remote; Chicago, IL")
        self.assertTrue(parsed.remote)
        self.assertEqual([str(p) for p in parsed.places],
                         ["Example Town, CA", "Chicago, IL"])

    def test_a_street_address_is_not_a_place(self):
        """The city in front of it still is, when the city is unambiguous."""
        parsed = places.parse("Bellevue - 110 110th Ave NE; Los Angeles, California")
        self.assertEqual([str(p) for p in parsed.places], ["Los Angeles, CA"])
        self.assertIn("Bellevue - 110 110th Ave NE", parsed.unplaced)

    def test_an_arrangement_in_front_of_the_place(self):
        """"Hybrid - San Francisco": 25 postings start with an arrangement."""
        self.assertEqual(self.placed("Hybrid - San Francisco"),
                         ["San Francisco, CA"])
        self.assertEqual(self.placed("Onsite - Austin, TX"), ["Austin, TX"])

    def test_a_state_named_between_dashes(self):
        """"US - California - San Diego", 13 postings."""
        self.assertEqual(self.placed("US - California - San Diego"),
                         ["San Diego, CA"])

    def test_a_dash_without_a_state_is_still_refused(self):
        """The guard on the rule above. Berlin is a town in New Hampshire and
        Cambridge is one in Massachusetts; neither posting is American."""
        for text in ("Berlin - Mitte", "England - Cambridge"):
            with self.subTest(text=text):
                self.assertEqual(self.placed(text), [])

    def test_a_list_of_cities_with_no_state(self):
        """"San Francisco, New York City, Austin" is three cities, not a city
        and a state -- but "Los Angeles, CA" must not be read that way."""
        self.assertEqual(
            self.placed("Hybrid - San Francisco, New York City, Austin"),
            ["San Francisco, CA"])
        self.assertEqual(self.placed("Los Angeles, CA"), ["Los Angeles, CA"])

    def test_a_country_name_alone_places_nothing(self):
        self.assertEqual(self.placed("United States"), [])

    def test_nothing_outside_the_us_is_placed(self):
        """The one that nearly shipped: 'England - Cambridge' is a UK posting,
        and England is also a town in Arkansas."""
        for text in ("England - Cambridge", "São Paulo", "Middle East",
                     "London, United Kingdom", "Toronto, ON",
                     "Bangalore, India"):
            with self.subTest(text=text):
                self.assertEqual(self.placed(text), [], text)

    def test_an_empty_field_is_not_an_error(self):
        parsed = places.parse("")
        self.assertEqual((parsed.places, parsed.remote, parsed.unplaced),
                         ([], False, []))


class TestTheOperatorsOrigin(unittest.TestCase):
    def test_a_zip(self):
        self.assertIsNotNone(places.origin("83702"))

    def test_a_city_and_state(self):
        """The Census name is kept as written -- Idaho's capital is filed as
        "Boise City" -- so this checks the place, not the spelling."""
        for text in ("Boise, ID", "Boise, Idaho"):
            with self.subTest(text=text):
                home = places.origin(text)
                self.assertIsNotNone(home, text)
                self.assertEqual(home.state, "ID")
                self.assertTrue(home.name.startswith("Boise"), home.name)

    def test_a_state_name_alone_is_not_a_town(self):
        """Pennsylvania has a borough called Oklahoma. A posting that says
        "Oklahoma" means the state, and placing it in PA is 1,100 miles wrong
        in a way nobody would notice."""
        for text in ("Oklahoma", "Washington", "New York"):
            with self.subTest(text=text):
                self.assertIsNone(places.resolve(text))

    def test_nonsense_is_none(self):
        for text in ("", "   ", "not a place at all", "Atlantis, ZZ"):
            with self.subTest(text=text):
                self.assertIsNone(places.origin(text))


class TestDistanceDecidesTheScore(unittest.TestCase):
    """What the map is for: ranking by how far away a job actually is.

    Before this, a posting scored on whether its text matched a city the
    operator had written down by hand. Somebody in Los Angeles had to list
    Example Town, Example Town, Example Town and every other suburb, or those jobs
    scored zero.
    """

    def prefs(self, home="Boise, ID", radius=40, locations=None):
        from jsa.config import Preferences

        return Preferences(
            target_titles=["Engineer"],
            locations=locations if locations is not None else ["Remote (US)"],
            home_location=home, radius_miles=radius)

    def score(self, location, prefs=None):
        from jsa.scoring import location_score

        return location_score(location, "onsite", prefs or self.prefs())

    def test_near_outranks_far(self):
        near, _ = self.score("Meridian, ID")        # ~10 miles from Boise
        far, _ = self.score("Twin Falls, ID")       # ~110 miles
        self.assertEqual(near, 1.0)
        self.assertLess(far, near)

    def test_a_town_nobody_listed_still_scores(self):
        """The whole point. Meridian is not in `locations`."""
        value, why = self.score("Meridian, ID")
        self.assertEqual(value, 1.0)
        self.assertIn("miles away", why)
        self.assertIn("radius", why)

    def test_a_city_you_named_wins_whatever_the_mileage(self):
        """An explicit choice outranks arithmetic about it."""
        prefs = self.prefs(locations=["Remote (US)", "New York, NY"])
        value, why = self.score("New York, NY", prefs)
        self.assertEqual(value, 1.0)
        self.assertIn("New York", why)

    def test_the_radius_is_the_operators(self):
        tight, loose = self.prefs(radius=10), self.prefs(radius=100)
        self.assertLess(self.score("Nampa, ID", tight)[0],
                        self.score("Nampa, ID", loose)[0])

    def test_far_away_and_never_listed_still_scores_nothing(self):
        """Distance must not become a floor under jobs across the country."""
        value, _ = self.score("Miami, FL")
        self.assertEqual(value, 0.0)

    def test_a_posting_that_cannot_be_placed_is_said_so(self):
        value, why = self.score("Starbase, TX")
        self.assertEqual(value, 0.2)
        self.assertIn("could not be placed", why)

    def test_outside_the_us_is_still_rejected_before_any_distance(self):
        for text in ("London, United Kingdom", "Toronto, ON", "São Paulo"):
            with self.subTest(text=text):
                value, why = self.score(text)
                self.assertEqual(value, 0.0)
                self.assertIn("outside the US", why)

    def test_remote_is_untouched_by_distance(self):
        from jsa.scoring import location_score

        value, why = location_score("Remote (US)", "remote", self.prefs())
        self.assertEqual(value, 1.0)
        self.assertNotIn("miles", why)

    def test_the_home_falls_back_to_the_first_real_location(self):
        """An existing profile gets distances without being edited."""
        prefs = self.prefs(home="", locations=["Remote (US)", "Boise, ID"])
        self.assertIsNotNone(prefs.home())
        self.assertEqual(prefs.home().state, "ID")

    def test_a_profile_with_nowhere_placeable_still_scores(self):
        prefs = self.prefs(home="", locations=["Remote (US)"])
        self.assertIsNone(prefs.home())
        self.assertEqual(self.score("Meridian, ID", prefs)[0], 0.0)

    def test_a_bad_radius_is_refused_by_name(self):
        from jsa.config import ConfigError, Preferences

        with self.assertRaises(ConfigError) as ctx:
            Preferences.from_profile({"job_search_preferences": {
                "target_titles": ["Engineer"], "locations": ["Remote (US)"],
                "radius_miles": "as far as I can drive"}})
        self.assertIn("miles", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
