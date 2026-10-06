"""The newcomer setup page (plan 20). The guard that matters most: a
personal profile is never overwritten."""

import shutil
import tempfile
import threading
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import yaml
from fastapi.testclient import TestClient

from jsa import config, db, setup, web
from jsa.config import ROOT, Preferences

EXAMPLE = ROOT / "jsa" / "resources" / "master_profile.example.yaml"
GOOD = {"target_titles": "Support Engineer\nData Analyst", "remote": "1",
        "home": "Boise, ID", "radius": "30", "work_authorization": "US citizen",
        "needs_visa_sponsorship": "no", "willing_to_relocate": "", "floor": "no floor",
        "max_years": "3", "years_filter": "rank", "exclude_keywords": "Senior"}


class Folder(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "profile").mkdir()
        shutil.copy(EXAMPLE, self.dir / "profile" / "master_profile.example.yaml")
        self.live = self.dir / "profile" / "master_profile.yaml"
        self.dbfile = self.dir / "jobsearch.db"
        db.init_db(self.dbfile)
        for patch in (mock.patch("jsa.config.PROFILE_PATH", self.live),
                      mock.patch("jsa.config.DB_PATH", self.dbfile),
                      mock.patch("jsa.backup.DB_PATH", self.dbfile),
                      mock.patch("jsa.backup.OUTPUT_DIR", self.dir / "output"),
                      mock.patch("jsa.backup.PROFILE_PATH", self.live)):
            patch.start()
            self.addCleanup(patch.stop)


class TestServeCreatesTheTracker(unittest.TestCase):
    def test_a_fresh_folder_gets_a_tracker_not_a_refusal(self):
        from jsa import cli
        path = Path(tempfile.mkdtemp()) / "jobsearch.db"
        with mock.patch("jsa.cli.DB_PATH", path), mock.patch("jsa.db.DB_PATH", path), \
             mock.patch("jsa.web.serve") as serve, \
             mock.patch("jsa.db.init_db", side_effect=lambda *a, **k: db.upgrade(path)):
            self.assertEqual(cli.cmd_serve(Namespace(host="127.0.0.1", port=8765)), 0)
        self.assertTrue(path.exists())
        serve.assert_called_once()


class TestPreferencesInPlace(Folder):
    def around(self, text):
        lines = text.splitlines()
        start = lines.index("job_search_preferences:")
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i] and not lines[i][0].isspace())
        while lines[end - 1].startswith("#") or not lines[end - 1].strip():
            end -= 1
        return lines[:start], lines[end:]

    def check_only_the_block_changes(self, path):
        before = path.read_text(encoding="utf-8")
        prefs, errors = setup.validate(GOOD)
        self.assertEqual(errors, {})
        setup.set_preferences(path, prefs)
        after = path.read_text(encoding="utf-8")
        self.assertEqual(self.around(before), self.around(after))
        data = yaml.safe_load(after)
        parsed = Preferences.from_profile(data)
        self.assertEqual(parsed.target_titles, ["Support Engineer", "Data Analyst"])
        self.assertEqual(parsed.locations, ["Remote (US)", "Boise, ID"])
        self.assertEqual(data["job_search_preferences"]["compensation_floor_usd"], "no_floor")

    def test_an_example_shaped_draft(self):
        self.check_only_the_block_changes(setup.start_from_example())
        self.assertFalse(self.live.exists())

    def test_an_import_shaped_draft(self):
        from jsa import resume_import as ri
        path = setup.draft_path()
        ri.write_draft(path, ri.Identity(full_name="Pat Example"), ri.Verified(), "r.docx")
        self.check_only_the_block_changes(path)

    def test_the_live_profile_is_never_written(self):
        self.live.write_text("mine\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            setup.set_preferences(self.live, {"target_titles": ["x"]})
        self.assertEqual(self.live.read_text(encoding="utf-8"), "mine\n")


class TestValidation(Folder):
    def test_each_bad_field_is_named(self):
        bad = {**GOOD, "target_titles": "", "home": "Nowhereville, ZZ", "remote": "",
               "radius": "-5", "floor": "lots", "max_years": "three"}
        _, errors = setup.validate(bad)
        for field in ("target_titles", "home", "floor", "max_years", "radius"):
            self.assertIn(field, errors, field)


class TestThePage(Folder):
    def client(self):
        app = web.create_app(db_path=self.dbfile, profile_loader=lambda: None)
        c = TestClient(app, base_url="http://127.0.0.1:8765")
        self.addCleanup(c.close)
        return c, app

    def test_start_save_and_errors_write_nothing(self):
        c, app = self.client()
        token = app.state.csrf_token
        self.assertIn("Start from the example", c.get("/setup").text)
        c.post("/setup/start", data={"csrf": token})
        draft = setup.draft_path().read_bytes()
        r = c.post("/setup/preferences", data={"csrf": token, **GOOD, "target_titles": ""})
        self.assertIn("Name at least one job title", r.text)
        self.assertEqual(setup.draft_path().read_bytes(), draft)
        c.post("/setup/preferences", data={"csrf": token, **GOOD})
        self.assertIn("Support Engineer", setup.draft_path().read_text(encoding="utf-8"))
        self.assertIn("Support Engineer", c.get("/setup").text)     # pre-filled
        self.assertEqual(c.post("/setup/start").status_code, 403)

    def test_adopt_creates_a_profile_when_there_is_none(self):
        c, app = self.client()
        setup.start_from_example()
        c.post("/setup/adopt", data={"csrf": app.state.csrf_token})
        self.assertEqual(self.live.read_bytes(), setup.draft_path().read_bytes())

    def test_adopt_replaces_the_unedited_example_after_a_backup(self):
        from jsa import backup
        shutil.copy(EXAMPLE, self.live)
        setup.start_from_example()
        setup.set_preferences(setup.draft_path(), setup.validate(GOOD)[0])
        c, app = self.client()
        r = c.post("/setup/adopt", data={"csrf": app.state.csrf_token}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("Support Engineer", self.live.read_text(encoding="utf-8"))
        copies = backup.listing(backup.default_root(self.dbfile))
        self.assertEqual([x.label for x in copies], ["manual"])

    def test_a_personal_profile_is_never_overwritten(self):
        self.live.write_text("identity: {full_name: Someone Real}\n", encoding="utf-8")
        before = self.live.read_bytes()
        setup.start_from_example()
        c, app = self.client()
        page = c.get("/setup").text
        self.assertNotIn("Make this my profile", page)
        self.assertIn("never overwritten", page)
        c.post("/setup/adopt", data={"csrf": app.state.csrf_token})
        with self.assertRaises(PermissionError):
            setup.adopt()
        self.assertEqual(self.live.read_bytes(), before)


class TestDiscoveryInTheBackground(Folder):
    def test_progress_one_at_a_time_and_done(self):
        release = threading.Event()
        con = db.connect(self.dbfile)
        self.addCleanup(con.close)

        def fake_run():
            con2 = db.connect(self.dbfile)
            con2.execute("INSERT INTO sources (name,kind,url,last_polled_at) VALUES "
                         "('a','lever','https://x.test', strftime('%Y-%m-%dT%H:%M:%SZ','now','+1 second'))")
            con2.commit()
            con2.close()
            release.wait(5)

        d = setup.Discovery()
        with mock.patch("jsa.config.load_sources",
                        return_value=[{"verified": True}, {"verified": True}]):
            self.assertTrue(d.start(fake_run))
            self.assertFalse(d.start(fake_run))          # a second start is refused
        for _ in range(50):
            if d.progress(con)["polled"]:
                break
            threading.Event().wait(0.05)
        self.assertEqual((d.progress(con)["polled"], d.progress(con)["total"]), (1, 2))
        release.set()
        d._thread.join(5)
        self.assertTrue(d.progress(con)["done"])


if __name__ == "__main__":
    unittest.main()


class TestReviewFixes(Folder):
    """Review R-17 (adopt re-checks before writing) and R-29."""

    def test_a_profile_saved_during_the_backup_is_never_overwritten(self):
        from jsa import backup
        shutil.copy(self.dir / "profile" / "master_profile.example.yaml", self.live)
        setup.start_from_example()
        real_make = backup.make

        def slow_make(*a, **k):
            path = real_make(*a, **k)
            self.live.write_text("identity: {full_name: Someone Real}\n", encoding="utf-8")
            return path
        with mock.patch("jsa.backup.make", side_effect=slow_make):
            with self.assertRaises(PermissionError):
                setup.adopt()
        self.assertIn("Someone Real", self.live.read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.live.parent.glob("*.tmp")], [])

    def test_a_profile_that_appears_first_is_never_replaced(self):
        setup.start_from_example()
        real = setup.can_adopt
        calls = []

        def appears(*a):
            ok = real(*a)
            if not calls:
                self.live.write_text("identity: {full_name: Someone Real}\n",
                                     encoding="utf-8")
            calls.append(ok)
            return ok
        with mock.patch("jsa.setup.can_adopt", side_effect=appears):
            with self.assertRaises(PermissionError):
                setup.adopt()
        self.assertIn("Someone Real", self.live.read_text(encoding="utf-8"))

    def test_a_byte_order_mark_does_not_duplicate_the_block(self):
        draft = setup.start_from_example()
        draft.write_bytes(b"\xef\xbb\xbf" + draft.read_bytes())
        setup.set_preferences(draft, {"target_titles": ["Analyst"]})
        text = draft.read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("\njob_search_preferences:")
                         + text.startswith("job_search_preferences:"), 1)
