"""Documents, prep and tailoring on the dashboard.

Every test here runs against a throwaway database and output directory. The
real tracker is only ever read (scenario a, b), never written.
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import db
from jsa.config import DB_PATH, ROOT

BASE = "http://127.0.0.1:8765"


def make_client(db_path=None, output_dir=None, profile=None):
    from fastapi.testclient import TestClient
    from jsa import web
    app = web.create_app(db_path=db_path, output_dir=output_dir,
                         profile_loader=(lambda: profile) if profile else None)
    return TestClient(app, base_url=BASE), app


class Sandbox:
    """A fresh tracker with one company, one saved job and one document."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "t.db"
        self.out = self.dir / "output"
        self.out.mkdir()
        db.init_db(self.db)
        con = self.connect()
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute("INSERT INTO jobs (id,company_id,title,url,description,track) "
                    "VALUES (1,1,'Support Engineer','https://x/1','Help customers.','engineering')")
        con.execute("INSERT INTO jobs (id,company_id,title,url) "
                    "VALUES (2,1,'Nothing Yet','https://x/2')")
        con.execute("INSERT INTO applications (id,job_id,status) VALUES (1,1,'ready')")
        con.commit()
        con.close()

    def connect(self):
        return db.connect(self.db)

    def add_document(self, path, version=1):
        con = self.connect()
        cur = con.execute(
            "INSERT INTO documents (job_id,kind,path,version,bullet_ids,"
            "keywords_missing,model) VALUES (1,'resume',?,?,'[]','[\"C++\"]','test/model')",
            (str(path), version))
        con.commit()
        doc_id = cur.lastrowid
        con.close()
        return doc_id

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestDocumentTraversal(unittest.TestCase):
    """Scenario c. A path read from the database is untrusted input.

    Written before the guard existed and run against a route that served
    whatever path the row held: 7 of these 8 failed. It served .env, the real
    profile, the example profile via output/../, a file in output-private/,
    and a .txt inside output/. The last test passed then and passes now -- it
    is there so a guard that refuses everything cannot pass.
    """

    def setUp(self):
        self.box = Sandbox()
        self.client, _ = make_client(self.box.db, self.box.out)

    def tearDown(self):
        self.box.cleanup()

    def assert_refused(self, stored_path, target):
        doc_id = self.box.add_document(stored_path)
        r = self.client.get(f"/document/{doc_id}")
        self.assertEqual(r.status_code, 404, f"served {stored_path!r}")
        if target.exists():
            # Compared, never printed: the target may hold real secrets.
            self.assertFalse(target.read_bytes()[:64] in r.content,
                             "response contains the target file's bytes")

    def test_profile_outside_output_is_refused(self):
        target = ROOT / "profile" / "master_profile.example.yaml"
        self.assert_refused(target, target)

    def test_real_profile_is_refused(self):
        target = ROOT / "profile" / "master_profile.yaml"
        self.assert_refused(target, target)

    def test_env_is_refused(self):
        self.assert_refused(ROOT / ".env", ROOT / ".env")

    def test_dotdot_out_of_output_is_refused(self):
        target = ROOT / "profile" / "master_profile.example.yaml"
        import os
        # output/../../..<to the repo>: starts inside output/, resolves outside.
        sneaky = str(self.box.out) + os.sep + os.path.relpath(target, self.box.out)
        self.assertIn("..", sneaky)
        self.assert_refused(sneaky, target)

    def test_relative_traversal_is_refused(self):
        target = ROOT / "profile" / "master_profile.example.yaml"
        self.assert_refused("output/../profile/master_profile.example.yaml", target)

    def test_a_sibling_directory_sharing_the_prefix_is_refused(self):
        """output-private/ starts with "output" but is not inside it."""
        sibling = self.box.dir / "output-private"
        sibling.mkdir()
        target = sibling / "resume.docx"
        target.write_bytes(b"PK-not-yours")
        self.assert_refused(target, target)

    def test_a_non_docx_inside_output_is_refused(self):
        target = self.box.out / "notes.txt"
        target.write_bytes(b"inside but not a document")
        self.assert_refused(target, target)

    def test_a_real_document_inside_output_is_served(self):
        """The guard must not simply refuse everything."""
        target = self.box.out / "acme" / "resume.docx"
        target.parent.mkdir()
        target.write_bytes(b"PK\x03\x04 fake docx")
        doc_id = self.box.add_document(target)
        r = self.client.get(f"/document/{doc_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, target.read_bytes())



# --- scenarios against a sandbox ---------------------------------------------

def tailor_profile():
    from tests.test_tailor_cmd import PROFILE
    return PROFILE


def render_real_docx(box, bullet_texts, summary=None):
    """A real .docx made by the real renderer, from the sandbox profile."""
    from jsa import render
    from jsa.tailor import DraftBullet, TailoredDraft
    profile = tailor_profile()
    if summary is None:
        summary = " ".join(profile["summaries"][0]["text"].split())
    draft = TailoredDraft(
        job_id=1, summary_id="s", summary=summary,
        bullets=[DraftBullet(source_id=i, text=t) for i, t in bullet_texts],
        model="test/model")
    out = box.out / "acme" / "support-engineer" / f"resume-{len(list(box.out.rglob('*.docx')))}.docx"
    render.render_resume(draft, profile, {"title": "Support Engineer"}, out)
    return out


def queue_document(box, path, bullet_ids, missing=("C++",)):
    import json
    from jsa import approvals
    con = box.connect()
    version = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM documents "
                          "WHERE job_id=1").fetchone()[0]
    cur = con.execute(
        "INSERT INTO documents (job_id,kind,path,version,bullet_ids,keywords_missing,model) "
        "VALUES (1,'resume',?,?,?,?,'test/model')",
        (str(path), version, json.dumps(bullet_ids), json.dumps(list(missing))))
    doc_id = cur.lastrowid
    approval_id = approvals.queue(con, "document", doc_id, f"resume v{version}")
    con.commit()
    con.close()
    return doc_id, approval_id


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)
        self.client, self.app = make_client(self.box.db, self.box.out,
                                            profile=tailor_profile())
        self.csrf = self.app.state.csrf_token

    def post(self, url, **data):
        return self.client.post(url, data={"csrf": self.csrf, **data},
                                follow_redirects=False)

    def some_bullet(self):
        from jsa.tailor import collect_bullets
        bullet = next(iter(collect_bullets(tailor_profile()).values()))
        return bullet.id, bullet.text


class TestJobPageSandbox(SandboxCase):
    def test_h_a_job_with_no_documents_renders(self):
        r = self.client.get("/job/2")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No documents drafted for this job yet", r.text)
        self.assertIn("Save as application", r.text)
        self.assertIn("No interview prep yet", r.text)

    def test_versions_are_listed_newest_first_with_status(self):
        bid, text = self.some_bullet()
        first = render_real_docx(self.box, [(bid, text)])
        second = render_real_docx(self.box, [(bid, text)])
        d1, a1 = queue_document(self.box, first, [bid])
        d2, _ = queue_document(self.box, second, [bid])
        from jsa import approvals
        con = self.box.connect()
        approvals.reject(con, a1, "too short")
        con.commit()
        con.close()
        page = self.client.get("/job/1").text
        self.assertLess(page.index(f'id="doc-{d2}"'), page.index(f'id="doc-{d1}"'))
        self.assertIn("rejected", page)
        self.assertIn("too short", page)
        self.assertIn("gap: C++", page)
        self.assertIn(bid, page)
        self.assertIn(text[:30], page, "the bullet id did not resolve to its text")

    def test_pipeline_links_to_the_job_page(self):
        """The pipeline once linked every row to "/job/": v_pipeline has no
        job_id. Found by clicking through in a browser."""
        page = self.client.get("/pipeline").text
        self.assertIn('href="/job/1"', page)
        self.assertNotIn('href="/job/"', page)

    def test_d_missing_ids_are_404_not_500(self):
        for url in ("/document/999", "/prep/999", "/job/999"):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 404)
                self.assertNotIn("Traceback", r.text)

    def test_a_document_whose_file_vanished_does_not_break_the_page(self):
        doc_id, _ = queue_document(self.box, self.box.out / "gone.docx", [])
        self.assertEqual(self.client.get("/job/1").status_code, 200)
        self.assertIn("missing from output/", self.client.get("/review").text)
        self.assertEqual(self.client.get(f"/document/{doc_id}").status_code, 404)

    def test_save_then_tailor_from_the_page(self):
        """The Tailor control runs the same drafting path as `jsa tailor`."""
        from tests.test_tailor_cmd import fake_completion, some_bullet_ids
        r = self.post("/job/2/save")
        self.assertEqual(r.status_code, 303)
        con = self.box.connect()
        event = con.execute("SELECT actor, to_status FROM application_events "
                            "WHERE application_id = (SELECT id FROM applications "
                            "WHERE job_id = 2)").fetchone()
        con.close()
        self.assertEqual(tuple(event), ("human", "saved"))

        with mock.patch("jsa.tailor.llm.complete_json",
                        return_value=fake_completion(some_bullet_ids(tailor_profile()))), \
             mock.patch("jsa.render.OUTPUT_DIR", self.box.out):
            r = self.post("/job/2/tailor", kind="resume")
        self.assertEqual(r.status_code, 303)
        page = self.client.get(r.headers["location"]).text
        self.assertIn("waiting in the review queue", page)
        con = self.box.connect()
        doc = con.execute("SELECT id, path FROM documents WHERE job_id = 2").fetchone()
        pending = con.execute("SELECT decision FROM approvals WHERE subject_type="
                              "'document' AND subject_id = ?", (doc["id"],)).fetchone()
        con.close()
        self.assertEqual(pending["decision"], "pending", "tailoring decided something")
        self.assertEqual(self.client.get(f"/document/{doc['id']}").status_code, 200)

    def test_tailor_without_a_saved_application_explains(self):
        r = self.post("/job/2/tailor", kind="resume")
        page = self.client.get(r.headers["location"]).text
        self.assertIn("has not been saved", page)

    def test_tailor_rejects_an_unknown_kind(self):
        r = self.post("/job/1/tailor", kind="../../etc")
        self.assertIn("Unknown document kind", self.client.get(r.headers["location"]).text)

    def test_a_guard_firing_is_reported_as_a_refusal(self):
        from tests.test_tailor_cmd import fake_completion
        with mock.patch("jsa.tailor.llm.complete_json",
                        return_value=fake_completion(["b_invented"])), \
             mock.patch("jsa.render.OUTPUT_DIR", self.box.out):
            r = self.post("/job/1/tailor", kind="resume")
        page = self.client.get(r.headers["location"]).text
        self.assertIn("Refused by a safety check", page)


class TestPrepPage(SandboxCase):
    def test_prep_renders_question_why_and_notes(self):
        import json
        con = self.box.connect()
        con.execute(
            "INSERT INTO interview_prep (id,application_id,round,questions,company_brief) "
            "VALUES (1,1,'phone_screen',?,?)",
            (json.dumps([{"question": "Walk me through the gap?",
                          "why": "Every reader notices it.",
                          "answer_notes": "I used the time to build real pipelines."},
                         "not a dict"]),
             "Acme builds <script>alert(1)</script> tools."))
        con.commit()
        con.close()
        r = self.client.get("/prep/1")
        self.assertEqual(r.status_code, 200)
        for text in ("Walk me through the gap?", "Every reader notices it.",
                     "I used the time to build real pipelines.", "Phone screen"):
            self.assertIn(text, r.text.replace("phone screen", "Phone screen"))
        self.assertNotIn("<script>alert", r.text, "prep text must be escaped")
        self.assertIn("Phone screen", self.client.get("/job/1").text)


class TestReviewShowsTheDraft(SandboxCase):
    def test_an_added_claim_is_highlighted_beside_its_source(self):
        bid, text = self.some_bullet()
        padded = text.rstrip(".") + " under strict turnaround requirements and SLA adherence."
        path = render_real_docx(self.box, [(bid, padded)])
        doc_id, _ = queue_document(self.box, path, [bid])
        page = self.client.get("/review").text
        self.assertIn(f'id="doc-{doc_id}"', page)
        self.assertIn("say something your profile does not", page)
        self.assertIn("<mark>turnaround</mark>", page)
        # Shown as written, not as the verifier's stem ("requirement").
        self.assertIn("<mark>requirements</mark>", page)
        self.assertIn(text, page, "the profile source is not shown beside it")
        self.assertIn("Read the whole draft", page)

    def test_a_faithful_draft_says_so_without_claiming_it_is_true(self):
        bid, text = self.some_bullet()
        path = render_real_docx(self.box, [(bid, text)])
        queue_document(self.box, path, [bid])
        page = self.client.get("/review").text
        self.assertIn("Every line traces back to your profile", page)
        self.assertIn("Only you know whether each one is true", page)

    def test_f_reject_with_empty_feedback_is_refused_with_instructions(self):
        bid, text = self.some_bullet()
        _, approval_id = queue_document(self.box, render_real_docx(self.box, [(bid, text)]), [bid])
        r = self.post("/reject", approval_id=approval_id, feedback="   ")
        page = self.client.get(r.headers["location"]).text
        self.assertIn("rejection requires feedback saying what to change", page)
        con = self.box.connect()
        self.assertEqual(con.execute("SELECT decision FROM approvals WHERE id=?",
                                     (approval_id,)).fetchone()[0], "pending")
        con.close()

    def test_deciding_twice_is_explained_not_crashed(self):
        bid, text = self.some_bullet()
        _, approval_id = queue_document(self.box, render_real_docx(self.box, [(bid, text)]), [bid])
        self.post("/approve", approval_id=approval_id)
        r = self.post("/approve", approval_id=approval_id)
        self.assertIn("already approved", self.client.get(r.headers["location"]).text)


class TestApproveMatchesTheCli(unittest.TestCase):
    """Scenario e. Two identical sandboxes: one decided by `jsa approve`, one by
    the dashboard. Everything but the timestamp must be identical."""

    def snapshot(self, box):
        con = box.connect()
        try:
            approval = con.execute(
                "SELECT id, subject_type, subject_id, decision, decided_by, feedback, "
                "decided_at IS NOT NULL FROM approvals").fetchall()
            events = con.execute(
                "SELECT application_id, from_status, to_status, actor, note "
                "FROM application_events ORDER BY id").fetchall()
            docs = con.execute("SELECT id, approved_at FROM documents").fetchall()
            apps = con.execute("SELECT id, status, resume_doc_id FROM applications").fetchall()
        finally:
            con.close()
        return [list(map(tuple, x)) for x in (approval, events, docs, apps)]

    def test_web_and_cli_leave_the_same_trail(self):
        from argparse import Namespace
        from jsa import cli
        boxes = []
        for _ in range(2):
            box = Sandbox()
            self.addCleanup(box.cleanup)
            path = box.out / "r.docx"
            path.write_bytes(b"PK")
            _, approval_id = queue_document(box, path, [])
            boxes.append((box, approval_id))

        (cli_box, cli_id), (web_box, web_id) = boxes
        real_connect = db.connect
        with mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real_connect(cli_box.db)):
            import io
            from contextlib import redirect_stdout
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.cmd_approve(Namespace(approval_id=cli_id, note=None)), 0)

        client, app = make_client(web_box.db, web_box.out)
        r = client.post("/approve", data={"csrf": app.state.csrf_token,
                                          "approval_id": web_id},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 303)

        cli_state, web_state = self.snapshot(cli_box), self.snapshot(web_box)
        self.assertEqual(cli_state, web_state)
        self.assertEqual(web_state[0][0][3:5], ("approved", "human"))


class TestForgedRequests(SandboxCase):
    """Loopback is not enough: the browser that visits other sites can reach it."""

    def pending(self):
        bid, text = self.some_bullet()
        return queue_document(self.box, render_real_docx(self.box, [(bid, text)]), [bid])[1]

    def decision(self, approval_id):
        con = self.box.connect()
        try:
            return con.execute("SELECT decision FROM approvals WHERE id=?",
                               (approval_id,)).fetchone()[0]
        finally:
            con.close()

    def test_a_post_without_the_token_decides_nothing(self):
        approval_id = self.pending()
        for data in ({"approval_id": approval_id},
                     {"approval_id": approval_id, "csrf": "guessed"}):
            with self.subTest(data=data):
                r = self.client.post("/approve", data=data, follow_redirects=False)
                self.assertEqual(r.status_code, 403)
        self.assertEqual(self.decision(approval_id), "pending")

    def test_every_post_route_requires_the_token(self):
        for url in ("/approve", "/reject", "/job/2/save", "/job/1/tailor"):
            with self.subTest(url=url):
                r = self.client.post(url, data={"approval_id": 1, "feedback": "x"})
                self.assertEqual(r.status_code, 403)

    def test_a_foreign_host_header_is_refused(self):
        """DNS rebinding: evil.example resolved to 127.0.0.1."""
        bid, text = self.some_bullet()
        doc_id, _ = queue_document(self.box, render_real_docx(self.box, [(bid, text)]), [bid])
        for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example",
                     "localhost.evil.example"):
            with self.subTest(host=host):
                r = self.client.get(f"/document/{doc_id}", headers={"host": host})
                self.assertEqual(r.status_code, 400)
                self.assertNotIn(b"PK", r.content)

    def test_loopback_names_are_accepted(self):
        from jsa.web import host_allowed
        for host in ("127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765"):
            self.assertTrue(host_allowed(host), host)
        for host in ("", "0.0.0.0:8765", "[::1].evil", "192.168.1.5:8765"):
            self.assertFalse(host_allowed(host), host)

    def test_forms_carry_the_token(self):
        self.pending()
        page = self.client.get("/review").text
        self.assertIn(f'name="csrf" value="{self.csrf}"', page)
        self.assertEqual(page.count('name="csrf"'), 2)

    def test_each_process_has_its_own_token(self):
        _, other = make_client(self.box.db, self.box.out)
        self.assertNotEqual(other.state.csrf_token, self.csrf)


class TestThirdPartyTextIsEscaped(SandboxCase):
    """Job descriptions come from other companies' boards.

    Autoescape was configured with select_autoescape(["html"]), which keys on
    the template NAME. The templates are named "base", "job"... so it was off,
    and a posting's HTML rendered live -- on the page that now holds the form
    token for approvals.
    """

    PAYLOAD = '<img src=x onerror="fetch(`/review`)">'

    def test_a_hostile_description_renders_as_text(self):
        con = self.box.connect()
        con.execute("UPDATE jobs SET description = ?, title = ? WHERE id = 1",
                    (self.PAYLOAD, "<b>Engineer</b>"))
        con.commit()
        con.close()
        page = self.client.get("/job/1").text
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<b>Engineer</b>", page)
        self.assertIn("&lt;img src=x", page)

    def test_the_environment_escapes_unconditionally(self):
        from jsa.web import env
        self.assertEqual(env.from_string("{{ x }}").render(x="<i>"), "&lt;i&gt;")


# --- scenarios against the real tracker, read-only -----------------------------

def real_document(job_id):
    """The newest document on disk for a job in the real tracker, or None."""
    from jsa import review
    from jsa.config import OUTPUT_DIR
    if not DB_PATH.exists():
        return None
    con = db.connect()
    try:
        rows = con.execute("SELECT * FROM documents WHERE job_id = ? "
                           "ORDER BY version DESC", (job_id,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    for row in rows:
        if review.safe_document_path(row["path"], OUTPUT_DIR):
            return row, rows
    return None


REAL_260 = real_document(260)


@unittest.skipUnless(REAL_260, "needs the author's tracker with job 260 drafted")
class TestRealTracker(unittest.TestCase):
    """Scenarios a and b. Only GET requests: nothing here writes."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from jsa import web
        cls.client = TestClient(web.create_app(), base_url=BASE)
        cls.newest, cls.rows = REAL_260

    def test_a_all_versions_newest_first_with_models_and_gap(self):
        from jsa.config import load_profile
        from jsa.tailor import collect_bullets
        page = self.client.get("/job/260").text
        positions = [page.index(f'id="doc-{r["id"]}"') for r in self.rows]
        self.assertEqual(positions, sorted(positions), "not newest first")
        self.assertEqual(len(positions), len(self.rows))
        for row in self.rows:
            self.assertIn(row["model"], page)
        self.assertIn("gap: C++", page)
        sources = collect_bullets(load_profile())
        import json
        for bullet_id in json.loads(self.newest["bullet_ids"]):
            self.assertIn(bullet_id, sources, f"{bullet_id} does not resolve")
            self.assertIn(sources[bullet_id].text[:40], page)

    def test_b_download_is_byte_identical_and_still_parses(self):
        from jsa import render, review
        from jsa.config import OUTPUT_DIR
        path = review.safe_document_path(self.newest["path"], OUTPUT_DIR)
        r = self.client.get(f"/document/{self.newest['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, path.read_bytes())
        self.assertIn("attachment", r.headers["content-disposition"])
        copy = Path(tempfile.mkdtemp()) / "downloaded.docx"
        copy.write_bytes(r.content)
        self.assertEqual(render.extract_text(copy), render.extract_text(path))
        self.assertIn("degree not conferred", render.extract_text(copy))

    def test_a_real_prep_is_readable(self):
        con = db.connect()
        try:
            prep = con.execute(
                "SELECT p.id FROM interview_prep p JOIN applications a "
                "ON a.id = p.application_id WHERE a.job_id = 260").fetchone()
        finally:
            con.close()
        if prep is None:
            self.skipTest("no prep for job 260")
        r = self.client.get(f"/prep/{prep['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Why they ask", r.text)


class TestGeneratedFilesStayUntracked(unittest.TestCase):
    """Scenario j. Asks git what it tracks, not what .gitignore says."""

    def test_output_is_not_tracked(self):
        import subprocess
        try:
            out = subprocess.run(["git", "ls-files", "-z", "--", "output", "documents"],
                                 cwd=ROOT, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        self.assertEqual(out, b"", "generated documents are tracked by git")

    def test_output_really_is_ignored(self):
        """A pattern that matches nothing would also track nothing -- so also
        ask git whether a file there WOULD be ignored."""
        import subprocess
        try:
            result = subprocess.run(
                ["git", "check-ignore", "-q", "output/any/resume.docx"],
                cwd=ROOT, capture_output=True)
        except OSError:
            self.skipTest("git unavailable")
        if result.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(result.returncode, 0, "output/ is not ignored")


if __name__ == "__main__":
    unittest.main()
