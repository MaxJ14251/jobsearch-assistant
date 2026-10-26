"""Outreach with the split prompt switched on (G8).

Off (the default), every outreach prompt and retry is byte-identical
(tests/test_prompts_unchanged.py). On, only the prompt and the retry change:
the check is plan 38's, untouched, so a claim about the sender in the
recipient's or the stack's words is still refused.
"""

import json
import unittest
from unittest import mock

from jsa import outreach
from jsa.llm import Completion, Usage
from jsa.tailor import FabricationError

PROFILE = {
    "identity": {"full_name": "Robin Example", "email": "robin@example.test"},
    "ats_keywords": {"have": [], "aspirational_do_not_claim": ["Kubernetes"]},
    "summaries": [{"id": "s", "family": "general", "text": "Support technician."}],
    "experience": [{"id": "e", "title": "Technician", "company": "Acme",
                    "family": "technical_field", "bullets": [
                        {"id": "b1", "strength": 1, "tags": ["support"],
                         "text": "Resolved forty customer tickets a day."}]}],
    "projects": [],
    "education": [{"institution": "State", "credential": "Coursework (not conferred)"}],
}
JOB = {"id": 1, "title": "Support Engineer", "company": "Globex", "track": "engineering",
       "description": "Help customers with Zendesk.", "tech_stack": json.dumps(["Zendesk"])}
CONTACT = {"name": "Jordan Example", "title": "Engineering Manager", "company_name": "Globex"}


class Split(unittest.TestCase):
    def setUp(self):
        on = mock.patch.object(outreach, "SUBJECT_AWARE", True)
        on.start()
        self.addCleanup(on.stop)


class TestThePrompt(Split):
    def test_the_split_rules_are_in_the_prompt_and_identity_is_not(self):
        prompt, system = outreach.build(PROFILE, JOB, CONTACT, channel="linkedin_dm",
                                        purpose="referral_ask")
        self.assertIn("Two kinds of sentence", prompt)
        self.assertIn("keeping their words", prompt)
        self.assertIn("kept apart", system)
        for value in ("Robin Example", "robin@example.test"):
            self.assertNotIn(value, prompt)

    def test_off_is_the_old_prompt(self):
        prompt, system = outreach.build(PROFILE, JOB, CONTACT, channel="linkedin_dm",
                                        purpose="referral_ask", split=False)
        self.assertNotIn("Two kinds of sentence", prompt)
        self.assertEqual(system, outreach.SYSTEM)


class TestTheCheckIsUnchanged(Split):
    def test_a_recipient_sentence_may_name_the_stack(self):
        body = "Hi Jordan, your team at Globex uses Zendesk. I resolved forty customer tickets a day."
        self.assertEqual(outreach.verify_message(body, PROFILE, JOB, CONTACT), body)

    def test_the_sender_claiming_the_stack_is_still_refused(self):
        with self.assertRaises(FabricationError):
            outreach.verify_message("I have used Zendesk for years.", PROFILE, JOB, CONTACT)


class TestTheRetry(Split):
    def test_the_retry_names_the_sentence_and_asks_to_split(self):
        sent = []

        def complete(prompt, **k):
            sent.append(prompt)
            return Completion(text=("I have used Zendesk for years." if len(sent) == 1
                                    else "Hi Jordan. I resolved forty customer tickets a day."),
                              usage=Usage(model="t"))

        prompt, system = outreach.build(PROFILE, JOB, CONTACT, channel="linkedin_dm",
                                        purpose="referral_ask")
        with mock.patch("jsa.outreach.llm.complete", side_effect=complete):
            body, unsupported, rules, bodies = outreach.attempts(
                prompt, system, PROFILE, JOB, CONTACT, limit=1200, tries=2)
        self.assertEqual(len(bodies), 2)
        self.assertIn("zendesk", sent[1].lower())
        self.assertIn("apart from sentences about the sender", sent[1])
        self.assertEqual((unsupported, rules), ([], []))


class TestTheSwitchStartsOff(unittest.TestCase):
    def test_off_until_measured(self):
        self.assertIs(outreach.SUBJECT_AWARE, False)


if __name__ == "__main__":
    unittest.main()
