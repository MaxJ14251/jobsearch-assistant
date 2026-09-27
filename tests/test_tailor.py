"""Tailoring safety tests.

The central one is TestFabricationResistance: a job description engineered to
tempt the model into claiming things the candidate has never done. On this
model tier that temptation is real — asked to tailor for a Kubernetes role, a
helpful model writes a Kubernetes bullet. It reads well, it fits the posting,
and it is false.

These tests assert the verifier catches it whether the claim arrives as an
invented bullet, a drifted rewrite, or a banned term smuggled into the summary.
"""

import unittest

import yaml

from jsa.config import ROOT
from jsa.tailor import (
    DraftBullet,
    FabricationError,
    IdentityLeakError,
    TailoredDraft,
    UndecidedPreferenceError,
    collect_bullets,
    keyword_gap,
    pick_summary,
    require_decided_preferences,
    scrub_prompt,
    select_bullets,
    source_overlap,
    verify_draft,
)


def load_profile():
    path = ROOT / "profile" / "master_profile.yaml"
    if not path.exists():                       # published checkout
        path = ROOT / "profile" / "master_profile.example.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


PROFILE = load_profile()


def any_bullet(*, ongoing: bool = False):
    """A bullet id from whatever profile is on disk, and its source.

    These tests used to name the author's own bullet ids while loading the
    profile from disk, so on any other machine -- a fresh clone's example
    profile included -- they raised KeyError before asserting anything. What
    they test is the verifier, not which bullet it is handed.
    """
    sources = collect_bullets(PROFILE)
    for key, bullet in sources.items():
        text = bullet.text.lower()
        if ongoing == text.startswith(("building", "designing", "integrating",
                                       "goal:")):
            return key, bullet
    key = next(iter(sources))
    return key, sources[key]


def credential_says_not_conferred(profile) -> bool:
    """True when this profile describes an unconferred credential.

    Assertions about the "not conferred" wording are specific to a profile
    that has one. The committed example profile deliberately shows an ordinary
    conferred degree, so those checks skip there rather than failing — caught
    by tools/fresh_clone_check.py, which runs the suite as a new user would.
    """
    for edu in (profile.get("education") or []):
        text = (edu.get("credential") or "").lower()
        if "not conferred" in text or "coursework" in text:
            return True
    return False


# The posting under test. Every headline requirement is something the profile
# does NOT support: Kubernetes and PyTorch are both on the do-not-claim list,
# and the degree was never conferred.
ADVERSARIAL_JD = """
Senior Machine Learning Platform Engineer

We are looking for an engineer to own our model-serving platform.

Requirements:
- 8+ years of production experience with Kubernetes, including operating
  multi-region clusters and writing custom controllers.
- Deep PyTorch expertise. You will be fine-tuning and distilling large models
  and optimising distributed training across GPU fleets.
- Strong C++ and Java for our inference runtime.
- Production ML experience at scale is essential.
- React for our internal tooling.
- PhD in Computer Science, Machine Learning or a related field is required.
- Experience with distributed systems and TensorFlow strongly preferred.

You will work on Kubernetes operators, PyTorch model parallelism, and
TensorFlow serving pipelines every single day.
"""


class TestFabricationResistance(unittest.TestCase):
    """A posting that demands what the candidate does not have."""

    def _draft_with(self, bullets, summary=""):
        return TailoredDraft(summary=summary, bullets=bullets)

    def test_invented_bullet_is_rejected(self):
        """The failure mode: a fluent, relevant, entirely made-up bullet."""
        draft = self._draft_with([
            DraftBullet(
                source_id="b_vid_design",
                text="Operated multi-region Kubernetes clusters serving PyTorch "
                     "models, writing custom controllers for GPU scheduling.",
            )
        ])
        with self.assertRaises(FabricationError) as ctx:
            verify_draft(draft, PROFILE)
        # It is caught as drift from its cited source, which is the real defect.
        self.assertIn("b_vid_design", str(ctx.exception))

    def test_unknown_source_id_is_rejected(self):
        draft = self._draft_with([
            DraftBullet(source_id="b_kubernetes_expert",
                        text="Ran production Kubernetes at scale.")
        ])
        with self.assertRaises(FabricationError) as ctx:
            verify_draft(draft, PROFILE)
        self.assertIn("unknown source id", str(ctx.exception))

    def test_banned_terms_rejected_even_when_traceable(self):
        """A real bullet with a banned term appended still fails.

        This is the subtle case: the id is valid and most of the wording
        survives, so only the do-not-claim check catches it.
        """
        bullet_id, source = any_bullet()
        for term in ("Kubernetes", "PyTorch", "TensorFlow"):
            with self.subTest(term=term):
                draft = self._draft_with([
                    DraftBullet(source_id=bullet_id,
                                text=f"{source.text} Deployed with {term}.")
                ])
                with self.assertRaises(FabricationError) as ctx:
                    verify_draft(draft, PROFILE)
                self.assertIn(term, str(ctx.exception))

    def test_banned_term_in_summary_is_rejected(self):
        bullet_id, source = any_bullet()
        draft = self._draft_with(
            [DraftBullet(source_id=bullet_id, text=source.text)],
            summary="Engineer with deep PyTorch and Kubernetes experience.",
        )
        with self.assertRaises(FabricationError):
            verify_draft(draft, PROFILE)

    def test_faithful_rewording_is_allowed(self):
        """The verifier must not be so strict that legitimate tailoring fails."""
        # A rewording of the bullet's own words: same claim, fewer of them.
        bullet_id, source = any_bullet(ongoing=True)
        words = source.text.split()
        draft = self._draft_with([
            DraftBullet(source_id=bullet_id,
                        text=" ".join(words[:max(6, len(words) - 3)]).rstrip(",") + ".")
        ])
        self.assertIs(verify_draft(draft, PROFILE), draft)

    def test_adversarial_jd_reports_gaps_instead_of_claiming_them(self):
        """What the posting demands must land in missing, never in the text."""
        matched, missing = keyword_gap(ADVERSARIAL_JD, PROFILE)
        for term in ("Kubernetes", "PyTorch", "TensorFlow", "Java", "C++", "React"):
            self.assertIn(term, missing, f"{term} should be reported as a gap")

    def test_selection_never_offers_a_bullet_containing_banned_terms(self):
        """Nothing the model is handed can seed a banned claim."""
        chosen = select_bullets(PROFILE, ADVERSARIAL_JD, "engineering")
        self.assertTrue(chosen)
        blob = " ".join(b.text for b in chosen).lower()
        for term in ("kubernetes", "pytorch", "tensorflow"):
            self.assertNotIn(term, blob)

    def test_every_selected_bullet_is_traceable(self):
        known = set(collect_bullets(PROFILE))
        for b in select_bullets(PROFILE, ADVERSARIAL_JD, "engineering"):
            self.assertIn(b.id, known)


class TestDegreeHonesty(unittest.TestCase):
    """The posting demands a PhD. Nothing may imply a conferred degree."""

    DENY = [
        "my degree in", "graduated with", "i hold a", "earned my",
        "phd", "doctorate", "b.s. in computer science", "degree in computer science",
    ]

    def test_no_profile_bullet_implies_a_degree(self):
        blob = " ".join(b.text for b in collect_bullets(PROFILE).values()).lower()
        for phrase in self.DENY:
            self.assertNotIn(phrase, blob)

    def test_no_summary_implies_a_degree(self):
        blob = " ".join(s["text"] for s in PROFILE["summaries"]).lower()
        for phrase in self.DENY:
            self.assertNotIn(phrase, blob)

    def test_credential_is_stated_and_names_a_field(self):
        """Generic: every profile must state a credential and a field."""
        edu = PROFILE["education"][0]
        self.assertTrue(edu.get("credential"))
        # The field of study is the strong half and must always be present.
        self.assertTrue(edu.get("field"))

    @unittest.skipUnless(credential_says_not_conferred(PROFILE),
                         "this profile has a conferred credential")
    def test_unconferred_credential_says_so_plainly(self):
        edu = PROFILE["education"][0]
        self.assertIn("not conferred", edu["credential"].lower())


class TestSourceOverlap(unittest.TestCase):
    def test_identical_text_is_full_overlap(self):
        self.assertEqual(source_overlap("built a python pipeline",
                                        "built a python pipeline"), 1.0)

    def test_unrelated_text_is_near_zero(self):
        self.assertLess(
            source_overlap("Operated Kubernetes clusters at scale",
                           "Sold fire alarm systems to commercial clients"), 0.2)

    def test_stopwords_do_not_inflate_the_score(self):
        self.assertLess(
            source_overlap("the a to of in on for with and from",
                           "Installed and commissioned fire alarm panels"), 0.2)


class TestIdentityScrubber(unittest.TestCase):
    def test_street_address_blocks_the_prompt(self):
        street = PROFILE["identity"]["location"]["street"]
        with self.assertRaises(IdentityLeakError):
            scrub_prompt(f"Tailor this for a candidate at {street}.", PROFILE)

    def test_email_blocks_the_prompt(self):
        with self.assertRaises(IdentityLeakError):
            scrub_prompt(f"Contact {PROFILE['identity']['email']}", PROFILE)

    def test_full_name_blocks_the_prompt(self):
        with self.assertRaises(IdentityLeakError):
            scrub_prompt(f"Candidate: {PROFILE['identity']['full_name']}", PROFILE)

    def test_reformatted_phone_still_blocks(self):
        """Digits are compared too, so formatting can't sneak it through."""
        digits = "".join(c for c in PROFILE["identity"]["phone"] if c.isdigit())[-10:]
        with self.assertRaises(IdentityLeakError):
            scrub_prompt(f"call {digits}", PROFILE)

    def test_clean_prompt_passes_through_unchanged(self):
        prompt = "Rewrite this bullet for a backend role: built a Python pipeline."
        self.assertEqual(scrub_prompt(prompt, PROFILE), prompt)


class TestSummarySelection(unittest.TestCase):
    def test_sales_track_gets_the_customer_facing_summary(self):
        s = pick_summary(PROFILE, "sales", "Account Executive selling to enterprises")
        self.assertEqual(s["family"], "customer_facing_technical")

    def test_llm_role_gets_the_ai_summary(self):
        s = pick_summary(PROFILE, "engineering",
                         "Build agentic LLM pipelines and prompt tooling")
        self.assertEqual(s["family"], "ai_engineering")



class TestTermBoundaries(unittest.TestCase):
    r"""Regression: `\bC\+\+\b` never matches.

    A word boundary cannot sit between '+' and a space, so the trailing \b
    fails on "C++ and Java" — C++ was silently undetectable, and so were C#,
    F# and .NET. Every one of those is a real requirement in this corpus.
    """

    def test_plus_suffixed_languages_are_detected(self):
        from jsa.tailor import term_pattern
        for term, text in [
            ("C++", "Strong C++ and Java for our inference runtime."),
            ("C++", "requires C++"),
            ("C#", "C# and .NET experience"),
        ]:
            with self.subTest(term=term, text=text):
                self.assertIsNotNone(term_pattern(term).search(text))

    def test_does_not_match_inside_a_longer_token(self):
        from jsa.tailor import term_pattern
        self.assertIsNone(term_pattern("Java").search("JavaScript developer"))
        self.assertIsNone(term_pattern("C").search("C++ engineer"))

    def test_plain_terms_still_work(self):
        from jsa.tailor import term_pattern
        self.assertIsNotNone(term_pattern("Kubernetes").search("we use kubernetes"))
        self.assertIsNone(term_pattern("Kubernetes").search("no k8s here"))

    def test_banned_cpp_claim_is_caught(self):
        """The bug's real consequence: a C++ claim would have slipped through."""
        bullet_id, source = any_bullet()
        draft = TailoredDraft(bullets=[
            DraftBullet(source_id=bullet_id,
                        text=source.text + " Written in C++.")
        ])
        with self.assertRaises(FabricationError) as ctx:
            verify_draft(draft, PROFILE)
        self.assertIn("C++", str(ctx.exception))


class TestUndecidedPreferences(unittest.TestCase):
    """Goal 01 specified this guard and it was never built.

    Until now tailoring would proceed on a null work_authorization and let the
    model decide what to say about it. Null is not "no constraint" — it is
    "nobody has said" — and a model asked to write around an unanswered
    question invents an answer.
    """

    def _profile(self, **prefs):
        return {"job_search_preferences": prefs}

    def test_null_work_authorization_refuses(self):
        with self.assertRaises(UndecidedPreferenceError) as ctx:
            require_decided_preferences(self._profile(work_authorization=None))
        self.assertIn("work_authorization", str(ctx.exception),
                      "the error must name the field")

    def test_blank_string_also_refuses(self):
        """An empty string is an unanswered question wearing a value."""
        with self.assertRaises(UndecidedPreferenceError):
            require_decided_preferences(self._profile(work_authorization="   "))

    def test_missing_key_refuses(self):
        with self.assertRaises(UndecidedPreferenceError):
            require_decided_preferences(self._profile())

    def test_stated_value_passes(self):
        require_decided_preferences(
            self._profile(work_authorization="US citizen"))

    def test_compensation_floor_is_not_required_to_draft(self):
        """A floor is about which jobs to pursue, not what a resume says.

        The operator may legitimately have none, and refusing to draft over it
        would block document generation for a reason that has nothing to do
        with the document.
        """
        require_decided_preferences(
            self._profile(work_authorization="US citizen",
                          compensation_floor_usd=None))


class TestPreferencesNeverReachThePrompt(unittest.TestCase):
    """Compensation expectations are not identity, and were outside the
    scrubber's scope — it strips the `identity` block, and these live under
    `job_search_preferences`. NVIDIA's free tier logs prompts.
    """

    def test_prompt_template_has_no_preference_placeholder(self):
        from jsa.tailor import PROMPT, SYSTEM
        for needle in ("work_authorization", "compensation", "salary",
                       "sponsorship", "relocate"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, PROMPT.lower())
                self.assertNotIn(needle, SYSTEM.lower())

    def test_built_prompt_omits_the_real_values(self):
        """Build the prompt the way tailor() does and grep what would be sent."""
        from jsa.tailor import PROMPT
        path = ROOT / "profile" / "master_profile.yaml"
        if not path.exists():
            self.skipTest("no personal profile on this machine")
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
        prefs = profile.get("job_search_preferences") or {}
        built = PROMPT.format(
            title="Software Engineer",
            description="We use Python and build internal tooling.",
            summary="A summary drawn from the profile.",
            bullets="- [exp_1] Did a thing.",
        ).lower()
        for name in ("work_authorization", "compensation_floor_usd"):
            value = prefs.get(name)
            if isinstance(value, str) and value.strip():
                self.assertNotIn(value.lower(), built,
                                 f"{name} reached the outbound prompt")


if __name__ == "__main__":
    unittest.main()
