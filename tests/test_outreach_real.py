"""What running outreach for the first time found.

The module had 536 lines of green tests and had produced zero messages. Then
three were drafted against real postings and real model calls, and four things
were wrong at once:

  verify_message did not verify   a draft inventing an employer, a team of
                                  twelve, six years' tenure, a $4M figure and
                                  a certification passed it without objection,
                                  while the module docstring promised "the
                                  same verifier as tailoring"
  nothing signed the message      the prompt says "the sender's name is added
                                  afterwards"; nothing added it, so every
                                  draft ended "Thanks," and stopped
  the model never saw the posting it was asked to be "concrete about why this
                                  role specifically" having been shown only a
                                  title and a company
  the track was hardcoded         select_bullets was always told "engineering",
                                  so a posting the tracker had classified as
                                  sales fell back to the wrong kind

Every test here is written against the shapes of those four, not against a
mock that would have agreed with the old code.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import db, outreach
from jsa.tailor import FabricationError

PROFILE = {
    "identity": {"full_name": "Dana Rivers", "preferred_name": "Dana",
                 "email": "dana@example.test", "phone": "+1 (555) 555-0100"},
    "job_search_preferences": {
        "target_titles": ["Support Engineer"],
        "locations": ["Remote (US)"],
        "work_authorization": "US citizen",
        "compensation_floor_usd": "no_floor",
    },
    "summaries": [{"id": "s", "family": "general",
                   "text": "Installer turned support engineer."}],
    "experience": [{
        "id": "e", "company": "Acme", "title": "Technician",
        "family": "technical_field",
        "bullets": [{"id": "b1", "tags": ["commissioning"], "strength": 3,
                     "text": "Installed and commissioned alarm panels for "
                             "commercial buildings."}]}],
    "education": [{"institution": "State University",
                   "credential": "Coursework completed (degree not conferred)"}],
    "ats_keywords": {"aspirational_do_not_claim": ["Kubernetes"]},
}

# An employer, a team, a tenure, a revenue figure and a certification, none of
# them in the profile, and none of them on the banned list. This is the text
# the old verify_message passed.
INVENTED = ("Hi Sam,\n\nI led a team of twelve engineers at Globex for six "
            "years and closed $4M in new business. I hold a professional "
            "certification.\n\nWould you refer me?\n\nThanks,")


def tracker() -> sqlite3.Connection:
    path = Path(tempfile.mkdtemp()) / "t.db"
    db.init_db(path)
    con = db.connect(path)
    con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Replit','replit')")
    con.execute(
        "INSERT INTO jobs (id,company_id,title,url,description,location,track,"
        "tech_stack) VALUES (7,1,'Premium Support Engineer',"
        "'https://replit.test/7','Handle customer tickets.','Foster City, CA',"
        "'sales',?)", (json.dumps(["Zendesk", "Linear"]),))
    con.commit()
    return con


def job_row(con) -> dict:
    con.row_factory = sqlite3.Row
    return dict(con.execute(
        "SELECT j.*, c.name AS company FROM jobs j "
        "JOIN companies c ON c.id = j.company_id WHERE j.id = 7").fetchone())


class TestVerifyMessageActuallyVerifies(unittest.TestCase):
    """Watch this fail against the old two-rule version: it passed."""

    def setUp(self):
        self.con = tracker()
        self.addCleanup(self.con.close)
        self.job = job_row(self.con)

    def test_an_invented_employer_and_team_is_rejected(self):
        with self.assertRaises(FabricationError) as caught:
            outreach.verify_message(INVENTED, PROFILE, job=self.job)
        message = str(caught.exception).lower()
        for word in ("globex", "twelve", "certification"):
            self.assertIn(word, message, word)

    def test_the_banned_list_still_applies(self):
        with self.assertRaises(FabricationError):
            outreach.verify_message("I run Kubernetes.", PROFILE)

    def test_a_conferred_degree_is_still_rejected(self):
        with self.assertRaises(FabricationError):
            outreach.verify_message("I graduated with honours.", PROFILE)

    def test_the_profile_s_own_words_pass(self):
        body = ("Hi Sam, I commissioned alarm panels for commercial "
                "buildings. Open to a quick referral? Thanks,")
        self.assertEqual(
            outreach.verify_message(body, PROFILE, job=self.job), body)

    def test_the_recipient_may_be_named(self):
        """A message addressed to nobody is not a message."""
        contact = {"name": "Sam Okonkwo", "title": "Support Lead",
                   "company_name": "Replit"}
        body = "Hi Sam Okonkwo, I commissioned alarm panels. Thanks,"
        self.assertEqual(
            outreach.unsupported_words(body, PROFILE, self.job, contact), [])

    def test_the_posting_s_stack_may_be_named(self):
        body = "Hi Sam, I commissioned alarm panels. I have read about Zendesk."
        self.assertNotIn("zendesk",
                         outreach.unsupported_words(body, PROFILE, self.job))

    def test_the_postings_prose_is_not_a_licence_to_claim_it(self):
        """The description is shown to the model, never added to the allowed
        vocabulary: otherwise the employer's words about the job come back as
        the candidate's words about themselves."""
        body = "Hi Sam, I handle customer tickets. Thanks,"
        self.assertIn("tickets",
                      outreach.unsupported_words(body, PROFILE, self.job))


class TestTheMessageIsSigned(unittest.TestCase):
    """The prompt promised a name was added afterwards. Nothing added it."""

    def test_the_name_is_appended(self):
        self.assertTrue(outreach.sign("Thanks,", PROFILE).endswith("Dana"))

    def test_it_is_not_appended_twice(self):
        once = outreach.sign("Thanks,", PROFILE)
        self.assertEqual(outreach.sign(once, PROFILE), once)

    def test_a_profile_with_no_name_is_left_alone(self):
        self.assertEqual(outreach.sign("Thanks,", {}), "Thanks,")


class TestTheSalutation(unittest.TestCase):
    """The model was handed a full name and guessed which part to use."""

    def test_the_first_name(self):
        self.assertEqual(outreach.salutation("Sam Okonkwo"), "Sam")

    def test_an_honorific_is_not_a_name(self):
        self.assertEqual(outreach.salutation("Dr. Alice Hernandez Ruiz"),
                         "Alice")

    def test_an_empty_record_degrades_rather_than_inventing(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertEqual(outreach.salutation(value), "there")


class TestTheDraftUsesThePostingAndItsTrack(unittest.TestCase):
    """Scenario: what actually reaches the model."""

    def setUp(self):
        self.con = tracker()
        self.addCleanup(self.con.close)
        self.seen = []

    def fake_complete(self, prompt, **kw):
        self.seen.append(prompt)
        return mock.Mock(text="Hi Sam, I commissioned alarm panels for "
                              "commercial buildings. Open to a referral?",
                         usage=mock.Mock(model="test"))

    def draft(self):
        cid = outreach.add_contact(self.con, name="Sam Okonkwo", company_id=1,
                                   title="Support Lead")
        with mock.patch("jsa.outreach.llm.complete",
                        side_effect=self.fake_complete):
            return outreach.draft(self.con, PROFILE, contact_id=cid, job_id=7,
                                  channel="linkedin_dm")

    def test_the_posting_reaches_the_model(self):
        self.draft()
        self.assertIn("Handle customer tickets.", self.seen[0])

    def test_the_recipient_s_first_name_is_given_not_guessed(self):
        self.draft()
        self.assertIn("Address them as Sam", self.seen[0])

    def test_the_job_s_own_track_is_used_not_a_hardcoded_one(self):
        """This posting is stored track='sales'. It used to be told
        'engineering' regardless, so the fallback was always wrong for a
        posting the tracker had already classified."""
        seen = {}
        real = outreach.select_bullets

        def capture(profile, description, track, **kw):
            seen["track"] = track
            return real(profile, description, track, **kw)

        with mock.patch("jsa.outreach.select_bullets", side_effect=capture):
            self.draft()
        self.assertEqual(seen["track"], "sales")

    def test_the_result_is_signed(self):
        self.assertTrue(self.draft().body.endswith("Dana"))


class TestTheAllowlistIsOnlyWords(unittest.TestCase):
    """A comment inside the allowlist string is not a comment.

    OUTREACH_CONNECTIVES is built by splitting a triple-quoted string. A "#"
    line inside it is split like any other line, so every word of the comment
    -- "purpose", "message", "claim", "sender" -- silently became supported
    vocabulary, weakening the guard it was written to document. It happened
    once, in this file's own history.
    """

    def test_every_entry_is_a_bare_word(self):
        for word in outreach.OUTREACH_CONNECTIVES:
            with self.subTest(word=word):
                self.assertTrue(word.isalpha(), f"{word!r} is not a word")

    def test_no_claim_word_slipped_in(self):
        """The rule from ADR 0007: no skill, tool, outcome or quality."""
        for word in ("claim", "purpose", "message", "expertise", "proven",
                     "stakeholders", "diagnosing", "kubernetes", "python"):
            with self.subTest(word=word):
                self.assertNotIn(word, outreach.OUTREACH_CONNECTIVES)

    def test_the_sender_may_sign_their_own_name(self):
        """Caught by a test after a live run passed only because the sender's
        name happened to collide with a word in the allowlist."""
        con = tracker()
        self.addCleanup(con.close)
        signed = outreach.sign("Hi Sam, I commissioned alarm panels.", PROFILE)
        self.assertEqual(
            outreach.unsupported_words(signed, PROFILE, job_row(con)), [])


class TestItStillCannotSend(unittest.TestCase):
    """Unchanged by any of this, and re-asserted because it changed a lot."""

    def test_no_transport_is_importable_from_this_module(self):
        source = (Path(outreach.__file__)).read_text(encoding="utf-8")
        for forbidden in ("smtplib", "import requests", "sendmail",
                          "httpx.post", "requests.post", "send_message"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_a_draft_is_queued_for_a_human_not_marked_sent(self):
        con = tracker()
        self.addCleanup(con.close)
        cid = outreach.add_contact(con, name="Sam Okonkwo", company_id=1)
        with mock.patch("jsa.outreach.llm.complete", return_value=mock.Mock(
                text="Hi Sam, I commissioned alarm panels.",
                usage=mock.Mock(model="test"))):
            outreach.draft(con, PROFILE, contact_id=cid, job_id=7,
                           channel="linkedin_dm")
        status, = con.execute("SELECT status FROM outreach").fetchone()
        self.assertNotEqual(status, "sent")
        decision, = con.execute(
            "SELECT decision FROM approvals WHERE subject_type='outreach'"
        ).fetchone()
        self.assertEqual(decision, "pending")


if __name__ == "__main__":
    unittest.main()
