"""What the extension's panel shows about a company and role (plan 34)."""

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from jsa import degree, research, roles, sources
from tests.test_ext import GREENHOUSE, Ext

LONG = " You will help customers every day and own their outcomes." * 12
OTHER = "https://job-boards.greenhouse.io/elsewhere/jobs/555"


def board(*jobs):
    """A Greenhouse board's API answer (fictional)."""
    return {"jobs": [{"id": i, "title": t, "content": d, "location": {"name": "Remote"},
                      "absolute_url": f"https://job-boards.greenhouse.io/elsewhere/jobs/{i}",
                      "updated_at": "2026-10-01"} for i, t, d in jobs]}


ELSEWHERE = board(
    (555, "Support Engineer", "Bachelor's degree or equivalent experience." + LONG),
    (556, "Mechanical Engineer", "BS in Mechanical Engineering required." + LONG),
    (557, "Account Executive", "CISSP preferred." + LONG),
    (558, "Stub", "Apply now."),
)


class Research(Ext):
    def setUp(self):
        super().setUp()
        from jsa import db
        db.backfill_degree(self.con)          # as an upgraded tracker has
        self.con.commit()

    def research(self, url=GREENHOUSE):
        return self.get("/ext/research", url=url).json()

    def refresh(self, url=OTHER, answer=ELSEWHERE):
        with mock.patch.object(sources, "_get_json", return_value=answer) as got:
            body = self.post("/ext/research/refresh", {"url": url}).json()
        return body, got


class TestTheKey(Research):
    def test_both_routes_need_it(self):
        self.assertEqual(self.get("/ext/research", key=False, url=GREENHOUSE).status_code, 401)
        self.assertEqual(self.post("/ext/research/refresh", {"url": OTHER}, key=False)
                         .status_code, 401)


class TestFromTheTracker(Research):
    def test_a_saved_job_shows_its_posting_role_and_the_caveats(self):
        body = self.research()
        self.assertEqual(body["posting"]["from"], "tracker")
        self.assertEqual(body["posting"]["level"], degree.classify("Help customers. " * 80).level)
        self.assertEqual(body["role"]["soc"], roles.occupation_for("Support Engineer").soc)
        self.assertEqual(body["caveats"], {"posting": degree.CAVEAT, "role": roles.CAVEAT})

    def test_a_recently_polled_company_needs_no_fetch(self):
        self.con.execute("UPDATE sources SET last_polled_at = ?",
                         (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),))
        self.con.commit()
        with mock.patch.object(sources, "_get_json") as got:
            body = self.research()
        self.assertEqual(body["state"], "ready")
        self.assertEqual(body["company"]["from"], "tracker")
        self.assertFalse(body["can_refresh"])
        got.assert_not_called()

    def test_a_company_not_polled_this_week_is_stale(self):
        body = self.research()               # never polled in this fixture
        self.assertEqual(body["state"], "stale")
        self.assertTrue(body["can_refresh"])


class TestRefresh(Research):
    def test_unknown_company_missing_then_read_once(self):
        before = self.research(OTHER)
        self.assertEqual(before["state"], "missing")
        self.assertIsNone(before["posting"])
        jobs_before = self.con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        body, got = self.refresh()
        self.assertEqual(got.call_count, 1)
        self.assertEqual(body["state"], "ready")
        self.assertEqual(body["company"]["from"], "board")
        self.assertIn("elsewhere, 3 open postings on its job board", body["company"]["line"])
        self.assertIn("1 more too short to read", body["company"]["line"])
        self.assertEqual(body["posting"]["from"], "board")
        self.assertEqual(body["posting"]["level"], "bachelors_or_equiv")
        self.assertEqual(body["role"]["occupation"],
                         roles.for_title("Support Engineer")["occupation"])
        # Research is not discovery: nothing joined the tracker's jobs.
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
                         jobs_before)

    def test_a_fresh_board_is_not_read_again(self):
        self.refresh()
        body, got = self.refresh()
        got.assert_not_called()
        self.assertEqual(body["state"], "ready")

    def test_a_week_old_board_is_read_again(self):
        self.refresh()
        old = (datetime.now(timezone.utc) - timedelta(days=research.FRESH_DAYS, hours=1))
        self.con.execute("UPDATE company_degree_snapshots SET fetched_at = ?",
                         (old.strftime("%Y-%m-%dT%H:%M:%SZ"),))
        self.con.commit()
        self.assertEqual(self.research(OTHER)["state"], "stale")
        _, got = self.refresh()
        self.assertEqual(got.call_count, 1)

    def test_the_daily_limit(self):
        for i in range(research.MAX_REFRESHES_PER_DAY):
            self.refresh(f"https://job-boards.greenhouse.io/board{i}/jobs/1")
        body, got = self.refresh("https://job-boards.greenhouse.io/oneMore/jobs/1")
        got.assert_not_called()
        self.assertEqual(body["state"], "failed")
        self.assertIn("board reads are used up", body["message"])
        self.assertFalse(self.research("https://job-boards.greenhouse.io/x/jobs/1")
                         ["can_refresh"])

    def test_only_the_boards_own_api_host_is_requested(self):
        seen = []

        def fake(url, browser_ua=False):
            guard = sources.REQUEST_GUARD.get()
            self.assertIsNotNone(guard, "the allowlist guard was not set")
            seen.append(url)
            return ELSEWHERE
        with mock.patch.object(sources, "_get_json", side_effect=fake):
            self.post("/ext/research/refresh", {"url": OTHER})
        self.assertEqual(seen, ["https://boards-api.greenhouse.io/v1/boards/elsewhere/jobs"
                                "?content=true&pay_transparency=true"])

    def test_a_page_that_isnt_a_board_is_refused_without_a_request(self):
        for url in ("https://www.linkedin.com/jobs/view/1", "https://example.com/careers",
                    ""):
            with self.subTest(url=url), mock.patch.object(sources, "_get_json") as got:
                self.assertEqual(self.research(url)["state"], "unsupported")
                body = self.post("/ext/research/refresh", {"url": url}).json()
                self.assertEqual(body["state"], "failed")
                got.assert_not_called()

    def test_a_board_that_doesnt_answer(self):
        with mock.patch.object(sources, "_get_json", side_effect=OSError("down")):
            body = self.post("/ext/research/refresh", {"url": OTHER}).json()
        self.assertEqual(body["state"], "failed")
        self.assertIn("Couldn't read the board", body["message"])

    def test_every_answer_carries_the_caveats(self):
        body, _ = self.refresh()
        for answer in (body, self.research(), self.research(OTHER)):
            self.assertEqual(answer["caveats"]["posting"], degree.CAVEAT)
            self.assertEqual(answer["caveats"]["role"], roles.CAVEAT)


class TestOnTheDashboard(Research):
    def test_the_companies_page_lists_boards_read(self):
        self.refresh()
        page = self.client.get("/companies").text
        self.assertIn("Job boards read while applying", page)
        self.assertIn("elsewhere, 3 open postings on its job board", page)

    def test_the_job_page_shows_its_boards_reading(self):
        self.refresh(GREENHOUSE, board(
            (4012345, "Support Engineer", "Bachelor's degree required." + LONG)))
        page = self.client.get("/job/1").text
        self.assertIn("Its whole job board:", page)
