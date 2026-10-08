"""Who holds each kind of job, nationwide (plan 33).

The shipped tables come from BLS table 5.3 and O*NET 31.0; these tests run
on them. Measured on the owner's tracker (ADR 0033): 74% of 1,480 distinct
open titles match, and 27 of the last 30 sampled matches were right.
"""

import gzip
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import roles
from jsa.config import DATA_DIR


class TestNormalize(unittest.TestCase):
    CASES = {
        "Sr. Software Engineer II": "software engineer",
        "Software Engineer (Starlink) - Remote": "software engineer",
        "Account Executive, Mid-Market": "account executive",
        "Support Engineer | Tier 2": "support engineer",
        "Lead Data Engineer, Subscriber Solutions": "data engineer",
        "Medical Assistant": "medical assistant",
        "Full Stack Engineer": "full stack engineer",
        "SWE Intern": "software engineer",
        "Sales Rep": "sales representative",
        "Electricians": "electrician",
        "": "",
    }

    def test_seniority_level_and_place_words_go(self):
        for title, expected in self.CASES.items():
            with self.subTest(title=title):
                self.assertEqual(roles.normalize(title), expected)


class TestMatching(unittest.TestCase):
    # Real-shaped fictional titles and the occupation they should land in.
    EXPECTED = {
        "Software Engineer": "15-1252",
        "Sr. Software Engineer II": "15-1252",
        "Software Engineer, Android": "15-1252",
        "Backend Software Engineer, Payments": "15-1252",
        "Software Developer": "15-1252",
        "Technical Support Specialist": "15-1232",
        "IT Support Technician": "15-1231",      # O*NET's own mapping
        "Help Desk Analyst": "15-1232",
        "Mechanical Engineer": "17-2141",
        "Mechanical Engineer II (Structures)": "17-2141",
        "Electrical Engineer": "17-2071",
        "Aerospace Engineer": "17-2011",
        "Civil Engineer": "17-2051",
        "Industrial Engineer": "17-2112",
        "Data Scientist": "15-2051",
        "Accountant": "13-2011",
        "Registered Nurse": "29-1141",
        "Medical Assistant": "31-9092",
        "Retail Sales Associate": "41-2031",
        "Cashier": "41-2011",
        "Customer Service Representative": "43-4051",
        "Sales Engineer": "41-9031",
        "Electrician": "47-2111",
        "Plumber": "47-2152",
        "Truck Driver": "53-3032",
        "Web Developer": "15-1254",
        "Graphic Designer": "27-1024",
        "Economist": "19-3011",
        "Paralegal": "23-2011",
        "Lawyer": "23-1011",
        "Pharmacist": "29-1051",
        "Machinist": "51-4041",
        "Welder": "51-4121",
        "Financial Analyst": "13-2051",
        "Human Resources Specialist": "13-1071",
        "Marketing Manager": "11-2021",
        "Sales Manager": "11-2022",
        "Network Engineer": "15-1241",
    }

    def test_about_forty_titles(self):
        self.assertGreaterEqual(len(self.EXPECTED), 38)
        wrong = {}
        for title, soc in self.EXPECTED.items():
            got = roles.occupation_for(title)
            if got is None or got.soc != soc:
                wrong[title] = got.soc if got else None
        self.assertEqual(wrong, {})

    def test_a_title_it_doesnt_know_gets_nothing_not_a_guess(self):
        for title in ("Forward Deployed Engineer", "Chief Vibes Officer", "Wizard",
                      "Head of Everything Interesting", ""):
            with self.subTest(title=title):
                self.assertIsNone(roles.occupation_for(title))

    def test_words_in_front_of_a_known_title_narrow_it(self):
        got = roles.occupation_for("Strategic Account Executive, Insurance")
        self.assertEqual(got.soc, "41-3091")
        self.assertEqual(got.confidence, roles.SUFFIX_CONFIDENCE)
        self.assertEqual(got.matched, "account executive")

    def test_the_documented_overrides(self):
        # O*NET files these under advertising managers and research scientists.
        self.assertEqual(roles.occupation_for("Account Executive").soc, "41-3091")
        self.assertEqual(roles.occupation_for("Site Reliability Engineer").soc, "15-1299")

    def test_word_matches_need_the_same_last_word(self):
        got = roles.occupation_for("Camera Software Engineer, Consumer Devices")
        self.assertEqual(got.soc, "15-1252")
        self.assertLess(got.confidence, 1.0)
        self.assertGreaterEqual(got.confidence, roles.MIN_CONFIDENCE)


class TestMix(unittest.TestCase):
    def test_shares_add_up_and_the_line_says_nationwide(self):
        mix = roles.education_mix("15-1252")
        total = mix.hs_or_less + mix.some_college_or_associate + mix.bachelors + mix.graduate
        self.assertAlmostEqual(total, 1.0, delta=0.01)
        self.assertIn("Software developers (15-1252):", mix.line())
        shown = roles.for_title("Software Engineer")
        self.assertIn("nationwide", shown["caveat"])
        self.assertIn("not this company's hires", shown["caveat"])
        self.assertIn("CC BY 4.0", shown["attribution"])
        self.assertIn("USDOL/ETA has not approved", shown["attribution"])

    def test_no_data_files_means_nothing_shown(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, True)
        with mock.patch.object(roles, "DATA_DIR", empty):
            self.assertFalse(roles.available())
            self.assertIsNone(roles.for_title("Software Engineer"))

    def test_the_shipped_files_are_what_the_builder_writes(self):
        with gzip.open(DATA_DIR / roles.EDUCATION_FILE, "rt", encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        self.assertEqual(header, ["soc", "occupation", "less_than_hs", "hs", "some_college",
                                  "associate", "bachelor", "master", "doctoral", "year"])
        with gzip.open(DATA_DIR / roles.TITLES_FILE, "rt", encoding="utf-8") as f:
            self.assertEqual(f.readline().strip(), "title,soc,weight")


class TestSurfaces(unittest.TestCase):
    def setUp(self):
        from tests.test_web_documents import Sandbox, make_client
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.client, _ = make_client(self.box.db, self.box.out)

    def test_job_page_shows_the_line_and_links_the_sources(self):
        page = self.client.get("/job/1").text        # "Support Engineer"
        self.assertIn("This role, nationwide:", page)
        self.assertIn("not this company&#39;s hires", page)
        self.assertIn('href="/about-data"', page)

    def test_nothing_when_the_title_doesnt_match(self):
        con = self.box.connect()
        con.execute("UPDATE jobs SET title = 'Chief Vibes Officer' WHERE id = 1")
        con.commit()
        con.close()
        self.assertNotIn("This role, nationwide", self.client.get("/job/1").text)

    def test_the_sources_page_carries_the_attribution(self):
        page = self.client.get("/about-data").text
        for needed in ("table 5.3", "American Community Survey", "O*NET 31.0 Database",
                       "USDOL/ETA", "CC BY 4.0", "creativecommons.org/licenses/by/4.0",
                       "has not approved, endorsed, or tested", "trademark of USDOL/ETA"):
            self.assertIn(needed, page)


if __name__ == "__main__":
    unittest.main()
