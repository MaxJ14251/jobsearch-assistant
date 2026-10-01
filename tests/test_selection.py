"""Bullet selection, section order, and the drift/fabrication boundary.

All four defects these tests cover were found by reading an actual generated
resume, not by reading code. `jsa tailor 260` — a Rocket Lab ROBOTICS role —
produced a document whose only EXPERIENCE entry was "Installer — Riverton", listing
fire alarm panels, with the AI projects below it.
"""

import unittest

from jsa.tailor import (
    FABRICATION_FLOOR,
    FAMILY_BONUS,
    MIN_BULLETS,
    MIN_SOURCE_OVERLAP,
    MIN_TAG_WEIGHT,
    DraftBullet,
    FabricationError,
    TailoredDraft,
    collect_bullets,
    select_bullets,
    tag_weights,
    verify_draft,
)

PROFILE = {
    "identity": {"full_name": "Dana Rivers"},
    "ats_keywords": {"have": [], "aspirational_do_not_claim": ["Kubernetes"]},
    "experience": [
        {"id": "exp_field", "title": "Installer", "company": "Acme",
         "family": "technical_field", "start": "2021-01", "end": "2022-01",
         "bullets": [{"id": "b_field", "strength": 1,
                      "text": "Installed alarm panels and commissioned hardware systems.",
                      "tags": ["systems", "hardware", "commissioning"]}]},
    ],
    "projects": [
        {"id": "proj_ai", "name": "Pipeline", "family": "ai_engineering",
         "bullets": [
             {"id": "b_ai_one", "strength": 1,
              "text": "Built a Python pipeline using Claude to process streaming video.",
              "tags": ["python", "claude", "streaming"]},
             {"id": "b_ai_two", "strength": 1,
              "text": "Designed multimodal captioning with real-time evaluation.",
              "tags": ["multimodal", "real-time", "evaluation"]},
             {"id": "b_ai_three", "strength": 2,
              "text": "Automated a manual editing workflow end to end.",
              "tags": ["automation", "workflow"]},
             {"id": "b_ai_four", "strength": 2,
              "text": "Integrated several AI APIs into one orchestration layer.",
              "tags": ["orchestration", "api-integration"]},
         ]},
    ],
}

# A posting that talks like the Rocket Lab one: heavy on generic engineering
# vocabulary, with the specific AI terms absent.
ROBOTICS_JD = (
    "Space systems at our company build real hardware for meaningful missions. "
    "You will own integration, test, deployment and production support for "
    "robotic systems. Python experience required."
)


def corpus(common: int = 100):
    """A synthetic corpus where 'systems' and 'hardware' are everywhere.

    Mirrors the real measurement: across the 514 postings in the tracker,
    'systems' appears in 86.6% and 'hardware' in 46.5%, while 'claude' appears
    in 3.5%.
    """
    everywhere = ["we build systems with hardware for missions"] * common
    rare = ["we use claude and multimodal streaming"] * 3
    return everywhere + rare


class TestTagWeights(unittest.TestCase):
    """A tag's worth is how rare it is."""

    def test_common_tags_are_discounted(self):
        weights = tag_weights(None, {"systems", "hardware", "claude"},
                              corpus=corpus())
        self.assertLess(weights["systems"], weights["claude"])
        self.assertLess(weights["hardware"], weights["claude"])

    def test_a_common_tag_still_counts_something(self):
        weights = tag_weights(None, {"systems"}, corpus=corpus())
        self.assertGreaterEqual(weights["systems"], MIN_TAG_WEIGHT)

    def test_no_corpus_means_no_weights_rather_than_invented_ones(self):
        """A fresh clone has an empty jobs table.

        Weighting from nothing would be worse than weighting equally, so the
        function returns {} and every tag counts the same.
        """
        self.assertEqual(tag_weights(None, {"systems"}, corpus=[]), {})
        self.assertEqual(tag_weights(None, {"systems"}, corpus=["one"] * 5), {})

    def test_word_boundaries_are_respected(self):
        """'systems' must not match inside 'subsystems'.

        A literal backspace byte once sat where this word boundary belongs. It
        compiled, it ran, and every tag came back weighted 1.000 because the
        pattern matched nothing.
        """
        weights = tag_weights(None, {"systems"}, corpus=["subsystems only"] * 50)
        self.assertEqual(weights["systems"], 1.0,
                         "matched inside another word")


class TestSelection(unittest.TestCase):
    def test_generic_tag_hits_do_not_beat_family_relevance(self):
        """The Rocket Lab defect, reproduced.

        The field bullet hits 'systems' and 'hardware' — both from the
        posting's own blurb — and must still not outrank AI work on an
        engineering role.
        """
        weights = tag_weights(None,
                              {t for b in collect_bullets(PROFILE).values()
                               for t in b.tags},
                              corpus=corpus())
        ranked = select_bullets(PROFILE, ROBOTICS_JD, "engineering",
                                weights=weights, keep_work_history=False)
        self.assertNotIn("b_field", [b.id for b in ranked],
                         "fire-alarm work was selected on relevance for a software role")
        # With the work-history rule it returns only as the one job bullet,
        # last -- present so the resume shows employment, never outranking.
        chosen = [b.id for b in select_bullets(PROFILE, ROBOTICS_JD, "engineering",
                                               weights=weights)]
        self.assertEqual(chosen[-1], "b_field")
        self.assertEqual(chosen.count("b_field"), 1)

    def test_ties_do_not_break_on_the_alphabet(self):
        """b_inst_codes once beat b_vid_goal purely because 'b_i' < 'b_v'.

        Tested by behaviour. An earlier version grepped the source for a
        variable name and broke on a rename while the ordering stayed correct.
        """
        profile = {"experience": [], "projects": [
            {"id": "p", "name": "P", "family": "ai_engineering", "bullets": [
                {"id": "z_relevant", "text": "Built a thing.", "tags": [], "strength": 2}]},
        ]}
        profile["experience"] = [
            {"id": "e", "company": "C", "family": "sales", "bullets": [
                {"id": "a_irrelevant", "text": "Sold a thing.", "tags": [], "strength": 2}]},
        ]
        # No tag hits at all: only the family preference separates them.
        # Alphabetically a_irrelevant comes first; the relevant one must still win.
        chosen = select_bullets(profile, "nothing matches here", "engineering",
                                limit=2, title="Software Engineer")
        self.assertEqual(chosen[0].id, "z_relevant")

    def test_never_returns_fewer_than_the_minimum(self):
        """A ratio floor once cut the second-best bullet by 0.02.

        That left a one-bullet resume. A floor decides what is padding; it must
        not decide that a resume has nothing to say.
        """
        chosen = select_bullets(PROFILE, "python", "engineering")
        self.assertGreaterEqual(len(chosen), min(MIN_BULLETS, 5))

    def test_selection_is_actually_selective(self):
        """Six of ten were previously chosen every time, regardless of fit."""
        chosen = select_bullets(PROFILE, ROBOTICS_JD, "engineering", limit=6)
        self.assertLessEqual(len(chosen), 5, "returned everything available")


class TestWorkHistoryIsNeverEmpty(unittest.TestCase):
    """Rocket Lab and Scale AI drafts once had no EXPERIENCE section at all.

    The projects outscored every job, so the resume read as if the candidate
    had never been employed. A recruiter checks work history first; for anyone
    changing fields that omission costs more than one off-field bullet does.
    """

    PROFILE = {
        "experience": [
            {"id": "exp_old", "company": "Acme", "family": "technical_field",
             "start": "2019-01", "end": "2021-01", "bullets": [
                 {"id": "b_old", "text": "Repaired pumps.", "tags": [], "strength": 1}]},
            {"id": "exp_recent", "company": "Acme", "family": "sales",
             "start": "2021-02", "end": "2023-11", "bullets": [
                 {"id": "b_recent_weak", "text": "Filed reports.", "tags": [], "strength": 3},
                 {"id": "b_recent", "text": "Sold pumps.", "tags": ["python"], "strength": 1}]},
        ],
        "projects": [
            {"id": "p", "name": "P", "family": "ai_engineering", "bullets": [
                {"id": f"b_p{i}", "text": f"Built python thing {i}.",
                 "tags": ["python"], "strength": 1} for i in range(6)]},
        ],
    }

    def ids(self, **kw):
        return [b.id for b in select_bullets(self.PROFILE, "python", "engineering",
                                             title="Software Engineer", **kw)]

    def test_the_most_recent_job_is_kept_on_an_engineering_resume(self):
        chosen = self.ids()
        self.assertIn("b_recent", chosen, "resume had no work history")
        self.assertNotIn("b_old", chosen)

    def test_its_best_bullet_is_the_one_kept(self):
        self.assertNotIn("b_recent_weak", self.ids())

    def test_the_limit_still_holds(self):
        self.assertEqual(len(self.ids(limit=6)), 6)
        self.assertEqual(self.ids(limit=6)[-1], "b_recent")

    def test_a_current_job_counts_as_most_recent(self):
        import copy
        profile = copy.deepcopy(self.PROFILE)
        profile["experience"][0].update(end=None, current=True)
        chosen = [b.id for b in select_bullets(profile, "python", "engineering",
                                               title="Software Engineer")]
        self.assertIn("b_old", chosen)

    def test_the_operator_can_name_the_bullet(self):
        """Tag hits on an off-field job are noise -- one incidental word decided
        it. The operator knows which bullet reads well on any resume."""
        import copy
        profile = copy.deepcopy(self.PROFILE)
        profile["experience"][1]["work_history_bullet"] = "b_recent_weak"
        chosen = [b.id for b in select_bullets(profile, "python", "engineering",
                                               title="Software Engineer")]
        self.assertEqual(chosen[-1], "b_recent_weak")
        self.assertNotIn("b_recent", chosen)

    def test_a_named_bullet_from_another_job_is_refused(self):
        import copy
        profile = copy.deepcopy(self.PROFILE)
        profile["experience"][1]["work_history_bullet"] = "b_old"
        with self.assertRaises(ValueError) as ctx:
            select_bullets(profile, "python", "engineering")
        self.assertIn("work_history_bullet", str(ctx.exception))

    def test_callers_may_opt_out(self):
        """A three-bullet outreach note is not a resume."""
        self.assertNotIn("b_recent", self.ids(keep_work_history=False))

    def test_a_profile_without_jobs_is_unaffected(self):
        profile = {"experience": [], "projects": self.PROFILE["projects"]}
        chosen = select_bullets(profile, "python", "engineering")
        self.assertTrue(all(b.origin == "project" for b in chosen))


class TestProjectsAreNeverEmpty(unittest.TestCase):
    """The Shield AI and Kyber drafts had no PROJECTS section.

    Both were forward-deployed roles, where field and customer-facing jobs
    outscored every project; the operator rejected both with "No projects
    section". The mirror of the work-history rule above.
    """

    PROFILE = {
        "experience": [
            {"id": "exp_field", "company": "Acme", "family": "technical_field",
             "start": "2021-01", "end": "2023-11", "bullets": [
                 {"id": f"b_f{i}", "text": f"Deployed hardware on site {i}.",
                  "tags": ["deployment", "customer"], "strength": 1}
                 for i in range(6)]},
        ],
        "projects": [
            {"id": "p_weak", "name": "Weak", "family": "ai_engineering", "bullets": [
                {"id": "b_p_weak", "text": "Wrote a script.", "tags": [], "strength": 3}]},
            {"id": "p_best", "name": "Best", "family": "ai_engineering", "bullets": [
                {"id": "b_p_best", "text": "Deployed a model.", "tags": ["deployment"],
                 "strength": 1}]},
        ],
    }
    JD = "Forward deployed: deployment at customer sites, customer support."

    def ids(self, **kw):
        return [b.id for b in select_bullets(
            self.PROFILE, self.JD, "engineering",
            title="Forward Deployed Engineer", **kw)]

    def test_a_project_is_kept_when_jobs_outscore_them(self):
        chosen = self.ids(keep_project=False)
        self.assertFalse(any(i.startswith("b_p") for i in chosen),
                         "fixture no longer reproduces the defect")
        self.assertIn("b_p_best", self.ids())

    def test_its_best_bullet_is_the_one_kept(self):
        self.assertNotIn("b_p_weak", self.ids())

    def test_the_limit_still_holds(self):
        chosen = self.ids(limit=6)
        self.assertEqual(len(chosen), 6)
        self.assertEqual(chosen[-1], "b_p_best")

    def test_nothing_changes_when_a_project_was_chosen(self):
        other = TestWorkHistoryIsNeverEmpty()
        self.assertEqual(other.ids(), other.ids(keep_project=False))

    def test_a_profile_without_projects_is_unaffected(self):
        profile = {"experience": self.PROFILE["experience"]}
        chosen = select_bullets(profile, self.JD, "engineering")
        self.assertTrue(all(b.origin == "experience" for b in chosen))


class TestTiesPreferAFinishedProject(unittest.TestCase):
    """On an exact tie, a released project with a public repo comes first.

    Kyber's posting had no text, every project bullet tied on its family
    bonus, and the alphabet picked an in-development tool ("b_game_...")
    over the released, public app ("b_jsa_..."). ADR 0005 section 11.
    """

    def profile(self, dev_tags=(), field_family="sales"):
        def project(pid, status, repo, tags=()):
            return {"id": pid, "name": pid, "status": status, "repo": repo,
                    "family": "ai_engineering",
                    "bullets": [{"id": f"b_{pid}", "text": f"Built {pid}.",
                                 "tags": list(tags), "strength": 1}]}
        return {
            "experience": [{"id": "exp", "company": "Co", "title": "Rep",
                            "family": field_family, "current": True,
                            "bullets": [{"id": "b_field", "text": "Sold things.",
                                         "tags": ["sales"], "strength": 1}]}],
            "projects": [
                # Alphabetical order is the WORST order here, on purpose.
                project("a_dev", "in_development", None, dev_tags),
                project("m_rel", "released", None),
                project("z_rel", "released", "https://github.com/example/z"),
            ],
        }

    def project_order(self, profile, description=""):
        chosen = select_bullets(profile, description, "engineering",
                                title="Software Engineer", limit=6)
        return [b.id for b in chosen if b.origin == "project"]

    def test_released_with_a_repo_beats_in_development_whatever_the_ids(self):
        self.assertEqual(self.project_order(self.profile()),
                         ["b_z_rel", "b_m_rel", "b_a_dev"])

    def test_real_evidence_still_wins(self):
        """Maturity only breaks ties; it never outranks the posting."""
        profile = self.profile(dev_tags=["robotics"])
        order = self.project_order(profile, "We build robotics software.")
        self.assertEqual(order[0], "b_a_dev")

    def test_family_still_decides_before_maturity(self):
        """An engineering role favours ai_engineering: the sales job stays last."""
        chosen = select_bullets(self.profile(), "", "engineering",
                                title="Software Engineer", limit=6)
        self.assertEqual(chosen[-1].id, "b_field")

    def test_the_project_fallback_keeps_the_finished_one(self):
        """The keep-project rule picks from the same order."""
        profile = self.profile(field_family="sales")
        chosen = select_bullets(profile, "sales sales", "sales",
                                title="Account Executive", limit=2)
        self.assertIn("b_z_rel", [b.id for b in chosen])


class TestSectionOrder(unittest.TestCase):
    """Experience once led unconditionally, whatever was actually selected."""

    def _order(self, bullet_ids):
        import tempfile
        from pathlib import Path

        from jsa import render
        draft = TailoredDraft(
            job_id=1, summary_id="s", summary="Summary.",
            bullets=[DraftBullet(source_id=b,
                                 text=collect_bullets(PROFILE)[b].text)
                     for b in bullet_ids],
            model="test/model",
        )
        out = Path(tempfile.mkdtemp()) / "r.docx"
        render.render_resume(draft, PROFILE, {"title": "Engineer"}, out)
        text = render.extract_text(out)
        return (text.find("EXPERIENCE"), text.find("PROJECTS"))

    def test_projects_lead_when_they_hold_the_selected_bullets(self):
        exp, proj = self._order(["b_ai_one", "b_ai_two", "b_ai_three"])
        self.assertEqual(exp, -1, "an empty Experience section was printed")
        self.assertGreaterEqual(proj, 0)

    def test_experience_leads_when_it_holds_more(self):
        exp, proj = self._order(["b_field", "b_ai_one"])
        self.assertLess(exp, proj, "Experience should lead here")

    def test_experience_wins_a_tie(self):
        """Convention, and what most readers expect."""
        exp, proj = self._order(["b_field", "b_ai_one"])
        self.assertLess(exp, proj)


class TestDriftVersusFabrication(unittest.TestCase):
    """Two different problems that were once treated identically.

    The boundary is measured. Overlap against the cited source: 0.00 for
    wholesale invention, 0.27 for a real drifted rewrite, 0.73 for a good one.
    """

    def _draft(self, source_id, text):
        return TailoredDraft(
            job_id=1, summary_id="s", summary="A summary.",
            bullets=[DraftBullet(source_id=source_id, text=text)],
            model="test/model",
        )

    def test_invention_still_raises(self):
        """The project's central threat: fluent, relevant, entirely made up."""
        draft = self._draft(
            "b_ai_one",
            "Operated multi-region clusters serving models with custom GPU "
            "controllers for scheduling workloads.")
        with self.assertRaises(FabricationError):
            verify_draft(draft, PROFILE)

    def test_drift_reverts_instead_of_killing_the_draft(self):
        """A resume with one verbatim bullet beats no resume at all."""
        source = collect_bullets(PROFILE)["b_ai_one"].text
        draft = self._draft(
            "b_ai_one",
            "Built a Python pipeline that handles an entirely different set of "
            "concerns not described anywhere in the original claim at all.")
        result = verify_draft(draft, PROFILE)
        self.assertEqual(result.bullets[0].text, source,
                         "the drifted text should have been replaced")
        self.assertIn("b_ai_one", result.reverted,
                      "a revert must be recorded, never silent")

    def test_a_good_rewrite_survives_untouched(self):
        draft = self._draft(
            "b_ai_one",
            "Built a Python pipeline using Claude that processes streaming video.")
        result = verify_draft(draft, PROFILE)
        self.assertNotIn("b_ai_one", result.reverted)

    def test_the_boundary_sits_between_the_two(self):
        self.assertLess(FABRICATION_FLOOR, MIN_SOURCE_OVERLAP)

    def test_unknown_source_id_still_raises(self):
        with self.assertRaises(FabricationError):
            verify_draft(self._draft("b_does_not_exist", "Anything."), PROFILE)

    def test_banned_terms_still_raise(self):
        draft = self._draft(
            "b_ai_one",
            "Built a Python pipeline using Claude and Kubernetes to process "
            "streaming video footage.")
        with self.assertRaises(FabricationError):
            verify_draft(draft, PROFILE)


if __name__ == "__main__":
    unittest.main()
