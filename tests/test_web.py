"""Review dashboard tests.

TestLongestDescription uses the single longest job description in the database,
because that is where layout actually breaks — a 10,000-character posting is
the realistic worst case, and it is already in the data.

Browser-verified alongside these: at a 375px viewport, neither the matches page
nor the longest job page produces any horizontal body overflow, and no element
is wider than the viewport.
"""

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsa import approvals, db
from jsa.config import DB_PATH


def client():
    from fastapi.testclient import TestClient
    from jsa import web
    # The dashboard refuses any Host but a loopback name (DNS rebinding).
    return TestClient(web.create_app(), base_url="http://127.0.0.1:8765")


def has_tracker_data() -> bool:
    """True only when the tracker holds real listings.

    `jsa init` creates an empty database, so checking the file exists is not
    enough — on a fresh clone these tests would fail rather than skip, which is
    exactly what the fresh-clone check caught.
    """
    if not DB_PATH.exists():
        return False
    try:
        con = db.connect()
        try:
            return con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


NEEDS_DATA = unittest.skipUnless(
    has_tracker_data(), "needs a populated tracker (run discover first)")


class TestBindsToLoopbackOnly(unittest.TestCase):
    """This database holds a real job search."""

    def test_refuses_to_bind_publicly(self):
        from jsa import web
        for host in ("0.0.0.0", "192.168.1.10", "::"):
            with self.subTest(host=host):
                with self.assertRaises(ValueError) as ctx:
                    web.serve(host=host)
                self.assertIn("loopback", str(ctx.exception))

    def test_default_host_is_loopback(self):
        from jsa import web
        self.assertEqual(web.HOST, "127.0.0.1")


@NEEDS_DATA
class TestLongestDescription(unittest.TestCase):
    """The realistic worst case for layout, taken from actual data."""

    @classmethod
    def setUpClass(cls):
        con = db.connect()
        try:
            row = con.execute(
                "SELECT id, title, length(description) AS n FROM jobs "
                "WHERE description IS NOT NULL ORDER BY n DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        cls.job_id, cls.title, cls.length = row["id"], row["title"], row["n"]
        cls.client = client()

    def test_the_sample_is_genuinely_long(self):
        self.assertGreater(self.length, 5000,
                           "pick a longer description to test against")

    def test_it_renders(self):
        r = self.client.get(f"/job/{self.job_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.title.split("-")[0].strip()[:20], r.text)

    def test_description_is_wrapped_not_clipped(self):
        """Long unbroken text must wrap, never overflow its container."""
        r = self.client.get(f"/job/{self.job_id}")
        css = r.text[: r.text.index("</style>")]
        self.assertIn("overflow-wrap:anywhere", css)
        self.assertIn("white-space:pre-wrap", css)

    def test_description_scrolls_in_its_own_container(self):
        r = self.client.get(f"/job/{self.job_id}")
        css = r.text[: r.text.index("</style>")]
        self.assertIn("overflow-y:auto", css)

    def test_full_text_is_present_not_truncated(self):
        r = self.client.get(f"/job/{self.job_id}")
        self.assertGreater(len(r.text), self.length,
                           "the description appears to have been cut short")


@NEEDS_DATA
class TestResponsive(unittest.TestCase):
    """Reviewing matches on a phone is a real use case."""

    def setUp(self):
        self.client = client()

    def test_viewport_meta_is_present(self):
        r = self.client.get("/")
        self.assertIn('name="viewport"', r.text)
        self.assertIn("width=device-width", r.text)

    def test_narrow_breakpoint_exists(self):
        r = self.client.get("/")
        self.assertRegex(r.text, r"@media\s*\(max-width:\s*4[0-9]{2}px\)")

    def test_nothing_sets_a_fixed_width_wider_than_a_phone(self):
        r = self.client.get("/")
        css = r.text[: r.text.index("</style>")]
        for match in re.finditer(r"min-width:\s*(\d+)px", css):
            self.assertLessEqual(int(match.group(1)), 375,
                                 "a min-width wider than a phone forces scroll")

    def test_flags_wrap_rather_than_overflow(self):
        r = self.client.get("/")
        css = r.text[: r.text.index("</style>")]
        self.assertIn(".flags{display:flex;gap:6px;flex-wrap:wrap", css)


@NEEDS_DATA
class TestFilters(unittest.TestCase):
    def setUp(self):
        self.client = client()

    def test_matches_page_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_degree_filter_narrows_the_set(self):
        everything = self.client.get("/?limit=500").text.count('class="card"')
        required = self.client.get("/?degree=yes&limit=500").text.count('class="card"')
        self.assertGreater(everything, 0)
        self.assertLess(required, everything)

    def test_per_company_cap_stops_one_board_dominating(self):
        """SpaceX posts thousands of reqs; it must not fill the page."""
        text = self.client.get("/?limit=200").text
        for company in ("SpaceX", "OpenAI", "Anthropic"):
            self.assertLessEqual(text.count(f">{company}<"), 3)

    def test_unknown_region_does_not_error(self):
        self.assertEqual(self.client.get("/?near=nowhere").status_code, 200)

    def test_missing_job_is_a_404_not_a_crash(self):
        self.assertEqual(self.client.get("/job/99999999").status_code, 404)


class TestApprovalGoesThroughTheSamePath(unittest.TestCase):
    """The UI must not be a second route to a decision."""

    def test_web_uses_the_approvals_module(self):
        import inspect
        from jsa import web
        src = inspect.getsource(web)
        self.assertIn("approvals.approve", src)
        self.assertIn("approvals.reject", src)

    def test_web_never_writes_decided_by_itself(self):
        import inspect
        from jsa import web
        self.assertNotIn("decided_by", inspect.getsource(web))

    def test_rejection_without_feedback_is_refused(self):
        tmp = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(tmp)
        con = db.connect(tmp)
        try:
            con.execute(
                "INSERT INTO companies (id,name,slug) VALUES (1,'X','x')")
            con.execute(
                "INSERT INTO jobs (id,company_id,title,url) "
                "VALUES (1,1,'E','https://x/1')")
            con.execute(
                "INSERT INTO applications (id,job_id,status) VALUES (1,1,'ready')")
            aid = approvals.queue(con, "application", 1, "Apply")
            with self.assertRaises(approvals.ApprovalError):
                approvals.reject(con, aid, "")
        finally:
            con.close()



class TestPublicBindIsOptIn(unittest.TestCase):
    """A container legitimately needs 0.0.0.0; nothing else does."""

    def setUp(self):
        import os
        from jsa import web
        self.env = web.ALLOW_PUBLIC_BIND_ENV
        self.saved = os.environ.pop(self.env, None)

    def tearDown(self):
        import os
        if self.saved is not None:
            os.environ[self.env] = self.saved
        else:
            os.environ.pop(self.env, None)

    def test_refused_without_the_opt_in(self):
        from jsa import web
        with self.assertRaises(ValueError) as ctx:
            web.serve(host="0.0.0.0")
        self.assertIn(self.env, str(ctx.exception))

    def test_the_error_explains_the_container_case(self):
        from jsa import web
        with self.assertRaises(ValueError) as ctx:
            web.serve(host="0.0.0.0")
        self.assertIn("127.0.0.1:8765:8765", str(ctx.exception))

    def test_compose_sets_the_opt_in_and_binds_the_host_to_loopback(self):
        """The one supported public bind must stay safe on the host side."""
        from jsa.config import ROOT
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("JSA_ALLOW_PUBLIC_BIND", compose)
        self.assertIn('"127.0.0.1:8765:8765"', compose)
        self.assertNotIn('"0.0.0.0:8765', compose)
        self.assertNotIn('"8765:8765"', compose)


if __name__ == "__main__":
    unittest.main()
