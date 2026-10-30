"""A made-up Toronto job seeker, end to end (Canada plan, phase 7).

A tracker of Canadian and US postings is scored for a Canadian profile and
the dashboard is rendered in Canada: places, kilometres, Canadian dollars,
the US abroad, and the national map in Canada's projection (setup's wording
is in tests/test_canada.py). Nothing reaches the network.
"""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from jsa import countries

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"

POSTINGS = [
    # id, title, location, remote, description
    (1, "Support Engineer", "Toronto, ON", "onsite",
     "Help customers in Toronto. Salary range: $85,000 - $105,000 CAD per year."),
    (2, "Customer Support Engineer", "Mississauga, Ontario, Canada", "hybrid",
     "Answer technical questions. We reasonably expect the total compensation for "
     "this position to be in the range of $90,000 TO $110,000 CAD."),
    (3, "Support Engineer", "Montreal, QC", "onsite",
     "Bilingual support. This role has a minimum annual salary of $80,000, a "
     "midpoint of $90,000, and a maximum of $100,000."),
    (4, "Support Engineer", "Austin, TX", "onsite",
     "Help customers in Texas. Pay range: $120,000 - $150,000 USD."),
    (5, "Support Engineer", "Remote (Canada)", "remote",
     "Remote anywhere in Canada. Salary range: $95,000 - $115,000 CAD per year."),
    (6, "Support Engineer", "Vancouver, BC", "onsite",
     "Help customers on the west coast. Salary range: $90,000 - $110,000 CAD."),
]


def toronto_profile() -> dict:
    data = yaml.safe_load((ROOT / "jsa" / "resources" / "master_profile.example.yaml")
                          .read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    prefs = data.setdefault("job_search_preferences", {})
    prefs.update({"country": "CA", "target_titles": ["Support Engineer"],
                  "locations": ["Toronto, ON", "Remote (Canada)"], "home_location": "M5V",
                  "radius": 50, "work_authorization": "Canadian citizen"})
    prefs.pop("radius_miles", None)
    prefs.pop("compensation_floor_usd", None)
    prefs["compensation_floor"] = "no_floor"
    return data


class TestTorontoEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from jsa import db, discover, sources, web
        from jsa.config import Preferences
        cls.addClassCleanup(countries.activate, countries.DEFAULT)
        countries.activate("CA")          # what loading a CA profile does
        cls.work = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.work, True)
        path, out = cls.work / "t.db", cls.work / "output"
        out.mkdir()
        db.init_db(path)
        con = db.connect(path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Maple Support','maple')")
        for jid, title, loc, remote, text in POSTINGS:
            con.execute(
                "INSERT INTO jobs (id,company_id,title,location,remote,url,description,"
                "discovered_at,dedup_key,track) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (jid, 1, title, loc, remote, f"https://jobs.ashbyhq.com/maple/{jid}",
                 text * 3, "2026-10-30T12:00:00Z", f"{jid}", "engineering"))
        cls.profile = toronto_profile()
        discover.rescore(con, Preferences.from_profile(cls.profile))
        con.commit()
        cls.jobs = {r["id"]: dict(r) for r in con.execute("SELECT * FROM jobs")}
        con.close()
        app = web.create_app(db_path=path, output_dir=out,
                             profile_loader=lambda: cls.profile)
        offline = [mock.patch("jsa.inbox.settings", return_value=None),
                   mock.patch("jsa.config.PROFILE_PATH", cls.work / "p" / "m.yaml"),
                   mock.patch("jsa.sources.fetch",
                              return_value=sources.FetchResult(False, [], "offline")),
                   mock.patch("jsa.llm.api_key", return_value="")]
        for patch in offline:
            patch.start()
            cls.addClassCleanup(patch.stop)
        cls.client = TestClient(app, base_url=BASE)
        cls.addClassCleanup(cls.client.close)

    def test_canadian_postings_score_and_the_us_is_abroad(self):
        self.assertGreater(self.jobs[1]["match_score"], 0)
        self.assertGreater(self.jobs[5]["match_score"], 0)
        self.assertEqual(self.jobs[4]["match_score"], 0)
        self.assertIn("outside Canada", json.loads(self.jobs[4]["match_reasons"])[0])

    def test_pay_is_read_in_canadian_dollars(self):
        for jid, low, high in ((1, 85000, 105000), (2, 90000, 110000), (3, 80000, 100000)):
            with self.subTest(job=jid):
                job = self.jobs[jid]
                self.assertEqual((job["salary_min"], job["salary_max"],
                                  job["salary_currency"]), (low, high, "CAD"))

    def test_distance_reasons_are_in_km(self):
        reasons = " ".join(json.loads(self.jobs[2]["match_reasons"]))
        self.assertRegex(reasons, r"\d+ km away, inside your 50-km radius")

    def test_matches_near_toronto_speak_km(self):
        page = self.client.get("/").text
        self.assertIn("within 50 km of M5V", page)
        self.assertIn('id="f-radius-out">50 km</output>', page)
        self.assertIn("var U = 0.621371192237334;", page)
        self.assertIn("km away", page)
        self.assertIn("Mississauga", page)
        self.assertNotIn("Austin, TX", page)
        for us_word in ("miles of", "mi away", " miles</output>", "lower 48"):
            self.assertNotIn(us_word, page)     # "miles" stays only in code comments

    def test_the_whole_country_is_canada(self):
        page = self.client.get("/?anywhere=1").text
        self.assertIn("anywhere in Canada", page)
        self.assertIn("across Canada", page)
        self.assertIn(" km</text>", page)
        self.assertNotIn("lower 48", page)

    def test_the_home_box_asks_for_a_canadian_place(self):
        page = self.client.get("/").text
        self.assertIn('placeholder="postal code or City, PR"', page)

    def test_resume_dates_and_paper(self):
        from docx import Document

        from jsa import render
        doc = Document()
        render._paper(doc)
        self.assertEqual(round(doc.sections[0].page_width.inches, 1), 8.5)
        self.assertEqual(render.format_date("2022-03", "numeric"), "2022-03")


if __name__ == "__main__":
    unittest.main()
