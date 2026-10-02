"""The dashboard's resume import (Plan 9): /import.

The model is mocked; the resume is the fictional one from
tests/test_resume_import.py. The promises under test: the upload is
token-checked and size-capped before it is read, only a real .docx is
accepted, the live profile is never written, and the file is not kept.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from jsa import db, web
from jsa.llm import Usage
from tests.test_resume_import import build_resume, model_reply

BASE = "http://127.0.0.1:8765"


class ImportBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        con = db.connect(self.dbfile)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute(
            "INSERT INTO jobs (id,company_id,title,url,description,location,remote) "
            "VALUES (5,1,'Support Engineer','https://acme.test/5',"
            "'Python and SQL support for customers.','Remote','remote')")
        con.commit()
        con.close()
        self.profile_dir = self.tmp / "profile"
        self.profile_dir.mkdir()
        self.live = self.profile_dir / "master_profile.yaml"
        self.live.write_bytes(b"identity: {full_name: Someone Else}\n")
        self.draft = self.profile_dir / "master_profile.draft.yaml"
        self.resume = build_resume(self.tmp / "resume.docx").read_bytes()
        for patch in (
            mock.patch("jsa.config.PROFILE_PATH", self.live),
            mock.patch("jsa.resume_import.llm.complete_json",
                       return_value=(model_reply(), Usage())),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.app = web.create_app(db_path=self.dbfile, output_dir=self.tmp / "out",
                                  profile_loader=lambda: {"identity": {}})
        self.client = TestClient(self.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.csrf = self.app.state.csrf_token

    def upload(self, data=None, name="resume.docx", csrf=None, **extra):
        form = {"csrf": self.csrf if csrf is None else csrf, **extra}
        return self.client.post(
            "/import", data=form,
            files={"resume": (name, self.resume if data is None else data,
                              "application/octet-stream")})


class TestGuard(ImportBase):
    def test_a_multipart_post_with_the_token_passes(self):
        r = self.upload()
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("Your draft profile", r.text)

    def test_a_wrong_or_missing_token_is_refused(self):
        for token in ("wrong", ""):
            with mock.patch("jsa.resume_import.run") as run:
                r = self.upload(csrf=token)
            self.assertEqual(r.status_code, 403)
            run.assert_not_called()
        self.assertFalse(self.draft.exists())

    def test_an_oversized_upload_is_refused_before_the_route_runs(self):
        with mock.patch("jsa.web.MAX_UPLOAD_BYTES", 100), \
             mock.patch("jsa.resume_import.run") as run:
            r = self.upload()
        self.assertEqual(r.status_code, 413)
        run.assert_not_called()

    def test_an_unsized_upload_is_refused(self):
        def chunks():
            yield b"--x\r\n"
        with mock.patch("jsa.resume_import.run") as run:
            r = self.client.post(
                "/import", content=chunks(),
                headers={"content-type": "multipart/form-data; boundary=x"})
        self.assertEqual(r.status_code, 413)
        run.assert_not_called()

    def test_another_content_type_is_refused(self):
        r = self.client.post("/import", content=f"csrf={self.csrf}",
                             headers={"content-type": "text/plain"})
        self.assertEqual(r.status_code, 403)

    def test_a_wrong_host_is_refused(self):
        r = self.client.post("/import", data={"csrf": self.csrf},
                             headers={"host": "evil.example"})
        self.assertEqual(r.status_code, 400)


class TestImportRoute(ImportBase):
    def test_the_upload_writes_the_draft_and_never_the_live_profile(self):
        before = self.live.read_bytes()
        r = self.upload()
        self.assertEqual(self.live.read_bytes(), before)
        self.assertTrue(self.draft.exists())
        self.assertIn("Northwind Supply", self.draft.read_text(encoding="utf-8"))
        # The result page: what was left out, what to fill in, the matches.
        self.assertIn("Left out", r.text)
        self.assertIn("not found word for word", r.text)
        self.assertIn("work_authorization is unanswered", r.text)
        self.assertIn('href="/job/5"', r.text)

    def test_a_renamed_text_file_is_refused(self):
        r = self.upload(data=b"just some text, not a Word file")
        self.assertEqual(r.status_code, 200)
        self.assertIn("is not one inside", r.text)
        self.assertFalse(self.draft.exists())

    def test_another_extension_is_refused(self):
        r = self.upload(name="resume.txt")
        self.assertIn("is not a .docx file", r.text)
        self.assertFalse(self.draft.exists())

    def test_the_uploaded_file_is_not_kept(self):
        spool = self.tmp / "spool"
        spool.mkdir()
        with mock.patch("tempfile.tempdir", str(spool)):
            self.upload()
        self.assertEqual(list(spool.iterdir()), [])

    def test_replace_controls_overwriting_the_draft(self):
        self.draft.write_text("mine\n", encoding="utf-8")
        r = self.upload()
        self.assertIn("already exists", r.text)
        self.assertEqual(self.draft.read_text(encoding="utf-8"), "mine\n")
        r = self.upload(replace="1")
        self.assertIn("Your draft profile", r.text)
        self.assertNotEqual(self.draft.read_text(encoding="utf-8"), "mine\n")

    def test_a_missing_key_comes_back_as_a_message(self):
        from jsa.llm import LLMError
        with mock.patch("jsa.resume_import.llm.complete_json",
                        side_effect=LLMError("no API key set")):
            r = self.upload()
        self.assertIn("no API key set", r.text)
        self.assertFalse(self.draft.exists())

    def test_the_page_offers_the_drop_zone_and_what_leaves(self):
        r = self.client.get("/import")
        self.assertIn('type="file" id="f-resume" name="resume" accept=".docx"', r.text)
        self.assertIn('enctype="multipart/form-data"', r.text)
        self.assertIn("name and contact details removed", r.text)


class TestEntryPoints(ImportBase):
    def test_matches_points_a_newcomer_to_import_only_without_a_profile(self):
        no_profile = web.create_app(db_path=self.dbfile, profile_loader=lambda: None)
        with TestClient(no_profile, base_url=BASE) as c:
            self.assertIn('href="/import"', c.get("/").text)
        self.assertNotIn("No profile yet", self.client.get("/").text)

    def test_the_add_page_links_to_import(self):
        self.assertIn('href="/import"', self.client.get("/add").text)

    def test_every_existing_form_post_still_passes_the_guard(self):
        # With no profile the route itself sends you back: past the guard.
        app = web.create_app(db_path=self.dbfile, profile_loader=lambda: None)
        with TestClient(app, base_url=BASE) as c:
            r = c.post("/add/link", data={"csrf": app.state.csrf_token,
                                          "url": "https://example.com/x"},
                       follow_redirects=False)
        self.assertEqual(r.status_code, 303)


if __name__ == "__main__":
    unittest.main()
