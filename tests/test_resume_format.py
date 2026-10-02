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
    (ROOT / "profile" / "master_profile.example.yaml").read_text(encoding="utf-8"))
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
