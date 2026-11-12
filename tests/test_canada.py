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

    def test_a_list_of_towns_with_commas_only(self):
        # Shapes from the 22 employers' postings, 2026-11-11.
        for text, want in [
                ("Vancouver, BC, Toronto, ON", ["Vancouver, BC", "Toronto, ON"]),
                ("Toronto, Ontario, Canada, Vancouver, British Columbia, Canada",
                 ["Toronto, ON", "Vancouver, BC"]),
                ("Kitchener Ontario,  Toronto, Ontario, Ontario (Remote)",
                 ["Kitchener, ON", "Toronto, ON"]),
                ("Toronto, ON", ["Toronto, ON"])]:
            with self.subTest(text):
                self.assertEqual([str(p) for p in places.parse(text).places], want)
        # Provinces alone are not towns.
        self.assertEqual(places.parse("Alberta, British Columbia").places, [])

    def test_a_list_of_towns_is_read_only_in_canada(self):
        with countries.using("US"):
            self.assertEqual(places.parse("Austin, TX, Seattle, WA").places, [])

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
                    "Seattle", "Berlin", "US - Remote", "Remote in the US",
                    "Ghent", "Glasgow", "The Hague"):
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

    def test_a_total_rewards_heading_over_a_salary_range(self):
        from jsa import salary
        pay = salary.extract("Our Approach to Total Rewards\n\xa0\n$96,000 - $130,000 reflects "
                             "the salary range for this role, depending on experience.")
        self.assertEqual((pay.minimum, pay.maximum), (96000, 130000))
        # A labelled total stays refused, as Rocket Lab's is.
        self.assertIsNone(salary.extract("Total Compensation (base and equity) "
                                         "$93,275 - $132,025 USD"))

    def test_canadian_dollars_win_over_us_ones(self):
        from jsa import salary
        pay = salary.extract("Salary range: $90,000 - $110,000 CAD in Toronto; "
                             "$100,000 - $125,000 USD in Seattle.")
        self.assertEqual((pay.minimum, pay.maximum, pay.currency), (90000, 110000, "CAD"))


class TestFrenchPay(InCanada):
    """Pay written the French way (2026-11-11: one of 28 French postings
    among the 22 employers' 727 stated it; the rest are the shapes French
    postings use)."""

    def test_the_measured_posting_keeps_its_base_and_not_its_target(self):
        from jsa import salary
        pay = salary.extract("Rémunération: Salaire de base de 20,00 $/heure + commission "
                             "instantanée et non plafonnée\n\nRémunération cible moyenne: "
                             "36,00 $/heure")
        self.assertEqual((pay.minimum, pay.maximum, pay.period), (20, 20, "hour"))

    def test_french_shapes(self):
        from jsa import salary
        for text, want in [
                ("Salaire : 80 000 $ à 95 000 $ par année", (80000, 95000, "year")),
                ("La fourchette salariale est de 80 000 à 95 000 $ CAD.",
                 (80000, 95000, "year")),
                ("Salaire annuel entre 72 500 $ et 90 000 $", (72500, 90000, "year")),
                ("Taux horaire : 45 $/h", (45, 45, "hour"))]:
            with self.subTest(text):
                pay = salary.extract(text)
                self.assertEqual((pay.minimum, pay.maximum, pay.period, pay.currency),
                                 want + ("CAD",))

    def test_not_pay(self):
        from jsa import salary
        for text in ("Une levée de financement de 50 000 000 $.",
                     "Allocation de 500 $ par année pour le bien-être.",
                     "60 jours par année; contribution REER jusqu'à 4 %"):
            with self.subTest(text):
                self.assertIsNone(salary.extract(text))

    def test_read_only_in_canada(self):
        from jsa import salary
        with countries.using("US"):
            self.assertIsNone(salary.extract("Salaire : 80 000 $ à 95 000 $ par année"))


class TestFrenchTitles(InCanada):
    """A French posting scores as its English twin does (2026-11-11: 27 of
    Lightspeed's Montreal postings are French, most with an English twin)."""

    def prefs(self, *targets):
        from jsa.config import Preferences
        return Preferences.from_profile(profile(target_titles=list(targets)))

    def test_a_french_title_meets_its_english_target(self):
        from jsa import scoring
        for french, target in [("Spécialiste du soutien à la facturation",
                                 "Billing Support Specialist"),
                                ("Gestionnaire du développement logiciel",
                                 "Software Development Manager"),
                                ("Administrateur(trice) réseau", "Network Administrator"),
                                ("Graphiste débutant(e)", "Graphic Designer"),
                                ("Représentant(e) au développement des ventes",
                                 "Sales Development Representative")]:
            with self.subTest(french):
                self.assertEqual(scoring.title_score(scoring.scoring_title(french),
                                                     [target])[0], 1.0)

    def test_the_senior_and_manager_rules_read_french(self):
        from jsa import scoring
        prefs = self.prefs("Analytics Engineer")
        for title in ("Ingénieur(e) Analytics Sénior", "Analyste Financier(ère) principal(e)",
                      "Chef d'équipe, Ventes - Restauration",
                      "Gestionnaire, Ingénierie de la fiabilité des sites (SRE)"):
            with self.subTest(title):
                why = scoring.title_rejection(title, prefs)
                self.assertIn(repr(title), why)          # quoted as posted

    def test_a_bilingual_title_uses_its_english_half(self):
        from jsa import scoring
        self.assertEqual(scoring.scoring_title(
            "Gestionnaire, Finance et stratégie// Finance & Strategy Manager"),
            "Finance & Strategy Manager")

    def test_english_titles_are_left_alone(self):
        from jsa import scoring
        for title in ("Senior Analytics Engineer", "Principal Engineer",
                      "Commercial Account Executive", "Client Success Manager"):
            with self.subTest(title):
                self.assertEqual(scoring.scoring_title(title), title)

    def test_years_asked_in_french(self):
        from jsa import scoring
        self.assertEqual(scoring.required_years(
            "Vous avez au moins 5 ans d'expérience en vente, ou 3 à 4 années d’expérience."), 5)
        self.assertIsNone(scoring.required_years("Fondée il y a 20 ans à Montréal."))

    def test_read_only_in_canada(self):
        from jsa import scoring
        with countries.using("US"):
            self.assertEqual(scoring.scoring_title("Graphiste débutant(e)"),
                             "Graphiste débutant(e)")
            self.assertIsNone(scoring.required_years("5 ans d'expérience"))


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

    def test_no_nationwide_source(self):
        self.assertTrue(countries.source_is_elsewhere("usajobs"))
        self.assertTrue(countries.source_is_elsewhere("themuse"))
        self.assertFalse(countries.source_is_elsewhere("ashby"))

    def test_role_education_from_statistics_canada(self):
        # 2021 Census by NOC 2021 occupation (ADR 0037, 2026-11-11).
        from jsa import roles
        self.assertTrue(roles.available())
        role = roles.for_title("Technical Support Specialist")
        self.assertEqual(role["soc"], "22221")          # not wholesale technical sales
        self.assertIn("trades, college or CEGEP", role["line"])
        self.assertNotIn("associate's", role["line"])
        self.assertIn("Statistics Canada", role["attribution"])
        self.assertEqual(roles.for_title("Customer Service Representative")["soc"], "64409")
        with countries.using("US"):
            self.assertEqual(roles.for_title("Software Engineer")["soc"], "15-1252")

    def test_the_authorization_question(self):
        from jsa import answers
        facts = answers.fact_answers(profile(), {})
        self.assertEqual(facts[0].question, "Are you legally entitled to work in Canada?")

    def test_every_fact_answer_for_a_canadian_profile(self):
        from jsa import answers
        prof = profile(work_authorization="Canadian citizen", needs_visa_sponsorship=False,
                       willing_to_relocate=True, work_arrangement=["remote", "hybrid"])
        got = {a.key: a for a in answers.fact_answers(
            prof, {"salary_min": 80000, "salary_max": 95000, "salary_currency": "CAD",
                   "salary_period": "year"})}
        self.assertEqual(got["work_authorization"].body, "Canadian citizen")
        self.assertEqual((got["sponsorship"].body, got["relocation"].body), ("No", "Yes"))
        self.assertEqual(got["arrangement"].body, "remote, hybrid")
        self.assertEqual(got["salary"].body,
                         "Your call. The posting states $80,000–$95,000 CAD per year.")
        self.assertNotIn("United States", " ".join(a.question + a.body for a in got.values()))

    def test_the_extension_gets_the_province_name_and_the_country(self):
        from jsa.web.routes_ext import identity
        ident = identity({"identity": {"full_name": "Robin Example",
                                       "location": {"city": "Barrie", "state": "on"}}})
        self.assertEqual((ident["city"], ident["state"], ident["region_name"]),
                         ("Barrie", "on", "Ontario"))
        self.assertNotIn("postal_code", ident)
        with countries.using("US"):
            self.assertEqual(identity({"identity": {"location": {"state": "ID"}}})
                             ["region_name"], "Idaho")


class TestEmployers(InCanada):
    def test_canada_ships_its_own_22_employers(self):
        from jsa import config
        self.assertEqual(config.companies_path().name, "companies_ca.yaml")
        entries = config.load_sources(countries.get("CA").SEED_COMPANIES)
        self.assertEqual(len(entries), 22)
        self.assertTrue(all(e["verified"] and e["kind"] in ("greenhouse", "lever", "ashby")
                            for e in entries))
        self.assertFalse(any(countries.source_is_elsewhere(e["kind"]) for e in entries))

    def test_the_us_keeps_its_list(self):
        from jsa import config
        countries.activate("US")
        self.assertEqual(config.companies_path(), config.SEED_COMPANIES)


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
