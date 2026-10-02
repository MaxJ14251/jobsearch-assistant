"""The resume report (Plan 11 part 3, ADR 0023).

One test per rule that fires and one that doesn't. Fiction only: the
Riverton example, and a nurse for the non-tech word lists.
"""

import ast
import copy
import datetime as dt
import io
import json
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from jsa import coach
from jsa.config import ROOT, load_coach
from jsa.tailor import DraftBullet, TailoredDraft
from tests.test_resume_format import example

TODAY = dt.date(2026, 10, 2)
CFG = load_coach()


def ids(profile, draft=None, today=TODAY):
    return [f.id for f in coach.review(profile, draft, today=today, config=CFG)]


def only(rule, profile, draft=None, today=TODAY):
    return [f for f in coach.review(profile, draft, today=today, config=CFG)
            if f.id == rule]


def nurse():
    return {
        "summaries": [{"id": "sum_rn", "family": "general",
                       "text": "Results-driven nurse passionate about patient care."}],
        "experience": [{
            "id": "exp_ward", "company": "Lakeside General", "title": "Staff Nurse",
            "start": "2019-05", "current": True,
            "bullets": [
                {"id": "b1", "text": "Cared for 6 patients per shift on a 30-bed surgical ward."},
                {"id": "b2", "text": "Trained 4 new graduate nurses on the charting system."}]}],
        "skills": {"clinical": ["IV therapy", "EHR charting"],
                   "personal": ["Compassion", "Teamwork"]},
        "certifications": [{"name": "BLS", "issuer": "Example Heart Association"}],
    }


class TestRules(unittest.TestCase):
    def test_thin_entry(self):
        profile = example()
        profile["experience"][1]["bullets"] = profile["experience"][1]["bullets"][:1]
        found = only("thin_entry", profile)
        self.assertEqual([f.where for f in found], ["experience[1].bullets"])
        self.assertEqual(only("thin_entry", example()), [])

    def test_thin_entry_looks_at_what_the_draft_shows(self):
        draft = TailoredDraft(bullets=[DraftBullet("b_riv_promo", "x")])
        found = only("thin_entry", example(), draft)
        self.assertEqual([f.where for f in found], ["experience[0].bullets"])

    def test_no_numbers(self):
        found = only("no_numbers", example())
        self.assertIn("3 of 3 bullets", found[0].message)
        self.assertEqual(only("no_numbers", nurse()), [])

    def test_unexplained_gap(self):
        profile = example()
        profile["certifications"] = []
        found = only("unexplained_gap", profile)
        self.assertEqual(len(found), 1)
        self.assertIn("Nov 2023 to now", found[0].message)
        self.assertIn("only if true", found[0].message)

    def test_a_certification_or_dated_project_covers_a_gap(self):
        self.assertEqual(only("unexplained_gap", example()), [])  # certs in 2025
        profile = example()
        profile["certifications"] = []
        profile["projects"][0].update(start="2023-12", end=None)
        self.assertEqual(only("unexplained_gap", profile), [])

    def test_a_short_gap_or_a_current_job_is_fine(self):
        self.assertEqual(only("unexplained_gap", nurse()), [])
        profile = example()
        profile["certifications"] = []
        profile["coach"] = {"gap_months": 60}
        self.assertEqual(only("unexplained_gap", profile), [])

    def test_gaps_between_jobs_and_bare_years(self):
        profile = {"experience": [
            {"company": "A", "title": "x", "start": 2015, "end": 2016},
            {"company": "B", "title": "y", "start": "2018-02", "current": True}]}
        found = only("unexplained_gap", profile)
        self.assertEqual(len(found), 1)
        self.assertIn("Jan 2017 to Jan 2018", found[0].message)

    def test_in_dev_only(self):
        found = only("in_dev_only", example())
        self.assertEqual({f.where for f in found},
                         {"projects[0].bullets", "projects[1].bullets"})
        profile = example()
        profile["projects"][0]["bullets"].append(
            {"id": "b_done", "text": "Drafts a first reply for 9 of 10 sample tickets."})
        self.assertNotIn("projects[0].bullets",
                         {f.where for f in only("in_dev_only", profile)})

    def test_soft_skill(self):
        found = only("soft_skill", nurse())
        self.assertEqual(sorted(f.message.split('"')[1] for f in found),
                         ["Compassion", "Teamwork"])
        profile = example()
        profile["skills"]["applied_focus"] = ["Data Analysis"]
        self.assertEqual(only("soft_skill", profile), [])

    def test_soft_skill_ignores_unverified_candidates(self):
        profile = example()
        profile["skills"] = {"unverified_candidates": ["Teamwork"]}
        self.assertEqual(only("soft_skill", profile), [])

    def test_vague_summary(self):
        found = only("vague_summary", nurse())
        self.assertIn("'results-driven'", found[0].message)
        self.assertIn("'passionate about'", found[0].message)
        self.assertEqual(only("vague_summary", example()), [])

    def test_vague_summary_reads_the_drafted_summary(self):
        draft = TailoredDraft(summary_id="sum_general", summary="A results-driven builder.")
        self.assertEqual(len(only("vague_summary", example(), draft)), 1)

    def test_opaque_cert(self):
        profile = example()
        profile["certifications"].append({"name": "Example ME-AGS Course", "issuer": "X"})
        found = only("opaque_cert", profile)
        self.assertEqual([f.where for f in found], ["certifications[2]"])
        self.assertEqual(only("opaque_cert", nurse()), [])   # BLS: an acronym, no code
        profile["certifications"][2]["display_name"] = "Applied Generative AI course"
        self.assertEqual(only("opaque_cert", profile), [])

    def test_missing_skill_evidence(self):
        found = only("missing_skill_evidence", example())
        self.assertTrue(any("LLM APIs" in f.message for f in found))
        profile = nurse()
        self.assertEqual(only("missing_skill_evidence", profile), [])

    def test_a_rule_can_be_turned_off(self):
        profile = example()
        profile["coach"] = {"disabled": ["no_numbers", "in_dev_only"]}
        self.assertNotIn("no_numbers", ids(profile))
        self.assertNotIn("in_dev_only", ids(profile))

    def test_messages_never_supply_a_claim(self):
        for f in coach.review(example(), today=TODAY, config=CFG) + \
                 coach.review(nurse(), today=TODAY, config=CFG):
            self.assertNotRegex(f.message, r"(?i)\b(write|say) that you\b")


class TestNoModel(unittest.TestCase):
    def test_coach_imports_no_model(self):
        tree = ast.parse((ROOT / "jsa" / "coach.py").read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [f"{n.module}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                  for a in n.names]
        self.assertFalse([n for n in names if n and "llm" in n], names)


class TestWhereItShows(unittest.TestCase):
    def test_jsa_coach_and_strict(self):
        from jsa import cli
        out = io.StringIO()
        with mock.patch("jsa.cli.load_profile", return_value=example()), \
             redirect_stdout(out):
            self.assertEqual(cli.cmd_coach(Namespace(strict=False)), 0)
            self.assertEqual(cli.cmd_coach(Namespace(strict=True)), 1)
        self.assertIn("resume report", out.getvalue())
        self.assertIn("no_numbers", out.getvalue())



from tests.test_tailor_cmd import PROFILE, TailorCase, fake_completion, some_bullet_ids  # noqa: E402


class TestDraftCarriesTheReport(TailorCase):
    """Stored with the document, printed by `jsa tailor`, shown on the review
    page, and never in the way of approving."""

    KNOWN = coach.Finding("no_numbers", "2 of 2 bullets have no figure.",
                          "experience[0].bullets")

    def draft(self):
        from jsa import approvals
        from jsa.cli import cmd_tailor
        con = self.con()
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        out = io.StringIO()
        with mock.patch("jsa.tailor.llm.complete_json",
                        return_value=fake_completion(some_bullet_ids(PROFILE))), \
             mock.patch("jsa.cli.load_profile", return_value=PROFILE), \
             mock.patch("jsa.render.OUTPUT_DIR", self.tmp / "output"), \
             mock.patch("jsa.coach.review", return_value=[self.KNOWN]), \
             redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cmd_tailor(Namespace(job_id=1, kind="resume", force=False))
        return code, out.getvalue()

    def test_stored_printed_and_approval_still_queued(self):
        code, said = self.draft()
        self.assertEqual(code, 0)
        self.assertIn("resume report (1; advice for your profile, not a gate)", said)
        self.assertIn("no_numbers: 2 of 2 bullets", said)
        con = self.con()
        stored = con.execute("SELECT coach_findings FROM documents").fetchone()[0]
        pending = con.execute(
            "SELECT COUNT(*) FROM approvals WHERE decision = 'pending'").fetchone()[0]
        con.close()
        self.assertEqual(json.loads(stored), [self.KNOWN.as_dict()])
        self.assertEqual(pending, 1)

    def test_the_review_page_shows_it_beside_approve(self):
        from fastapi.testclient import TestClient
        from jsa import web
        self.draft()
        app = web.create_app(db_path=self.dbfile, output_dir=self.tmp / "output",
                             profile_loader=lambda: PROFILE)
        with TestClient(app, base_url="http://127.0.0.1:8765") as c:
            page = c.get("/review").text
        self.assertIn("Resume report", page)
        self.assertIn("it does not block approving", page)
        self.assertLess(page.index("Resume report"), page.index(">Approve<"))


if __name__ == "__main__":
    unittest.main()
