"""Dashboard fixes from the October review (docs/reviews/2026-10-review.md).

R-07 the "I applied" confirm box, R-09 links from stored URLs, R-12 a
non-ASCII token, R-13 no API docs routes, R-14 framing, R-16 chunked
bodies, R-18 two saves at once, R-33 Setup's nav.
"""

import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from jsa import approvals, db, web
from tests.test_apply_by_hand import Page

BASE = "http://127.0.0.1:8765"


class App(unittest.TestCase):
    URL = "https://boards.greenhouse.io/acme/jobs/1"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute("INSERT INTO jobs (id,company_id,title,url,description,match_score) "
                    "VALUES (1,1,'Support Engineer',?,'x',0.5)", (self.URL,))
        con.commit()
        con.close()
        self.app = web.create_app(db_path=self.path, output_dir=self.dir,
                                  profile_loader=lambda: None)
        self.client = TestClient(self.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.app.state.csrf_token

    def set_url(self, url):
        con = db.connect(self.path)
        con.execute("UPDATE jobs SET url = ? WHERE id = 1", (url,))
        con.commit()
        con.close()


class TestLinks(App):
    def test_a_javascript_url_never_becomes_a_link(self):
        self.set_url("javascript:fetch('/turbo/status')")
        page = self.client.get("/job/1").text
        self.assertNotIn("javascript:", page)

    def test_an_ordinary_link_is_kept(self):
        self.assertIn(f'href="{self.URL}"', self.client.get("/job/1").text)

    def test_the_filter(self):
        for url, want in (("https://a.test/x", "https://a.test/x"),
                          ("HTTP://a.test", "HTTP://a.test"),
                          (" javascript:alert(1)", ""), ("data:text/html,x", ""),
                          ("//evil.test", ""), (None, "")):
            self.assertEqual(web.safe_url(url), want)


class TestTheGuard(App):
    def test_a_non_ascii_token_is_refused_not_a_crash(self):
        r = self.client.post("/job/1/save", content="csrf=%C3%A9",
                             headers={"content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status_code, 403)

    def test_no_api_docs(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_pages_cannot_be_framed_by_another_site(self):
        r = self.client.get("/add")
        self.assertEqual(r.headers["x-frame-options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", r.headers["content-security-policy"])

    def test_a_chunked_post_is_refused(self):
        body = f"csrf={self.csrf}".encode()
        r = self.client.post("/job/1/save", content=body, headers={
            "content-type": "application/x-www-form-urlencoded",
            "content-length": str(len(body)), "transfer-encoding": "chunked"})
        self.assertEqual(r.status_code, 411)

    def test_setup_is_its_own_page(self):
        page = self.client.get("/setup").text
        self.assertNotIn('<a href="/" class="on">', page)


class TestTwoSavesAtOnce(App):
    def test_the_second_returns_the_first(self):
        first_inserted, results = threading.Event(), []

        def first():
            con = db.connect(self.path)
            try:
                results.append(approvals.save_application(con, 1))
                first_inserted.set()
                time.sleep(0.3)
                con.commit()
            finally:
                con.close()

        def second():
            first_inserted.wait()
            con = db.connect(self.path)
            try:
                results.append(approvals.save_application(con, 1))
                con.commit()
            finally:
                con.close()

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 2, "the second save raised")
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual([r[1] for r in results], [True, False])


class TestIAppliedWithAnotherResume(Page):
    def test_the_box_is_there_and_it_records(self):
        self.draft(approve=True)
        page = self.page()
        self.assertIn('name="confirm"', page)
        r = self.applied(via="employer", resume="0", cover="0", confirm="1")
        self.assertIn("Recorded", r.text)
        self.assertEqual(self.application()["status"], "applied")


if __name__ == "__main__":
    unittest.main()
