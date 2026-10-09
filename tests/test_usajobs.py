"""USAJOBS, the second nationwide source (plan 29).

The fake responses copy the documented Search API shape
(developer.usajobs.gov, read 2026-10-07) with fictional values. No request
reaches USAJOBS from a test.
"""

import os
import sqlite3
import unittest
from unittest import mock

from jsa import approvals, db, discover, doctor, sources
from jsa.config import SCHEMA_PATH, Preferences
from tests.test_map import PROFILE

EMAIL = "applicant@example.com"


def item(ident="801", title="IT Specialist (Customer Support)", city="Boise, Idaho",
         remote=False, telework=False, pay=("62000", "81000", "PA")):
    return {
        "MatchedObjectId": ident,
        "MatchedObjectDescriptor": {
            "PositionID": f"ANN-{ident}",
            "PositionTitle": title,
            "PositionURI": f"https://www.usajobs.gov/job/{ident}",
            "PositionLocationDisplay": city,
            "PositionLocation": [{"LocationName": city, "CityName": city}],
            "OrganizationName": "Riverton Field Office",
            "DepartmentName": "Department of Examples",
            "PositionSchedule": [{"Name": "Full-Time", "Code": "1"}],
            "PositionRemuneration": [{"MinimumRange": pay[0], "MaximumRange": pay[1],
                                      "RateIntervalCode": pay[2]}] if pay else [],
            "PublicationStartDate": "2026-10-01T00:00:00.0000",
            "UserArea": {"Details": {
                "JobSummary": "<p>Support staff across the field office.</p>",
                "MajorDuties": ["Resolve help-desk tickets.", "Train new users."],
                "RemoteIndicator": remote, "TeleworkEligible": telework}},
        },
    }


def page(*items, pages=1):
    return {"SearchResult": {"SearchResultCount": len(items),
                             "SearchResultItems": list(items),
                             "UserArea": {"NumberOfPages": str(pages)}}}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fetch_with(*responses, **entry):
    client = FakeClient(*responses)
    entry = {"locations": ["Boise, ID"], "api_key": "test-key", "email": EMAIL, **entry}
    with mock.patch.object(sources, "_client", return_value=client), \
            mock.patch.object(sources, "POLITE_DELAY_S", 0):
        result = sources.fetch({"kind": "usajobs", **entry})
    return result, client


class TestTheAdapter(unittest.TestCase):
    def test_a_posting_in_the_trackers_shape(self):
        result, _ = fetch_with(FakeResponse(page(item())))
        self.assertTrue(result.ok, result.status)
        job = result.jobs[0]
        self.assertEqual(job["external_id"], "801")
        self.assertEqual(job["employer"], "Riverton Field Office")
        self.assertEqual(job["title"], "IT Specialist (Customer Support)")
        self.assertEqual(job["location"], "Boise, Idaho")
        self.assertEqual(job["remote"], "onsite")
        self.assertEqual(job["employment_type"], "full-time")
        # Its USAJOBS page: where the terms say people view and apply.
        self.assertEqual(job["url"], "https://www.usajobs.gov/job/801")
        self.assertIn("Support staff", job["description"])
        self.assertIn("Train new users.", job["description"])
        self.assertNotIn("<p>", job["description"])
        self.assertEqual((job["pay"].minimum, job["pay"].maximum, job["pay"].period),
                         (62000, 81000, "year"))
        self.assertIn("USAJOBS pay field", job["pay"].text)

    def test_the_documented_headers_are_sent(self):
        _, client = fetch_with(FakeResponse(page(item())))
        url, params, headers = client.calls[0]
        self.assertEqual(url, "https://data.usajobs.gov/api/search")
        self.assertEqual(headers, {"Host": "data.usajobs.gov", "User-Agent": EMAIL,
                                   "Authorization-Key": "test-key"})
        self.assertEqual(params["LocationName"], "Boise, ID")

    def test_remote_and_telework(self):
        result, _ = fetch_with(FakeResponse(page(item("1", remote=True),
                                                 item("2", telework=True))))
        remote = {j["external_id"]: j["remote"] for j in result.jobs}
        self.assertEqual(remote, {"1": "remote", "2": "hybrid"})

    def test_hourly_pay_and_other_intervals(self):
        result, _ = fetch_with(FakeResponse(page(item("1", pay=("21.50", "27", "PH")),
                                                 item("2", pay=("300", "400", "PD")),
                                                 item("3", pay=None))))
        pay = {j["external_id"]: j["pay"] for j in result.jobs}
        self.assertEqual((pay["1"].minimum, pay["1"].maximum, pay["1"].period),
                         (22, 27, "hour"))
        self.assertIsNone(pay["2"])        # per day: left unknown, as everywhere
        self.assertIsNone(pay["3"])

    def test_each_city_once_per_page_and_one_row_across_cities(self):
        result, client = fetch_with(FakeResponse(page(item("1"), item("2"))),
                                    locations=["Boise, ID", "Nampa, ID"])
        self.assertEqual([c[1]["LocationName"] for c in client.calls],
                         ["Boise, ID", "Nampa, ID"])
        self.assertEqual(sorted(j["external_id"] for j in result.jobs), ["1", "2"])

    def test_pages_are_bounded(self):
        full = page(*(item(str(i)) for i in range(sources.USAJOBS_PAGE_SIZE)), pages=9)
        result, client = fetch_with(FakeResponse(full))
        self.assertEqual(len(client.calls), sources.USAJOBS_MAX_PAGES)
        self.assertEqual([c[1]["Page"] for c in client.calls],
                         list(range(1, sources.USAJOBS_MAX_PAGES + 1)))

    def test_without_the_key_or_the_email_it_is_skipped_with_no_request(self):
        for entry in ({"api_key": ""}, {"email": ""}):
            result, client = fetch_with(FakeResponse(page(item())), **entry)
            self.assertTrue(result.ok)
            self.assertEqual(result.jobs, [])
            self.assertIn("skipped", result.status)
            self.assertIn("developer.usajobs.gov", result.status)
            self.assertEqual(client.calls, [])

    def test_no_locations_is_a_configuration_mistake(self):
        result, _ = fetch_with(FakeResponse(page()), locations=[])
        self.assertFalse(result.ok)
        self.assertIn("locations", result.status)

    def test_a_refused_key_and_a_rate_limit_say_so(self):
        refused, _ = fetch_with(FakeResponse({}, 401))
        self.assertFalse(refused.ok)
        self.assertIn("401", refused.status)
        limited, _ = fetch_with(FakeResponse({}, 429))
        self.assertIn("rate limited by USAJOBS (429)", limited.status)

    def test_the_email_appears_in_no_output(self):
        class Boom(FakeClient):
            def get(self, url, params=None, headers=None, **kwargs):
                raise RuntimeError(f"connection failed for {headers}")
        outcomes = [fetch_with(FakeResponse({}, code))[0] for code in (401, 429, 500)]
        with mock.patch.object(sources, "_client", return_value=Boom()):
            outcomes.append(sources.fetch({"kind": "usajobs", "locations": ["Boise, ID"],
                                           "api_key": "test-key", "email": EMAIL}))
        for result in outcomes:
            self.assertFalse(result.ok)
            self.assertNotIn(EMAIL, result.status)
            self.assertNotIn("test-key", result.status)


class TestDiscovery(unittest.TestCase):
    def test_the_settings_come_from_env_and_the_cities_from_the_profile(self):
        prefs = Preferences.from_profile(PROFILE)
        with mock.patch.dict(os.environ, {"USAJOBS_API_KEY": "k", "USAJOBS_EMAIL": EMAIL}):
            entry = discover._with_context({"kind": "usajobs"}, prefs)
        self.assertEqual(entry["locations"], prefs.locations)
        self.assertEqual((entry["api_key"], entry["email"]), ("k", EMAIL))
        # The Muse unchanged.
        with mock.patch.dict(os.environ, {"MUSE_API_KEY": "m"}):
            self.assertEqual(discover._with_context({"kind": "themuse"}, prefs)["api_key"], "m")

    def test_the_shipped_entry_holds_no_settings(self):
        # Verified with a real key on 2026-10-09; like The Muse, it is skipped
        # without one, so shipping it verified costs a fresh clone nothing.
        from jsa.config import SEED_COMPANIES
        import yaml
        entries = yaml.safe_load(SEED_COMPANIES.read_text(encoding="utf-8"))["sources"]
        usajobs = [e for e in entries if e.get("kind") == "usajobs"]
        self.assertEqual(len(usajobs), 1)
        self.assertIs(usajobs[0]["verified"], True)
        self.assertNotIn("api_key", usajobs[0])
        self.assertNotIn("email", usajobs[0])

    def test_an_old_tracker_takes_the_kind_after_upgrade(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA_PATH.read_text(encoding="utf-8").replace("'usajobs',", ""))
        self.assertIn("sources", db.rebuilds_pending(con))
        db.migrate(con)
        db.upsert_source(con, name="usajobs-usajobs", kind="usajobs",
                         url=sources.USAJOBS_BASE, company_id=None)
        con.close()


class TestCredit(unittest.TestCase):
    def test_the_job_page_credits_usajobs(self):
        """The API terms: 'USAJOBS is clearly credited as the source' and
        'users are directed to USAJOBS to view and apply'."""
        from tests.test_web_documents import Sandbox, make_client
        box = Sandbox()
        self.addCleanup(box.cleanup)
        con = box.connect()
        sid = db.upsert_source(con, name="usajobs-usajobs", kind="usajobs",
                               url=sources.USAJOBS_BASE, company_id=None)
        con.execute("INSERT INTO jobs (id,company_id,source_id,external_id,title,url,"
                    "description,track) VALUES (99,1,?,'801','IT Specialist',"
                    "'https://www.usajobs.gov/job/801','Support staff.','engineering')", (sid,))
        con.commit()
        con.close()
        client, _ = make_client(box.db, box.out)
        self.assertIn("from USAJOBS", client.get("/job/99").text)
        self.assertNotIn("from USAJOBS", client.get("/job/1").text)


class TestAround(unittest.TestCase):
    def test_a_usajobs_link_is_the_employers_own_channel(self):
        self.assertEqual(approvals.channel("https://www.usajobs.gov/job/801"), "employer")

    def test_doctor_wants_both_settings_or_neither(self):
        for env, flagged in (({"USAJOBS_API_KEY": "k"}, "USAJOBS_EMAIL"),
                             ({"USAJOBS_EMAIL": EMAIL}, "USAJOBS_API_KEY")):
            report = doctor.Report()
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("USAJOBS_EMAIL" if "USAJOBS_API_KEY" in env
                               else "USAJOBS_API_KEY", None)
                doctor.check_usajobs(report)
            text = " ".join(f.what + f.fix for f in report.findings)
            self.assertIn(flagged, text)
            self.assertNotIn(EMAIL, text)
        report = doctor.Report()
        with mock.patch.dict(os.environ, {"USAJOBS_API_KEY": "k", "USAJOBS_EMAIL": EMAIL}):
            doctor.check_usajobs(report)
        self.assertEqual(report.findings, [])

    def test_the_scanner_catches_the_key_and_the_email_outside_env(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "scan_secrets", Path(__file__).resolve().parents[1] / "tools" / "scan_secrets.py")
        scan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scan)
        # Assembled here, so this file does not trip the scanner it tests.
        leak = ("USAJOBS_API_KEY" + "=abc123\n"
                "USAJOBS_EMAIL=" + "someone" + "@" + "realmail.net\n")
        found = scan.scan_text("notes.md", "notes.md", leak, [], [])
        self.assertTrue(any("USAJOBS API key" in f for f in found), found)
        self.assertTrue(any("email address" in f for f in found), found)
        # The template ships both empty.
        empty = scan.scan_text(".env.example", ".env.example",
                               "USAJOBS_API_KEY" + "=\nUSAJOBS_EMAIL=\n", [], [])
        self.assertEqual(empty, [])


if __name__ == "__main__":
    unittest.main()
