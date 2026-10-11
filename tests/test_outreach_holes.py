"""Outreach's verifier holes, closed (plan 38).

Outreach's check was looser than the answers' and the letter's in four ways,
each letting a claim about the person through: the posting's tech stack and
the recipient's title and company were allowed in any sentence, the degree
check was three literal phrases, and with no job there was no word check at
all. Fictional throughout (the profile of tests/test_outreach_real.py).
"""

import unittest

from jsa import outreach
from jsa.tailor import FabricationError
from tests.test_outreach_real import PROFILE, job_row, tracker

CONTACT = {"name": "Priya Okafor", "title": "Senior Engineer", "company_name": "Globex"}


class Base(unittest.TestCase):
    def setUp(self):
        self.con = tracker()
        self.addCleanup(self.con.close)
        self.job = job_row(self.con)

    def refused(self, body, job="job", contact=None, word=None):
        job = self.job if job == "job" else job
        with self.assertRaises(FabricationError) as caught:
            outreach.verify_message(body, PROFILE, job=job, contact=contact)
        if word:
            self.assertIn(word, str(caught.exception).lower())

    def passes(self, body, contact=None):
        self.assertEqual(outreach.verify_message(body, PROFILE, job=self.job,
                                                 contact=contact), body)


class TestTheDegree(Base):
    def test_a_completed_degree_in_any_words(self):
        self.refused("Hi Sam, I completed my CS degree. Thanks,", word="degree")
        self.refused("As a CS graduate, I commissioned alarm panels. Thanks,")

    def test_with_no_job_too(self):
        self.refused("I completed my CS degree.", job=None)


class TestNoJob(Base):
    def test_the_words_are_still_checked(self):
        self.refused("I led a team of twelve engineers.", job=None, word="twelve")

    def test_the_profiles_own_words_pass(self):
        body = "Hi Priya Okafor, I commissioned alarm panels for commercial buildings. Thanks,"
        self.assertEqual(outreach.verify_message(body, PROFILE, job=None, contact=CONTACT),
                         body)


class TestTheStackAndTheRecipient(Base):
    def test_the_stack_only_in_a_sentence_about_them(self):
        self.refused("Hi Sam, I have used Zendesk for years. Thanks,", word="zendesk")
        self.passes("Hi Sam, I commissioned alarm panels. Your support team uses Zendesk.")

    def test_the_recipients_title_and_company_only_about_them(self):
        self.refused("I worked at Globex as a Senior Engineer.", contact=CONTACT,
                     word="globex")
        self.passes("Hi Priya, your team at Globex commissions alarm panels. Thanks,",
                    contact=CONTACT)

    def test_rules_for_sentences_about_them(self):
        body = "Hi Priya, your team at Globex doesn't use Zendesk. Thanks,"
        self.refused(body, contact=CONTACT)
        self.assertIn("don't do or have", " ".join(
            outreach.rule_problems(body, PROFILE, self.job, CONTACT)))
        self.refused("Hi Priya, your 40 engineers at Globex use Zendesk. Thanks,",
                     contact=CONTACT, word="40")

    def test_the_postings_prose_is_still_not_allowed(self):
        self.refused("Hi Sam, your team handles customer tickets. Thanks,", word="tickets")

    def test_the_retry_names_the_sentence(self):
        found = outreach.checked_sentences("Hi Sam, I commissioned alarm panels. "
                                           "I have used Zendesk for years.", PROFILE,
                                           self.job, None)
        bad = [s for s in found if s["refused"]]
        self.assertEqual([s["index"] for s in bad], [2])
        self.assertIn("zendesk", bad[0]["refused"])


if __name__ == "__main__":
    unittest.main()
