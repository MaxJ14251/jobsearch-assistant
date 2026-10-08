"""The browser extension's endpoints (plan 30, ADR 0031).

Every `/ext/` route needs the pairing key; `/ext/fill` hands over only what
an application form asks for; `/ext/document` serves only a document of a
job being applied to by hand; `/ext/applied` records only on the person's
explicit confirm. Nothing here submits anything anywhere.
"""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from jsa import approvals, db, pairing
from jsa.config import EXAMPLE_PROFILE
from tests.test_web_documents import make_client

FLOOR = 91234
GREENHOUSE = "https://job-boards.greenhouse.io/riverton/jobs/4012345"
EMBED = "https://boards.greenhouse.io/embed/job_app?for=riverton&token=4012345"


def profile():
    p = yaml.safe_load(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    prefs = p["job_search_preferences"]
    prefs.update(work_authorization="Authorized to work in the US (fictional)",
                 needs_visa_sponsorship=False, willing_to_relocate=None,
                 compensation_floor_usd=FLOOR)
    p["identity"]["full_name"] = "Robin Q Example"
    return p


class Ext(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db, self.out = self.dir / "t.db", self.dir / "output"
        self.out.mkdir()
        db.init_db(self.db)
        con = db.connect(self.db)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Riverton Grid','riverton')")
        src = db.upsert_source(con, name="riverton-greenhouse", kind="greenhouse",
                               url="https://boards-api.greenhouse.io/v1/boards/riverton/jobs",
                               company_id=1)
        con.execute("INSERT INTO jobs (id,company_id,source_id,external_id,title,url,"
                    "description,track) VALUES (1,1,?,'4012345','Support Engineer',?,?,"
                    "'engineering')", (src, GREENHOUSE, "Help customers. " * 80))
        con.execute("INSERT INTO jobs (id,company_id,source_id,external_id,title,url) "
                    "VALUES (2,1,?,'999','Not Saved',"
                    "'https://job-boards.greenhouse.io/riverton/jobs/999')", (src,))
        self.app_id, _ = approvals.save_application(con, 1)
        con.commit()
        self.con = con
        self.addCleanup(con.close)
        self.profile = profile()
        self.client, self.app = make_client(self.db, self.out, profile=self.profile)
        self.key = pairing.connect(con)
        con.commit()

    def doc(self, kind="resume", approve=True, job_id=1):
        version = self.con.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM documents WHERE job_id=? AND kind=?",
            (job_id, kind)).fetchone()[0]
        path = self.out / f"{kind}-{job_id}-v{version}.docx"
        path.write_bytes(b"PK\x03\x04 fictional")
        doc = int(self.con.execute(
            "INSERT INTO documents (job_id,kind,path,version,bullet_ids) VALUES (?,?,?,?,'[]')",
            (job_id, kind, str(path), version)).lastrowid)
        approval = approvals.queue(self.con, "document", doc, f"{kind} v{version}")
        if approve:
            approvals.approve(self.con, approval)
        self.con.commit()
        return doc

    def get(self, path, key=True, **params):
        headers = {pairing.HEADER: self.key} if key is True else (
            {pairing.HEADER: key} if key else {})
        return self.client.get(path, params=params, headers=headers)

    def post(self, url, data, key=True):
        headers = {pairing.HEADER: self.key} if key else {}
        return self.client.post(url, data=data, headers=headers)


class TestTheKey(Ext):
    def test_every_ext_route_needs_the_key(self):
        doc = self.doc()
        for url in ("/ext/ping", "/ext/fill", f"/ext/document/{doc}"):
            for key in (False, "wrong-key", ""):
                with self.subTest(url=url, key=key):
                    self.assertEqual(self.get(url, key=key, url=GREENHOUSE).status_code, 401)
        # A web page's form post: the page token, no key.
        r = self.client.post("/ext/applied", data={
            "csrf": self.app.state.csrf_token, "job_id": 1, "confirm": "submitted"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.get("/ext/ping").json()["ok"], True)

    def test_with_nothing_paired_nothing_passes(self):
        pairing.disconnect(self.con)
        self.con.commit()
        self.assertEqual(self.get("/ext/ping").status_code, 401)

    def test_a_new_key_retires_the_old_one(self):
        old = self.key
        self.key = pairing.connect(self.con)
        self.con.commit()
        self.assertEqual(self.get("/ext/ping", key=old).status_code, 401)
        self.assertEqual(self.get("/ext/ping").status_code, 200)

    def test_only_a_hash_is_stored(self):
        stored = [r["value"] for r in self.con.execute("SELECT value FROM settings")]
        self.assertNotIn(self.key, stored)

    def test_the_host_check_and_size_limit_still_apply(self):
        r = self.client.get("/ext/ping", headers={pairing.HEADER: self.key,
                                                  "host": "evil.example"})
        self.assertEqual(r.status_code, 400)
        with mock.patch("jsa.web.MAX_FORM_BYTES", 10):
            r = self.post("/ext/applied", {"job_id": 1, "confirm": "submitted",
                                           "resume": "x" * 50})
        self.assertEqual(r.status_code, 413)

    def test_the_dashboard_page_shows_a_key_once(self):
        pairing.disconnect(self.con)
        self.con.commit()
        page = self.client.get("/extension").text
        self.assertIn("Not connected", page)
        r = self.client.post("/extension/connect", data={"csrf": self.app.state.csrf_token})
        key = r.text.split('id="ext-key" class="keybox" value="')[1].split('"')[0]
        self.assertTrue(pairing.check(self.con, key))
        self.assertNotIn(key, self.client.get("/extension").text)
        self.client.post("/extension/disconnect", data={"csrf": self.app.state.csrf_token})
        self.assertFalse(pairing.check(self.con, key))

    def test_pairing_needs_the_page_token(self):
        self.assertEqual(self.client.post("/extension/connect", data={}).status_code, 403)


class TestFill(Ext):
    def test_ready_with_what_a_form_asks_for(self):
        resume, cover = self.doc(), self.doc("cover_letter")
        body = self.get("/ext/fill", url=GREENHOUSE).json()
        self.assertEqual(body["state"], "ready")
        self.assertEqual(body["job"], {"id": 1, "title": "Support Engineer",
                                       "company": "Riverton Grid"})
        ident = body["identity"]
        self.assertEqual((ident["first_name"], ident["last_name"]), ("Robin Q", "Example"))
        self.assertEqual(ident["email"], "you@example.com")
        self.assertEqual(ident["links"]["linkedin"], self.profile["links"]["linkedin"])
        self.assertEqual(body["facts"]["sponsorship"], "No")
        self.assertEqual(body["facts"]["work_authorization"],
                         "Authorized to work in the US (fictional)")
        self.assertEqual(body["documents"]["resume"]["id"], resume)
        self.assertTrue(body["documents"]["resume"]["approved"])
        self.assertEqual(body["documents"]["cover_letter"]["id"], cover)
        self.assertEqual(body["warnings"], [])

    def test_the_greenhouse_embed_finds_the_same_job(self):
        self.assertEqual(self.get("/ext/fill", url=EMBED).json()["job"]["id"], 1)

    def test_never_the_floor_the_street_or_notes(self):
        self.doc()
        self.con.execute("UPDATE applications SET notes = 'private note zzq' WHERE id = ?",
                         (self.app_id,))
        self.con.commit()
        text = self.get("/ext/fill", url=GREENHOUSE).text
        for private in (str(FLOOR), "91,234", "123 Example St", "00000", "private note zzq"):
            self.assertNotIn(private, text)
        body = json.loads(text)
        self.assertNotIn("salary", body["facts"])

    def test_undecided_facts_are_left_out_with_their_note(self):
        body = self.get("/ext/fill", url=GREENHOUSE).json()
        self.assertNotIn("relocation", body["facts"])
        self.assertIn("relocation", [x["key"] for x in body["left_out"]])
        self.assertNotIn("authorized_to_work_us", body["facts"])

    def test_an_explicit_yes_no_authorization_is_passed_on(self):
        self.profile["job_search_preferences"]["authorized_to_work_us"] = True
        self.assertEqual(self.get("/ext/fill", url=GREENHOUSE).json()
                         ["facts"]["authorized_to_work_us"], "Yes")

    def test_warnings_for_an_unapproved_or_superseded_resume(self):
        self.doc(approve=False)
        body = self.get("/ext/fill", url=GREENHOUSE).json()
        self.assertFalse(body["documents"]["resume"]["approved"])
        self.assertTrue(any("No approved resume" in w for w in body["warnings"]))
        approved = self.doc()
        self.doc(approve=False)
        body = self.get("/ext/fill", url=GREENHOUSE).json()
        self.assertEqual(body["documents"]["resume"]["id"], approved)
        self.assertTrue(any("newer resume draft" in w for w in body["warnings"]))

    def test_unknown_not_saved_past_and_unsupported(self):
        self.assertEqual(self.get("/ext/fill", url="https://job-boards.greenhouse.io/"
                                  "riverton/jobs/777").json()["state"], "unknown")
        self.assertEqual(self.get("/ext/fill", url="https://job-boards.greenhouse.io/"
                                  "riverton/jobs/999").json()["state"], "not_saved")
        self.assertEqual(self.get("/ext/fill", url="https://www.linkedin.com/jobs/view/1")
                         .json()["state"], "unsupported")
        approvals.mark_applied(self.con, 1, via="employer")
        self.con.commit()
        self.assertEqual(self.get("/ext/fill", url=GREENHOUSE).json()["state"], "past")

    def test_fill_writes_nothing(self):
        self.doc()
        before = self.con.total_changes
        self.get("/ext/fill", url=GREENHOUSE)
        status = self.con.execute("SELECT status FROM applications WHERE id = ?",
                                  (self.app_id,)).fetchone()["status"]
        self.assertEqual(status, "saved")
        self.assertEqual(self.con.total_changes, before)


class TestDocument(Ext):
    def test_a_document_of_this_application(self):
        doc = self.doc()
        r = self.get(f"/ext/document/{doc}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"PK\x03\x04 fictional")

    def test_not_another_jobs_or_a_finished_application(self):
        other = self.doc(job_id=2)              # job 2 has no application
        self.assertEqual(self.get(f"/ext/document/{other}").status_code, 404)
        doc = self.doc()
        approvals.mark_applied(self.con, 1, via="employer")
        self.con.commit()
        self.assertEqual(self.get(f"/ext/document/{doc}").status_code, 404)

    def test_not_a_path_outside_output(self):
        outside = self.dir / "elsewhere.docx"
        outside.write_bytes(b"PK\x03\x04")
        doc = int(self.con.execute(
            "INSERT INTO documents (job_id,kind,path,version,bullet_ids) "
            "VALUES (1,'resume',?,9,'[]')", (str(outside),)).lastrowid)
        self.con.commit()
        self.assertEqual(self.get(f"/ext/document/{doc}").status_code, 404)


class TestApplied(Ext):
    def status(self):
        return self.con.execute("SELECT status, applied_via FROM applications WHERE id = ?",
                                (self.app_id,)).fetchone()

    def test_nothing_without_the_explicit_confirm(self):
        resume = self.doc()
        for confirm in ("", "yes", "1"):
            r = self.post("/ext/applied", {"job_id": 1, "resume": resume, "confirm": confirm})
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.status()["status"], "saved")

    def test_recorded_on_the_employers_site_by_the_person(self):
        resume, cover = self.doc(), self.doc("cover_letter")
        r = self.post("/ext/applied", {"job_id": 1, "resume": resume, "cover": cover,
                                       "confirm": "submitted"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(tuple(self.status()), ("applied", "employer"))
        sent = approvals.submitted(self.con, self.app_id)
        self.assertEqual(sorted(s.kind for s in sent), ["cover_letter", "resume"])
        actor = self.con.execute(
            "SELECT actor FROM application_events WHERE application_id = ? "
            "AND to_status = 'applied'", (self.app_id,)).fetchone()["actor"]
        self.assertEqual(actor, "human")

    def test_an_unapproved_resume_needs_a_second_confirm(self):
        resume = self.doc(approve=False)
        r = self.post("/ext/applied", {"job_id": 1, "resume": resume, "confirm": "submitted"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["needs"], "confirm_unapproved")
        self.assertEqual(self.status()["status"], "saved")
        r = self.post("/ext/applied", {"job_id": 1, "resume": resume, "confirm": "submitted",
                                       "confirm_unapproved": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.status()["status"], "applied")

    def test_json_is_refused(self):
        r = self.client.post("/ext/applied", json={"job_id": 1, "confirm": "submitted"},
                             headers={pairing.HEADER: self.key})
        self.assertEqual(r.status_code, 415)
        self.assertEqual(self.status()["status"], "saved")


class TestJobForLink(Ext):
    def test_kind_and_id_case_insensitively(self):
        from jsa import intake
        link = intake.parse_link(GREENHOUSE)
        self.assertEqual(db.job_for_link(self.con, link), 1)
        lever = intake.parse_link("https://jobs.lever.co/riverton/"
                                  "0A1B2C3D-0000-4000-8000-000000000001/apply")
        self.assertIsNone(db.job_for_link(self.con, lever))
        src = db.upsert_source(self.con, name="riverton-lever", kind="lever",
                               url="https://api.lever.co/v0/postings/riverton", company_id=1)
        self.con.execute("INSERT INTO jobs (id,company_id,source_id,external_id,title,url) "
                         "VALUES (3,1,?,'0a1b2c3d-0000-4000-8000-000000000001','X','u')", (src,))
        self.assertEqual(db.job_for_link(self.con, lever), 3)


if __name__ == "__main__":
    unittest.main()
