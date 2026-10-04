"""Application answers (plan 17, ADR 0027): facts verbatim, written answers
checked or built from the person's own sentences, no salary answer, nothing
submitted. Fiction only (the Riverton example)."""

import ast
import copy
import datetime as dt
import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from jsa import answers, approvals, db, facts, web
from jsa.config import ROOT
from jsa.llm import LLMError, Usage
from jsa.prep import assert_no_degree_claim
from tests.test_resume_format import example

TODAY = dt.date(2026, 10, 3)


def ready():
    p = example()
    prefs = p["job_search_preferences"]
    prefs.update(work_authorization="US citizen - no sponsorship required",
                 needs_visa_sponsorship=False, willing_to_relocate=None,
                 compensation_floor_usd=95000)
    p["identity"]["full_name"] = "Pat Example"
    return p


class TestFacts(unittest.TestCase):
    def test_a_completed_degree_is_stated_exactly(self):
        p = example()
        p["education"][0]["credential"] = "BS Computer Science, 2020"
        answer = facts.degree_answer(p)
        self.assertTrue(answer.startswith("BS Computer Science, 2020"))
        self.assertNotIn("did not finish", answer)

    def test_coursework_stays_verbatim_and_claims_nothing(self):
        p = example()
        answer = facts.degree_answer(p)
        self.assertIn("Coursework completed (degree not conferred)", answer)
        assert_no_degree_claim(answer)
        self.assertNotIn("2026", answer)            # no hardcoded year any more

    def test_the_education_line_is_the_resumes(self):
        self.assertEqual(facts.education_line(example()),
                         "State University — Computer Science, 2018–2020 · "
                         "Coursework completed (degree not conferred)")

    def test_years_merge_overlaps_and_count_a_current_job(self):
        p = {"experience": [
            {"company": "A", "title": "x", "start": "2020-01", "end": "2021-12"},
            {"company": "B", "title": "y", "start": "2021-06", "end": "2022-06"},
            {"company": "C", "title": "z", "start": "2026-01", "current": True}]}
        months, text = facts.years_of_experience(p, TODAY)
        self.assertEqual(months, 30 + 10)          # Jan 2020-Jun 2022, Jan-Oct 2026
        self.assertEqual(text, "about 3 years 4 months (from your profile dates)")

    def test_no_gap_means_no_gap_answer(self):
        p = {"experience": [{"company": "C", "title": "z", "start": "2024-01",
                             "current": True}]}
        self.assertIsNone(facts.gap_answer(p, TODAY))
        self.assertIn("Oct 2023", facts.gap_answer(example(), TODAY))


class TestFactAnswers(unittest.TestCase):
    def by_key(self, profile, job=None):
        return {a.key: a for a in answers.fact_answers(profile, job or {})}

    def test_work_authorization_is_verbatim(self):
        a = self.by_key(ready())["work_authorization"]
        self.assertEqual((a.body, a.source), ("US citizen - no sponsorship required", "profile"))

    def test_an_undecided_tristate_says_so(self):
        got = self.by_key(ready())
        self.assertEqual(got["sponsorship"].body, "No")
        self.assertEqual(got["relocation"].source, "undecided")
        self.assertIn("job_search_preferences.willing_to_relocate", got["relocation"].body)

    def test_no_salary_answer_only_the_postings_range(self):
        profile = ready()
        stated = self.by_key(profile, {"salary_min": 90000, "salary_max": 120000,
                                       "salary_period": "year"})["salary"].body
        self.assertEqual(stated, "Your call. The posting states $90,000–$120,000 per year.")
        none = self.by_key(profile, {})["salary"].body
        self.assertEqual(none, "Your call. The posting states no pay.")
        everything = " ".join(a.body + a.note for a in answers.fact_answers(profile, {}))
        self.assertNotIn("95", everything)          # the floor never appears


GOOD = ("I want this support engineer role because my work has been "
        "about helping customers with systems. I installed and commissioned "
        "rooftop units and building-control panels, then sold replacements to "
        "property managers, answering technical objections from field "
        "experience. I am designing a Python service that drafts a first reply "
        "to a support ticket for a person to approve.")


class Base(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.db"
        db.init_db(self.path)
        con = db.connect(self.path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        con.execute("INSERT INTO jobs (id,company_id,title,url,description,track) VALUES "
                    "(1,1,'Support Engineer','https://acme.test/1',?, 'engineering')",
                    ("We hire for Kubernetes, synergy and world-class customer obsession. "
                     * 5,))
        approvals.save_application(con, 1)
        con.commit()
        con.close()
        self.profile = ready()

    def con(self):
        con = db.connect(self.path)
        self.addCleanup(con.close)
        return con

    def write(self, reply, **kw):
        seen = {}

        def capture(prompt, **k):
            seen["prompt"] = prompt + (k.get("system") or "")
            if isinstance(reply, Exception):
                raise reply
            return reply, Usage(model="test")

        con = self.con()
        with mock.patch("jsa.answers.llm.complete_json", side_effect=capture):
            got = {a.key: a for a in answers.write(con, 1, self.profile, **kw)}
        con.commit()
        return got, seen.get("prompt", "")


class TestWritten(Base):
    def test_a_good_answer_is_kept(self):
        got, _ = self.write({"why_role": GOOD, "relevant_project": GOOD, "why_company": GOOD})
        self.assertEqual(got["why_role"].source, "model")
        self.assertEqual(got["why_role"].body, GOOD)

    def test_why_company_is_composed_only(self):
        got, prompt = self.write({"why_role": GOOD, "relevant_project": GOOD,
                                  "why_company": GOOD})
        self.assertEqual(got["why_company"].source, "composed")
        self.assertIn("composed only", got["why_company"].note)
        self.assertNotIn("why_company", prompt)

    def test_each_failure_falls_back_with_a_note(self):
        invented = GOOD + " I led a team of forty engineers at Google."
        posting = GOOD + " I love your world-class customer obsession and synergy."
        degree = GOOD + " I graduated with a bachelor's degree in computer science."
        finished = GOOD.replace("I am designing", "I designed and shipped")
        got, _ = self.write({"why_role": invented, "relevant_project": degree,
                             "why_company": posting})
        for key in ("why_role", "relevant_project", "why_company"):
            self.assertEqual(got[key].source, "composed", key)
            self.assertIn("from your own sentences", got[key].note)
        self.assertIn("implies a degree", got["relevant_project"].note)
        got, _ = self.write({"why_role": finished, "relevant_project": GOOD,
                             "why_company": GOOD})
        self.assertEqual(got["why_role"].source, "composed")

    def test_a_model_failure_composes_everything(self):
        got, _ = self.write(LLMError("rate limited"))
        self.assertTrue(all(a.source == "composed" for a in got.values()))
        self.assertIn("the model call failed", got["why_role"].note)

    def test_the_prompt_has_no_identity_and_no_floor(self):
        _, prompt = self.write({"why_role": GOOD, "relevant_project": GOOD, "why_company": GOOD})
        self.assertNotIn("Pat Example", prompt)
        self.assertNotIn("95000", prompt)
        self.assertNotIn("95,000", prompt)
        self.assertNotIn("Kubernetes", prompt)       # the posting isn't sent

    def test_regenerate_archives_the_old_set(self):
        self.write({"why_role": GOOD, "relevant_project": GOOD, "why_company": GOOD})
        self.write({"why_role": GOOD, "relevant_project": GOOD, "why_company": GOOD})
        con = self.con()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM application_answers").fetchone()[0], 6)
        self.assertEqual(len(answers.stored(con, 1)), 3)

    def test_an_unsaved_job_is_refused(self):
        con = self.con()
        con.execute("INSERT INTO jobs (id,company_id,title,url) VALUES (2,1,'X','https://a.test/2')")
        with self.assertRaises(ValueError):
            answers.write(con, 2, self.profile)


class TestWhereTheyShow(Base):
    def test_cli(self):
        from jsa import cli
        real = db.connect
        out = io.StringIO()
        with mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(self.path)), \
             mock.patch("jsa.cli.load_profile", return_value=self.profile), \
             mock.patch("jsa.answers.llm.complete_json",
                        return_value=({"why_role": GOOD, "relevant_project": GOOD,
                                       "why_company": GOOD}, Usage())), \
             redirect_stdout(out):
            self.assertEqual(cli.cmd_answers(Namespace(job_id=1, regenerate=False)), 0)
        said = out.getvalue()
        self.assertIn("  US citizen - no sponsorship required", said)
        self.assertIn("[model]", said)
        self.assertIn("nothing is submitted", said)

    def test_the_job_page_copy_buttons_token_and_composed_label(self):
        app = web.create_app(db_path=self.path, profile_loader=lambda: self.profile)
        c = TestClient(app, base_url="http://127.0.0.1:8765")
        self.addCleanup(c.close)
        page = c.get("/job/1").text
        self.assertIn("Application answers", page)
        self.assertIn('class="ghost copy"', page)
        self.assertEqual(c.post("/job/1/answers").status_code, 403)
        with mock.patch("jsa.answers.llm.complete_json", side_effect=LLMError("down")):
            r = c.post("/job/1/answers", data={"csrf": app.state.csrf_token},
                       follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("from your own sentences", c.get("/job/1").text)


class TestGuards(unittest.TestCase):
    def test_answers_imports_no_sending_module(self):
        tree = ast.parse((ROOT / "jsa" / "answers.py").read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        for banned in ("smtplib", "imaplib", "httpx", "requests", "urllib", "socket",
                       "webbrowser", "subprocess"):
            self.assertFalse([n for n in names if n.split(".")[0] == banned], banned)


if __name__ == "__main__":
    unittest.main()
