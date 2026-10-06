"""Find a LinkedIn or Indeed listing on the employer's own board (Plan 21).

All HTTP is mocked: every request goes through `sources._get_json`, which a
fake board answers. The promises under test: only the boards' API hosts are
ever requested, a guessed board can never say "same job", and a company name
is matched to its own row even when the board's slug drops a word.
"""

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from jsa import cli, db, find, web

BASE = "http://127.0.0.1:8765"
REAL_CONNECT = db.connect


class FakeBoards:
    """Answers board API URLs; records every URL asked for."""

    def __init__(self, greenhouse=None, lever=None, ashby=None):
        self.greenhouse = greenhouse or {}   # token -> (name, [jobs])
        self.lever = lever or {}             # token -> [jobs]
        self.ashby = ashby or {}
        self.urls: list[str] = []

    def __call__(self, url, browser_ua=False):
        self.urls.append(url)
        parts = urlparse(url)
        segs = [s for s in parts.path.split("/") if s]
        if parts.hostname == "boards-api.greenhouse.io":
            token = segs[2]
            if token not in self.greenhouse:
                raise OSError("404")
            name, jobs = self.greenhouse[token]
            if len(segs) == 3:
                return {"name": name}
            return {"jobs": [{"id": j["id"], "title": j["title"],
                              "location": {"name": j["location"]},
                              "absolute_url": f"https://boards.greenhouse.io/{token}/jobs/{j['id']}",
                              "content": "A posting."} for j in jobs]}
        if parts.hostname == "api.lever.co":
            if segs[2] not in self.lever:
                raise OSError("404")
            return [{"id": j["id"], "text": j["title"], "categories": {"location": j["location"]},
                     "hostedUrl": f"https://jobs.lever.co/{segs[2]}/{j['id']}",
                     "descriptionPlain": "A posting."} for j in self.lever[segs[2]]]
        if parts.hostname == "api.ashbyhq.com":
            if segs[2] not in self.ashby:
                raise OSError("404")
            return {"jobs": []}
        raise AssertionError(f"unexpected request to {url}")


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        db.init_db(self.dir / "t.db")
        self.con = db.connect(self.dir / "t.db")
        self.addCleanup(self.con.close)
        self.perplexity = db.upsert_company(self.con, name="Perplexity", slug="perplexity")
        self.mitek = db.upsert_company(self.con, name="Mitek", slug="mitek")
        self.con.commit()
        self.boards = FakeBoards()
        patch = mock.patch("jsa.sources._get_json", side_effect=self.boards)
        patch.start()
        self.addCleanup(patch.stop)
        sleep = mock.patch("jsa.sources.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def job(self, company_id, title, location, ext="1"):
        source = db.upsert_source(self.con, name=f"c{company_id}-greenhouse", kind="greenhouse",
                                  url="https://boards-api.greenhouse.io/x", company_id=company_id)
        job_id, _ = db.upsert_job(self.con, {
            "company_id": company_id, "source_id": source, "external_id": ext,
            "title": title, "location": location,
            "url": f"https://boards.greenhouse.io/x/jobs/{ext}", "match_score": 0.5})
        self.con.commit()
        return job_id

    def find(self, company, title, city="", entries=(), **kw):
        return find.find(self.con, company, title, city, entries=list(entries), **kw)

    def assert_allowed_hosts_only(self):
        for url in self.boards.urls:
            self.assertTrue(find.allowed(url), url)


class TestCompanyNames(Case):
    def names(self, typed, entries=()):
        return [c.slug for c in find.company_candidates(self.con, typed, list(entries))]

    def test_a_dropped_word_still_finds_its_row(self):
        self.assertEqual(self.names("Perplexity AI"), ["perplexity"])
        self.assertEqual(self.names("Mitek Systems"), ["mitek"])
        self.assertEqual(self.names("Utz Brands", [{"company": "Utz", "slug": "utz",
                                                     "kind": "workday"}]), ["utz"])

    def test_an_ambiguous_name_gives_a_choice(self):
        db.upsert_company(self.con, name="Acme Robotics", slug="acme-robotics")
        db.upsert_company(self.con, name="Acme Labs", slug="acme-labs")
        self.assertEqual(sorted(self.names("Acme")), ["acme-labs", "acme-robotics"])

    def test_a_nonsense_name_finds_nothing(self):
        self.assertEqual(self.names("Zzqxv"), [])
        self.assertEqual(self.names("Per"), [])     # a part of a word is not a name

    def test_each_candidate_says_why(self):
        cand = find.company_candidates(self.con, "Perplexity AI", [])[0]
        self.assertIn("'ai'", cand.why)


class TestTheTracker(Case):
    def test_same_title_and_city_is_the_same_job(self):
        jid = self.job(self.perplexity, "Support Engineer", "Austin, TX")
        report = self.find("Perplexity AI", "Support Engineer", "Austin, TX", live=False)
        self.assertEqual(report.verdict, "same")
        self.assertEqual(report.same[0].job_id, jid)

    def test_a_place_qualifier_on_the_title_is_folded(self):
        self.job(self.perplexity, "Support Engineer (Austin)", "Austin, TX")
        self.assertEqual(self.find("Perplexity", "Support Engineer", "Austin",
                                   live=False).verdict, "same")

    def test_no_city_is_at_most_possible(self):
        self.job(self.perplexity, "Support Engineer", "Austin, TX")
        report = self.find("Perplexity", "Support Engineer", live=False)
        self.assertEqual(report.verdict, "possible")
        self.assertIn("no city", report.possible[0].reason)

    def test_another_city_is_possible(self):
        self.job(self.perplexity, "Support Engineer", "Austin, TX")
        self.assertEqual(self.find("Perplexity", "Support Engineer", "Denver, CO",
                                   live=False).verdict, "possible")

    def test_a_half_overlapping_title_is_not_shown(self):
        self.job(self.perplexity, "Data Support Engineer", "Austin, TX")
        report = self.find("Perplexity", "Support Engineer Lead", "Austin, TX", live=False)
        self.assertEqual(report.verdict, "not_found")

    def test_a_close_title_is_possible(self):
        self.job(self.perplexity, "Senior Support Engineer", "Austin, TX")
        self.assertEqual(self.find("Perplexity", "Support Engineer", "Austin, TX",
                                   live=False).verdict, "possible")

    def test_it_says_whether_you_saved_or_passed_it(self):
        from jsa import approvals
        jid = self.job(self.perplexity, "Support Engineer", "Austin, TX")
        approvals.save_application(self.con, jid)
        self.con.commit()
        hit = self.find("Perplexity", "Support Engineer", "Austin", live=False).same[0]
        self.assertEqual(hit.status, "saved")

    def test_offline_requests_nothing(self):
        self.find("Perplexity", "Support Engineer", "Austin", live=False)
        self.assertEqual(self.boards.urls, [])


class TestAConfiguredBoard(Case):
    ENTRY = {"company": "Perplexity", "slug": "perplexity", "kind": "greenhouse",
             "board": "perplexityai", "verified": True}

    def test_the_live_check_finds_a_posting_newer_than_the_tracker(self):
        self.boards.greenhouse["perplexityai"] = ("Perplexity", [
            {"id": 77, "title": "Support Engineer", "location": "Austin, TX"}])
        report = self.find("Perplexity AI", "Support Engineer", "Austin, TX",
                           entries=[self.ENTRY])
        self.assertEqual(report.verdict, "same")
        hit = report.same[0]
        self.assertEqual(hit.where, "board")
        self.assertIsNone(hit.job_id)
        self.assertEqual(hit.add_url, "https://boards.greenhouse.io/perplexityai/jobs/77")
        self.assertEqual(len(self.boards.urls), 1)        # no guessing
        self.assert_allowed_hosts_only()

    def test_a_stored_posting_is_not_listed_twice(self):
        source = db.upsert_source(self.con, name="perplexity-greenhouse", kind="greenhouse",
                                  url="x", company_id=self.perplexity)
        jid, _ = db.upsert_job(self.con, {
            "company_id": self.perplexity, "source_id": source, "external_id": "77",
            "title": "Support Engineer", "location": "Austin, TX", "url": "u"})
        self.boards.greenhouse["perplexityai"] = ("Perplexity", [
            {"id": 77, "title": "Support Engineer", "location": "Austin, TX"}])
        report = self.find("Perplexity", "Support Engineer", "Austin", entries=[self.ENTRY])
        self.assertEqual([h.job_id for h in report.hits], [jid])


class TestGuessedBoards(Case):
    def test_a_greenhouse_board_with_another_name_is_never_read(self):
        self.boards.greenhouse["globex"] = ("Globex Shipping Ltd", [
            {"id": 1, "title": "Support Engineer", "location": "Austin, TX"}])
        report = self.find("Globex", "Support Engineer", "Austin, TX")
        self.assertEqual(report.verdict, "not_found")
        self.assertFalse(any(u.endswith("/jobs?content=true&pay_transparency=true")
                             for u in self.boards.urls))

    def test_a_named_greenhouse_match_is_possible_never_same(self):
        self.boards.greenhouse["globex"] = ("Globex", [
            {"id": 5, "title": "Support Engineer", "location": "Austin, TX"}])
        report = self.find("Globex Inc", "Support Engineer", "Austin, TX")
        self.assertEqual(report.verdict, "possible")
        hit = report.possible[0]
        self.assertEqual(hit.where, "guess")
        self.assertIn("on a guessed board", hit.reason)
        self.assertEqual(hit.add_url, "https://boards.greenhouse.io/globex/jobs/5")
        self.assertIn("board: globex", report.boards[0].yaml)
        self.assertIn("verified: false", report.boards[0].yaml)

    def test_a_lever_hit_must_be_confirmed(self):
        self.boards.lever["initech"] = [{"id": "a1", "title": "Support Engineer",
                                         "location": "Austin, TX"}]
        report = self.find("Initech", "Support Engineer", "Austin, TX")
        self.assertEqual(report.verdict, "possible")
        self.assertIn("confirm it is this employer", report.possible[0].reason)

    def test_an_empty_board_is_not_suggested(self):
        self.boards.ashby["initech"] = None      # answers, with no postings
        report = self.find("Initech", "Support Engineer", "Austin, TX")
        self.assertEqual(report.boards, [])
        self.assertIn("lists no jobs", " ".join(report.notes))

    def test_at_most_nine_requests(self):
        # Three guesses, every board answering, Greenhouse's name check
        # included: 12 requests uncapped.
        job = {"id": 1, "title": "Accountant", "location": "Austin, TX"}
        for token in ("hooli", "hooli-labs-inc", "hoolilabsinc"):
            self.boards.greenhouse[token] = ("Hooli Labs", [job])
            self.boards.lever[token] = [{**job, "id": "a1"}]
            self.boards.ashby[token] = None
        report = self.find("Hooli Labs Inc", "Support Engineer", "Austin")
        self.assertEqual(len(self.boards.urls), find.MAX_GUESS_REQUESTS)
        self.assertIn("Stopped guessing", " ".join(report.notes))
        self.assert_allowed_hosts_only()

    def test_a_configured_company_is_not_guessed(self):
        self.find("Perplexity", "Support Engineer", entries=[TestAConfiguredBoard.ENTRY])
        self.assertTrue(all("perplexityai" in u for u in self.boards.urls))


class TestNeverLinkedInOrIndeed(Case):
    def test_a_listing_link_is_kept_as_text_and_never_requested(self):
        report = self.find("Initech", "Support Engineer", "Austin",
                           link="https://www.linkedin.com/jobs/view/123")
        self.assertEqual(report.link, "https://www.linkedin.com/jobs/view/123")
        self.assertFalse(any("linkedin" in u for u in self.boards.urls))
        self.assert_allowed_hosts_only()
        self.assertIn("never opened", " ".join(report.notes))

    def test_a_link_typed_as_the_company_is_refused(self):
        with self.assertRaises(find.FindError) as ctx:
            self.find("https://www.indeed.com/viewjob?jk=1", "Support Engineer")
        self.assertIn("type the company", str(ctx.exception))
        self.assertEqual(self.boards.urls, [])

    def test_the_allowlist_refuses_any_other_host(self):
        report = find.Report("x", "y", "")
        for url in ("https://www.linkedin.com/jobs/view/1", "https://indeed.com/x",
                    "https://boards-api.greenhouse.io.evil.test/v1/boards/x",
                    "https://example.com/careers"):
            with self.assertRaises(find.HostNotAllowed):
                find._check(report, url)
        self.assertTrue(find.allowed("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/x/jobs"))

    def test_the_module_imports_no_browser_or_mail_code(self):
        source = Path(find.__file__).read_text(encoding="utf-8")
        for banned in ("smtplib", "imaplib", "selenium", "playwright", "webbrowser"):
            self.assertNotIn(banned, source)


class TestTheCommand(Case):
    def run_cli(self, *argv):
        out, opened = io.StringIO(), []

        def connect(*a, **k):
            opened.append(REAL_CONNECT(self.dir / "t.db"))
            return opened[-1]
        with mock.patch("jsa.db.DB_PATH", self.dir / "t.db"),              mock.patch("jsa.db.connect", side_effect=connect), redirect_stdout(out):
            code = cli.main(["find", *argv])
        for con in opened:
            con.close()
        return code, out.getvalue()

    def test_the_three_answers(self):
        self.job(self.perplexity, "Support Engineer", "Austin, TX")
        code, out = self.run_cli("Perplexity AI", "Support Engineer", "--city", "Austin, TX",
                                 "--offline")
        self.assertEqual(code, 0)
        self.assertIn("SAME JOB", out)
        self.assertIn("LinkedIn and Indeed were not opened", out)
        _, out = self.run_cli("Perplexity", "Support Engineer", "--offline")
        self.assertIn("POSSIBLE MATCHES", out)
        _, out = self.run_cli("Perplexity", "Accountant", "--offline")
        self.assertIn("Not found", out)


class TestTheDashboard(Case):
    def setUp(self):
        super().setUp()
        self.app = web.create_app(db_path=self.dir / "t.db", output_dir=self.dir / "out",
                                  profile_loader=lambda: {"identity": {}})
        self.client = TestClient(self.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.app.state.csrf_token

    def post(self, **form):
        return self.client.post("/find", data={"csrf": self.csrf, **form})

    def test_the_add_page_has_the_form(self):
        page = self.client.get("/add").text
        self.assertIn("Seen it on LinkedIn or Indeed?", page)
        self.assertIn('action="/find"', page)

    def test_a_missing_token_is_refused(self):
        r = self.client.post("/find", data={"company": "Perplexity", "title": "x"})
        self.assertEqual(r.status_code, 403)

    def test_same_job_links_to_it(self):
        jid = self.job(self.perplexity, "Support Engineer", "Austin, TX")
        r = self.post(company="Perplexity", title="Support Engineer", city="Austin, TX")
        self.assertIn("Same job, on the employer", r.text)
        self.assertIn(f'href="/job/{jid}"', r.text)

    def test_a_guessed_hit_offers_add_this_job(self):
        self.boards.lever["initech"] = [{"id": "a1", "title": "Support Engineer",
                                         "location": "Austin, TX"}]
        with mock.patch("jsa.find.load_sources", return_value=[]):
            r = self.post(company="Initech", title="Support Engineer", city="Austin, TX")
        self.assertIn("Possible matches", r.text)
        self.assertIn("Add this job", r.text)
        self.assertIn('value="https://jobs.lever.co/initech/a1"', r.text)
        self.assertIn("jsa verify", r.text)

    def test_not_found_offers_to_paste_it(self):
        with mock.patch("jsa.find.load_sources", return_value=[]):
            r = self.post(company="Perplexity", title="Accountant", city="Austin",
                          link="https://www.linkedin.com/jobs/view/9")
        self.assertIn("Not found", r.text)
        self.assertIn("/add?company=Perplexity&amp;title=Accountant", r.text)
        self.assertFalse(any("linkedin" in u for u in self.boards.urls))
        page = self.client.get("/add?company=Perplexity&title=Accountant").text
        self.assertIn('id="paste-company" required value="Perplexity"', page)

    def test_a_link_as_company_comes_back_with_the_reason(self):
        r = self.post(company="https://linkedin.com/x", title="y")
        self.assertIn("looks like a link", r.text)


if __name__ == "__main__":
    unittest.main()
