"""Cover letters. See docs/decisions/0007-*.

The model is mocked so the suite runs offline. What is real: the same check
that runs in production, the same fallback, and assertions on the letter TEXT
rather than on a guard having been called.
"""

import copy
import unittest
from unittest import mock

from jsa import letter
from jsa.tailor import DraftBullet, TailoredDraft

PROFILE = {
    "identity": {"full_name": "Dana Rivers", "email": "dana@example.test",
                 "phone": "+1 (555) 555-0100",
                 "location": {"street": "1 Example Way", "postal_code": "12345"}},
    "links": {"linkedin": "https://linkedin.com/in/dana",
              "github": "https://github.com/dana"},
    "ats_keywords": {"have": [], "aspirational_do_not_claim": ["Kubernetes"]},
    "summaries": [{"id": "sum_main", "family": "general",
                   "text": "Field technician turned Python developer building "
                           "automation with language models."}],
    "skills": {"technical": ["Python", "Automation"]},
    "experience": [
        {"id": "exp_field", "title": "Technician", "company": "Acme",
         "family": "technical_field", "bullets": [
             {"id": "b_install", "strength": 1, "tags": ["commissioning"],
              "text": "Installed and commissioned alarm panels in commercial buildings."},
             {"id": "b_codes", "strength": 2, "tags": ["compliance"],
              "text": "Ensured installations met safety codes."}]},
    ],
    "projects": [
        {"id": "proj_pipe", "name": "Clip Pipeline", "family": "ai_engineering",
         "status": "in_development", "bullets": [
             {"id": "b_goal", "strength": 2, "tags": ["automation"],
              "text": "Goal: reduce a multi-hour manual editing workflow to a "
                      "repeatable automated process."},
             {"id": "b_building", "strength": 1, "tags": ["python"],
              "text": "Building a Python pipeline that detects highlights in raw video."}]},
    ],
    "education": [{"institution": "State University", "field": "Computer Science",
                   "credential": "Undergraduate coursework completed (degree not conferred)"}],
}

JOB = {"id": 1, "title": "Support Engineer (Weekend Shift)", "company": "Replit",
       "location": "Foster City, CA", "track": "engineering",
       "description": "You will support customers. Python is a plus."}

DRAFT = TailoredDraft(
    job_id=1, summary_id="sum_main", summary=PROFILE["summaries"][0]["text"],
    bullets=[DraftBullet(source_id="b_install",
                         text=PROFILE["experience"][0]["bullets"][0]["text"]),
             DraftBullet(source_id="b_goal",
                         text=PROFILE["projects"][0]["bullets"][0]["text"]),
             DraftBullet(source_id="b_building",
                         text=PROFILE["projects"][0]["bullets"][1]["text"])],
    model="test/model")

GOOD = """Dear Hiring Manager,

I am applying for the Support Engineer (Weekend Shift) role at Replit. I am a
field technician turned Python developer, and I am building automation with
language models.

I installed and commissioned alarm panels in commercial buildings, and I
ensured installations met safety codes. I am building a Python pipeline that
detects highlights in raw video. My goal is to reduce a multi-hour manual
editing workflow to a repeatable automated process.

I would welcome the chance to discuss the role and how my background fits it.
I have attached my resume and can talk at any time that suits your team. I am
available for the weekend shift and would welcome a conversation about the
role. Thank you for your time and your consideration.

Sincerely,"""


def fake_completion(text):
    return mock.Mock(text=text, usage=mock.Mock(model="test/model"))


class TestTheCheck(unittest.TestCase):
    def test_an_honest_letter_passes(self):
        self.assertEqual(letter.check(GOOD, PROFILE, JOB), [])

    def test_your_own_name_and_contact_details_are_not_foreign_words(self):
        """Found on a real letter on the dashboard, which reported eight
        "words your profile does not": three groups of digits from a phone
        number, two mail and link domains, a surname, a first name.

        Every one of those is the operator's own name, phone, email or link,
        printed in the letterhead and the signature by render.py. The check
        built its vocabulary from bullets, summaries and skills only, so the
        operator's identity read as invention. The letter was fine.
        """
        header = ("Dana Rivers\n"
                  "Example City, CA  |  +1 (555) 555-0100  |  dana@example.test"
                  "  |  linkedin.com/in/dana  |  github.com/dana\n\n")
        problems = letter.check(header + GOOD + "\nDana Rivers", PROFILE, JOB)
        self.assertEqual([p for p in problems if "words your profile" in p], [],
                         "the operator's own identity is not a foreign claim")

    def test_somebody_elses_name_is_still_a_foreign_word(self):
        problems = letter.check(GOOD + "\nJordan Hayes", PROFILE, JOB)
        self.assertTrue(any("hayes" in p for p in problems), problems)

    def test_a_claim_the_profile_does_not_make_is_named(self):
        text = GOOD.replace("detects highlights in raw video.",
                            "detects highlights, and I have excellent "
                            "communication skills.")
        problems = letter.check(text, PROFILE, JOB)
        self.assertTrue(any("communication" in p for p in problems), problems)

    def test_c_an_adversarial_posting_cannot_put_words_in_the_letter(self):
        """Scenario c, asserted on the text."""
        text = GOOD.replace(
            "I am building a Python pipeline",
            "I have a PhD and ten years of Kubernetes in production, and I am "
            "building a Python pipeline")
        problems = letter.check(text, PROFILE, JOB)
        self.assertTrue(any("Kubernetes" in p for p in problems), problems)
        self.assertTrue(any("degree" in p.lower() for p in problems), problems)

    def test_the_degree_is_never_implied(self):
        for phrase in ("I hold a bachelor's degree.", "Since I graduated,",
                       "My B.S. is in Computer Science.", "As a CS graduate,"):
            with self.subTest(phrase=phrase):
                problems = letter.check(GOOD + " " + phrase, PROFILE, JOB)
                self.assertTrue(any("degree" in p for p in problems), problems)

    def test_ongoing_work_may_not_be_described_as_finished(self):
        text = GOOD.replace("I am building a Python pipeline",
                            "I built a Python pipeline")
        problems = letter.check(text, PROFILE, JOB)
        self.assertTrue(any("finished" in p for p in problems), problems)

    def test_a_word_the_profile_uses_itself_is_allowed(self):
        """The rule bans finished forms of ONGOING verbs, not every past
        tense: "Installed and commissioned" is the operator's own sentence."""
        self.assertNotIn("installed", letter.ongoing_verbs(PROFILE))
        self.assertIn("built", letter.ongoing_verbs(PROFILE))

    def test_flattery_about_the_company_fails(self):
        text = GOOD.replace("at Replit.",
                            "at Replit, an industry-leading platform I admire.")
        problems = letter.check(text, PROFILE, JOB)
        self.assertTrue(any("industry" in p or "leading" in p for p in problems),
                        problems)

    def test_the_posting_vocabulary_is_not_allowed(self):
        """ADR 0005: borrowing the posting's words is how resumes got
        embellished. A letter may name the role and the company, not the
        posting's demands."""
        job = dict(JOB, description="You will own SLA adherence and escalation.")
        text = GOOD.replace("safety codes", "safety codes and SLA adherence")
        problems = letter.check(text, PROFILE, job)
        self.assertTrue(any("sla" in p.lower() for p in problems), problems)

    def test_structure_is_required(self):
        cases = {
            GOOD.replace("Dear Hiring Manager,", ""): "no greeting",
            GOOD.replace("Sincerely,", ""): "no closing",
            GOOD.replace("Replit", "Acme Corp"): "does not name the company",
            "Dear Hiring Manager, Sincerely, Dana": "words, outside",
        }
        for text, expected in cases.items():
            with self.subTest(expected=expected):
                problems = letter.check(text, PROFILE, JOB)
                self.assertTrue(any(expected in p for p in problems), problems)

    def test_d_a_letter_that_runs_long_is_refused(self):
        """Scenario d. A 900-word letter is a bug, not a long letter."""
        long_letter = GOOD.replace(
            "Sincerely,", "I am building a Python pipeline. " * 200 + "Sincerely,")
        problems = letter.check(long_letter, PROFILE, JOB)
        self.assertTrue(any("outside" in p for p in problems), problems)


class TestWriting(unittest.TestCase):
    def write(self, model_text):
        with mock.patch("jsa.letter.llm.complete",
                        return_value=fake_completion(model_text)) as call:
            return letter.write(JOB, PROFILE, DRAFT), call

    def test_a_clean_letter_is_used_as_written(self):
        result, _ = self.write(GOOD)
        self.assertEqual(result.source, "model")
        self.assertEqual(result.body, GOOD.replace("\n\n", "\n\n"))
        self.assertEqual(result.note, "")

    def test_f_a_letter_that_fails_falls_back_to_your_own_sentences(self):
        """Scenario f. Never a fabricated letter, never no letter."""
        result, call = self.write(GOOD.replace(
            "I am building a Python pipeline", "I ran Kubernetes in production"))
        self.assertEqual(result.source, "composed")
        self.assertIn("Kubernetes", " ".join(result.problems))
        self.assertNotIn("Kubernetes", result.body)
        self.assertIn("refused", result.note)
        self.assertEqual(call.call_count, letter.ATTEMPTS)

    def test_the_retry_names_the_words_that_failed(self):
        _, call = self.write(GOOD.replace("safety codes",
                                          "safety codes and SLA adherence"))
        retry = call.call_args_list[1].args[0]
        self.assertIn("Do not use any of these words", retry)
        self.assertIn("sla", retry.lower())

    def test_the_fallback_is_made_of_verified_sentences(self):
        result, _ = self.write("nope")
        for bullet in DRAFT.bullets[:3]:
            self.assertIn(bullet.text, result.body)
        self.assertIn("Dear Hiring Manager,", result.body)
        self.assertIn("Sincerely,", result.body)
        self.assertIn("Replit", result.body)

    def test_a_failed_model_call_still_produces_a_letter(self):
        with mock.patch("jsa.letter.llm.complete", side_effect=RuntimeError("boom")):
            result = letter.write(JOB, PROFILE, DRAFT)
        self.assertEqual(result.source, "composed")
        self.assertIn("model call failed", result.note)

    def test_identity_never_reaches_the_prompt(self):
        """The letter says "Dana Rivers" only because render adds it locally."""
        captured = {}

        def capture(prompt, **kw):
            captured["prompt"] = prompt + str(kw.get("system", ""))
            return fake_completion(GOOD)

        with mock.patch("jsa.letter.llm.complete", side_effect=capture):
            letter.write(JOB, PROFILE, DRAFT)
        sent = captured["prompt"].lower()
        for value in ("dana rivers", "dana@example.test", "5550100",
                      "1 example way", "12345"):
            self.assertNotIn(value, sent.replace("(555) 555-0100", "5550100"))

    def test_a_profile_carrying_identity_in_a_bullet_refuses_to_send_it(self):
        """scrub_prompt fails closed rather than redacting."""
        from jsa.tailor import IdentityLeakError
        profile = copy.deepcopy(PROFILE)
        profile["identity"]["full_name"] = "Installed"   # appears in a bullet
        with self.assertRaises(IdentityLeakError):
            letter.write(JOB, profile, DRAFT)


class TestAllowedWords(unittest.TestCase):
    def test_the_connective_list_names_no_skill_or_tool(self):
        """The rule for that list, asserted. Anything here is a claim."""
        for banned in ("python", "kubernetes", "sql", "leadership",
                       "communication", "expert", "excellent", "proven",
                       "sla", "agile", "docker", "senior"):
            self.assertNotIn(banned, letter.CONNECTIVES)

    def test_the_role_title_and_company_are_allowed(self):
        stems = letter.allowed_stems(PROFILE, JOB)
        from jsa.tailor import _stem
        for word in ("support", "engineer", "weekend", "replit", "foster"):
            self.assertIn(_stem(word), stems)

    def test_ordinary_english_is_not_a_claim(self):
        """Measured: the first real letter was rejected for these."""
        text = GOOD.replace("I installed", "I spent three years and gave every "
                            "day to the work, and installed")
        self.assertEqual(letter.check(text, PROFILE, JOB), [])


if __name__ == "__main__":
    unittest.main()
