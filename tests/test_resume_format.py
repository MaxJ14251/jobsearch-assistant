"""Plan 11 part 1: renderer fixes any profile can rely on.

Fiction only: the Riverton example profile, edited per test.
"""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from jsa import render, tailor
from jsa.config import ROOT
from jsa.render import RenderError, extract_text, format_date, render_resume, skill_label
from jsa.tailor import DraftBullet, TailoredDraft, collect_bullets, pick_summary

EXAMPLE = yaml.safe_load(
    (ROOT / "jsa" / "resources" / "master_profile.example.yaml").read_text(encoding="utf-8"))
JOB = {"title": "Support Engineer", "company": "Acme"}


def example():
    return copy.deepcopy(EXAMPLE)


def every_bullet(profile, summary=True):
    s = profile["summaries"][0] if summary and profile.get("summaries") else None
    return TailoredDraft(summary_id=s["id"] if s else "",
                         summary=" ".join(s["text"].split()) if s else "",
                         bullets=[DraftBullet(b.id, b.text)
                                  for b in collect_bullets(profile).values()])


def rendered(profile, draft=None):
    out = Path(tempfile.mkdtemp()) / "r.docx"
    render_resume(draft or every_bullet(profile), profile, JOB, out)
    return extract_text(out)


class TestOneKey(unittest.TestCase):
    def test_experience_without_ids_still_renders_every_bullet(self):
        profile = example()
        for section in ("experience", "projects"):
            for entry in profile[section]:
                entry.pop("id", None)
        text = rendered(profile)
        for b in collect_bullets(profile).values():
            self.assertIn(" ".join(b.text.split()), text)
        self.assertIn("EXPERIENCE", text)

    def test_two_roles_at_one_employer_without_ids_stay_apart(self):
        profile = example()
        for entry in profile["experience"]:
            entry.pop("id", None)
        text = rendered(profile).splitlines()
        rep = text.index("Account Representative — Riverton Mechanical")
        tech = text.index("Service Technician — Riverton Mechanical")
        promo = next(i for i, ln in enumerate(text) if ln.startswith("Promoted from"))
        install = next(i for i, ln in enumerate(text) if ln.startswith("Installed and"))
        self.assertTrue(rep < promo < tech < install)


class TestPlacementGuard(unittest.TestCase):
    def test_two_entries_with_one_key_refuse_to_render(self):
        profile = example()
        twin = copy.deepcopy(profile["experience"][0])
        for entry in (profile["experience"][0], twin):
            entry.pop("id", None)
        twin["bullets"] = []
        profile["experience"].append(twin)
        out = Path(tempfile.mkdtemp()) / "r.docx"
        with self.assertRaises(RenderError) as ctx:
            render_resume(every_bullet(profile), profile, JOB, out)
        self.assertIn("2 time(s)", str(ctx.exception))
        self.assertFalse(out.exists())

    def test_a_bullet_under_the_wrong_entry_refuses(self):
        profile = example()
        sources = collect_bullets(profile)
        draft = TailoredDraft(bullets=[DraftBullet("b_riv_promo", "x")])
        render.check_placement(draft, sources,
                               [("b_riv_promo", "experience::exp_riverton_sales")])
        with self.assertRaises(RenderError) as ctx:
            render.check_placement(draft, sources,
                                   [("b_riv_promo", "experience::exp_riverton_tech")])
        self.assertIn("b_riv_promo", str(ctx.exception))
        with self.assertRaises(RenderError):   # never written at all
            render.check_placement(draft, sources, [])


class TestDates(unittest.TestCase):
    def test_month_year_by_default(self):
        self.assertEqual(format_date("2022-03"), "Mar 2022")
        self.assertEqual(format_date("2020"), "2020")
        self.assertEqual(format_date(2020), "2020")
        self.assertEqual(render._dates("2022-03", "2023-10"), "Mar 2022 – Oct 2023")
        self.assertEqual(render._dates("2022-03", None, current=True), "Mar 2022 – Present")

    def test_other_styles_and_odd_values(self):
        self.assertEqual(format_date("2022-03", "numeric"), "03/2022")
        self.assertEqual(format_date("2022-03", "iso"), "2022-03")
        self.assertEqual(format_date("2022-13"), "2022-13")
        self.assertEqual(format_date("Summer 2021"), "Summer 2021")

    def test_months_do_not_follow_the_os_locale(self):
        import locale
        with mock.patch.object(locale, "setlocale"):
            self.assertEqual(format_date("2022-05"), "May 2022")
        self.assertEqual(len(render.MONTHS), 12)

    def test_an_unknown_style_is_refused(self):
        profile = example()
        profile["resume"] = {"date_style": "%b %Y"}
        with self.assertRaises(RenderError):
            rendered(profile)

    def test_the_resume_prints_months(self):
        text = rendered(example())
        self.assertIn("Mar 2022 – Oct 2023", text)
        self.assertNotIn("2022-03", text)


class TestSkills(unittest.TestCase):
    def test_every_category_prints_except_unverified(self):
        profile = example()
        profile["skills"]["clinical_tools"] = ["EHR charting", "CPR"]
        text = rendered(profile)
        self.assertIn("Clinical Tools: EHR charting, CPR", text)
        self.assertIn("AI Tools: Prompt Engineering", text)
        self.assertNotIn("Unverified", text)
        self.assertNotIn("Docker", text)

    def test_labels(self):
        self.assertEqual(skill_label("ai_tools", {}, ["AI"]), "AI Tools")
        self.assertEqual(skill_label("crm_and_sql", {}, ["CRM", "SQL"]), "CRM And SQL")
        self.assertEqual(skill_label("ai_tools", {"ai_tools": "Machine learning"}, ["AI"]),
                         "Machine learning")


class TestWhatTheProfileAlreadySays(unittest.TestCase):
    """Plan 11 part 2."""

    def test_the_company_descriptor_is_on_the_meta_line(self):
        text = rendered(example())
        self.assertIn("Commercial HVAC & Building Controls  |  Mar 2022 – Oct 2023", text)

    def test_certifications_use_display_name_and_components(self):
        profile = example()
        profile["certifications"][0]["display_name"] = "Applied ML Certificate"
        text = rendered(profile)
        self.assertIn("Applied ML Certificate — Example Online Institute (2025)", text)
        self.assertIn("Example Provider (Jun 2025): Python for Automation; "
                      "Working with APIs; Introduction to Data Analysis", text)
        self.assertNotIn("assessed by project", text)  # description stays off

    def test_education_is_one_line_with_the_credential_verbatim(self):
        from jsa.prep import assert_no_degree_claim
        text = rendered(example())
        self.assertIn("State University — Computer Science, 2018 – 2020 · "
                      "Coursework completed (degree not conferred)", text)
        assert_no_degree_claim(text)


class TestSummaryNeverCrashes(unittest.TestCase):
    def test_no_general_summary_falls_back_to_the_first(self):
        profile = example()
        profile["summaries"] = [s for s in profile["summaries"]
                                if s["family"] != "general"]
        self.assertEqual(pick_summary(profile, "engineering", "plain")["id"],
                         profile["summaries"][0]["id"])
        self.assertEqual(pick_summary(profile, "sales", "")["family"],
                         "customer_facing_technical")

    def test_no_summaries_means_no_summary_section(self):
        profile = example()
        profile["summaries"] = []
        self.assertIsNone(pick_summary(profile, "engineering", ""))
        text = rendered(profile, every_bullet(profile, summary=False))
        self.assertNotIn("SUMMARY", text)

    def test_tailor_without_summaries_writes_none(self):
        profile = example()
        profile["summaries"] = []
        profile["job_search_preferences"]["work_authorization"] = "US citizen"
        reply = {"summary": "An invented summary.", "bullets": []}
        with mock.patch("jsa.tailor.llm.complete_json",
                        return_value=(reply, tailor.llm.Usage())):
            draft = tailor.tailor({"title": "Support Engineer",
                                   "description": "python"}, profile)
        self.assertEqual(draft.summary, "")
        self.assertEqual(draft.summary_id, "")


if __name__ == "__main__":
    unittest.main()


class TestSmarterSelection(unittest.TestCase):
    """Plan 11 part 4."""

    def test_skills_the_posting_mentions_come_first(self):
        ordered = tailor.order_skills(example(), "We use SQLite and API Integration daily.")
        self.assertEqual(ordered["technical"][:2], ["API Integration", "SQLite"])
        self.assertEqual(ordered["ai_tools"], example()["skills"]["ai_tools"])
        self.assertNotIn("unverified_candidates", ordered)

    def test_project_stack_joins_only_when_asked_and_never_unverified(self):
        profile = example()
        self.assertNotIn("LLM APIs", tailor.order_skills(profile, "")["technical"])
        profile["resume"] = {"skills_include_project_stack": True}
        technical = tailor.order_skills(profile, "")["technical"]
        self.assertIn("LLM APIs", technical)
        self.assertEqual(sum(t.lower() == "python" for t in technical), 1)
        self.assertNotIn("pandas", technical)        # in unverified_candidates
        self.assertNotIn("FastAPI", technical)       # "FastAPI / Flask" there

    def test_the_guard_refuses_an_unlisted_or_unverified_skill(self):
        profile = example()
        for bad in ("Kubernetes", "Docker", "Rust"):
            draft = TailoredDraft(skills={"technical": ["Python", bad]})
            with self.assertRaises(tailor.FabricationError):
                tailor.check_skills(draft, profile)
        tailor.check_skills(TailoredDraft(skills=tailor.order_skills(profile, "python")),
                            profile)

    def test_the_resume_prints_the_drafted_order(self):
        profile = example()
        draft = every_bullet(profile)
        draft.skills = tailor.order_skills(profile, "SQLite shop")
        self.assertIn("Technical: SQLite, Python", rendered(profile, draft))

    def test_work_history_bullet_can_be_a_list(self):
        profile = example()
        recent = profile["experience"][0]
        recent["work_history_bullet"] = ["b_riv_cycle", "b_riv_promo"]
        picks = tailor.select_bullets(profile, "python llm evaluation", "engineering",
                                      title="AI Engineer")
        own = [b.id for b in picks if b.parent == "exp_riverton_sales"]
        self.assertEqual(len(own), 1)
        self.assertIn(own[0], recent["work_history_bullet"])
        recent["work_history_bullet"] = ["b_riv_promo", "b_not_there"]
        with self.assertRaises(ValueError):
            tailor.select_bullets(profile, "python llm evaluation", "engineering",
                                  title="AI Engineer")

    def tied(self, a_text, b_text, a_strength=1, b_strength=1):
        profile = {"experience": [{
            "id": "e", "company": "C", "title": "T", "family": "sales",
            "bullets": [
                {"id": "b_a", "text": a_text, "strength": a_strength, "tags": []},
                {"id": "b_b", "text": b_text, "strength": b_strength, "tags": []}]}]}
        return [b.id for b in tailor.select_bullets(
            profile, "", "sales", keep_project=False, title="Account Executive")]

    def test_a_figure_breaks_an_exact_tie(self):
        self.assertEqual(self.tied("Ran the desk.", "Ran 40 accounts."), ["b_b", "b_a"])

    def test_strength_still_outranks_a_figure(self):
        self.assertEqual(self.tied("Ran the desk.", "Ran 40 accounts.", 1, 2),
                         ["b_a", "b_b"])


class TestSummaryAnyProfileCanDrive(unittest.TestCase):
    """Plan 11 part 5."""

    POSTINGS = [("engineering", "Support Engineer", "customer-facing support for our API"),
                ("engineering", "AI Engineer", "LLM agents and prompt evaluation"),
                ("sales", "Account Executive", "quota, pipeline"),
                ("engineering", "Data Analyst", "SQL dashboards")]

    def test_unset_profiles_choose_exactly_as_before(self):
        def old(profile, track, blob):
            import re as _re
            s = {x["family"]: x for x in profile["summaries"]}
            blob = blob.lower()
            if track == "sales" or _re.search(
                    r"customer[- ]facing|client[- ]facing|account executive|sales", blob):
                return s.get("customer_facing_technical") or s["general"]
            if _re.search(r"\bllm\b|generative ai|agentic|prompt", blob):
                return s.get("ai_engineering") or s["general"]
            return s.get("general")
        profile = example()
        for track, title, blob in self.POSTINGS:
            self.assertEqual(pick_summary(profile, track, blob, title)["id"],
                             old(profile, track, blob)["id"])

    def test_keywords_choose(self):
        profile = example()
        profile["summaries"][0]["keywords"] = ["SQL", "dashboards"]   # sum_general
        profile["summaries"][1]["keywords"] = ["LLM"]                 # sum_ai_eng
        self.assertEqual(pick_summary(profile, "engineering", "SQL dashboards",
                                      "Data Analyst")["id"], "sum_general")
        self.assertEqual(pick_summary(profile, "engineering", "LLM agents",
                                      "AI Engineer")["id"], "sum_ai_eng")

    def test_role_kinds_choose_and_no_match_falls_back(self):
        profile = example()
        profile["summaries"][2]["role_kinds"] = ["support"]  # customer-facing variant
        self.assertEqual(pick_summary(profile, "engineering", "tickets",
                                      "Technical Support Engineer")["id"],
                         "sum_customer_facing")
        # Nothing matches: the old mapping decides (an LLM posting -> ai_eng).
        self.assertEqual(pick_summary(profile, "engineering", "LLM agents",
                                      "Software Engineer")["id"], "sum_ai_eng")


class TestDoctorHint(unittest.TestCase):
    """Plan 11 part 7: unused optional fields are a hint, never a blocker."""

    def test_unused_fields_are_one_advisory_line(self):
        from jsa import doctor
        report = doctor.Report()
        doctor.check_resume_fields(example(), report)
        self.assertEqual(len(report.findings), 1)
        self.assertFalse(report.findings[0].blocking)
        self.assertIn("resume.date_style", report.findings[0].fix)
        self.assertNotIn("company_descriptor", report.findings[0].fix)  # example uses it

    def test_a_profile_using_them_all_gets_no_hint(self):
        from jsa import doctor
        profile = example()
        profile["projects"][0]["start"] = "2025-01"
        profile["certifications"][0]["display_name"] = "Applied ML"
        profile["skill_labels"] = {"ai_tools": "AI tools"}
        profile["summaries"][0]["keywords"] = ["python"]
        profile["resume"] = {"date_style": "iso", "skills_include_project_stack": False}
        profile["coach"] = {"gap_months": 6}
        report = doctor.Report()
        doctor.check_resume_fields(profile, report)
        self.assertEqual(report.findings, [])
