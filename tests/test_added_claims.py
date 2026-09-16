"""What a rewrite ADDS. See ADR 0005, decisions 6 and 7.

source_overlap measured recall -- how much of the source survived -- and never
looked at what was appended. A rewrite that kept a whole bullet and bolted on
"during high-priority support scenarios" scored higher, not lower. Replayed
over every document generated before this check existed, it reverted 13 of 19
rewrites and 2 of 3 summaries.

Fixtures are synthetic but modelled on those real failures. The operator's
actual bullets stay in their gitignored profile, not in a public test file.
"""

import copy
import unittest

from jsa.tailor import (
    INVENTION_CEILING,
    DraftBullet,
    FabricationError,
    TailoredDraft,
    added_share,
    claims_completion,
    verify_draft,
)

PROFILE = {
    "identity": {"full_name": "Dana Rivers"},
    "ats_keywords": {"have": [], "aspirational_do_not_claim": ["Kubernetes"]},
    "summaries": [{"id": "sum_main", "family": "ai_engineering",
                   "text": "Field technician turned Python developer building "
                           "automation with language models."}],
    "skills": {"technical": ["Python", "Automation"]},
    "experience": [
        {"id": "exp_field", "title": "Technician", "company": "Acme",
         "family": "technical_field",
         "bullets": [
             {"id": "b_install", "strength": 1,
              "text": "Installed and commissioned alarm panels in commercial buildings.",
              "tags": ["commissioning", "troubleshooting"]},
             {"id": "b_codes", "strength": 2,
              "text": "Ensured installations met safety codes, building product "
                      "knowledge that supported a move into sales.",
              "tags": ["compliance"]},
         ]},
    ],
    "projects": [
        {"id": "proj_pipe", "name": "Clip Pipeline", "family": "ai_engineering",
         "status": "in_development",
         "bullets": [
             {"id": "b_goal", "strength": 2,
              "text": "Goal: reduce a multi-hour manual editing workflow to a "
                      "repeatable automated process.",
              "tags": ["automation", "workflow"]},
             {"id": "b_building", "strength": 1,
              "text": "Building a Python pipeline that detects highlights in raw video.",
              "tags": ["python", "pipeline"]},
         ]},
    ],
}


def draft(bullets, summary=None):
    return TailoredDraft(
        job_id=1, summary_id="sum_main",
        summary=summary or PROFILE["summaries"][0]["text"],
        bullets=[DraftBullet(source_id=i, text=t) for i, t in bullets],
        model="test/model")


class TestAppendedClaimsRevert(unittest.TestCase):
    """The shape of every real failure: keep the source, append a new claim."""

    CASES = [
        ("b_codes",
         "Ensured installations met safety codes, building product knowledge "
         "that enabled clear, confident communication during high-priority "
         "support scenarios."),
        ("b_install",
         "Installed and commissioned alarm panels in commercial buildings, "
         "gaining integrated software experience under strict deadlines."),
        ("b_building",
         "Building a Python pipeline that detects highlights in raw video, "
         "debugging live data-quality issues to meet turnaround requirements."),
    ]

    def test_each_appended_claim_reverts_to_the_source(self):
        for bullet_id, text in self.CASES:
            with self.subTest(bullet=bullet_id):
                result = verify_draft(draft([(bullet_id, text)]), PROFILE)
                self.assertIn(bullet_id, result.reverted)
                original = next(b["text"] for s in ("experience", "projects")
                                for e in PROFILE[s] for b in e["bullets"]
                                if b["id"] == bullet_id)
                self.assertEqual(result.bullets[0].text, original)
                self.assertIn("added unsupported words",
                              result.revert_reasons[bullet_id])

    def test_recall_alone_would_have_passed_them(self):
        """The bug, pinned: every one of these keeps nearly all of its source."""
        from jsa.tailor import MIN_SOURCE_OVERLAP, source_overlap
        for bullet_id, text in self.CASES:
            original = next(b["text"] for s in ("experience", "projects")
                            for e in PROFILE[s] for b in e["bullets"]
                            if b["id"] == bullet_id)
            with self.subTest(bullet=bullet_id):
                self.assertGreaterEqual(source_overlap(text, original),
                                        MIN_SOURCE_OVERLAP)


class TestHonestEditsSurvive(unittest.TestCase):
    """A guard that reverts everything has only moved the failure."""

    def test_cutting_words_is_allowed(self):
        text = "Ensured installations met safety codes, building product knowledge."
        result = verify_draft(draft([("b_codes", text)]), PROFILE)
        self.assertEqual(result.reverted, [])

    def test_facts_from_a_sibling_bullet_of_the_same_project_are_allowed(self):
        """Merging true facts about the same work is re-presentation."""
        text = ("Building a repeatable automated Python pipeline that detects "
                "highlights in raw video.")
        result = verify_draft(draft([("b_building", text)]), PROFILE)
        self.assertEqual(result.reverted, [])

    def test_the_bullets_own_tags_are_allowed(self):
        """The operator tagged the install work 'troubleshooting' -- their word.

        Goes through verify_draft rather than a hand-built stem set: the first
        draft of this test guessed the stems and got them wrong.
        """
        text = ("Commissioned alarm panels in commercial buildings, including "
                "troubleshooting.")
        result = verify_draft(draft([("b_install", text)]), PROFILE)
        self.assertEqual(result.reverted, [])

    def test_facts_from_another_project_are_not_allowed(self):
        """Doc 4 moved 'orchestration' and 'API' work onto the wrong project."""
        text = ("Goal: reduce a multi-hour manual editing workflow through "
                "orchestration of several model APIs and prompt engineering.")
        result = verify_draft(draft([("b_goal", text)]), PROFILE)
        self.assertIn("b_goal", result.reverted)


class TestOngoingWorkStaysOngoing(unittest.TestCase):
    def test_goal_rewritten_as_a_result_reverts(self):
        text = ("Reduced a multi-hour manual editing workflow to a repeatable "
                "automated process.")
        self.assertTrue(claims_completion(
            PROFILE["projects"][0]["bullets"][0]["text"], text))
        result = verify_draft(draft([("b_goal", text)]), PROFILE)
        self.assertEqual(result.revert_reasons["b_goal"],
                         "rewrote ongoing work as finished")

    def test_progressive_rewritten_as_past_reverts(self):
        for verb in ("Built", "Developed", "Shipped"):
            with self.subTest(verb=verb):
                self.assertTrue(claims_completion(
                    "Building a Python pipeline.", f"{verb} a Python pipeline."))

    def test_ongoing_stays_ongoing_is_fine(self):
        self.assertFalse(claims_completion(
            "Building a Python pipeline.", "Building a highlight pipeline in Python."))
        self.assertFalse(claims_completion(
            "Goal: reduce a workflow.", "Reducing a multi-hour workflow."))

    def test_finished_work_may_stay_finished(self):
        self.assertFalse(claims_completion(
            "Installed alarm panels.", "Installed and commissioned alarm panels."))


class TestSummaryIsHeldToTheSameStandard(unittest.TestCase):
    def test_an_invented_summary_reverts_to_the_chosen_variant(self):
        """Doc 5 claimed 'strict SLA adherence' and 'cross-functional
        escalation'; nothing in the profile says either."""
        invented = ("Support engineer with strict SLA adherence, calm incident "
                    "response and cross-functional escalation experience.")
        result = verify_draft(draft([], summary=invented), PROFILE)
        self.assertEqual(result.summary, PROFILE["summaries"][0]["text"])
        self.assertIn("summary", result.revert_reasons)

    def test_a_reordered_summary_survives(self):
        reordered = ("Python developer building automation with language "
                     "models, formerly a field technician.")
        result = verify_draft(draft([], summary=reordered), PROFILE)
        self.assertEqual(result.summary, reordered)


class TestOrderOfChecks(unittest.TestCase):
    def test_a_banned_term_refuses_the_draft_even_though_it_would_revert(self):
        """Checked against what the model WROTE. Reverting first would let an
        attempt to claim a forbidden term pass quietly."""
        text = ("Building a Python pipeline that detects highlights in raw "
                "video on Kubernetes.")
        with self.assertRaises(FabricationError) as ctx:
            verify_draft(draft([("b_building", text)]), PROFILE)
        self.assertIn("Kubernetes", str(ctx.exception))

    def test_an_unknown_id_is_named_before_a_banned_term(self):
        with self.assertRaises(FabricationError) as ctx:
            verify_draft(draft([("b_invented", "Ran Kubernetes clusters.")]), PROFILE)
        self.assertIn("unknown source id", str(ctx.exception))

    def test_verification_does_not_mutate_the_profile(self):
        before = copy.deepcopy(PROFILE)
        verify_draft(draft([("b_codes", self_text := "Ensured safety codes.")]), PROFILE)
        self.assertEqual(PROFILE, before)
        del self_text


if __name__ == "__main__":
    unittest.main()


class TestBracketedIds(unittest.TestCase):
    """The prompt lists bullets as "[b_id] text" and models copy the brackets.

    Found on a live adversarial run: a valid draft was refused as citing an
    "unknown source id" because the id read '[b_vid_design]'.
    """

    def _tailor(self, ids):
        from unittest import mock
        from jsa import tailor as t
        profile = copy.deepcopy(PROFILE)
        profile["job_search_preferences"] = {"work_authorization": "US citizen"}
        payload = {"summary": PROFILE["summaries"][0]["text"],
                   "bullets": [{"id": i, "text": "Building a Python pipeline "
                                "that detects highlights in raw video."} for i in ids]}
        usage = mock.Mock(model="test/model")
        with mock.patch("jsa.tailor.llm.complete_json", return_value=(payload, usage)):
            return t.tailor({"id": 1, "title": "Engineer",
                             "description": "python pipeline", "track": "engineering"},
                            profile)

    def test_a_bracketed_real_id_is_accepted(self):
        result = self._tailor(["[b_building]"])
        self.assertIn("b_building", [b.source_id for b in result.bullets])

    def test_a_bracketed_invented_id_is_still_refused(self):
        with self.assertRaises(FabricationError) as ctx:
            self._tailor(["[b_invented]"])
        self.assertIn("unknown source id", str(ctx.exception))
