"""Role kind and tag vocabulary. See docs/decisions/0005-*.

A Premium Support Engineer posting said "customer" nine times and "support"
ten, and the tailored resume contained no customer-facing work at all. Dead
tags were half the reason; the binary track handing its family bonus to AI work
was the larger half.
"""

import copy
import unittest
from unittest import mock

from jsa import tailor
from jsa.tailor import (
    COMPONENT_DISCOUNT,
    DraftBullet,
    FabricationError,
    TailoredDraft,
    collect_bullets,
    is_dead,
    role_kind,
    select_bullets,
    tag_components,
    tag_report,
    tag_value,
    tag_weights,
    verify_draft,
    vocabulary,
)

PROFILE = {
    "identity": {"full_name": "Dana Rivers"},
    "ats_keywords": {"have": [], "aspirational_do_not_claim": ["Kubernetes"]},
    "experience": [
        {"id": "exp_sales", "title": "Sales Rep", "company": "Acme",
         "family": "sales", "start": "2022-01", "end": "2023-01",
         "bullets": [{"id": "b_sell", "strength": 1,
                      "text": "Sold security systems to residential customers.",
                      "tags": ["customer-facing", "sales"]}]},
        {"id": "exp_field", "title": "Installer", "company": "Acme",
         "family": "technical_field", "start": "2021-01", "end": "2022-01",
         "bullets": [{"id": "b_field", "strength": 1,
                      "text": "Installed alarm panels and diagnosed faults on site.",
                      "tags": ["troubleshooting", "systems"]}]},
    ],
    "projects": [
        {"id": "proj_ai", "name": "Pipeline", "family": "ai_engineering",
         "bullets": [
             {"id": "b_ai", "strength": 1,
              "text": "Built a Python pipeline using Claude.",
              "tags": ["python", "claude"]},
             {"id": "b_ai_two", "strength": 1,
              "text": "Orchestrated several model APIs.",
              "tags": ["orchestration", "python"]},
         ]},
    ],
}

SUPPORT_JD = ("You will be the front line for our customers, delivering expert "
              "technical support and troubleshooting. Python is a plus.")
ROBOTICS_JD = ("Space systems at our company build real hardware. You will own "
               "troubleshooting and support for robotic systems in Python.")


def corpus():
    """No posting says "customer facing"; many say "customer"."""
    return (["we support customers with systems and python"] * 40
            + ["python orchestration pipelines"] * 10
            + ["claude based tooling"] * 2)


class TestRoleKind(unittest.TestCase):
    def test_support_titles(self):
        for title in ("Premium Support Engineer (Foster City, Weekend Shift)",
                      "Technical Support Engineer II - W&B",
                      "IT Support / Operations Engineer",
                      "Forward Deployed Engineer, Agentic Platform",
                      "Solutions Architect",
                      "Scaled Enterprise Customer Success Manager"):
            with self.subTest(title=title):
                self.assertEqual(role_kind(title, "engineering"), "support")

    def test_hardware_titles_that_merely_say_support(self):
        """Pinned from the real tracker. The first draft of the pattern put
        all five of these in the customer-facing bucket."""
        for title in ("Launch Engineer, Ground Support Equipment (Falcon)",
                      "Thermal Control and Life Support Hardware Engineer",
                      "Mechanical Engineer II, Life Support Systems",
                      "Lead Engineer, Tooling Design and Support",
                      "Propulsion Engineer, Fleet Support (R5005)"):
            with self.subTest(title=title):
                self.assertEqual(role_kind(title, "engineering"), "engineering")

    def test_sales(self):
        self.assertEqual(role_kind("Mid-Market Account Executive", "engineering"), "sales")
        self.assertEqual(role_kind("Anything", "sales"), "sales")

    def test_engineering_is_the_default(self):
        for title in ("Software Engineer II - Robotics", "", None):
            with self.subTest(title=title):
                self.assertEqual(role_kind(title, "engineering"), "engineering")


class TestDeadTagFallback(unittest.TestCase):
    def setUp(self):
        self.weights = tag_weights(None, vocabulary(PROFILE), corpus=corpus())

    def test_components_ignore_filler(self):
        self.assertEqual(tag_components("customer-facing"), ["customer"])
        self.assertEqual(tag_components("ffmpeg-adjacent"), ["ffmpeg"])
        self.assertEqual(tag_components("python"), [])

    def test_a_dead_phrase_matches_through_its_component(self):
        self.assertTrue(is_dead("customer-facing", self.weights))
        value = tag_value("customer-facing", SUPPORT_JD.lower(), self.weights)
        self.assertGreater(value, 0.0)
        self.assertAlmostEqual(
            value, self.weights["customer"] * COMPONENT_DISCOUNT, places=6)

    def test_a_live_phrase_never_splits(self):
        """Splitting every tag was measured and rejected: it let a fire-alarm
        bullet back into a robotics resume."""
        live = {"real-time": 0.5, "real": 0.9, "time": 0.9}
        self.assertFalse(is_dead("real-time", live))
        self.assertEqual(tag_value("real-time", "a real problem, in time", live), 0.0)

    def test_no_corpus_means_no_reinterpretation(self):
        """A fresh clone knows nothing is dead, so nothing is reinterpreted."""
        self.assertFalse(is_dead("customer-facing", {}))
        self.assertEqual(tag_value("customer-facing", "customers", {}), 0.0)


class TestSelectionByRole(unittest.TestCase):
    def setUp(self):
        self.weights = tag_weights(None, vocabulary(PROFILE), corpus=corpus())

    def ids(self, jd, title):
        return [b.id for b in select_bullets(PROFILE, jd, "engineering",
                                             weights=self.weights, title=title)]

    def test_support_role_selects_customer_facing_work(self):
        chosen = self.ids(SUPPORT_JD, "Premium Support Engineer")
        self.assertIn("b_sell", chosen)

    def test_engineering_role_does_not_regress_to_field_work(self):
        """The robotics case that rarity weighting fixed must stay fixed."""
        chosen = self.ids(ROBOTICS_JD, "Software Engineer II - Robotics")
        self.assertEqual(chosen[0], "b_ai")
        self.assertNotEqual(chosen[0], "b_field")

    def test_the_same_posting_ranks_differently_by_role(self):
        support = self.ids(SUPPORT_JD, "Premium Support Engineer")
        engineering = self.ids(SUPPORT_JD, "Software Engineer")
        self.assertNotEqual(support, engineering)


class TestSafeByConstruction(unittest.TestCase):
    """Matching ranks the operator's own bullets. It never produces text.

    Scenario g. Proven by the test failing when selection is made to return a
    bullet the profile does not contain.
    """

    @staticmethod
    def profile_ids(profile):
        return {b["id"] for section in ("experience", "projects")
                for entry in profile.get(section) or []
                for b in entry.get("bullets") or []}

    def test_selection_returns_only_profile_bullets(self):
        weights = tag_weights(None, vocabulary(PROFILE), corpus=corpus())
        for jd, title in ((SUPPORT_JD, "Premium Support Engineer"),
                          (ROBOTICS_JD, "Software Engineer")):
            chosen = select_bullets(PROFILE, jd, "engineering",
                                    weights=weights, title=title)
            self.assertLessEqual({b.id for b in chosen}, self.profile_ids(PROFILE))
            texts = {b["text"] for s in ("experience", "projects")
                     for e in PROFILE[s] for b in e["bullets"]}
            for bullet in chosen:
                self.assertIn(bullet.text, texts, "selection produced new text")

    def test_the_subset_check_catches_an_injected_bullet(self):
        """Fail-first, kept as a test: a broken selector that fabricates."""
        real = tailor.collect_bullets

        def fabricating(profile):
            out = dict(real(profile))
            out["b_fake"] = tailor.SourceBullet(
                id="b_fake", text="Ran Kubernetes in production.",
                tags=("customer",), family="sales", strength=1,
                origin="experience", parent="exp_sales")
            return out

        with mock.patch("jsa.tailor.collect_bullets", side_effect=fabricating):
            chosen = select_bullets(PROFILE, SUPPORT_JD, "engineering",
                                    title="Premium Support Engineer")
        leaked = {b.id for b in chosen} - self.profile_ids(PROFILE)
        self.assertEqual(leaked, {"b_fake"},
                         "the subset check must be able to see a fabricated bullet")

    def test_a_banned_component_cannot_become_a_claim(self):
        """A dead tag whose component is a banned word may help RANK a bullet.
        It can never put the word in the document."""
        profile = copy.deepcopy(PROFILE)
        profile["projects"][0]["bullets"][0]["tags"].append("kubernetes-adjacent")
        weights = tag_weights(None, vocabulary(profile),
                              corpus=["kubernetes everywhere"] * 30)
        chosen = select_bullets(profile, "We run Kubernetes.", "engineering",
                                weights=weights, title="Software Engineer")
        for bullet in chosen:
            self.assertNotIn("kubernetes", bullet.text.lower())

        draft = TailoredDraft(
            job_id=1, summary_id="s", summary="A summary.",
            bullets=[DraftBullet(source_id="b_ai",
                                 text="Built a Python pipeline using Claude on Kubernetes.")],
            model="test/model")
        with self.assertRaises(FabricationError):
            verify_draft(draft, profile)


class TestTagReport(unittest.TestCase):
    def test_reports_dead_tags_and_what_revives_them(self):
        weights = tag_weights(None, vocabulary(PROFILE), corpus=corpus())
        rows = {r["tag"]: r for r in tag_report(PROFILE, weights, len(corpus()))}
        self.assertTrue(rows["customer-facing"]["dead"])
        self.assertEqual(rows["customer-facing"]["revived_by"][0][0], "customer")
        self.assertFalse(rows["python"]["dead"])
        self.assertIn("b_sell", rows["customer-facing"]["bullets"])

    def test_the_report_never_edits_the_profile(self):
        before = copy.deepcopy(PROFILE)
        weights = tag_weights(None, vocabulary(PROFILE), corpus=corpus())
        tag_report(PROFILE, weights, len(corpus()))
        self.assertEqual(PROFILE, before)

    def test_the_command_does_not_write_files(self):
        import inspect
        from jsa import cli
        source = inspect.getsource(cli.cmd_tags)
        for needle in ("write_text", "open(", "safe_dump", "yaml.dump"):
            self.assertNotIn(needle, source)


if __name__ == "__main__":
    unittest.main()
