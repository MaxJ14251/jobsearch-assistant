"""Who each sentence is about (plan 37): every doubt goes to strict."""

import unittest

from jsa import subject

PERSON = ["Morgan Eval-Fixture"]


def kinds(text, company="Vercel", frame=None):
    return [s.kind for s in subject.sort(text, company, PERSON, frame)]


class TestSplit(unittest.TestCase):
    def test_sentence_ends_and_line_breaks_split_commas_do_not(self):
        self.assertEqual(subject.split("One, two - three: four. Five! Six? Seven; eight\nNine"),
                         ["One, two - three: four.", "Five!", "Six?", "Seven;", "eight", "Nine"])

    def test_short_words_are_tokens(self):
        self.assertEqual(subject.tokens("I'm sure we, my team and us"),
                         ["i'm", "sure", "we", "my", "team", "and", "us"])
        self.assertEqual(subject.tokens("I’ve"), ["i've"])


class TestEmployer(unittest.TestCase):
    CASES = [
        "Vercel ships developer tools used by front-end teams.",
        "Vercel's platform serves front-end teams.",
        "vercel builds for the web.",
        "This role focuses on developer experience.",
        "The role owns the support queue.",
        "The position covers enterprise accounts.",
        "Your team ships every week.",
        "The team supports enterprise customers.",
        "The company serves front-end teams.",
        "And Vercel ships developer tools.",
        "Today Vercel ships developer tools.",
    ]

    def test_positively_about_the_employer(self):
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(kinds(text), ["employer"])

    def test_a_pronoun_only_after_an_employer_sentence(self):
        self.assertEqual(kinds("Vercel ships developer tools. Its customers are front-end "
                               "teams."), ["employer", "employer"])
        self.assertEqual(kinds("Its customers are front-end teams."), ["strict"])
        self.assertEqual(kinds("I build tools. They are front-end teams."), ["strict", "strict"])

    def test_the_company_name_must_be_the_whole_name(self):
        self.assertEqual(kinds("Riverton Grid builds tools.", "Riverton Grid"), ["employer"])
        self.assertEqual(kinds("Riverton builds tools.", "Riverton Grid"), ["strict"])


class TestStrict(unittest.TestCase):
    CASES = {
        "Vercel's team, like me, uses Kubernetes daily.": "first person",
        "Vercel needs a Kubernetes expert, which is exactly who is applying.": "stands in",
        "Vercel needs a candidate who knows Kubernetes.": "stands in",
        "Your team needs someone like that.": "stands in",
        "Morgan runs Vercel-scale deployments.": "names the person",
        "Vercel will love Morgan's work.": "names the person",
        "Built developer tools at Vercel scale.": "unclear",
        "Having shipped developer tools, Vercel's mission fits.": "opens about the person",
        "As a support engineer, Vercel's mission fits.": "opens about the person",
        "I'm excited to bring my Kubernetes experience to Vercel.": "first person",
        "At Replit, an industry-leading platform I admire.": "first person",
        "I love your world-class customer obsession and synergy.": "first person",
        "We ship developer tools at Vercel.": "first person",
        "Our team at Vercel ships tools.": "first person",
        "Developer experience matters.": "unclear",
        "It is a great place.": "unclear",
        "The right person for Vercel knows Next.js.": "stands in",
        "Vercel and I share a focus on developer tools.": "first person",
    }

    def test_about_the_person_mixed_or_unclear(self):
        for text, why in self.CASES.items():
            with self.subTest(text=text):
                (s,) = subject.sort(text, "Vercel", PERSON)
                self.assertEqual(s.kind, "strict")
                self.assertIn(why, s.why)


class TestTheAttitudeFrame(unittest.TestCase):
    """Off by default since 2026-10-11 (it added no pass, ADR 0035); these
    hold the mechanism to its rules for if it is turned back on."""

    def test_off_by_default_so_an_attitude_is_strict(self):
        self.assertFalse(subject.ATTITUDE_FRAME)
        (s,) = subject.sort("I'm drawn to Vercel's focus on developer experience.", "Vercel",
                            PERSON)
        self.assertEqual(s.kind, "strict")

    def test_an_attitude_toward_the_employer(self):
        for text in ("I'm drawn to Vercel's focus on developer experience.",
                     "I admire Vercel's focus on developer tools.",
                     "What excites me about Vercel is its developer tools.",
                     "I appreciate your focus on developer experience."):
            with self.subTest(text=text):
                (s,) = subject.sort(text, "Vercel", PERSON, attitude_frame=True)
                self.assertEqual(s.kind, "employer")
                self.assertNotIn("drawn", s.body.lower().split()[:1])

    def test_the_rest_is_what_gets_checked(self):
        (s,) = subject.sort("I'm drawn to Vercel's focus on developer experience.", "Vercel",
                            PERSON, attitude_frame=True)
        self.assertEqual(s.body, "Vercel's focus on developer experience.")

    def test_any_clause_that_could_be_about_the_person_breaks_it(self):
        for text in ("I admire Vercel's team, which I led.",
                     "I'm drawn to your stack because I've run it.",
                     "I admire Vercel's tools and my own work on them.",
                     "I like Vercel's tools, having used them.",
                     "I admire Vercel's team as someone who ran one.",
                     "I admire that I fit Vercel's team.",
                     "I like developer tools.",              # not about the employer
                     "I admire Morgan's work at Vercel."):
            with self.subTest(text=text):
                self.assertEqual(kinds(text, frame=True), ["strict"])

    def test_it_can_be_switched_off(self):
        self.assertEqual(kinds("I'm drawn to Vercel's focus on developer experience.",
                               frame=False), ["strict"])


class TestExistingRefusalsStillRefused(unittest.TestCase):
    """Every refusal the suites already hold, run through the new path with
    the posting supplied, and with the posting carrying the very words:
    each is still refused (plan 37)."""

    def test_the_letter_cases(self):
        from jsa import letter
        from tests.test_letter import GOOD, JOB, PROFILE
        flattery = GOOD.replace("at Replit.", "at Replit, an industry-leading platform I admire.")
        problems = letter.sentence_problems(
            flattery, PROFILE, JOB, "Replit is an industry-leading platform. " + JOB["description"])
        self.assertTrue(any("industry" in p or "leading" in p for p in problems), problems)
        sla = GOOD.replace("safety codes", "safety codes and SLA adherence")
        problems = letter.sentence_problems(sla, PROFILE, JOB,
                                            "You will own SLA adherence and escalation.")
        self.assertTrue(any("sla" in p.lower() for p in problems), problems)

    def test_the_answers_cases(self):
        from jsa import answers
        from tests.test_answers import GOOD, ready
        prof = ready()
        job = {"title": "Support Engineer", "company": "Acme", "location": None}
        posting = "We hire for Kubernetes, synergy and world-class customer obsession. " * 5
        for text, word in (
                (GOOD + " I love your world-class customer obsession and synergy.", "synergy"),
                (GOOD + " I led a team of forty engineers at Google.", "google"),
                (GOOD + " I graduated with a bachelor's degree in computer science.", "degree"),
                (GOOD.replace("I am designing", "I designed and shipped"), "finished"),
                (GOOD + " Acme runs Kubernetes for synergy.", "")):
            with self.subTest(word=word or "do-not-claim or posting word"):
                problems = answers.check(text, prof, job, posting_text=posting)
                self.assertTrue(problems)
                if word:
                    self.assertIn(word, " ".join(problems).lower())

    def test_the_outreach_and_suggest_cases(self):
        from jsa import answers, letter
        from tests.test_answers import ready
        from tests.test_outreach_real import PROFILE as OUT
        problems = letter.sentence_problems("Hi Sam, I handle customer tickets. Thanks,", OUT,
                                            {"title": "Premium Support Engineer",
                                             "company": "Replit"}, "Handle customer tickets.")
        self.assertTrue(any("tickets" in p for p in problems), problems)
        from tests.test_answers import GOOD
        problems = answers.check(GOOD + " I run Kubernetes clusters every day.", ready(),
                                 {"title": "Support Engineer", "company": "Riverton Grid"},
                                 posting_text="How have you used Kubernetes? Kubernetes clusters.")
        self.assertTrue(any("kubernetes" in p.lower() for p in problems), problems)


class TestHelpers(unittest.TestCase):
    def test_negation_and_numbers(self):
        self.assertTrue(subject.negated("Vercel doesn't prioritize it."))
        self.assertTrue(subject.negated("Vercel ships without tests."))
        self.assertFalse(subject.negated("Vercel ships developer tools."))
        self.assertEqual(subject.numbers("Serves 1,000,000 developers in 3 regions."),
                         ["1,000,000", "3"])


if __name__ == "__main__":
    unittest.main()
