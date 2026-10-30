"""The Canada pack (ADR 0037): places, scoring, pay, setup and privacy.

Every case here is a shape measured in real Canadian postings on
2026-10-30, or one of the owner's decisions: kilometres, postal areas only,
no nationwide source, English first.
"""

import unittest

from jsa import countries, places


def profile(**prefs):
    return {"job_search_preferences": {"target_titles": ["Support Engineer"],
                                       "locations": ["Toronto, ON"],
                                       "country": "CA", **prefs}}


class InCanada(unittest.TestCase):
    def setUp(self):
        self.addCleanup(countries.activate, countries.DEFAULT)
        countries.activate("CA")


class TestPlaces(InCanada):
    def test_towns_with_and_without_accents(self):
        for text, want in [("Montreal, QC", "Montréal, QC"),
                           ("Montréal, Québec", "Montréal, QC"),
                           ("Quebec City", "Québec, QC"),
                           ("Toronto, Ontario, Canada", "Toronto, ON"),
                           ("Scarborough, ON", "Toronto, ON"),
                           ("Saint-Jean-sur-Richelieu, QC", "Saint-Jean-sur-Richelieu, QC"),
                           ("Vancouver, BC", "Vancouver, BC")]:
            with self.subTest(text):
                self.assertEqual([str(p) for p in places.parse(text).places], [want])

    def test_a_hyphenated_name_is_not_cut_into_an_alias(self):
        self.assertIsNone(places.resolve("Saint"))

    def test_a_us_place_is_not_placed(self):
        self.assertEqual(places.parse("Austin, TX").places, [])
        self.assertEqual(places.parse("Vancouver, WA").places, [])

    def test_postal_areas_only(self):
        self.assertEqual(str(places.origin("M5V 2T6")), "M5V")
        self.assertEqual(places.origin("m5v"), places.origin("M5V 2T6"))
        self.assertAlmostEqual(places.origin("M5V").lat, 43.64, places=1)

    def test_the_shipped_data_loads(self):
        self.assertTrue(places.data_is_present())


class TestScoring(InCanada):
    def test_the_us_is_abroad_and_canada_is_home(self):
        from jsa import scoring
        for loc in ("Austin, TX", "New York, NY", "Remote (US)", "Vancouver, WA",
                    "Seattle", "Berlin"):
            with self.subTest(loc):
                self.assertTrue(scoring.outside_home_country(loc))
        for loc in ("Toronto, ON", "Remote (United States | Canada)", "Remote (Canada)",
                    "Montréal", "Toronto, in office", "Ottawa, or remote"):
            with self.subTest(loc):
                self.assertFalse(scoring.outside_home_country(loc))

    def test_reasons_are_in_km_and_name_canada(self):
        from jsa import scoring
        from jsa.config import Preferences
        prefs = Preferences.from_profile(profile(home_location="M5V", radius=50))
        score, why = scoring.location_score("Mississauga, ON", "onsite", prefs)
        self.assertEqual(score, 1.0)
        self.assertRegex(why, r"^\d+ km away, inside your 50-km radius")
        self.assertEqual(scoring.location_score("Austin, TX", "onsite", prefs),
                         (0.0, "location 'Austin, TX' is outside Canada"))


class TestPay(InCanada):
    def test_canadian_dollars(self):
        from jsa import salary
        pay = salary.extract("Salary range: $90,000 - $120,000 CAD per year.")
        self.assertEqual((pay.minimum, pay.maximum, pay.currency), (90000, 120000, "CAD"))
        self.assertEqual(pay.label(), "$90,000–$120,000/yr")


class TestPayShapesMeasured(InCanada):
    """The shapes Canadian postings used that the parser missed (ADR 0037)."""

    def test_an_upper_case_to(self):
        from jsa import salary
        pay = salary.extract("We reasonably expect the total compensation for this position "
                             "to be in the range of $100,000 TO $140,000 CAD.")
        self.assertEqual((pay.minimum, pay.maximum, pay.currency), (100000, 140000, "CAD"))

    def test_minimum_midpoint_maximum_in_words(self):
        from jsa import salary
        pay = salary.extract("This role has a minimum annual salary of $98,300, a midpoint "
                             "of $115,700, and a maximum of $133,100.")
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (98300, 133100, "year"))

    def test_a_usd_range_stays_usd_and_is_not_compared(self):
        from jsa import salary
        pay = salary.extract("Pay range: $120,000 - $150,000 USD")
        self.assertEqual(pay.currency, "USD")
        self.assertEqual(pay.label(), "$120,000–$150,000/yr USD")
        self.assertIsNone(salary.from_row(salary.columns(pay)))

    def test_canadian_dollars_win_over_us_ones(self):
        from jsa import salary
        pay = salary.extract("Salary range: $90,000 - $110,000 CAD in Toronto; "
                             "$100,000 - $125,000 USD in Seattle.")
        self.assertEqual((pay.minimum, pay.maximum, pay.currency), (90000, 110000, "CAD"))


class TestPayInTheUS(unittest.TestCase):
    def test_a_cad_range_is_cad_in_the_us_too(self):
        from jsa import salary
        pay = salary.extract("Salary: $132,000—$151,000 CAD")
        self.assertEqual(pay.currency, "CAD")
        self.assertEqual(pay.label(), "$132,000–$151,000/yr CAD")

    def test_an_upper_case_to_in_the_us(self):
        from jsa import salary
        pay = salary.extract("Salary range: $90,000 TO $120,000 per year.")
        self.assertEqual((pay.minimum, pay.maximum, pay.currency), (90000, 120000, "USD"))


class TestSetupAndForms(InCanada):
    def test_setup_keeps_only_the_postal_area(self):
        from jsa import setup
        prefs, errors = setup.validate({"target_titles": "Support Engineer", "remote": "1",
                                        "home": "M5V 2T6"})
        self.assertEqual(errors, {})
        self.assertEqual(prefs["home_location"], "M5V")
        self.assertIn("Remote (Canada)", prefs["locations"])
        self.assertEqual(prefs["radius"], 65)

    def test_no_nationwide_source_and_no_role_data(self):
        from jsa import roles
        self.assertTrue(countries.source_is_elsewhere("usajobs"))
        self.assertTrue(countries.source_is_elsewhere("themuse"))
        self.assertFalse(countries.source_is_elsewhere("ashby"))
        self.assertFalse(roles.available())

    def test_the_authorization_question(self):
        from jsa import answers
        facts = answers.fact_answers(profile(), {})
        self.assertEqual(facts[0].question, "Are you legally entitled to work in Canada?")


class TestMap(InCanada):
    def test_the_national_map_draws_canada(self):
        from jsa import mapview
        rows = [{"job_id": i, "title": "x", "location": loc, "remote": "onsite"}
                for i, loc in enumerate(["Vancouver, BC", "Toronto, ON", "Halifax, NS"], 1)]
        view = mapview.national(rows)
        self.assertEqual(view.kind, "national")
        x = {b.place: b.x for b in view.bubbles}
        self.assertLess(x["Vancouver"], x["Toronto"])
        self.assertLess(x["Toronto"], x["Halifax"])


class TestEveryPacksContactsAreRemoved(unittest.TestCase):
    """The resume import's privacy guard: every registered pack's samples are
    removed, whichever country is working (ADR 0036, decision 4)."""

    def test_each_packs_samples_are_removed_in_each_country(self):
        from jsa import resume_import
        self.addCleanup(countries.activate, countries.DEFAULT)
        for working in countries.codes():
            countries.activate(working)
            for code in countries.codes():
                sample = countries.get(code).CONTACT_SAMPLE
                lines = ["Pat Example", f"{sample['street']}, {sample['city_region']}",
                         f"{sample['postal']} | {sample['phone']}", "EXPERIENCE",
                         "Answered tickets."]
                _, redacted = resume_import.split_identity(lines)
                text = "\n".join(redacted)
                for field, value in sample.items():
                    with self.subTest(working=working, pack=code, field=field):
                        self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
