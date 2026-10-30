"""The country packs (country pack split, phase 2).

A profile has one home country; the US is the only pack so far. These hold
the pack's shape and the promise that there is now one table of US states,
read by places, scoring and the map-data builder alike.
"""

import importlib.util
import unittest
from pathlib import Path

from jsa import countries, places, scoring
from jsa.config import ConfigError, Preferences, parse_country
from jsa.countries import us

ROOT = Path(__file__).resolve().parents[1]


def profile(**prefs):
    return {"job_search_preferences": {"target_titles": ["Support Engineer"],
                                       "locations": ["Austin, TX"], **prefs}}


class TestThePacks(unittest.TestCase):
    def test_the_us_is_the_default_and_the_only_pack(self):
        self.assertEqual(countries.DEFAULT, "US")
        self.assertEqual(countries.codes(), ["US"])
        self.assertIs(countries.get(), us)
        self.assertIs(countries.get("us"), us)
        with self.assertRaises(KeyError):
            countries.get("CA")

    def test_the_us_pack_ships_its_data(self):
        for path in (us.PLACES_FILE, us.POSTAL_FILE, us.OUTLINE_FILE,
                     us.basemap_file("coarse"), us.DATA / "role_education.csv.gz"):
            self.assertTrue(path.is_file(), path)

    def test_states_and_territories(self):
        self.assertEqual(len(us.STATES), 51)                  # fifty and DC
        self.assertEqual(us.TERRITORIES, {"PR": "Puerto Rico"})
        self.assertEqual(us.SUBDIVISIONS, {**us.STATES, **us.TERRITORIES})


class TestOneStateTable(unittest.TestCase):
    """Three hand-copied tables became one: each reader derives its own view."""

    def test_places_reads_the_pack(self):
        self.assertEqual(places.region_by_name(),
                         {n.lower(): c for c, n in us.SUBDIVISIONS.items()})
        self.assertEqual(places.region_codes(), set(us.SUBDIVISIONS))

    def test_scoring_reads_the_pack_without_puerto_rico_as_before(self):
        self.assertEqual(scoring._state_names(),
                         {c.lower(): n.lower() for c, n in us.STATES.items()})
        self.assertNotIn("pr", scoring._state_names())

    def test_the_map_builder_reads_the_pack(self):
        spec = importlib.util.spec_from_file_location(
            "build_map_data", ROOT / "tools" / "build_map_data.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.STATE_CODES, {n: c for c, n in us.SUBDIVISIONS.items()})


class TestTheCountrySetting(unittest.TestCase):
    def test_unset_means_the_us(self):
        self.assertEqual(Preferences.from_profile(profile()).country, "US")
        self.assertEqual(parse_country(""), "US")

    def test_a_known_code_in_any_case(self):
        self.assertEqual(Preferences.from_profile(profile(country="us")).country, "US")

    def test_an_unknown_country_is_a_plain_message(self):
        with self.assertRaises(ConfigError) as caught:
            Preferences.from_profile(profile(country="Narnia"))
        self.assertIn("job_search_preferences.country", str(caught.exception))
        self.assertIn("US", str(caught.exception))


class TestTheWorkingCountry(unittest.TestCase):
    def setUp(self):
        self.addCleanup(countries.activate, countries.DEFAULT)

    def test_the_us_until_a_profile_says_otherwise(self):
        self.assertIs(countries.active(), us)

    def test_using_puts_the_previous_country_back(self):
        with countries.using("us") as pack:
            self.assertIs(pack, us)
        self.assertIs(countries.active(), us)

    def test_the_live_profile_sets_it_and_a_draft_does_not(self):
        import shutil
        import tempfile
        from unittest import mock

        from jsa import config
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        live, draft = folder / "master_profile.yaml", folder / "draft.yaml"
        live.write_text("job_search_preferences:\n  country: us\n", encoding="utf-8")
        draft.write_text("job_search_preferences:\n  country: Narnia\n", encoding="utf-8")
        with mock.patch.object(config, "PROFILE_PATH", live):
            config.load_profile(live)
            self.assertIs(countries.active(), us)
            config.load_profile(draft)               # read, never activated
            self.assertIs(countries.active(), us)
            live.write_text("job_search_preferences:\n  country: Narnia\n",
                            encoding="utf-8")
            with self.assertRaises(ConfigError):
                config.load_profile(live)


if __name__ == "__main__":
    unittest.main()
