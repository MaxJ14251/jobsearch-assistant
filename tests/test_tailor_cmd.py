"""`jsa tailor` — the command that finally makes tailoring reachable.

The model is mocked here so the suite runs offline and in CI. The guards being
exercised are real: the same verify_draft, the same scrub_prompt, the same
approvals path. What is faked is only the network call.

TestTwoRolesAtOneEmployer is a regression test for a bug this command found on
its first real run: bullets were grouped by COMPANY, so two roles at the same
employer both printed the same bullets. The document looked plausible and was
wrong.
"""

import copy
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

from jsa import approvals, db, render
from jsa.config import ROOT
from jsa.tailor import TailoredDraft, collect_bullets
from tests.test_tailor import ADVERSARIAL_JD
from tests.test_tailor import PROFILE as DISK_PROFILE


def tailoring_profile() -> dict:
    """A profile these tests can tailor with, on any machine.

    tests/test_tailor.PROFILE is load_profile() -- the real profile here, the
    example on a fresh clone, where work_authorization is null and
    require_decided_preferences correctly refuses to draft. Hardcoding the
    author's bullet ids had the same problem. So: take whatever profile is on
    disk and fill in the one preference drafting needs.
    """
    profile = copy.deepcopy(DISK_PROFILE)
    prefs = profile.setdefault("job_search_preferences", {})
    if not (prefs.get("work_authorization") or "").strip():
        prefs["work_authorization"] = "US citizen - no sponsorship required"
    return profile


PROFILE = tailoring_profile()


def some_bullet_ids(profile: dict, count: int = 1) -> list[str]:
    """Real ids from whichever profile is on disk, never hardcoded."""
    return list(collect_bullets(profile))[:count]


def fake_completion(bullet_ids, profile=None, summary=None):
    """What the model would return, in the shape tailor() parses.

    The text is the SOURCE bullet lightly reworded, because that is what a
    working model produces. An earlier version of this helper invented text
    from nothing and verify_draft rejected every draft at 0% overlap -- the
    guard doing exactly its job, on the test's own fixture.
    """
    profile = profile if profile is not None else PROFILE
    sources = collect_bullets(profile)
    bullets = []
    for bullet_id in bullet_ids:
        source = sources.get(bullet_id)
        text = source.text if source else bullet_id
        bullets.append({"id": bullet_id, "text": f"{text}"})
    payload = {
        "summary": summary or (profile.get("summaries") or [{}])[0].get("text", "A summary."),
        "bullets": bullets,
    }
    usage = mock.Mock(model="test/model", prompt_tokens=1, completion_tokens=1)
    return payload, usage


def quiet(fn, *args, **kwargs):
    """Run a CLI command without its output polluting the suite.

    The command prints where it wrote the file, which is right for a human and
    noise in a 299-test run -- real failures should not have to be spotted
    among them.
    """
    with redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class TailorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbfile = self.tmp / "t.db"
        db.init_db(self.dbfile)
        con = db.connect(self.dbfile)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute(
            "INSERT INTO jobs (id,company_id,title,url,description,track) "
            "VALUES (1,1,'Support Engineer','https://acme.test/1',?,'engineering')",
            ("We want Python and customer-facing skills.",),
        )
        con.commit()
        con.close()
        # Capture the real function BEFORE patching; a side_effect that calls
        # db.connect would call the mock and recurse forever.
        real_connect = db.connect
        self._real_connect = real_connect
        self._connect = mock.patch(
            "jsa.db.connect",
            side_effect=lambda *a, **k: real_connect(self.dbfile))
        self._connect.start()
        self.addCleanup(self._connect.stop)

    def con(self):
        return self._real_connect(self.dbfile)


class TestTwoRolesAtOneEmployer(unittest.TestCase):
    """Bullets are grouped by the ENTRY, not the employer.

    Found on the first real run: a profile with 'Sales Representative — Riverton'
    and 'Installer — Riverton' printed the installer's bullets under BOTH roles,
    because the grouping key was the company name. Every bullet was traceable
    and the resume was still false about who did what.
    """

    PROFILE = {
        "identity": {"full_name": "Dana Rivers"},
        "experience": [
            {"id": "exp_a", "title": "Sales Rep", "company": "Acme",
             "start": "2022-01", "end": "2023-01",
             "bullets": [{"id": "b_sales", "text": "Sold systems to customers.",
                          "tags": ["sales"], "strength": 3}]},
            {"id": "exp_b", "title": "Installer", "company": "Acme",
             "start": "2021-01", "end": "2022-01",
             "bullets": [{"id": "b_install", "text": "Installed alarm panels.",
                          "tags": ["install"], "strength": 3}]},
        ],
    }

    def test_bullets_do_not_leak_between_roles_at_one_company(self):
        sources = collect_bullets(self.PROFILE)
        self.assertNotEqual(
            sources["b_sales"].parent, sources["b_install"].parent,
            "two roles at one employer must not share a grouping key",
        )

    def test_rendered_resume_shows_each_bullet_once(self):
        draft = TailoredDraft(
            job_id=1, summary_id="s", summary="Summary.",
            bullets=[type("B", (), {"source_id": "b_install",
                                    "text": "Installed alarm panels."})()],
            model="test/model",
        )
        out = Path(tempfile.mkdtemp()) / "r.docx"
        render.render_resume(draft, self.PROFILE, {"title": "Engineer"}, out)
        text = render.extract_text(out)
        self.assertEqual(
            text.count("Installed alarm panels."), 1,
            "the bullet appeared under more than one role",
        )
        self.assertNotIn("Sales Rep", text,
                         "a role with no selected bullets must not be printed")


class TestTailorCommand(TailorCase):
    def run_tailor(self, job_id=1, kind="resume", force=False):
        from jsa.cli import cmd_tailor
        ids = some_bullet_ids(PROFILE)
        with mock.patch("jsa.tailor.llm.complete_json",
                        return_value=fake_completion(ids)), \
             mock.patch("jsa.config.load_profile", return_value=PROFILE), \
             mock.patch("jsa.cli.load_profile", return_value=PROFILE), \
             mock.patch("jsa.render.OUTPUT_DIR", self.tmp / "output"):
            return quiet(cmd_tailor,
                         Namespace(job_id=job_id, kind=kind, force=force))

    def test_refuses_without_a_saved_application(self):
        """ADR 0003 decision 1 — and the refusal must name the command."""
        con = self.con()
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.require_application(con, 1)
        self.assertIn("jsa save 1", str(ctx.exception))
        con.close()

    def test_full_chain(self):
        con = self.con()
        approvals.save_application(con, 1)
        con.commit()
        con.close()

        self.assertEqual(self.run_tailor(), 0)

        con = self.con()
        doc = con.execute("SELECT * FROM documents").fetchone()
        self.assertIsNotNone(doc, "no documents row was written")
        for column in ("bullet_ids", "keywords_matched", "keywords_missing",
                       "model", "prompt_hash", "generated_at", "version"):
            self.assertIsNotNone(doc[column], f"{column} was left null")

        app = con.execute("SELECT * FROM applications WHERE job_id=1").fetchone()
        self.assertEqual(app["status"], "ready", "Goal 02's orphan #2")
        self.assertEqual(app["resume_doc_id"], doc["id"], "Goal 02's orphan #1")

        event = con.execute(
            "SELECT * FROM application_events WHERE to_status='ready'").fetchone()
        self.assertEqual(event["actor"], "agent", "Goal 02's orphan #3")

        appr = con.execute("SELECT * FROM approvals").fetchone()
        self.assertEqual(appr["subject_type"], "document")
        self.assertEqual(appr["subject_id"], doc["id"])
        self.assertEqual(appr["decision"], "pending")
        con.close()

    def test_bullet_ids_all_resolve_against_the_profile(self):
        """Round trip. A dangling id means provenance is decorative."""
        con = self.con()
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        self.run_tailor()
        con = self.con()
        ids = json.loads(con.execute("SELECT bullet_ids FROM documents").fetchone()[0])
        con.close()
        sources = collect_bullets(PROFILE)
        self.assertTrue(ids)
        for bullet_id in ids:
            self.assertIn(bullet_id, sources, f"{bullet_id} resolves to nothing")

    def test_second_draft_needs_force_and_then_versions(self):
        con = self.con()
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        self.assertEqual(self.run_tailor(), 0)
        self.assertEqual(self.run_tailor(), 1, "a redraft must not be implicit")
        self.assertEqual(self.run_tailor(force=True), 0)

        con = self.con()
        rows = con.execute(
            "SELECT id, version, path FROM documents ORDER BY version").fetchall()
        self.assertEqual([r["version"] for r in rows], [1, 2])
        self.assertNotEqual(rows[0]["path"], rows[1]["path"],
                            "v2 overwrote the file v1's row still points at")
        decisions = con.execute(
            "SELECT decision FROM approvals ORDER BY id").fetchall()
        self.assertEqual([d["decision"] for d in decisions], ["pending", "pending"])
        con.close()


class TestIdentityNeverLeaves(TailorCase):
    """Assert what left the machine, not that the scrubber was called."""

    def test_the_outbound_payload_carries_no_identity(self):
        from jsa.cli import cmd_tailor
        captured = {}

        def capture(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["system"] = kwargs.get("system", "")
            return fake_completion(some_bullet_ids(PROFILE))

        con = self.con()
        approvals.save_application(con, 1)
        con.commit()
        con.close()

        with mock.patch("jsa.tailor.llm.complete_json", side_effect=capture), \
             mock.patch("jsa.cli.load_profile", return_value=PROFILE), \
             mock.patch("jsa.render.OUTPUT_DIR", self.tmp / "output"):
            quiet(cmd_tailor, Namespace(job_id=1, kind="resume", force=False))

        sent = (captured["prompt"] + captured["system"]).lower()
        ident = PROFILE.get("identity") or {}
        loc = ident.get("location") or {}
        for value in (ident.get("full_name"), ident.get("email"),
                      ident.get("phone"), loc.get("street"), loc.get("postal_code")):
            if isinstance(value, str) and value.strip():
                self.assertNotIn(value.lower(), sent,
                                 f"{value!r} reached the outbound prompt")


class TestAdversarialThroughTheCommand(unittest.TestCase):
    """The fabrication guard, exercised through selection rather than trust."""

    def test_banned_terms_are_reported_as_gaps_not_claimed(self):
        from jsa import tailor
        matched, missing = tailor.keyword_gap(ADVERSARIAL_JD, PROFILE)
        self.assertTrue(missing, "an empty gap report means the check regressed")
        banned = {t.lower() for t in tailor.do_not_claim(PROFILE)}
        chosen = tailor.select_bullets(PROFILE, ADVERSARIAL_JD, "engineering")
        for bullet in chosen:
            for term in banned:
                self.assertNotIn(term, bullet.text.lower(),
                                 f"a selected bullet claims {term!r}")


class TestAtsStructure(TailorCase):
    """ATS safety is a functional requirement, not a style preference."""

    def _render(self):
        from jsa.cli import cmd_tailor
        con = self.con()
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        with mock.patch("jsa.tailor.llm.complete_json",
                        return_value=fake_completion(some_bullet_ids(PROFILE))),              mock.patch("jsa.cli.load_profile", return_value=PROFILE),              mock.patch("jsa.render.OUTPUT_DIR", self.tmp / "output"):
            quiet(cmd_tailor, Namespace(job_id=1, kind="resume", force=False))
        con = self.con()
        path = Path(con.execute("SELECT path FROM documents").fetchone()[0])
        con.close()
        return path

    def test_no_tables_images_or_header_content(self):
        from docx import Document
        doc = Document(self._render())
        self.assertEqual(len(doc.tables), 0, "tables break ATS parsers")
        images = sum(1 for r in doc.part.rels.values() if "image" in r.reltype)
        self.assertEqual(images, 0, "images are invisible to an ATS")
        header_text = "".join(
            para.text for section in doc.sections
            for para in section.header.paragraphs).strip()
        self.assertEqual(header_text, "", "header content is routinely dropped")

    def test_bullets_are_real_list_paragraphs(self):
        from docx import Document
        doc = Document(self._render())
        styles = [para.style.name for para in doc.paragraphs]
        self.assertIn("List Bullet", styles,
                      "bullets must be real list paragraphs, not typed dashes")


class TestLongPostingsAreNotSilentlyTruncated(TailorCase):
    """88% of the tracker's descriptions are longer than the model ever sees.

    Requirements stated late in a posting cannot influence the draft. That is a
    real limitation; the point of this test is that it is announced rather than
    discovered.
    """

    def test_truncation_is_reported_on_stderr(self):
        import io
        from contextlib import redirect_stderr
        from jsa.cli import cmd_tailor
        from jsa.tailor import MAX_DESCRIPTION_CHARS

        con = self.con()
        con.execute("UPDATE jobs SET description = ? WHERE id = 1",
                    ("word " * (MAX_DESCRIPTION_CHARS // 2),))
        approvals.save_application(con, 1)
        con.commit()
        con.close()

        err = io.StringIO()
        with redirect_stderr(err),              mock.patch("jsa.tailor.llm.complete_json",
                        return_value=fake_completion(some_bullet_ids(PROFILE))),              mock.patch("jsa.cli.load_profile", return_value=PROFILE),              mock.patch("jsa.render.OUTPUT_DIR", self.tmp / "output"):
            quiet(cmd_tailor, Namespace(job_id=1, kind="resume", force=False))
        self.assertIn("the model saw the first", err.getvalue())

    def test_the_limit_is_named_not_inline(self):
        """It was a bare 4000 in the middle of a format call."""
        from jsa.tailor import MAX_DESCRIPTION_CHARS
        source = (ROOT / "jsa" / "tailor.py").read_text(encoding="utf-8")
        self.assertIsInstance(MAX_DESCRIPTION_CHARS, int)
        self.assertNotIn("description[:4000]", source)


class TestOutputStaysUntracked(unittest.TestCase):
    def test_no_generated_document_is_tracked(self):
        """Checked against git, not against .gitignore.

        A pattern that matches nothing looks identical to one that works.
        """
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
        ).stdout.splitlines()
        leaked = [f for f in tracked
                  if f.startswith("output/") or f.endswith(".docx")]
        self.assertEqual(leaked, [], f"generated documents are tracked: {leaked}")


if __name__ == "__main__":
    unittest.main()
