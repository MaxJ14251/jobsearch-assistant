"""The app in a country that isn't the US (country pack split, phase 4).

Testland (tests/testland.py) is a fictional pack sharing nothing with the
US. Registered and made the working country, it is run through places,
scoring, pay, setup, forms, sources, the map, resumes and the resume
import's privacy guard. Anything still assuming the US fails here.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import countries, places
from jsa.countries import us
from tests import testland


def profile(**prefs):
    return {"job_search_preferences": {"target_titles": ["Support Engineer"],
                                       "locations": ["Northville, NO"],
                                       "country": "ZZ", **prefs}}


def _clear_caches():
    from jsa import mapview, roles, salary, scoring
    for cache in (places._places_of, places._zips_of, places._noise, scoring._pattern,
                  salary._patterns, mapview._outline_of, roles._education_of,
                  roles._titles_of):
        cache.cache_clear()


class InTestland(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp())
        testland.use_folder(cls.folder)
        cls.packs = mock.patch.dict(countries.PACKS, {"ZZ": testland})
        cls.packs.start()
        _clear_caches()

    @classmethod
    def tearDownClass(cls):
        cls.packs.stop()
        _clear_caches()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def setUp(self):
        self.addCleanup(countries.activate, countries.DEFAULT)
        countries.activate("ZZ")


class TestPlaces(InTestland):
    def test_postal_codes_and_provinces(self):
        self.assertEqual(places.origin("A1B 2C3").lat, 46.0)
        self.assertEqual(str(places.origin("Southport, SO")), "Southport, SO")
        self.assertEqual(str(places.origin("Northville, Northshire")), "Northville, NO")
        self.assertEqual(str(places.resolve("nville")), "Northville, NO")   # its alias

    def test_no_us_place_is_known(self):
        self.assertIsNone(places.origin("Austin, TX"))
        self.assertIsNone(places.origin("92101"))

    def test_the_country_noise_is_its_own(self):
        self.assertEqual(str(places.parse("Testland - Lakeside, NO").places[0]),
                         "Lakeside, NO")


class TestScoring(InTestland):
    def test_home_and_abroad_are_testlands(self):
        from jsa import scoring
        self.assertTrue(scoring.outside_home_country("Austin, Texas"))
        self.assertTrue(scoring.outside_home_country("London"))
        self.assertFalse(scoring.outside_home_country("Remote (Testland)"))
        self.assertFalse(scoring.outside_home_country("Toronto"))   # not abroad here

    def test_the_reasons_say_testland_and_province(self):
        from jsa import scoring
        from jsa.config import Preferences
        prefs = Preferences.from_profile(profile(locations=["Southport, SO"]))
        self.assertEqual(prefs.country, "ZZ")
        score, why = scoring.location_score("London", "onsite", prefs)
        self.assertEqual((score, why), (0.0, "location 'London' is outside Testland"))
        score, why = scoring.location_score("Lakeside, SO", "onsite", prefs)
        self.assertIn("a province you listed", why)
        job = {"title": "Support Engineer", "location": "Berlin", "remote": "onsite",
               "description": "Help customers."}
        self.assertIn("outside Testland", scoring.score_job(job, prefs)[1][0])


class TestPay(InTestland):
    def test_its_currency_is_read_and_dollars_are_not(self):
        from jsa import salary
        pay = salary.extract("Salary range: T$60,000 - T$80,000 per year.")
        self.assertEqual((pay.minimum, pay.maximum, pay.currency), (60000, 80000, "TLD"))
        self.assertEqual(pay.label(), "T$60,000–T$80,000/yr")
        self.assertIsNone(salary.extract("Salary range: $90,000 - $120,000 per year."))
        self.assertIsNone(salary.from_row({"salary_min": 90000, "salary_max": 120000,
                                           "salary_currency": "USD"}))

    def test_its_hours_and_pay_band(self):
        from jsa import salary, scoring
        hourly = salary.extract("Pay: T$30.00 - T$40.00 per hour.")
        self.assertEqual(hourly.annual_minimum(), 30 * 1950)
        self.assertIn("about T$58,500/yr", scoring.pay_score(hourly)[1])


class TestKilometres(InTestland):
    """Testland reads distances in km; the app still measures in miles."""

    def test_the_radius_is_typed_in_km_and_kept_in_miles(self):
        from jsa.config import ConfigError, Preferences
        prefs = Preferences.from_profile(profile(radius=100))
        self.assertAlmostEqual(prefs.radius_miles, 62.1371, places=3)
        old = Preferences.from_profile(profile(radius_miles=40))
        self.assertEqual(old.radius_miles, 40)                  # still miles
        unset = Preferences.from_profile(profile())
        self.assertAlmostEqual(countries.shown(unset.radius_miles), 60)
        with self.assertRaisesRegex(ConfigError, "both radius and radius_miles"):
            Preferences.from_profile(profile(radius=100, radius_miles=40))
        with self.assertRaisesRegex(ConfigError, "number of km"):
            Preferences.from_profile(profile(radius="far"))

    def test_reasons_say_km(self):
        from jsa import scoring
        self.assertEqual(scoring.distance_score(31.0686, 37.2823)[1],
                         "50 km away, inside your 60-km radius")
        self.assertEqual(scoring.distance_score(0.62, 37.2823)[1],
                         "1 km away, inside your 60-km radius")

    def test_setup_writes_radius_in_km(self):
        from jsa import setup
        prefs, errors = setup.validate({"target_titles": "Support Engineer", "remote": "1",
                                        "radius": "80"})
        self.assertEqual(errors, {})
        self.assertEqual(prefs["radius"], 80)
        self.assertNotIn("radius_miles", prefs)
        prefs, _ = setup.validate({"target_titles": "Support Engineer", "remote": "1"})
        self.assertEqual(prefs["radius"], 60)

    def test_the_dashboard_slider_is_in_km(self):
        from jsa import web
        self.assertAlmostEqual(web._radius("100", ""), 62.1371, places=3)
        self.assertAlmostEqual(web._radius("9000", ""), countries.to_miles(web.RADIUS_MAX))
        self.assertEqual(web.env.filters["shown"](31.0686), 50)

    def test_the_scale_bar_is_a_round_number_of_km(self):
        from jsa import mapview
        self.assertEqual(mapview._nice(countries.shown(100)), 50)


class TestSetupAndForms(InTestland):
    def test_setup_writes_testlands_remote_entry_and_wording(self):
        from jsa import setup
        prefs, errors = setup.validate({"target_titles": "Support Engineer", "remote": "1",
                                        "home": "nowhere at all"})
        self.assertIn("Remote (Testland)", prefs["locations"])
        self.assertIn("isn't a postal code or a \"City, PR\"", errors["home"])
        self.assertIn("a postal code like A1B 2C3", errors["home"])

    def test_the_authorization_question(self):
        from jsa import answers
        facts = answers.fact_answers(profile(), {})
        self.assertEqual(facts[0].question, "Are you legally able to work in Testland?")


class TestSourcesMapResumesData(InTestland):
    def test_us_sources_are_skipped_and_its_own_are_not(self):
        self.assertTrue(countries.source_is_elsewhere("usajobs"))
        self.assertTrue(countries.source_is_elsewhere("themuse"))
        self.assertFalse(countries.source_is_elsewhere("testboard"))
        self.assertFalse(countries.source_is_elsewhere("greenhouse"))

    def test_no_role_data_means_no_line(self):
        from jsa import roles
        self.assertFalse(roles.available())
        self.assertIsNone(roles.for_title("Software Engineer"))

    def test_a4_and_its_dates(self):
        from docx import Document

        from jsa import render
        doc = Document()
        render._paper(doc)
        self.assertEqual(round(doc.sections[0].page_width.mm), 210)
        self.assertEqual(render.format_date("2022-03", "numeric"), "03.2022")

    def test_the_map_has_no_outline_and_says_so(self):
        from jsa import mapview
        self.assertEqual(mapview.outline(), {})


class TestPrivacyInEveryPack(InTestland):
    """The resume import removes contact details before any text reaches the
    model. Every pack's samples are removed, whichever country is working:
    a resume written elsewhere is still cleaned."""

    def test_each_packs_samples_are_removed(self):
        from jsa import resume_import
        for working in ("ZZ", "US"):
            countries.activate(working)
            for pack in (testland, us):
                sample = pack.CONTACT_SAMPLE
                lines = ["Pat Example", f"{sample['street']}, {sample['city_region']}",
                         f"{sample['postal']} | {sample['phone']}", "EXPERIENCE",
                         "Answered tickets."]
                _, redacted = resume_import.split_identity(lines)
                text = "\n".join(redacted)
                for field, value in sample.items():
                    with self.subTest(working=working, pack=pack.CODE, field=field):
                        self.assertNotIn(value, text)

    def test_the_working_countrys_fill_the_draft(self):
        from jsa import resume_import
        sample = testland.CONTACT_SAMPLE
        ident, _ = resume_import.split_identity(
            ["Pat Example", sample["phone"], sample["city_region"], "EXPERIENCE"])
        self.assertEqual((ident.phone, ident.city, ident.state),
                         ("+99 1234 5678", "Northville", "NO"))


class TestTheUSComesBack(InTestland):
    def test_after_testland_the_us_is_as_it_was(self):
        countries.activate("US")
        self.assertEqual(str(places.origin("Austin, TX")), "Austin, TX")
        from jsa import salary
        self.assertEqual(salary.extract("Salary range: $90,000 - $120,000 per year.").currency,
                         "USD")
        from jsa import scoring
        from jsa.config import Preferences
        us_profile = {"job_search_preferences": {"target_titles": ["x"],
                                                 "locations": ["Austin, TX"], "radius": 25}}
        self.assertEqual(Preferences.from_profile(us_profile).radius_miles, 25)
        self.assertEqual(scoring.distance_score(12, 25)[1],
                         "12 miles away, inside your 25-mile radius")


if __name__ == "__main__":
    unittest.main()
