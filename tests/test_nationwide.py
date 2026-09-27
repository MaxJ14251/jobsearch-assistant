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


class TestAnOlderTrackerSurvivesTheNewSource(unittest.TestCase):
    """What actually happened on 2026-09-26, on the author's own tracker.

    The migration existed and `jsa init` applied it, but discovery did not
    call it, so the first real run died on:

        sqlite3.IntegrityError: CHECK constraint failed: kind IN (...)

    1,190 stored postings and a run that got most of the way through before
    failing. The fix is that writing commands upgrade first.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "old.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        # Put the table back the way it was before the kind existed.
        con.executescript(
            "PRAGMA foreign_keys=OFF;"
            "ALTER TABLE sources RENAME TO sources_old;"
            "CREATE TABLE sources ("
            " id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,"
            " kind TEXT NOT NULL CHECK (kind IN ('greenhouse','lever','ashby',"
            "   'workday','workable','custom','rss','manual','other')),"
            " url TEXT NOT NULL, company_id INTEGER,"
            " enabled INTEGER NOT NULL DEFAULT 1,"
            " poll_interval_h INTEGER NOT NULL DEFAULT 24,"
            " last_polled_at TEXT, last_status TEXT,"
            " created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),"
            " archived_at TEXT);"
            "DROP TABLE sources_old; PRAGMA foreign_keys=ON;")
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.commit()
        con.close()

    def test_the_old_check_refuses_the_new_kind(self):
        """The failure this is about, asserted before the fix is applied."""
        con = db.connect(self.path)
        self.addCleanup(con.close)
        with self.assertRaises(sqlite3.IntegrityError):
            db.upsert_source(con, name="themuse-themuse", kind="themuse",
                             url="https://x", company_id=None)

    def test_upgrade_widens_it_and_keeps_the_rows(self):
        applied = db.upgrade(self.path)
        self.assertTrue(any("sources" in a for a in applied), applied)
        con = db.connect(self.path)
        self.addCleanup(con.close)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)
        source = db.upsert_source(con, name="themuse-themuse", kind="themuse",
                                  url="https://x", company_id=1)
        self.assertTrue(source)

    def test_upgrading_a_current_tracker_changes_nothing(self):
        db.upgrade(self.path)
        self.assertEqual(db.upgrade(self.path), [])


class TestRebuildingATableKeepsEveryoneElsesReferences(unittest.TestCase):
    """Since SQLite 3.25, renaming a table rewrites every REFERENCES to it.

    The sources rebuild renamed sources to sources_old before recreating it,
    so jobs.source_id was helpfully rewritten to point at sources_old -- and
    then sources_old was dropped. SQLite accepts that until the first insert
    and then says:

        sqlite3.OperationalError: no such table: main.sources_old

    which is what the author's tracker did on the first run of the new
    source, with 1,190 postings in it and nothing wrong with the data.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "t.db"
        db.init_db(self.path)

    def jobs_ddl(self) -> str:
        con = db.connect(self.path)
        self.addCleanup(con.close)
        return con.execute(
            "SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0]

    def old_sources_table(self):
        """Put the sources table back the way it was before 'themuse'."""
        con = db.connect(self.path)
        con.executescript(
            "PRAGMA foreign_keys=OFF; PRAGMA legacy_alter_table=ON;"
            "ALTER TABLE sources RENAME TO sources_tmp;"
            "CREATE TABLE sources ("
            " id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,"
            " kind TEXT NOT NULL CHECK (kind IN ('greenhouse','manual')),"
            " url TEXT NOT NULL, company_id INTEGER,"
            " enabled INTEGER NOT NULL DEFAULT 1,"
            " poll_interval_h INTEGER NOT NULL DEFAULT 24,"
            " last_polled_at TEXT, last_status TEXT,"
            " created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z',"
            " archived_at TEXT);"
            "DROP TABLE sources_tmp;"
            "PRAGMA legacy_alter_table=OFF; PRAGMA foreign_keys=ON;")
        con.commit()
        con.close()

    def test_after_the_rebuild_jobs_still_points_at_sources(self):
        self.old_sources_table()
        db.upgrade(self.path)
        ddl = self.jobs_ddl()
        self.assertIn("REFERENCES sources(id)", ddl.replace('"', ""))
        self.assertNotIn("sources_old", ddl)

    def test_and_a_posting_can_still_be_stored(self):
        """The assertion the DDL check exists for."""
        self.old_sources_table()
        db.upgrade(self.path)
        con = db.connect(self.path)
        self.addCleanup(con.close)
        company = db.upsert_company(con, name="Acme", slug="acme")
        source = db.upsert_source(con, name="themuse-themuse", kind="themuse",
                                  url="https://x", company_id=company)
        job_id, is_new = db.upsert_job(con, {
            "company_id": company, "source_id": source, "external_id": "1",
            "title": "Support Engineer", "url": "https://x/1"})
        self.assertTrue(is_new and job_id)

    def test_a_tracker_already_damaged_is_repaired_with_its_rows(self):
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute("INSERT INTO sources (id,name,kind,url) "
                    "VALUES (1,'acme-greenhouse','greenhouse','https://x')")
        con.execute("INSERT INTO jobs (company_id,source_id,external_id,title,url) "
                    "VALUES (1,1,'1','Support Engineer','https://x/1')")
        con.commit()
        # Exactly the damage, on the real table: every column as it is, with
        # the one reference pointing at a table that no longer exists.
        ddl = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0]
        broken_ddl = ddl.replace("REFERENCES sources(id)",
                                 "REFERENCES sources_old(id)")
        self.assertNotEqual(ddl, broken_ddl, "the fixture no longer matches")
        cols = ", ".join(r["name"] for r in con.execute("PRAGMA table_info(jobs)"))
        con.executescript(
            "PRAGMA foreign_keys=OFF; PRAGMA legacy_alter_table=ON;"
            "ALTER TABLE jobs RENAME TO jobs_tmp;"
            f"{broken_ddl};"
            f"INSERT INTO jobs ({cols}) SELECT {cols} FROM jobs_tmp;"
            "DROP TABLE jobs_tmp;"
            "PRAGMA legacy_alter_table=OFF; PRAGMA foreign_keys=ON;")
        con.commit()
        con.close()

        applied = db.upgrade(self.path)
        self.assertTrue(any("jobs" in a for a in applied), applied)
        con = db.connect(self.path)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        self.assertNotIn("sources_old", self.jobs_ddl())
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
