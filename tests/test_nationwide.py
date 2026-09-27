"""Coverage for somebody who does not live where the author lives.

Measured 2026-09-26 over the 1,147 postings the 50 shipped feeds had
collected: 25 states had no on-site posting at all, because every one of
those feeds is a single employer's board and those employers hire in a few
metros. Boise had none; Los Angeles had 250.

One aggregating source fixes that, and brings the problem this file mostly
tests: it lists jobs the tracker already has from the employer's own board,
under its own posting id and its own URL.

No network here. The sample below is the shape The Muse's API returned on
2026-09-26, trimmed.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import db, sources

SAMPLE = {
    "page": 1,
    "page_count": 1,
    "results": [
        {
            "id": 9910001,
            "name": "Support Engineer",
            "publication_date": "2026-09-24T10:00:00Z",
            "levels": [{"name": "Entry Level"}],
            "categories": [{"name": "Customer Service"}],
            "locations": [{"name": "Boise, ID"}],
            "company": {"name": "Acme Robotics", "id": 42},
            "contents": "<p>Help customers debug <b>Python</b> integrations.</p>",
            "refs": {"landing_page": "https://www.themuse.com/jobs/acme/support-engineer"},
        },
        {
            "id": 9910002,
            "name": "Data Analyst",
            "publication_date": "2026-09-23T10:00:00Z",
            "levels": [{"name": "Mid Level"}],
            "categories": [{"name": "Data Science"}],
            "locations": [{"name": "Flexible / Remote"}],
            "company": {"name": "Globex", "id": 43},
            "contents": "<p>Reporting and SQL.</p>",
            "refs": {"landing_page": "https://www.themuse.com/jobs/globex/data-analyst"},
        },
        {   # No landing page: nowhere to apply, so not a posting.
            "id": 9910003,
            "name": "Ghost Role",
            "locations": [{"name": "Boise, ID"}],
            "company": {"name": "Nowhere"},
            "contents": "<p>-</p>",
            "refs": {},
        },
    ],
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fetch_with(response, **entry):
    client = FakeClient(response)
    with mock.patch.object(sources, "_client", return_value=client):
        result = sources.fetch({"kind": "themuse", **entry})
    return result, client


class TestTheAdapter(unittest.TestCase):
    def test_it_parses_a_posting_into_the_trackers_shape(self):
        result, _ = fetch_with(FakeResponse(SAMPLE), locations=["Boise, ID"],
                               api_key="test-key")
        self.assertTrue(result.ok, result.status)
        jobs = {j["title"]: j for j in result.jobs}
        self.assertEqual(sorted(jobs), ["Data Analyst", "Support Engineer"])
        job = jobs["Support Engineer"]
        self.assertEqual(job["external_id"], "9910001")
        self.assertEqual(job["location"], "Boise, ID")
        self.assertEqual(job["seniority"], "entry")
        self.assertEqual(job["url"],
                         "https://www.themuse.com/jobs/acme/support-engineer")
        self.assertIn("Python", job["description"])
        self.assertNotIn("<b>", job["description"], "HTML reached the tracker")

    def test_a_flexible_posting_is_remote(self):
        result, _ = fetch_with(FakeResponse(SAMPLE), locations=["Boise, ID"],
                               api_key="k")
        remote = {j["title"]: j["remote"] for j in result.jobs}
        self.assertEqual(remote["Data Analyst"], "remote")
        self.assertEqual(remote["Support Engineer"], "onsite")

    def test_a_posting_with_nowhere_to_apply_is_dropped(self):
        result, _ = fetch_with(FakeResponse(SAMPLE), locations=["Boise, ID"],
                               api_key="k")
        self.assertNotIn("Ghost Role", [j["title"] for j in result.jobs])

    def test_the_operators_cities_are_what_it_asks_for(self):
        _, client = fetch_with(FakeResponse(SAMPLE),
                               locations=["Boise, ID", "Remote (US)"], api_key="k")
        asked = [params.get("location") for _, params in client.calls]
        self.assertEqual(asked, ["Boise, ID", "Remote (US)"])

    def test_the_same_posting_in_two_cities_is_one_job(self):
        result, _ = fetch_with(FakeResponse(SAMPLE),
                               locations=["Boise, ID", "Nampa, ID"], api_key="k")
        ids = [j["external_id"] for j in result.jobs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_key_is_skipped_not_failed(self):
        """Their terms ask you to register. Everything else still runs."""
        result, client = fetch_with(FakeResponse(SAMPLE), locations=["Boise, ID"])
        self.assertTrue(result.ok)
        self.assertEqual(result.jobs, [])
        self.assertIn("MUSE_API_KEY", result.status)
        self.assertEqual(client.calls, [], "it asked anyway")

    def test_no_locations_says_what_to_fill_in(self):
        result, _ = fetch_with(FakeResponse(SAMPLE), locations=[], api_key="k")
        self.assertFalse(result.ok)
        self.assertIn("locations", result.status)

    def test_a_rate_limit_is_reported_not_raised(self):
        result, _ = fetch_with(FakeResponse({}, status=403),
                               locations=["Boise, ID"], api_key="k")
        self.assertFalse(result.ok)
        self.assertIn("rate limit", result.status.lower())

    def test_a_truncated_response_is_a_failed_feed_not_a_crash(self):
        result, _ = fetch_with(FakeResponse({"results": [{"id": 1}]}),
                               locations=["Boise, ID"], api_key="k")
        self.assertTrue(result.ok)
        self.assertEqual(result.jobs, [], "a posting with no url is not a posting")

    def test_the_key_is_not_in_the_committed_config(self):
        from jsa.config import COMPANIES_PATH
        text = COMPANIES_PATH.read_text(encoding="utf-8")
        self.assertIn("MUSE_API_KEY", text, "the config should say where it goes")
        self.assertNotIn("api_key:", text, "a key in a committed file")


class TestTheDuplicateRule(unittest.TestCase):
    """The real work. Erring toward a duplicate, never toward a merge."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        db.init_db(self.dir / "t.db")
        self.con = db.connect(self.dir / "t.db")
        self.addCleanup(self.con.close)
        self.company = db.upsert_company(self.con, name="Acme", slug="acme")
        self.board = db.upsert_source(self.con, name="acme-greenhouse",
                                      kind="greenhouse", url="https://x",
                                      company_id=self.company)
        self.muse = db.upsert_source(self.con, name="themuse-themuse",
                                     kind="themuse", url="https://y",
                                     company_id=None)
        self.first, _ = db.upsert_job(self.con, self.job(
            external_id="gh-1", title="Support Engineer",
            location="Boise, ID", source_id=self.board))

    def job(self, **kw):
        base = {"company_id": self.company, "title": "Support Engineer",
                "location": "Boise, ID", "url": "https://acme.test/1",
                "external_id": "x", "source_id": self.board}
        base.update(kw)
        return base

    def test_the_aggregators_copy_does_not_become_a_second_row(self):
        job_id, is_new = db.upsert_job(self.con, self.job(
            external_id="muse-1", source_id=self.muse,
            title="Support engineer",              # their capitalisation
            location="Boise, ID; Flexible / Remote",
            url="https://www.themuse.com/jobs/acme/support-engineer"))
        self.assertEqual(job_id, self.first)
        self.assertFalse(is_new)
        self.assertEqual(self.count(), 1)

    def test_the_employers_own_row_is_the_one_kept(self):
        db.upsert_job(self.con, self.job(external_id="muse-1", source_id=self.muse,
                                         url="https://www.themuse.com/jobs/x"))
        url, source = self.con.execute(
            "SELECT url, source_id FROM jobs WHERE id = ?", (self.first,)).fetchone()
        self.assertEqual(source, self.board, "the aggregator took the row over")
        self.assertIn("acme.test", url, "the apply link became the aggregator's")

    def test_the_same_title_in_another_city_is_a_different_job(self):
        _, is_new = db.upsert_job(self.con, self.job(
            external_id="muse-2", source_id=self.muse, location="Columbus, OH"))
        self.assertTrue(is_new)
        self.assertEqual(self.count(), 2)

    def test_a_different_role_at_the_same_company_and_city_is_a_different_job(self):
        _, is_new = db.upsert_job(self.con, self.job(
            external_id="muse-3", source_id=self.muse, title="Data Analyst"))
        self.assertTrue(is_new)

    def test_another_companys_identical_title_is_a_different_job(self):
        other = db.upsert_company(self.con, name="Globex", slug="globex")
        _, is_new = db.upsert_job(self.con, self.job(
            external_id="muse-4", source_id=self.muse, company_id=other))
        self.assertTrue(is_new)

    def test_a_missing_location_never_merges(self):
        """When it cannot tell, it shows the job twice rather than hide one."""
        _, is_new = db.upsert_job(self.con, self.job(
            external_id="muse-5", source_id=self.muse, location=""))
        self.assertTrue(is_new)

    def test_the_same_feed_still_updates_its_own_row(self):
        job_id, is_new = db.upsert_job(self.con, self.job(
            external_id="gh-1", title="Support Engineer (Weekend)"))
        self.assertEqual(job_id, self.first)
        self.assertFalse(is_new)
        title, = self.con.execute("SELECT title FROM jobs WHERE id = ?",
                                  (self.first,)).fetchone()
        self.assertEqual(title, "Support Engineer (Weekend)")

    def test_a_closed_posting_does_not_block_a_relisting(self):
        self.con.execute("UPDATE jobs SET closed_at = '2026-01-01T00:00:00Z' "
                         "WHERE id = ?", (self.first,))
        _, is_new = db.upsert_job(self.con, self.job(
            external_id="muse-6", source_id=self.muse))
        self.assertTrue(is_new, "a job that closed and came back is news")

    def count(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
